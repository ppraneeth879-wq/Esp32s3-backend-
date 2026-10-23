"""Google OAuth helpers."""

from __future__ import annotations

import os
import time
import httpx
from typing import Any
from .database import get_conn

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = {
    "calendar": "https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events",
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
}


# The OAuth credentials are read at call time, not at import time.
#
# Reading them at import time breaks the native (non-Docker) run: main.py
# imports this module before it loads server/.env, so the values would be
# empty and Google would reject the request with
# "invalid_request: Missing required parameter: client_id".
# In Docker it happened to work because docker-compose sets the variables in
# the process environment before Python even starts.
def client_id() -> str:
    return (os.getenv("GOOGLE_CLIENT_ID") or "").strip()


def client_secret() -> str:
    return (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()


def redirect_uri() -> str:
    return (os.getenv("GOOGLE_REDIRECT_URI") or "").strip()


def is_configured() -> bool:
    """True when the OAuth credentials are present."""
    return bool(client_id() and client_secret() and redirect_uri())


def configured_account_email() -> str:
    """The account the user wants to sign in with, if one was configured."""
    return (os.getenv("GOOGLE_ACCOUNT_EMAIL") or "").strip()


def stored_account_email() -> str:
    """The email of the account that is currently connected, if any.

    Reads the stored row directly so it never triggers a token refresh.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT email FROM google_credentials ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return ""
    finally:
        conn.close()
    if not row:
        return ""
    return (row["email"] or "").strip()


def target_account_email(explicit: str = "") -> str:
    """Which account to sign in as, if one was explicitly requested.

    Only an *explicit* request counts: the ``email`` query parameter or the
    ``GOOGLE_ACCOUNT_EMAIL`` setting. The account that is already connected is
    deliberately NOT used as a hint, because that would skip the account
    chooser and leave the user with no way to pick a different account.
    """
    for candidate in (explicit, configured_account_email()):
        candidate = (candidate or "").strip()
        if candidate:
            return candidate
    return ""


def build_auth_url(
    scopes: list[str],
    mobile: bool = False,
    email: str = "",
    choose_account: bool = True,
) -> str:
    """Build the Google consent URL.

    ``prompt=consent select_account`` is what makes Google show the "choose
    an account" page even when the browser is already signed in. With only
    ``prompt=consent`` Google skips the chooser and drops straight into the
    consent screen for whichever account happens to be active, and there is no
    way back to pick a different one.

    ``login_hint`` is only sent when an account was explicitly requested. It
    pre-selects that account, so it is not used by default.
    """
    scope_list = ["openid", "email", "profile"]
    for s in scopes:
        scope_list.append(SCOPES.get(s, s))

    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(scope_list),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent select_account" if choose_account else "consent",
        "state": "mobile" if mobile else "web",
    }

    login_hint = target_account_email(email)
    if login_hint:
        params["login_hint"] = login_hint

    query = "&".join(
        f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items()
    )
    return AUTH_URL + "?" + query


def exchange_code(code: str) -> dict:
    resp = httpx.post(TOKEN_URL, data={
        "code": code,
        "client_id": client_id(),
        "client_secret": client_secret(),
        "redirect_uri": redirect_uri(),
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
                    "client_id": client_id(),
                    "client_secret": client_secret(),
                    "refresh_token": d["refresh_token"],
                    "grant_type": "refresh_token",
                }, timeout=30)
                resp.raise_for_status()
                tok = resp.json()
                d["access_token"] = tok.get("access_token", d["access_token"])
                d["expires_at"] = time.time() + tok.get("expires_in", 3600)
                conn = get_conn()
                try:
                    conn.execute("UPDATE google_credentials SET access_token=?, expires_at=? WHERE id=?",
                                 (d["access_token"], d["expires_at"], d["id"]))
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                return None
    return d


def delete_credentials():
    conn = get_conn()
    conn.execute("DELETE FROM google_credentials")
    conn.commit()
    conn.close()