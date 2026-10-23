"""Google Calendar helpers."""

from __future__ import annotations

import re

import httpx
from .google_auth import get_credentials

BASE = "https://www.googleapis.com/calendar/v3"


class GoogleApiError(RuntimeError):
    """A Google API call failed. The message carries Google's own text."""

    def __init__(self, message: str, status_code: int = 0, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# Strict RFC 3339, which is what the Google Calendar API requires:
# a full date, a full time (seconds included) and an offset or Z.
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$"
)


def normalise_time(value: str | None) -> str | None:
    """Make a timestamp something the Google Calendar API accepts.

    The Calendar API only accepts RFC 3339. Clients often send a slightly
    broken form such as ``2026-10-22T00:00+05:30`` (no seconds) or a bare
    date, and Google answers those with a 400. This repairs the common cases
    and drops anything that cannot be understood.

    ``datetime.fromisoformat`` is deliberately not used as the first check:
    it accepts ``T00:00+05:30`` because that is valid ISO 8601, but Google
    rejects it.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None

    if _RFC3339.match(text):
        return text

    # Bare date: 2026-10-22
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00+00:00"

    # Missing the seconds: 2026-10-22T00:00+05:30
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})([+-]\d{2}:?\d{2}|Z)", text)
    if m:
        return f"{m.group(1)}:00{m.group(2)}"

    # Missing the offset entirely: 2026-10-22T00:00:00
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", text):
        return f"{text}+00:00"

    # Missing both: 2026-10-22T00:00
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text):
        return f"{text}:00+00:00"

    return None


def _headers():
    c = get_credentials()
    if not c:
        return None
    return {"Authorization": f"Bearer {c['access_token']}"}


def _raise_for_status(r: httpx.Response) -> None:
    if r.status_code >= 400:
        body = r.text[:600]
        raise GoogleApiError(
            f"Google Calendar returned {r.status_code}",
            status_code=r.status_code,
            body=body,
        )


def list_calendars():
    h = _headers()
    if not h:
        return []
    r = httpx.get(f"{BASE}/users/me/calendarList", headers=h, timeout=30)
    _raise_for_status(r)
    return r.json().get("items", [])


def list_events(calendar_id="primary", time_min=None, time_max=None):
    h = _headers()
    if not h:
        return []
    params = {}
    min_value = normalise_time(time_min)
    max_value = normalise_time(time_max)
    if min_value:
        params["timeMin"] = min_value
    if max_value:
        params["timeMax"] = max_value
    r = httpx.get(f"{BASE}/calendars/{calendar_id}/events", headers=h, params=params, timeout=30)
    _raise_for_status(r)
    return r.json().get("items", [])


def create_event(summary, start, end, description=None, location=None, calendar_id="primary"):
    h = _headers()
    if not h:
        return None
    body = {"summary": summary, "start": start, "end": end}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    r = httpx.post(f"{BASE}/calendars/{calendar_id}/events", headers=h, json=body, timeout=30)
    _raise_for_status(r)
    return r.json()


def delete_event(event_id, calendar_id="primary"):
    h = _headers()
    if not h:
        return False
    r = httpx.delete(f"{BASE}/calendars/{calendar_id}/events/{event_id}", headers=h, timeout=30)
    return r.status_code in (200, 204)


def freebusy(time_min, time_max):
    h = _headers()
    if not h:
        return {}
    body = {"timeMin": time_min, "timeMax": time_max, "items": [{"id": "primary"}]}
    r = httpx.post(f"{BASE}/freeBusy", headers=h, json=body, timeout=30)
    _raise_for_status(r)
    return r.json()
