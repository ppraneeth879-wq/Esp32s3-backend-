"""Local storage helpers for LifeLog sessions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


class Storage:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)

    def session_dir(self, session_id: str) -> Path:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.base_dir / "audio" / date / session_id

    def create_session_dir(self, session_id: str) -> Path:
        d = self.session_dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def recording_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "recording.wav"

    def transcript_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "transcript.txt"

    def metadata_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "metadata.json"

    def write_transcript(self, session_id: str, text: str) -> None:
        self.create_session_dir(session_id)
        self.transcript_path(session_id).write_text(text, encoding="utf-8")

    def write_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        self.create_session_dir(session_id)
        self.metadata_path(session_id).write_text(
            json.dumps(metadata, indent=2, default=str), encoding="utf-8"
        )

    def read_metadata(self, session_id: str) -> dict[str, Any] | None:
        p = self.metadata_path(session_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))