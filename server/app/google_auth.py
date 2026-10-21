"""Google OAuth helpers."""

from __future__ import annotations

import os
import time
import httpx
from typing import Any
from .database import get_conn

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = {
    "calendar": "https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events",
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
}


def build_auth_url(scopes: list[str], mobile: bool = False) -> str:
    scope_list = ["openid", "email", "profile"]
    for s in scopes:
        scope_list.append(SCOPES.get(s, s))
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scope_list),
        "access_type": "offline",
        "prompt": "consent",
        "state": "mobile" if mobile else "web",
    }
    return AUTH_URL + "?" + "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())


def exchange_code(code: str) -> dict:
    resp = httpx.post(TOKEN_URL, data={
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()


def store_credentials(tok: dict, email: str = "", user_id: str = ""):
    conn = get_conn()
    c = conn.cursor()
    expires_at = time.time() + tok.get("expires_in", 3600)
    c.execute("DELETE FROM google_credentials")
    c.execute("""INSERT INTO google_credentials (email, google_user_id, access_token, refresh_token, expires_at, scopes)
                 VALUES (?,?,?,?,?,?)""",
              (email, user_id, tok.get("access_token"), tok.get("refresh_token"), expires_at, tok.get("scope", "")))
    conn.commit()
    conn.close()


def get_credentials() -> dict | None:
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT * FROM google_credentials ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    # refresh if expired
    if d["expires_at"] and time.time() > d["expires_at"] - 60:
        if d["refresh_token"]:
            try:
                resp = httpx.post(TOKEN_URL, data={
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "refresh_token": d["refresh_token"],
                    "grant_type": "refresh_token",
                }, timeout=30)
                resp.raise_for_status()
                tok = resp.json()
                d["access_token"] = tok.get("access_token", d["access_token"])
                d["expires_at"] = time.time() + tok.get("expires_in", 3600)
                conn = get_conn()
                conn.execute("UPDATE google_credentials SET access_token=?, expires_at=? WHERE id=?",
                             (d["access_token"], d["expires_at"], d["id"]))
                conn.commit()
                conn.close()
            except Exception:
                return None
    return d


def delete_credentials():
    conn = get_conn()
    conn.execute("DELETE FROM google_credentials")
    conn.commit()
    conn.close()