"""Tests for the rolling-window summarizer components."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.summarizer import (
    DEFAULT_MODEL,
    EXTRACTIVE_ENGINE,
    ROUTER_FALLBACK_MODEL,
    Summarizer,
    extractive_summary,
    normalize_whitespace,
    split_sentences,
)


# ---------------------------------------------------------------- sentences


def test_split_sentences_keeps_decimal_numbers_intact():
    text = "We measured 99.5 percent accuracy. That was good."
    sentences = split_sentences(text)
    joined = " ".join(sentences)
    assert "99.5 percent" in joined
    assert "99.5" in joined
    # the decimal must not have been split into two sentences
    assert not any(s.strip() == "99." for s in sentences)
    assert not any(s.strip() == "5 percent accuracy." for s in sentences)


def test_split_sentences_handles_multiple_punctuation():
    text = "Is that right? Yes! It is. Fine."
    sentences = split_sentences(text)
    assert len(sentences) == 4
    assert sentences[0] == "Is that right?"
    assert sentences[1] == "Yes!"


def test_split_sentences_of_empty_text_is_empty():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_split_sentences_keeps_version_numbers():
    text = "We deployed version 2.1.3 to production yesterday."
    joined = " ".join(split_sentences(text))
    assert "2.1.3" in joined


# -------------------------------------------------------------- whitespace


def test_normalize_whitespace_flattens_lines():
    raw = "First line of speech\nsecond line of speech\nthird line"
    flat = normalize_whitespace(raw)
    assert "\n" not in flat
    assert flat == "First line of speech second line of speech third line"


def test_normalize_whitespace_of_empty_text():
    assert normalize_whitespace("") == ""
    assert normalize_whitespace(None) == ""


# ------------------------------------------------------------- extractive


def test_extractive_summary_returns_bullets():
    text = (
        "The quarterly budget review is scheduled for Thursday. "
        "Maria will present the revenue forecast for the enterprise segment. "
        "I need to send Priya the updated headcount spreadsheet before Thursday. "
        "Lunch was fine."
    )
    summary = extractive_summary(text)
    assert summary
    for line in summary.splitlines():
        assert line.startswith("- ")


def test_extractive_summary_of_empty_text_is_empty():
    assert extractive_summary("") == ""
    assert extractive_summary("   ") == ""


def test_extractive_summary_prefers_information_dense_sentences():
    text = (
        "Okay. Yeah. Sure. "
        "I have to book the flight to Berlin for the client visit next Tuesday. "
        "The invoice number is 4471 and the amount is 99.5 dollars."
    )
    summary = extractive_summary(text)
    assert "Berlin" in summary or "invoice" in summary.lower()


# ------------------------------------------------------------- summarizer


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    """Minimal httpx.Client stand-in."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(json)
        if not self.responses:
            raise AssertionError("no response configured")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


def _ok(content: str) -> _FakeResponse:
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def test_summarizer_reports_the_model_that_actually_answered():
    client = _FakeClient([_ok("- did the thing\n- and another thing")])
    s = Summarizer(model=DEFAULT_MODEL, api_key="key", client=client)
    result = s.summarize("Some speech about the thing and another thing.")
    assert result.engine == DEFAULT_MODEL
    assert result.fallback_used is False
    assert "did the thing" in result.summary


def test_summarizer_falls_back_to_the_router_on_failure():
    client = _FakeClient([_FakeResponse(429, text="rate limited"), _ok("- router answer")])
    s = Summarizer(model=DEFAULT_MODEL, api_key="key", client=client)
    result = s.summarize("Some speech.")
    assert result.engine == ROUTER_FALLBACK_MODEL
    assert result.fallback_used is False
    assert result.summary == "- router answer"


def test_summarizer_falls_back_to_extractive_when_every_model_fails():
    client = _FakeClient([_FakeResponse(500, text="boom"), _FakeResponse(429, text="nope")])
    s = Summarizer(model=DEFAULT_MODEL, api_key="key", client=client)
    result = s.summarize("I have to call the bank about my card today.")
    assert result.engine == EXTRACTIVE_ENGINE
    assert result.fallback_used is True
    assert result.summary


def test_summarizer_uses_extractive_when_no_api_key():
    s = Summarizer(model=DEFAULT_MODEL, api_key="", client=_FakeClient([]))
    result = s.summarize("I have to call the bank about my card today.")
    assert result.engine == EXTRACTIVE_ENGINE
    assert result.fallback_used is True
    assert "no OPENROUTER_API_KEY" in result.error


def test_daily_budget_stops_further_api_calls():
    client = _FakeClient([_ok("- one"), _ok("- two"), _ok("- three")])
    s = Summarizer(model=DEFAULT_MODEL, api_key="key", daily_budget=2, client=client)
    s.summarize("first chunk of speech")
    s.summarize("second chunk of speech")
    assert s.requests_today == 2
    assert s.budget_remaining == 0

    # The third call must not reach the API at all.
    result = s.summarize("third chunk of speech")
    assert s.requests_today == 2
    assert len(client.calls) == 2
    assert result.engine == EXTRACTIVE_ENGINE
    assert result.fallback_used is True
    assert "budget" in result.error


def test_a_typical_hour_uses_a_single_api_request():
    client = _FakeClient([_ok("- one"), _ok("- two")])
    s = Summarizer(model=DEFAULT_MODEL, api_key="key", client=client)
    # An hour of speech is far larger than the input cap.
    hour_of_speech = " ".join(
        f"This is sentence number {i} about the release plan and the deploy date."
        for i in range(2000)
    )
    assert len(hour_of_speech) > 100000
    s.summarize(hour_of_speech)
    assert len(client.calls) == 1


def test_summarizer_status_reports_budget():
    client = _FakeClient([_ok("- one")])
    s = Summarizer(model=DEFAULT_MODEL, api_key="key", daily_budget=10, client=client)
    s.summarize("speech")
    status = s.status()
    assert status["daily_budget"] == 10
    assert status["requests_today"] == 1
    assert status["budget_remaining"] == 9
    assert status["api_configured"] is True


def test_summarizer_of_empty_text_makes_no_api_call():
    client = _FakeClient([])
    s = Summarizer(model=DEFAULT_MODEL, api_key="key", client=client)
    result = s.summarize("   ")
    assert result.summary == ""
    assert len(client.calls) == 0
