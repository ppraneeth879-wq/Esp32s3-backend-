"""Tests for the /summaries HTTP endpoints."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    """A TestClient wired to a throwaway storage directory."""
    monkeypatch.setenv("LIFELOG_STORAGE_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFELOG_SUMMARY_ENABLED", "0")
    # Do not download models during the tests.
    monkeypatch.setenv("LIFELOG_PREWARM", "0")

    import importlib

    import app.main as main

    importlib.reload(main)

    with TestClient(main.app) as c:
        yield c


def test_summaries_latest_is_null_when_there_is_nothing_yet(client):
    resp = client.get("/summaries/latest")
    assert resp.status_code == 200
    assert resp.json() == {"summary": None}


def test_summaries_list_is_empty_at_first(client):
    resp = client.get("/summaries")
    assert resp.status_code == 200
    assert resp.json() == {"summaries": []}


def test_summaries_status_reports_health(client):
    resp = client.get("/summaries/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "budget_remaining" in body
    assert "requests_today" in body
    assert "model" in body
    assert "journal_records" in body


def test_route_order_latest_and_status_are_not_treated_as_keys(client):
    """`/summaries/status` must not be matched by `/summaries/{key}`."""
    resp = client.get("/summaries/status")
    assert resp.status_code == 200
    assert "budget_remaining" in resp.json()

    resp = client.get("/summaries/latest")
    assert resp.status_code == 200
    assert "summary" in resp.json()


def test_summaries_by_key_returns_404_for_an_unknown_key(client):
    resp = client.get("/summaries/2026-10-22T0139")
    assert resp.status_code == 404


def test_summaries_run_returns_a_document(client, monkeypatch):
    """POST /summaries/run triggers a pass."""
    import app.main as main

    # Put one record in the journal.
    main.journal.append_text(text="hello world", session_id="s1", timestamp=1000.0)

    def fake_run_once(now=None):
        return {
            "key": "2026-10-22T0139",
            "summary": "- hello",
            "segment_count": 1,
            "engine": "stub",
            "fallback_used": False,
        }

    monkeypatch.setattr(main.summary_scheduler, "run_once", fake_run_once)
    resp = client.post("/summaries/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ran"] is True
    assert body["summary"]["key"] == "2026-10-22T0139"


def test_summaries_run_reports_when_nothing_was_produced(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.summary_scheduler, "run_once", lambda now=None: None)
    resp = client.post("/summaries/run")
    assert resp.status_code == 200
    assert resp.json() == {"summary": None, "ran": False}
