"""Gmail helpers."""

from __future__ import annotations

import httpx
from .google_auth import get_credentials

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# The default listing shows the latest mail, newest first.
DEFAULT_QUERY = "in:anywhere"

# The subset Gmail itself flags as important.
IMPORTANT_QUERY = "is:important"
IMPORTANT_UNREAD_QUERY = "is:important is:unread"


def _headers():
    c = get_credentials()
    if not c:
        return None
    return {"Authorization": f"Bearer {c['access_token']}"}


def list_messages(query=None, max_results=10):
    """List messages, newest first.

    Defaults to the latest mail. Pass a Gmail query to narrow it down, e.g.
    ``query="is:important"``.
    """
    h = _headers()
    if not h:
        return []
    params = {"maxResults": max_results}
    params["q"] = query if query else DEFAULT_QUERY
    r = httpx.get(f"{BASE}/messages", headers=h, params=params, timeout=30)
    r.raise_for_status()
    msgs = r.json().get("messages", [])
    out = []
    for m in msgs:
        detail = httpx.get(f"{BASE}/messages/{m['id']}", headers=h, params={"format": "metadata"}, timeout=30)
        if detail.status_code == 200:
            out.append(detail.json())
    return out


def list_important(max_results=10, unread_only=True):
    """The messages Gmail flags as important."""
    query = IMPORTANT_UNREAD_QUERY if unread_only else IMPORTANT_QUERY
    return list_messages(query, max_results)


def list_recent(max_results=10):
    """The latest mail, newest first."""
    return list_messages(DEFAULT_QUERY, max_results)


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