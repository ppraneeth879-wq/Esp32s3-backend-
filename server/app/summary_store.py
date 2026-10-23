"""On-disk storage for rolling-window summaries.

One JSON document per window plus a small state file holding the watermark -
the cursor marking how far the transcript journal has been consumed.

There is no database in this feature. Files are the source of truth.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("lifelog.summary_store")

__all__ = ["SummaryStore"]

STATE_FILENAME = "_state.json"


class SummaryStore:
    """Persists summary documents and the rolling-window watermark."""

    def __init__(self, summaries_dir: Path | str):
        self.dir = Path(summaries_dir)
        self._lock = threading.Lock()

    # -- paths ----------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.dir / STATE_FILENAME

    def path_for(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def ensure_dir(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("could not create summaries directory %s", self.dir)

    # -- summaries ------------------------------------------------------

    def save(self, document: dict[str, Any]) -> Path:
        """Write one summary document and return its path."""
        key = str(document.get("key") or "")
        if not key:
            raise ValueError("summary document needs a key")
        self.ensure_dir()
        path = self.path_for(key)
        with self._lock:
            tmp = path.with_suffix(".json.tmp")
            try:
                tmp.write_text(
                    json.dumps(document, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                os.replace(tmp, path)
            except OSError:
                logger.exception("could not write summary %s", key)
                raise
        return path

    def load(self, key: str) -> Optional[dict[str, Any]]:
        """Load one summary by key, or ``None`` when it does not exist."""
        path = self.path_for(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("could not read summary %s", key)
            return None
        return data if isinstance(data, dict) else None

    def all(self) -> list[dict[str, Any]]:
        """Every stored summary, oldest first."""
        if not self.dir.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in self.dir.glob("*.json"):
            if path.name == STATE_FILENAME:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                out.append(data)
        out.sort(key=lambda d: (str(d.get("generated_at") or ""), str(d.get("key") or "")))
        return out

    def latest(self) -> Optional[dict[str, Any]]:
        """The most recently generated summary, or ``None``."""
        summaries = self.all()
        return summaries[-1] if summaries else None

    def delete(self, key: str) -> bool:
        path = self.path_for(key)
        try:
            path.unlink()
            return True
        except OSError:
            return False

    # -- watermark ------------------------------------------------------

    def read_watermark(self) -> Optional[float]:
        """The cursor: how far the journal has been consumed.

        ``None`` means the summarizer has never run. The scheduler then
        starts just before the oldest journal record.
        """
        path = self.state_path
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("could not read the summarizer watermark")
            return None
        value = data.get("watermark")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def write_watermark(self, timestamp: float) -> None:
        """Persist the cursor. It must survive a restart, or work is redone."""
        self.ensure_dir()
        payload = {"watermark": float(timestamp), "updated_at": timestamp}
        path = self.state_path
        with self._lock:
            tmp = path.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                logger.exception("could not write the summarizer watermark")
