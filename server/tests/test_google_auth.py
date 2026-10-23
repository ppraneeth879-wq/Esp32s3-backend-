"""Tests for the Google OAuth URL construction.

The account chooser must appear by default: with only `prompt=consent` Google
skips it and drops straight into the consent screen for whichever account
happens to be active, leaving no way to pick a different one.
"""

from __future__ import annotations

import os
import urllib.parse as up

import pytest

from app import google_auth


def _query(url: str) -> dict:
    return {k: v[0] for k, v in up.parse_qs(up.urlparse(url).query).items()}


def test_the_account_chooser_is_shown_by_default(monkeypatch):
    monkeypatch.delenv("GOOGLE_ACCOUNT_EMAIL", raising=False)
    q = _query(google_auth.build_auth_url(["calendar", "gmail"], mobile=True))
    assert "select_account" in q["prompt"]
    assert "consent" in q["prompt"]
    assert "login_hint" not in q


def test_the_chooser_can_be_switched_off(monkeypatch):
    monkeypatch.delenv("GOOGLE_ACCOUNT_EMAIL", raising=False)
    q = _query(
        google_auth.build_auth_url(["calendar"], mobile=True, choose_account=False)
    )
    assert q["prompt"] == "consent"
    assert "login_hint" not in q

def test_an_explicit_email_preselects_that_account(monkeypatch):
    monkeypatch.delenv("GOOGLE_ACCOUNT_EMAIL", raising=False)
    q = _query(
        google_auth.build_auth_url(["calendar"], mobile=True, email="me@gmail.com")
    )
    assert q["login_hint"] == "me@gmail.com"
    # The chooser is still shown, the account is just pre-selected.
    assert "select_account" in q["prompt"]


def test_the_configured_account_is_used_as_a_hint(monkeypatch):
    monkeypatch.setenv("GOOGLE_ACCOUNT_EMAIL", "configured@gmail.com")
    q = _query(google_auth.build_auth_url(["calendar"], mobile=True))
    assert q["login_hint"] == "configured@gmail.com"


def test_an_explicit_email_beats_the_configured_one(monkeypatch):
    monkeypatch.setenv("GOOGLE_ACCOUNT_EMAIL", "configured@gmail.com")
    q = _query(
        google_auth.build_auth_url(["calendar"], mobile=True, email="explicit@gmail.com")
    )
    assert q["login_hint"] == "explicit@gmail.com"


def test_the_stored_account_is_not_used_as_a_hint(monkeypatch):
    """A connected account must not silently skip the chooser."""
    monkeypatch.delenv("GOOGLE_ACCOUNT_EMAIL", raising=False)
    monkeypatch.setattr(google_auth, "stored_account_email", lambda: "stored@gmail.com")
    q = _query(google_auth.build_auth_url(["calendar"], mobile=True))
    assert "login_hint" not in q
    assert "select_account" in q["prompt"]


def test_the_scopes_are_requested(monkeypatch):
    monkeypatch.delenv("GOOGLE_ACCOUNT_EMAIL", raising=False)
    q = _query(google_auth.build_auth_url(["calendar", "gmail"], mobile=True))
    assert "https://www.googleapis.com/auth/calendar" in q["scope"]
    assert "https://www.googleapis.com/auth/gmail.readonly" in q["scope"]
    assert "email" in q["scope"]
    assert "openid" in q["scope"]


def test_offline_access_is_requested(monkeypatch):
    """Offline access is what gets us a refresh token."""
    monkeypatch.delenv("GOOGLE_ACCOUNT_EMAIL", raising=False)
    q = _query(google_auth.build_auth_url(["calendar"], mobile=True))
    assert q["access_type"] == "offline"
    assert q["include_granted_scopes"] == "true"


def test_the_state_marks_the_mobile_flow(monkeypatch):
    monkeypatch.delenv("GOOGLE_ACCOUNT_EMAIL", raising=False)
    assert _query(google_auth.build_auth_url(["calendar"], mobile=True))["state"] == "mobile"
    assert _query(google_auth.build_auth_url(["calendar"], mobile=False))["state"] == "web"


# --- regression: the credentials must be read at call time ---


def test_the_credentials_are_read_at_call_time_not_import_time(monkeypatch):
    """Setting the env after import must still produce a valid URL.

    Regression test: google_auth used to read the env at import time, so a
    native run (where server/.env is loaded after the import) sent an empty
    client_id and Google rejected the request with
    "invalid_request: Missing required parameter: client_id".
    """
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "late-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "late-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://example.test/callback")
    q = _query(google_auth.build_auth_url(["calendar"], mobile=True))
    assert q["client_id"] == "late-client-id.apps.googleusercontent.com"
    assert q["redirect_uri"] == "https://example.test/callback"


def test_is_configured_reflects_the_environment(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://example.test/callback")
    assert google_auth.is_configured() is True

    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    assert google_auth.is_configured() is False


# --- regression: the credentials must be read at call time ---


def test_the_credentials_are_read_at_call_time(monkeypatch):
    """The env must be able to change after the module is imported.

    Regression test: google_auth read the credentials at import time, so in a
    native run - where server/.env is loaded after the import - it sent
    Google an empty client_id and Google answered
    "invalid_request: Missing required parameter: client_id".
    """
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "late-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "late-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://example.test/callback")

    q = _query(google_auth.build_auth_url(["calendar"], mobile=True))
    assert q["client_id"] == "late-client-id"
    assert q["redirect_uri"] == "https://example.test/callback"
    assert google_auth.is_configured() is True


def test_the_auth_url_carries_everything_google_needs(monkeypatch):
    """A missing parameter means Google shows an error page instead of login."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "the-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "the-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://example.test/callback")
    monkeypatch.delenv("GOOGLE_ACCOUNT_EMAIL", raising=False)

    q = _query(google_auth.build_auth_url(["calendar", "gmail"], mobile=True))
    assert q["client_id"] == "the-id"
    assert q["redirect_uri"] == "https://example.test/callback"
    assert q["response_type"] == "code"
    assert q["access_type"] == "offline"
    assert q["include_granted_scopes"] == "true"
    assert "select_account" in q["prompt"]
    assert "calendar" in q["scope"]
    assert "gmail.readonly" in q["scope"]
    assert q["state"] == "mobile"
