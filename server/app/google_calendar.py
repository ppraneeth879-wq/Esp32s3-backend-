"""Google Calendar helpers."""

from __future__ import annotations

import httpx
from .google_auth import get_credentials

BASE = "https://www.googleapis.com/calendar/v3"


def _headers():
    c = get_credentials()
    if not c:
        return None
    return {"Authorization": f"Bearer {c['access_token']}"}


def list_calendars():
    h = _headers()
    if not h:
        return []
    r = httpx.get(f"{BASE}/users/me/calendarList", headers=h, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def list_events(calendar_id="primary", time_min=None, time_max=None):
    h = _headers()
    if not h:
        return []
    params = {}
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    r = httpx.get(f"{BASE}/calendars/{calendar_id}/events", headers=h, params=params, timeout=30)
    r.raise_for_status()
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
    r.raise_for_status()
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
    r.raise_for_status()
    return r.json()