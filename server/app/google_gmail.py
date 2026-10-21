"""Gmail helpers."""

from __future__ import annotations

import httpx
from .google_auth import get_credentials

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _headers():
    c = get_credentials()
    if not c:
        return None
    return {"Authorization": f"Bearer {c['access_token']}"}


def list_messages(query=None, max_results=10):
    h = _headers()
    if not h:
        return []
    params = {"maxResults": max_results}
    if query:
        params["q"] = query
    r = httpx.get(f"{BASE}/messages", headers=h, params=params, timeout=30)
    r.raise_for_status()
    msgs = r.json().get("messages", [])
    out = []
    for m in msgs:
        detail = httpx.get(f"{BASE}/messages/{m['id']}", headers=h, params={"format": "metadata"}, timeout=30)
        if detail.status_code == 200:
            out.append(detail.json())
    return out


def get_message(message_id):
    h = _headers()
    if not h:
        return None
    r = httpx.get(f"{BASE}/messages/{message_id}", headers=h, timeout=30)
    r.raise_for_status()
    return r.json()


def profile():
    h = _headers()
    if not h:
        return None
    r = httpx.get(f"{BASE}/profile", headers=h, timeout=30)
    r.raise_for_status()
    return r.json()