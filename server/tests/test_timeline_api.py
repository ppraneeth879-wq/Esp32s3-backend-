"""Tests for the memory timeline endpoints and the Google sync endpoints."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIFELOG_STORAGE_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFELOG_SUMMARY_ENABLED", "0")
    monkeypatch.setenv("LIFELOG_PREWARM", "0")

    import importlib

    import app.database as database

    # Point the database at a throwaway file. DB_PATH is read at import time,
    # so it has to be reassigned, not just set in the environment.
    database.DB_PATH = tmp_path / "test.db"

    import app.main as main

    importlib.reload(main)

    with TestClient(main.app) as c:
        yield c

    database.DB_PATH = Path("lifelog.db")


def _seed(client, **row):
    """Insert a memory row directly."""
    import app.database as database

    conn = database.get_conn()
    columns = {
        "vector_id": "v",
        "transcript": "",
        "summary": "",
        "sentiment": "neutral",
        "tags": "[]",
        "topics": "[]",
        "tasks": "[]",
        "people": "[]",
        "dates": "[]",
        "action_items": "[]",
        "created_at": time.time(),
        "is_important": 0,
        "importance": 0.5,
        "category": "memory",
        "reason": "",
        "source": "llm",
        "session_id": "",
        "content_hash": "",
        "external_id": "",
        "starts_at": None,
        "ends_at": None,
        "location": "",
        "status": "",
        "link": "",
    }
    columns.update(row)
    # An event with a start but no end would never be "upcoming", so give it
    # a one hour end unless the caller says otherwise.
    if columns["category"] == "event" and columns["starts_at"] and not columns["ends_at"]:
        columns["ends_at"] = columns["starts_at"] + 3600
    keys = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    cur = conn.execute(
        f"INSERT INTO memories ({keys}) VALUES ({marks})", list(columns.values())
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


# --------------------------------------------------------------- memories


def test_memories_can_be_filtered_by_category(client):
    _seed(client, summary="a spoken memory", category="memory", source="llm")
    _seed(client, summary="Team standup", category="event", source="google_calendar")
    _seed(client, summary="Quarterly report", category="email", source="gmail")

    body = client.get("/api/memories?category=event").json()
    assert body["total"] == 1
    assert len(body["memories"]) == 1
    assert body["memories"][0]["summary"] == "Team standup"

    body = client.get("/api/memories?source=gmail").json()
    assert body["total"] == 1
    assert len(body["memories"]) == 1
    assert body["memories"][0]["category"] == "email"


def test_the_total_matches_the_filter(client):
    """A filtered list must report the filtered count, not the whole table."""
    _seed(client, summary="a spoken memory", category="memory")
    _seed(client, summary="an event", category="event")
    _seed(client, summary="an email", category="email")

    body = client.get("/api/memories?category=email").json()
    assert body["total"] == 1
    assert len(body["memories"]) == 1

    body = client.get("/api/memories").json()
    assert body["total"] == 3


def test_the_recent_order_puts_the_newest_first(client):
    """order=recent sorts by the actual time, not by insertion order."""
    now = time.time()
    _seed(client, summary="older mail", category="email", starts_at=now - 7200)
    _seed(client, summary="newer mail", category="email", starts_at=now - 60)
    _seed(client, summary="middle mail", category="email", starts_at=now - 3600)

    body = client.get("/api/memories?category=email&order=recent").json()
    summaries = [m["summary"] for m in body["memories"]]
    assert summaries == ["newer mail", "middle mail", "older mail"]

    body = client.get("/api/memories?category=email&order=oldest").json()
    summaries = [m["summary"] for m in body["memories"]]
    assert summaries == ["older mail", "middle mail", "newer mail"]


def test_memories_upcoming_filter(client):
    now = time.time()
    _seed(client, summary="past event", category="event", starts_at=now - 3600)
    _seed(client, summary="soon event", category="event", starts_at=now + 3600)
    _seed(client, summary="later event", category="event", starts_at=now + 86400 * 30)
    _seed(client, summary="a memory", category="memory")

    body = client.get("/api/memories?upcoming=1&days=14").json()
    summaries = [m["summary"] for m in body["memories"]]
    assert "soon event" in summaries
    assert "past event" not in summaries
    assert "later event" not in summaries


def test_upcoming_endpoint_is_ordered_by_start_time(client):
    now = time.time()
    _seed(client, summary="later", category="event", starts_at=now + 7200)
    _seed(client, summary="sooner", category="event", starts_at=now + 60)
    _seed(client, summary="a memory", category="memory")

    body = client.get("/api/upcoming").json()
    assert body["count"] == 2
    assert body["events"][0]["summary"] == "sooner"
    assert body["events"][1]["summary"] == "later"
    assert body["next"]["summary"] == "sooner"


def test_upcoming_endpoint_excludes_past_events(client):
    now = time.time()
    _seed(client, summary="yesterday", category="event", starts_at=now - 86400)
    body = client.get("/api/upcoming").json()
    assert body["count"] == 0
    assert body["next"] is None


def test_an_event_that_started_but_has_not_finished_is_still_upcoming(client):
    """A meeting that started an hour ago is still coming up."""
    now = time.time()
    _seed(
        client,
        summary="in progress",
        category="event",
        starts_at=now - 3600,
        ends_at=now + 3600,
    )
    body = client.get("/api/upcoming").json()
    assert body["count"] == 1
    assert body["events"][0]["summary"] == "in progress"


def test_an_all_day_event_that_began_at_midnight_is_still_upcoming(client):
    """An all-day event for today has not finished yet."""
    from datetime import datetime, timedelta

    now = time.time()
    local = datetime.fromtimestamp(now).astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    _seed(
        client,
        summary="all day today",
        category="event",
        starts_at=midnight,
        ends_at=midnight + 86400,
    )
    body = client.get("/api/upcoming").json()
    assert body["count"] == 1
    assert body["events"][0]["summary"] == "all day today"


def test_the_memory_row_exposes_the_event_fields(client):
    now = time.time()
    _seed(
        client,
        summary="Team standup",
        category="event",
        source="google_calendar",
        starts_at=now,
        ends_at=now + 3600,
        location="Room 3",
        status="confirmed",
        link="https://calendar.google.com/event?eid=x",
    )
    row = client.get("/api/memories?category=event").json()["memories"][0]
    assert row["starts_at"] is not None
    assert row["ends_at"] is not None
    assert row["location"] == "Room 3"
    assert row["status"] == "confirmed"
    assert row["link"] == "https://calendar.google.com/event?eid=x"


# ------------------------------------------------------------- dashboard


def test_daily_briefing_contains_upcoming_and_important_mail(client):
    now = time.time()
    _seed(client, summary="Team standup", category="event", starts_at=now + 3600)
    _seed(
        client,
        summary="Quarterly report",
        category="email",
        is_important=1,
        starts_at=now - 60,
    )
    body = client.get("/api/dashboard/daily-briefing").json()
    assert len(body["important_emails"]) == 1
    assert body["important_emails"][0]["summary"] == "Quarterly report"
    assert len(body["emails"]) == 1


def test_email_entries_carry_the_gmail_shape(client):
    """Clients read the subject from payload.headers, like a Gmail message."""
    now = time.time()
    _seed(
        client,
        summary="Quarterly report ready",
        transcript="The numbers look good.",
        category="email",
        source="gmail",
        starts_at=now,
        ends_at=None,
        people='["Sarah Smith", "sarah@example.com"]',
        external_id="gmail:abc123",
    )
    row = client.get("/api/memories?category=email").json()["memories"][0]

    assert row["subject"] == "Quarterly report ready"
    assert row["snippet"] == "The numbers look good."
    assert row["id"] == "abc123"
    headers = {h["name"]: h["value"] for h in row["payload"]["headers"]}
    assert headers["Subject"] == "Quarterly report ready"
    assert "Sarah Smith" in headers["From"]


def test_the_daily_briefing_mail_is_newest_first(client):
    now = time.time()
    _seed(client, summary="older mail", category="email", starts_at=now - 7200)
    _seed(client, summary="newer mail", category="email", starts_at=now - 60)
    body = client.get("/api/dashboard/daily-briefing").json()
    summaries = [m["summary"] for m in body["important_emails"]]
    assert summaries == ["newer mail", "older mail"]


def test_dashboard_summary_counts_upcoming(client):
    now = time.time()
    _seed(client, summary="soon", category="event", starts_at=now + 3600)
    _seed(client, summary="later", category="event", starts_at=now + 86400 * 30)
    body = client.get("/api/dashboard/summary").json()
    assert body["upcoming"] == 1
    assert body["by_category"]["event"] == 2


# ----------------------------------------------------------------- sync


def test_google_sync_endpoint_reports_a_result(client, monkeypatch):
    import app.main as main
    import app.google_indexer as indexer

    monkeypatch.setattr(
        indexer, "sync_all", lambda *a, **k: {"events": 3, "emails": 2}
    )
    resp = client.post("/api/google/sync?force=1")
    assert resp.status_code == 200
    assert resp.json()["result"]["events"] == 3


def test_google_sync_status_endpoint(client):
    body = client.get("/api/google/sync/status").json()
    assert "last_sync_at" in body
