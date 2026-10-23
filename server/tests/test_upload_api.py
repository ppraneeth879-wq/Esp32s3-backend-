"""Tests for the /api/upload endpoint."""

from __future__ import annotations

import io
import wave
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

    database.DB_PATH = tmp_path / "test.db"

    import app.main as main

    importlib.reload(main)

    with TestClient(main.app) as c:
        yield c

    database.DB_PATH = Path("lifelog.db")


def _wav(frames: int = 1600) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x11\x22" * frames)
    return buf.getvalue()


def test_a_text_file_is_rejected_with_a_readable_message(client):
    resp = client.post(
        "/api/upload",
        files={"file": ("notes.txt", b"just some text", "text/plain")},
    )
    assert resp.status_code == 400
    assert "not an audio file" in resp.json()["detail"]


def test_an_empty_upload_is_rejected(client):
    resp = client.post(
        "/api/upload", files={"file": ("empty.wav", b"", "audio/wav")}
    )
    assert resp.status_code == 400


def test_a_broken_wav_is_rejected_not_crashed(client):
    resp = client.post(
        "/api/upload",
        files={"file": ("broken.wav", b"RIFF" + b"\x00" * 30, "audio/wav")},
    )
    assert resp.status_code == 400
    assert "WAV" in resp.json()["detail"]


def test_a_valid_wav_is_accepted(client, monkeypatch):
    """A real WAV goes through the whole pipeline."""
    import app.main as main

    class _FakeResult:
        text = "I need to email Sarah back about the invoice."

    class _FakeEngine:
        def __init__(self, **kwargs):
            pass

        def transcribe(self, pcm, rate=16000):
            # The decoded audio must be 16-bit mono at the right rate.
            assert len(pcm) % 2 == 0
            assert len(pcm) == 1600 * 2
            return _FakeResult()

    monkeypatch.setattr("app.whisper_engine.WhisperEngine", _FakeEngine)
    monkeypatch.setattr(
        "app.extraction.extract",
        lambda *a, **k: None,  # no LLM call in the test
    )

    resp = client.post(
        "/api/upload", files={"file": ("speech.wav", _wav(), "audio/wav")}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "Sarah" in body["transcript"]
