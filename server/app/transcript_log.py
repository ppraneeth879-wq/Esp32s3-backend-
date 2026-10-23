"""Append-only JSONL journal of finalized transcript segments.

The journal is the source of truth for the rolling-window summarizer. Each
finalized speech segment is appended as one JSON object; the summarizer reads
ranges of records and never rewrites history.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

__all__ = ["TranscriptRecord", "TranscriptLog", "window_key"]


def window_key(moment: datetime) -> str:
    """Local-time window key, e.g. ``2026-10-22T0139``.

    No ``:`` so the key is a valid filename on every platform, and it sorts
    lexicographically in chronological order.
    """
    return moment.strftime("%Y-%m-%dT%H%M")


@dataclass
class TranscriptRecord:
    """One finalized transcript segment."""

    timestamp: float
    session_id: str = ""
    device_id: str = ""
    text: str = ""
    start_ms: int = 0
    end_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "device_id": self.device_id,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }
        if self.extra:
            d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TranscriptRecord":
        known = {"timestamp", "session_id", "device_id", "text", "start_ms", "end_ms"}
        return cls(
            timestamp=float(d.get("timestamp") or 0.0),
            session_id=str(d.get("session_id") or ""),
            device_id=str(d.get("device_id") or ""),
            text=str(d.get("text") or ""),
            start_ms=int(d.get("start_ms") or 0),
            end_ms=int(d.get("end_ms") or 0),
            extra={k: v for k, v in d.items() if k not in known},
        )


class TranscriptLog:
    """Append-only JSONL file of :class:`TranscriptRecord` entries."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    # -- writing --------------------------------------------------------

    def append(self, record: TranscriptRecord) -> None:
        """Append one record. A write failure must never break recording."""
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    def append_text(
        self,
        text: str,
        session_id: str = "",
        device_id: str = "",
        timestamp: Optional[float] = None,
        start_ms: int = 0,
        end_ms: int = 0,
    ) -> TranscriptRecord:
        """Convenience wrapper around :meth:`append`."""
        rec = TranscriptRecord(
            timestamp=timestamp if timestamp is not None else datetime.now().timestamp(),
            session_id=session_id,
            device_id=device_id,
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        self.append(rec)
        return rec

    # -- reading --------------------------------------------------------

    def read_all(self) -> list[TranscriptRecord]:
        """Every readable record, oldest first. Corrupt lines are skipped."""
        if not self.path.exists():
            return []
        out: list[TranscriptRecord] = []
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            try:
                out.append(TranscriptRecord.from_dict(data))
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda r: r.timestamp)
        return out

    def read_range(self, start_ts: float, end_ts: float) -> list[TranscriptRecord]:
        """Records with ``start_ts <= timestamp < end_ts`` (half-open)."""
        return [r for r in self.read_all() if start_ts <= r.timestamp < end_ts]

    def oldest_timestamp(self) -> Optional[float]:
        """Timestamp of the oldest record, or ``None`` when the journal is empty."""
        records = self.read_all()
        if not records:
            return None
        return records[0].timestamp

    def newest_timestamp(self) -> Optional[float]:
        records = self.read_all()
        if not records:
            return None
        return records[-1].timestamp

    def count(self) -> int:
        return len(self.read_all())

    # -- maintenance ----------------------------------------------------

    def trim(self, before_ts: float, protect_after_ts: Optional[float] = None) -> int:
        """Drop records older than ``before_ts``.

        Records at or after ``protect_after_ts`` are never dropped, so text
        that is still waiting to be summarized survives compaction. When no
        protection boundary is given, only the age rule applies.
        Returns the number of records removed.
        """
        records = self.read_all()
        if not records:
            return 0
        if protect_after_ts is None:
            keep = [r for r in records if r.timestamp >= before_ts]
        else:
            keep = [
                r for r in records
                if r.timestamp >= before_ts or r.timestamp >= protect_after_ts
            ]
        removed = len(records) - len(keep)
        if removed <= 0:
            return 0
        with self._lock:
            try:
                tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                with tmp.open("w", encoding="utf-8") as fh:
                    for r in keep:
                        fh.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
                os.replace(tmp, self.path)
            except OSError:
                return 0
        return removed

    def __len__(self) -> int:
        return self.count()
