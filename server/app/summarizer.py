"""LLM summarizer for rolling transcript windows.

Three levels of fallback, and the engine that actually produced the text is
always reported back honestly:

1. Primary model (free tier, 1M context).
2. ``openrouter/free`` - routes to whichever free model is up right now.
3. Offline extractive summary - no network at all.

The OpenRouter free tier allows a small number of requests per day, so the
summarizer tracks its own daily budget and stops calling the API once it is
exhausted rather than burning requests on 429s.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import httpx

logger = logging.getLogger("lifelog.summarizer")

__all__ = [
    "MAX_INPUT_CHARS",
    "DEFAULT_MODEL",
    "ROUTER_FALLBACK_MODEL",
    "SummaryResult",
    "Summarizer",
    "normalize_whitespace",
    "split_sentences",
    "extractive_summary",
]

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
ROUTER_FALLBACK_MODEL = "openrouter/free"

# One hour of speech is one request. Lowering this multiplies the number of
# daily API calls and will exhaust the free-tier quota.
MAX_INPUT_CHARS = 100000

EXTRACTIVE_ENGINE = "extractive"

SUMMARY_SYSTEM_PROMPT = (
    "You summarise a continuous stream of speech captured by a wearable microphone. "
    "Write a concise summary as a markdown bullet list.\n"
    "Rules:\n"
    "- 3 to 6 bullets, each one short and factual.\n"
    "- Start each bullet with '- '.\n"
    "- Only use facts present in the text. Never invent anything.\n"
    "- Capture decisions, commitments, plans, deadlines, names and numbers exactly.\n"
    "- Keep numbers, dates and names verbatim.\n"
    "- Do not add a preamble or a conclusion. Output only the bullets."
)

# Words too common to signal importance when scoring sentences.
_STOPWORDS = frozenset(
    """
    a an the and or but if then else of to in on at by for with from as is are was were be been
    being it its this that these those i me my we our you your he she they them their do does did
    have has had will would can could should just about so very really quite much more most some
    any all no not yes ok okay right well now here there when where what which who whom how why
    like get got go going went come came make made take took see saw know knew think thought
    want wanted need needed say said tell told look looking really thing things stuff lot
    """.split()
)


def normalize_whitespace(text: str) -> str:
    """Flatten a transcript into a single line.

    The raw transcript is one segment per line. A model that sees hard line
    breaks treats each line as its own item and splits facts across bullets.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    """Split text into sentences without breaking decimals.

    A naive period split turns "99.5 percent" into "99." + "5 percent". A
    period is only a sentence boundary when it is *not* between two digits.
    """
    if not text:
        return []
    text = normalize_whitespace(text)
    # Protect decimals, versions and times ("99.5", "3.12", "1.5.2").
    protected = re.sub(r"(?<=\d)\.(?=\d)", "\u0000", text)
    parts = re.split(r"(?<=[.!?])\s+", protected)
    out: list[str] = []
    for part in parts:
        cleaned = part.replace("\u0000", ".").strip()
        if cleaned:
            out.append(cleaned)
    return out


def _distinctive_words(sentence: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", sentence.lower())
    return {w for w in words if len(w) > 3 and w not in _STOPWORDS}


def extractive_summary(text: str, max_sentences: int = 4) -> str:
    """Offline fallback: pick the sentences that introduce the most new content.

    Each sentence is scored by how many *new* distinctive words it introduces
    relative to the sentences before it. The top scoring sentences are emitted
    as bullets, in their original order so the result still reads sensibly.
    """
    sentences = split_sentences(text)
    if not sentences:
        return ""

    seen: set[str] = set()
    scored: list[tuple[int, float, str]] = []
    for index, sentence in enumerate(sentences):
        words = _distinctive_words(sentence)
        new_words = words - seen
        seen |= words
        # Favour sentences that are both new and information-dense.
        score = len(new_words) / (1.0 + 0.15 * len(sentence.split()) / 10.0)
        scored.append((index, score, sentence))

    scored.sort(key=lambda item: item[1], reverse=True)
    chosen = sorted(scored[:max_sentences], key=lambda item: item[0])
    return "\n".join(f"- {sentence}" for _, _, sentence in chosen)


@dataclass
class SummaryResult:
    """Outcome of one summarization attempt."""

    summary: str
    engine: str
    fallback_used: bool = False
    error: str = ""


class Summarizer:
    """Summarizes text with an LLM, falling back gracefully.

    The engine field of the result always names the engine that actually
    produced the text. If the API was unavailable the engine is
    ``"extractive"`` and ``fallback_used`` is ``True``. A summary claiming an
    LLM ran when it did not would hide a broken configuration.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str = "",
        daily_budget: int = 50,
        timeout: float = 120.0,
        client: Optional[httpx.Client] = None,
        max_input_chars: int = MAX_INPUT_CHARS,
    ):
        self.model = model or DEFAULT_MODEL
        self.api_key = api_key or ""
        self.daily_budget = max(0, int(daily_budget))
        self.timeout = timeout
        self.max_input_chars = max_input_chars
        self._client = client
        self._owns_client = client is None
        # Budget bookkeeping
        self._budget_day: date = date.today()
        self._requests_today = 0

    # -- budget ---------------------------------------------------------

    def _roll_budget_if_needed(self) -> None:
        """Reset the daily counter when the calendar day rolls over."""
        today = date.today()
        if today != self._budget_day:
            self._budget_day = today
            self._requests_today = 0

    @property
    def requests_today(self) -> int:
        self._roll_budget_if_needed()
        return self._requests_today

    @property
    def budget_remaining(self) -> int:
        self._roll_budget_if_needed()
        return max(0, self.daily_budget - self._requests_today)

    @property
    def has_budget(self) -> bool:
        return self.budget_remaining > 0

    # -- http -----------------------------------------------------------

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
            self._owns_client = True
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def _call_api(self, model: str, text: str) -> tuple[bool, str]:
        """One API attempt. Returns ``(ok, content_or_error)``."""
        if not self.api_key:
            return False, "no OPENROUTER_API_KEY configured"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        }
        try:
            resp = self._get_client().post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            return False, f"bad response shape: {e}"
        if not content or not str(content).strip():
            return False, "empty content"
        return True, str(content).strip()

    # -- public ---------------------------------------------------------

    def summarize(self, text: str) -> SummaryResult:
        """Summarize ``text``, falling back to extraction when needed."""
        cleaned = normalize_whitespace(text)
        if not cleaned:
            return SummaryResult(summary="", engine=EXTRACTIVE_ENGINE, fallback_used=True)

        if len(cleaned) > self.max_input_chars:
            cleaned = cleaned[: self.max_input_chars]

        if not self.api_key:
            logger.warning(
                "summarizer: no OPENROUTER_API_KEY - using the offline extractive fallback"
            )
            return SummaryResult(
                summary=extractive_summary(cleaned),
                engine=EXTRACTIVE_ENGINE,
                fallback_used=True,
                error="no OPENROUTER_API_KEY configured",
            )

        if not self.has_budget:
            logger.warning(
                "summarizer: daily budget exhausted (%d/%d) - using extractive fallback",
                self.requests_today,
                self.daily_budget,
            )
            return SummaryResult(
                summary=extractive_summary(cleaned),
                engine=EXTRACTIVE_ENGINE,
                fallback_used=True,
                error="daily request budget exhausted",
            )

        errors: list[str] = []
        for model in self._model_chain():
            self._roll_budget_if_needed()
            self._requests_today += 1
            ok, content = self._call_api(model, cleaned)
            if ok:
                return SummaryResult(summary=content, engine=model, fallback_used=False)
            errors.append(f"{model}: {content}")
            logger.warning("summarizer: model %s failed: %s", model, content[:200])

        logger.warning("summarizer: all models failed, using extractive fallback")
        return SummaryResult(
            summary=extractive_summary(cleaned),
            engine=EXTRACTIVE_ENGINE,
            fallback_used=True,
            error="; ".join(errors)[:500],
        )

    def _model_chain(self) -> list[str]:
        """Primary model first, then the router fallback, without duplicates."""
        chain = [self.model, ROUTER_FALLBACK_MODEL]
        seen: set[str] = set()
        out: list[str] = []
        for model in chain:
            if model and model not in seen:
                seen.add(model)
                out.append(model)
        return out

    def status(self) -> dict[str, Any]:
        """Health and remaining budget, for ``GET /summaries/status``."""
        self._roll_budget_if_needed()
        return {
            "model": self.model,
            "router_fallback": ROUTER_FALLBACK_MODEL,
            "api_configured": bool(self.api_key),
            "daily_budget": self.daily_budget,
            "requests_today": self._requests_today,
            "budget_remaining": self.budget_remaining,
            "max_input_chars": self.max_input_chars,
        }
