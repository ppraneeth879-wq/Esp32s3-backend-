"""Tests for indexing Google Calendar events and important mail into memories."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import google_indexer
from app.database import get_conn, init_db


@pytest.fixture()
def db(tmp_path: Path, monkeypatch):
    """A throwaway database."""
    monkeypatch.setenv("LIFELOG_DB_PATH", str(tmp_path / "test.db"))
    import app.database as database

    database.DB_PATH = tmp_path / "test.db"
    init_db()
    return database


# ------------------------------------------------------------ time parsing


def test_calendar_time_normalisation():
    """The Calendar API only accepts RFC 3339. Repair the common mistakes."""
    from app.google_calendar import normalise_time

    # Already valid - passed through untouched.
    assert normalise_time("2026-10-23T10:00:00+05:30") == "2026-10-23T10:00:00+05:30"
    assert normalise_time("2026-10-23T10:00:00Z") == "2026-10-23T10:00:00Z"

    # Missing the seconds - this is what the Android app sends.
    assert normalise_time("2026-10-22T00:00+05:30") == "2026-10-22T00:00:00+05:30"
    assert normalise_time("2026-10-22T00:00+0530") == "2026-10-22T00:00:00+0530"

    # Bare date.
    assert normalise_time("2026-10-22") == "2026-10-22T00:00:00+00:00"

    # Missing the offset.
    assert normalise_time("2026-10-22T00:00:00") == "2026-10-22T00:00:00+00:00"

    # Missing both: 2026-10-22T00:00
    assert normalise_time("2026-10-22T00:00") == "2026-10-22T00:00:00+00:00"

    # Nothing usable.
    assert normalise_time("not a time") is None
    assert normalise_time("") is None
    assert normalise_time(None) is None


def test_list_events_normalises_the_time_bounds(monkeypatch):
    from app import google_calendar

    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"items": []}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["params"] = params
        return _Resp()

    monkeypatch.setattr(google_calendar.httpx, "get", fake_get)
    google_calendar.list_events(
        "primary", "2026-10-22T00:00+05:30", "2026-11-21T23:59:59+05:30"
    )
    assert seen["params"]["timeMin"] == "2026-10-22T00:00:00+05:30"
    assert seen["params"]["timeMax"] == "2026-11-21T23:59:59+05:30"


def test_list_events_drops_an_unusable_time_bound(monkeypatch):
    from app import google_calendar

    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"items": []}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["params"] = params
        return _Resp()

    monkeypatch.setattr(google_calendar.httpx, "get", fake_get)
    google_calendar.list_events("primary", "garbage", None)
    assert "timeMin" not in seen["params"]
    assert "timeMax" not in seen["params"]


def test_parse_google_datetime_with_offset():
    ts = google_indexer.parse_google_time(
        {"dateTime": "2026-10-23T10:00:00+05:30"}
    )
    assert ts is not None
    expected = datetime(2026, 10, 23, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))).timestamp()
    assert ts == expected


def test_parse_google_all_day_date():
    ts = google_indexer.parse_google_time({"date": "2026-10-23"})
    assert ts is not None
    local = datetime.fromtimestamp(ts).astimezone()
    assert local.date() == datetime(2026, 10, 23).date()
    # Local midnight, not UTC midnight.
    assert local.hour == 0


def test_all_day_event_times_span_the_whole_day():
    """An all-day event starts at local midnight and ends at the next one."""
    event = {"start": {"date": "2026-10-22"}, "end": {"date": "2026-10-23"}}
    starts_at, ends_at, all_day = google_indexer.parse_event_times(event)
    assert all_day is True
    assert starts_at is not None and ends_at is not None
    assert ends_at > starts_at
    assert ends_at - starts_at == 86400


def test_timed_event_times():
    event = {
        "start": {"dateTime": "2026-10-23T10:00:00+05:30"},
        "end": {"dateTime": "2026-10-23T11:00:00+05:30"},
    }
    starts_at, ends_at, all_day = google_indexer.parse_event_times(event)
    assert all_day is False
    assert ends_at - starts_at == 3600


def test_an_event_that_started_today_is_still_upcoming():
    """An all-day event that began at midnight has not finished yet.

    Regression test: the "upcoming" filter used to compare starts_at with
    now, which hid an all-day event for the current day.
    """
    import time as _time

    now = _time.time()
    local = datetime.fromtimestamp(now).astimezone()
    today = local.strftime("%Y-%m-%d")
    tomorrow = (local + timedelta(days=1)).strftime("%Y-%m-%d")
    event = {"start": {"date": today}, "end": {"date": tomorrow}}
    starts_at, ends_at, _ = google_indexer.parse_event_times(event)
    assert starts_at <= now < ends_at


def test_parse_google_time_of_nothing():
    assert google_indexer.parse_google_time(None) is None
    assert google_indexer.parse_google_time({}) is None
    assert google_indexer.parse_google_time("") is None
    assert google_indexer.parse_google_time({"dateTime": "not a date"}) is None


# --------------------------------------------------------------- calendar


def _event(event_id="evt1", summary="Team standup", start_offset_hours=2):
    start = datetime.now(timezone.utc) + timedelta(hours=start_offset_hours)
    end = start + timedelta(hours=1)
    return {
        "id": event_id,
        "summary": summary,
        "description": "Daily sync",
        "location": "Room 3",
        "status": "confirmed",
        "htmlLink": f"https://calendar.google.com/event?eid={event_id}",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "attendees": [{"email": "sarah@example.com"}, {"email": "tom@example.com"}],
    }


def test_calendar_events_become_memories(db, monkeypatch):
    monkeypatch.setattr(
        google_indexer.google_calendar, "list_events", lambda *a, **k: [_event()]
    )
    result = google_indexer.sync_calendar()
    assert result["events"] == 1

    conn = get_conn()
    row = conn.execute("SELECT * FROM memories").fetchone()
    conn.close()
    assert row is not None
    assert row["category"] == "event"
    assert row["source"] == "google_calendar"
    assert row["summary"] == "Team standup"
    assert row["location"] == "Room 3"
    assert row["external_id"] == "gcal:evt1"
    assert row["starts_at"] is not None
    assert row["ends_at"] is not None
    assert json.loads(row["people"]) == ["sarah@example.com", "tom@example.com"]


def test_calendar_sync_updates_instead_of_duplicating(db, monkeypatch):
    events = [_event(summary="Original title")]
    monkeypatch.setattr(
        google_indexer.google_calendar, "list_events", lambda *a, **k: list(events)
    )
    google_indexer.sync_calendar()
    events[0] = _event(summary="Renamed event")
    google_indexer.sync_calendar()

    conn = get_conn()
    rows = conn.execute("SELECT * FROM memories").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["summary"] == "Renamed event"


def test_calendar_sync_survives_a_google_error(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Google is down")

    monkeypatch.setattr(google_indexer.google_calendar, "list_events", boom)
    result = google_indexer.sync_calendar()
    assert result["events"] == 0
    assert "error" in result


# ------------------------------------------------------------------ gmail


def _message(message_id="msg1", subject="Quarterly report ready"):
    received = datetime.now(timezone.utc) - timedelta(hours=1)
    return {
        "id": message_id,
        "snippet": "The numbers look good, see you then.",
        "internalDate": str(int(received.timestamp() * 1000)),
        "labelIds": ["INBOX", "IMPORTANT", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": '"Sarah Smith" <sarah@example.com>'},
            ]
        },
    }


def test_important_mail_becomes_a_memory(db, monkeypatch):
    monkeypatch.setattr(
        google_indexer.google_gmail, "list_messages", lambda *a, **k: [_message()]
    )
    result = google_indexer.sync_recent_emails()
    assert result["emails"] == 1

    conn = get_conn()
    row = conn.execute("SELECT * FROM memories").fetchone()
    conn.close()
    assert row["category"] == "email"
    assert row["source"] == "gmail"
    assert row["summary"] == "Quarterly report ready"
    assert row["is_important"] == 1
    assert row["external_id"] == "gmail:msg1"
    assert "Sarah Smith" in json.loads(row["people"])


def test_a_message_with_no_subject_and_no_body_is_not_stored(db, monkeypatch):
    """An empty message has nothing to show, so it is not in the timeline."""
    blank = {
        "id": "blank1",
        "snippet": "",
        "internalDate": "1782448782000",
        "labelIds": ["INBOX", "IMPORTANT"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": ""},
                {"name": "From", "value": "LLM here <llmhere49@gmail.com>"},
            ]
        },
    }
    monkeypatch.setattr(
        google_indexer.google_gmail, "list_messages", lambda *a, **k: [blank]
    )
    result = google_indexer.sync_recent_emails()
    assert result["emails"] == 0
    assert result["emails_skipped"] == 1

    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
    conn.close()
    assert count == 0


def test_an_empty_message_is_removed_from_the_timeline(db, monkeypatch):
    """An earlier copy of an empty message is deleted, not left behind."""
    blank = {
        "id": "blank1",
        "snippet": "",
        "internalDate": "1782448782000",
        "labelIds": ["INBOX", "IMPORTANT"],
        "payload": {"headers": [{"name": "Subject", "value": ""}]},
    }

    def fake_list(*a, **k):
        # First call has a subject, the second one does not.
        if not getattr(fake_list, "done", False):
            fake_list.done = True
            return [
                {
                    "id": "blank1",
                    "snippet": "hello",
                    "internalDate": "1782448782000",
                    "labelIds": ["INBOX", "IMPORTANT"],
                    "payload": {"headers": [{"name": "Subject", "value": "Real"}]},
                }
            ]
        return [blank]

    monkeypatch.setattr(google_indexer.google_gmail, "list_messages", fake_list)
    google_indexer.sync_recent_emails()

    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 1
    conn.close()

    google_indexer.sync_recent_emails()

    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 0
    conn.close()


def test_a_message_with_no_subject_uses_the_snippet(db, monkeypatch):
    blank = {
        "id": "blank2",
        "snippet": "Here is the thing I wanted to tell you about.",
        "internalDate": "1782448782000",
        "labelIds": ["INBOX", "IMPORTANT"],
        "payload": {"headers": [{"name": "Subject", "value": ""}]},
    }
    monkeypatch.setattr(
        google_indexer.google_gmail, "list_messages", lambda *a, **k: [blank]
    )
    google_indexer.sync_recent_emails()

    conn = get_conn()
    row = conn.execute("SELECT * FROM memories").fetchone()
    conn.close()
    assert row["summary"] == "Here is the thing I wanted to tell you about."


def test_an_event_deleted_in_google_is_removed_from_the_timeline(db, monkeypatch):
    """An event the user deletes must not linger in the timeline."""
    monkeypatch.setattr(
        google_indexer.google_calendar, "list_events", lambda *a, **k: [_event()]
    )
    google_indexer.sync_calendar()

    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 1
    conn.close()

    # The event is gone from Google.
    monkeypatch.setattr(
        google_indexer.google_calendar, "list_events", lambda *a, **k: []
    )
    result = google_indexer.sync_calendar()
    assert result["events_removed"] == 1

    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 0
    conn.close()


def test_an_event_outside_the_window_is_not_removed(db, monkeypatch):
    """An event further ahead than the sync window is left alone."""
    far = _event(event_id="far1", summary="Next year")
    far["start"] = {"dateTime": "2027-06-01T10:00:00+05:30"}
    far["end"] = {"dateTime": "2027-06-01T11:00:00+05:30"}
    monkeypatch.setattr(
        google_indexer.google_calendar, "list_events", lambda *a, **k: [far]
    )
    google_indexer.sync_calendar()

    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 1
    conn.close()

    # A later sync does not see it (it is outside the window) and must not
    # delete it.
    monkeypatch.setattr(
        google_indexer.google_calendar, "list_events", lambda *a, **k: []
    )
    result = google_indexer.sync_calendar()
    assert result["events_removed"] == 0

    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"] == 1
    conn.close()


def test_an_event_with_nothing_in_it_is_not_stored(db, monkeypatch):
    """An event with no title and no description is not in the timeline."""
    monkeypatch.setattr(
        google_indexer.google_calendar,
        "list_events",
        lambda *a, **k: [
            {
                "id": "blank1",
                "summary": "",
                "description": "",
                "start": {"dateTime": "2026-10-30T10:00:00+05:30"},
                "end": {"dateTime": "2026-10-30T11:00:00+05:30"},
            }
        ],
    )
    result = google_indexer.sync_calendar()
    assert result["events"] == 0
    assert result["events_skipped"] == 1

    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
    conn.close()
    assert count == 0


def test_the_latest_mail_is_requested(db, monkeypatch):
    """The timeline shows the latest mail, not an importance-filtered subset."""
    seen = {}

    def fake_list(query, max_results=10):
        seen["query"] = query
        return []

    monkeypatch.setattr(google_indexer.google_gmail, "list_messages", fake_list)
    google_indexer.sync_recent_emails()
    assert seen["query"] == "in:anywhere"


def test_an_email_is_only_flagged_important_when_gmail_says_so(db, monkeypatch):
    """The important flag is a truthful signal, not our own filter."""
    plain = _message(message_id="plain1", subject="Just a newsletter")
    plain["labelIds"] = ["INBOX"]  # not flagged by Gmail
    monkeypatch.setattr(
        google_indexer.google_gmail, "list_messages", lambda *a, **k: [plain]
    )
    google_indexer.sync_recent_emails()

    conn = get_conn()
    row = conn.execute("SELECT * FROM memories").fetchone()
    conn.close()
    assert row["summary"] == "Just a newsletter"
    assert row["is_important"] == 0
    assert json.loads(row["tags"]) == ["email"]

    flagged = _message(message_id="flagged1", subject="Urgent: action needed")
    flagged["labelIds"] = ["INBOX", "IMPORTANT"]
    monkeypatch.setattr(
        google_indexer.google_gmail, "list_messages", lambda *a, **k: [flagged]
    )
    google_indexer.sync_recent_emails()

    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM memories WHERE external_id='gmail:flagged1'"
    ).fetchone()
    conn.close()
    assert row["is_important"] == 1
    assert "important" in json.loads(row["tags"])


def test_gmail_sync_survives_a_google_error(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Gmail is down")

    monkeypatch.setattr(google_indexer.google_gmail, "list_messages", boom)
    result = google_indexer.sync_recent_emails()
    assert result["emails"] == 0
    assert "error" in result


# -------------------------------------------------------------- throttling


def test_sync_all_is_throttled(db, monkeypatch):
    calls = {"n": 0}

    def fake_calendar(*a, **k):
        calls["n"] += 1
        return []

    monkeypatch.setattr(google_indexer.google_calendar, "list_events", fake_calendar)
    monkeypatch.setattr(google_indexer.google_gmail, "list_messages", lambda *a, **k: [])

    first = google_indexer.sync_all(force=True)
    assert not first.get("throttled")
    assert calls["n"] == 1

    second = google_indexer.sync_all(force=False)
    assert second.get("throttled") is True
    assert calls["n"] == 1


def test_sync_all_can_be_forced(db, monkeypatch):
    calls = {"n": 0}

    def fake_calendar(*a, **k):
        calls["n"] += 1
        return []

    monkeypatch.setattr(google_indexer.google_calendar, "list_events", fake_calendar)
    monkeypatch.setattr(google_indexer.google_gmail, "list_messages", lambda *a, **k: [])

    google_indexer.sync_all(force=True)
    google_indexer.sync_all(force=True)
    assert calls["n"] == 2
