"""Index Google Calendar events and important Gmail messages into the memories.

The memories list is meant to be the single timeline of a person's life:
what they said, what is coming up, and what needs attention. This module
keeps the Google-sourced half of that timeline up to date.

Entries are upserted on their Google id, so syncing repeatedly updates an
event in place instead of creating duplicates. Entries from Google are kept
distinct from spoken memories by their ``source`` field, and they never touch
the rolling-window summarizer.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .database import get_conn
from . import google_calendar, google_gmail

logger = logging.getLogger("lifelog.google_indexer")

__all__ = [
    "CATEGORY_EVENT",
    "CATEGORY_EMAIL",
    "parse_google_time",
    "parse_event_times",
    "sync_calendar",
    "sync_recent_emails",
    "sync_all",
    "last_sync",
]

CATEGORY_EVENT = "event"
CATEGORY_EMAIL = "email"

SOURCE_CALENDAR = "google_calendar"
SOURCE_GMAIL = "gmail"

# How long to wait between syncs when called without force.
DEFAULT_THROTTLE_SECONDS = 300.0

_last_sync_at: float = 0.0
_last_result: dict[str, Any] = {}


def last_sync() -> dict[str, Any]:
    """When the last sync ran and what it did."""
    return {"last_sync_at": _last_sync_at, **_last_result}


def parse_google_time(value: Any, local_date: bool = True) -> Optional[float]:
    """Parse a Google date/time into an epoch float.

    Handles both ``{"dateTime": "2026-10-23T10:00:00+05:30"}`` and the
    all-day ``{"date": "2026-10-23"}`` form.

    An all-day event has no timezone, so it is taken to start at midnight in
    the *local* timezone. Treating it as UTC would put it hours out and, for
    an event today, in the past.
    """
    if isinstance(value, dict):
        raw = value.get("dateTime") or value.get("date")
        is_date_only = not value.get("dateTime") and bool(value.get("date"))
    else:
        raw = value
        is_date_only = False
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if len(text) == 10:  # all-day event, e.g. "2026-10-23"
        is_date_only = True
        text += "T00:00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        if is_date_only and local_date:
            # Local midnight for an all-day event.
            dt = dt.astimezone()
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_event_times(event: dict) -> tuple[Optional[float], Optional[float], bool]:
    """The start and end of a calendar event, and whether it is all-day.

    For an all-day event Google gives ``{"date": "2026-10-22"}`` for the start
    and ``{"date": "2026-10-23"}`` for the end. The end date is exclusive, so
    the event runs until midnight at the start of that day.
    """
    start_raw = event.get("start") or {}
    end_raw = event.get("end") or {}
    all_day = bool(start_raw.get("date")) and not start_raw.get("dateTime")

    starts_at = parse_google_time(start_raw)
    ends_at = parse_google_time(end_raw)
    if all_day and ends_at is not None and starts_at is not None and ends_at <= starts_at:
        ends_at = ends_at + 86400
    return starts_at, ends_at, all_day


def _iso(value: Optional[float]) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().strftime(
        "%Y-%m-%dT%H:%M:%S%z"
    )


def _upsert_memory(
    conn,
    *,
    external_id: str,
    category: str,
    source: str,
    summary: str,
    transcript: str = "",
    starts_at: Optional[float] = None,
    ends_at: Optional[float] = None,
    location: str = "",
    status: str = "",
    link: str = "",
    people: Optional[list[str]] = None,
    dates: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    topics: Optional[list[str]] = None,
    importance: float = 0.5,
    important: bool = False,
) -> None:
    """Insert or update one Google-sourced memory row."""
    existing = conn.execute(
        "SELECT id FROM memories WHERE external_id=?", (external_id,)
    ).fetchone()

    values = (
        transcript,
        summary,
        "neutral",
        json.dumps(tags or []),
        json.dumps(topics or []),
        json.dumps([]),
        json.dumps(people or []),
        json.dumps(dates or []),
        json.dumps([]),
        time.time(),
        1 if important else 0,
        importance,
        category,
        "",
        source,
        "",
        "",
        external_id,
        starts_at,
        ends_at,
        location,
        status,
        link,
    )

    if existing:
        conn.execute(
            """UPDATE memories SET transcript=?, summary=?, sentiment=?, tags=?, topics=?,
               tasks=?, people=?, dates=?, action_items=?, created_at=?, is_important=?,
               importance=?, category=?, reason=?, source=?, session_id=?, content_hash=?,
               external_id=?, starts_at=?, ends_at=?, location=?, status=?, link=?
               WHERE id=?""",
            values + (existing["id"],),
        )
    else:
        conn.execute(
            """INSERT INTO memories (transcript, summary, sentiment, tags, topics, tasks,
               people, dates, action_items, created_at, is_important, importance,
               category, reason, source, session_id, content_hash, external_id,
               starts_at, ends_at, location, status, link)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )


def _event_attendees(event: dict) -> list[str]:
    people: list[str] = []
    for attendee in event.get("attendees") or []:
        email = (attendee.get("email") or "").strip()
        if email:
            people.append(email)
    return people


def sync_calendar(
    days_ahead: int = 14,
    days_back: int = 1,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """Index upcoming calendar events into the memory store."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days_back)
    window_end = now + timedelta(days=days_ahead)
    time_min = window_start.isoformat()
    time_max = window_end.isoformat()
    window_start_ts = window_start.timestamp()
    window_end_ts = window_end.timestamp()

    try:
        events = google_calendar.list_events(calendar_id, time_min, time_max)
    except Exception as e:
        logger.warning("calendar sync failed: %s", e)
        return {"events": 0, "error": f"{type(e).__name__}: {e}"}

    conn = get_conn()
    indexed = 0
    skipped = 0
    seen_ids: set[str] = set()
    try:
        for event in events or []:
            event_id = event.get("id")
            if not event_id:
                continue
            seen_ids.add(f"gcal:{event_id}")
            starts_at, ends_at, all_day = parse_event_times(event)
            summary = (event.get("summary") or "").strip()
            description = (event.get("description") or "").strip()
            location = (event.get("location") or "").strip()
            attendees = _event_attendees(event)
            dates = [_iso(starts_at)] if starts_at else []

            # An event with no title and no description has nothing to show.
            # It is not put in the timeline at all, and any earlier copy of
            # it is removed.
            if not summary and not description:
                conn.execute(
                    "DELETE FROM memories WHERE external_id=?", (f"gcal:{event_id}",)
                )
                skipped += 1
                continue

            _upsert_memory(
                conn,
                external_id=f"gcal:{event_id}",
                category=CATEGORY_EVENT,
                source=SOURCE_CALENDAR,
                summary=summary or description[:80],
                transcript=description,
                starts_at=starts_at,
                ends_at=ends_at,
                location=location,
                status=(event.get("status") or "").strip(),
                link=(event.get("htmlLink") or "").strip(),
                people=attendees,
                dates=dates,
                tags=["calendar", "event", "all-day"] if all_day else ["calendar", "event"],
                topics=[summary] if summary else [],
                importance=0.6,
                important=False,
            )
            indexed += 1

        # Remove events that are gone from Google. Only events inside the
        # window we just looked at are considered, so an event further out
        # is never removed just because it is outside this sync's range.
        removed = 0
        if seen_ids:
            placeholders = ",".join("?" for _ in seen_ids)
            rows = conn.execute(
                f"""SELECT external_id FROM memories
                    WHERE source=? AND external_id LIKE 'gcal:%'
                      AND starts_at >= ? AND starts_at <= ?
                      AND external_id NOT IN ({placeholders})""",
                [SOURCE_CALENDAR, window_start_ts, window_end_ts, *seen_ids],
            ).fetchall()
        else:
            # Google has no events at all in this window, so everything we
            # hold for that window has gone.
            rows = conn.execute(
                """SELECT external_id FROM memories
                   WHERE source=? AND external_id LIKE 'gcal:%'
                     AND starts_at >= ? AND starts_at <= ?""",
                [SOURCE_CALENDAR, window_start_ts, window_end_ts],
            ).fetchall()
        for row in rows:
            conn.execute(
                "DELETE FROM memories WHERE external_id=?", (row["external_id"],)
            )
            removed += 1
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("calendar sync failed while storing")
        return {"events": 0, "error": "storage error"}
    finally:
        conn.close()

    logger.info(
        "calendar sync: indexed %d events, skipped %d empty, removed %d deleted",
        indexed,
        skipped,
        removed,
    )
    return {
        "events": indexed,
        "events_skipped": skipped,
        "events_removed": removed,
    }


def sync_recent_emails(limit: int = 20) -> dict[str, Any]:
    """Index the latest Gmail messages into the memory store.

    The latest mail is what gets shown. A message is only flagged as
    important when Gmail itself says so - the flag is a truthful signal, not a
    filter on what is worth seeing.
    """
    try:
        messages = google_gmail.list_messages(google_gmail.DEFAULT_QUERY, limit)
    except Exception as e:
        logger.warning("gmail sync failed: %s", e)
        return {"emails": 0, "error": f"{type(e).__name__}: {e}"}

    conn = get_conn()
    indexed = 0
    skipped = 0
    try:
        for message in messages or []:
            message_id = message.get("id")
            if not message_id:
                continue
            headers = {
                h.get("name", "").lower(): h.get("value", "")
                for h in (message.get("payload") or {}).get("headers") or []
            }
            subject = (headers.get("subject") or "").strip()
            sender = headers.get("from") or ""
            sender_name = sender.split("<")[0].strip().strip('"')
            sender_email = ""
            if "<" in sender and ">" in sender:
                sender_email = sender[sender.find("<") + 1 : sender.find(">")].strip()
            snippet = (message.get("snippet") or "").strip()
            people = [p for p in (sender_name, sender_email) if p]
            internal_date = message.get("internalDate")
            try:
                received_at = int(internal_date) / 1000.0 if internal_date else None
            except (TypeError, ValueError):
                received_at = None

            # A message with no subject and no body has nothing to show. It
            # is not put in the timeline at all, and any earlier copy of it is
            # removed.
            if not subject and not snippet:
                conn.execute(
                    "DELETE FROM memories WHERE external_id=?", (f"gmail:{message_id}",)
                )
                skipped += 1
                continue

            # The subject is the title. Fall back to the first line of the
            # body when there is no subject.
            title = subject or snippet[:80]

            # Flagged important by Gmail, not by us.
            is_flagged = "IMPORTANT" in (message.get("labelIds") or [])

            _upsert_memory(
                conn,
                external_id=f"gmail:{message_id}",
                category=CATEGORY_EMAIL,
                source=SOURCE_GMAIL,
                summary=title,
                transcript=snippet,
                starts_at=received_at,
                ends_at=None,
                location="",
                status="unread" if "UNREAD" in (message.get("labelIds") or []) else "read",
                link=f"https://mail.google.com/mail/u/0/#all/{message_id}",
                people=people,
                dates=[_iso(received_at)] if received_at else [],
                tags=["email", "important"] if is_flagged else ["email"],
                topics=[title] if title else [],
                importance=0.7 if is_flagged else 0.5,
                important=is_flagged,
            )
            indexed += 1
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("gmail sync failed while storing")
        return {"emails": 0, "error": "storage error"}
    finally:
        conn.close()

    logger.info(
        "gmail sync: indexed %d recent messages, skipped %d empty",
        indexed,
        skipped,
    )
    return {"emails": indexed, "emails_skipped": skipped}


def sync_all(
    force: bool = False,
    days_ahead: int = 14,
    days_back: int = 1,
    email_limit: int = 20,
) -> dict[str, Any]:
    """Sync calendar events and important mail into the memory store.

    Unless ``force`` is set, this is throttled so it does not hammer the
    Google APIs when it is called on every dashboard poll.
    """
    global _last_sync_at, _last_result

    now = time.time()
    if not force and (now - _last_sync_at) < DEFAULT_THROTTLE_SECONDS:
        return {"throttled": True, "reason": "throttled", **last_sync()}

    result: dict[str, Any] = {}
    result.update(sync_calendar(days_ahead=days_ahead, days_back=days_back))
    result.update(sync_recent_emails(limit=email_limit))

    _last_sync_at = now
    _last_result = result
    return result
