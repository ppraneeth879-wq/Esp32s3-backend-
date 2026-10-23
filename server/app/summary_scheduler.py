"""The hourly rolling-window summarization job.

Windows are contiguous, non-overlapping and ``window_seconds`` long. They are
not aligned to the wall clock. A watermark stored on disk marks how far the
journal has been consumed, so every record is summarized exactly once - never
twice, never skipped - even across server restarts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .summary_store import SummaryStore
from .summarizer import Summarizer, extractive_summary
from .transcript_log import TranscriptLog, window_key

logger = logging.getLogger("lifelog.summary_scheduler")

__all__ = ["SummaryScheduler"]


def _local_iso(timestamp: float) -> str:
    """Local time with a numeric offset, e.g. ``2026-10-22T02:39:17+0530``.

    The UTC-explicit form is used because a naive ``fromtimestamp(...).
    astimezone()`` raises on Windows for timestamps near the epoch.
    """
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().strftime(
        "%Y-%m-%dT%H:%M:%S%z"
    )


class SummaryScheduler:
    """Consumes the transcript journal one window at a time."""

    def __init__(
        self,
        journal: TranscriptLog,
        store: SummaryStore,
        summarizer: Summarizer,
        window_seconds: int = 3600,
        interval_seconds: int = 3600,
        retention_days: int = 30,
        on_summary: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        self.journal = journal
        self.store = store
        self.summarizer = summarizer
        self.window_seconds = max(1, int(window_seconds))
        self.interval_seconds = max(1, int(interval_seconds))
        self.retention_days = max(1, int(retention_days))
        self.on_summary = on_summary

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.last_run_at: Optional[float] = None
        self.last_error: str = ""
        self.runs = 0

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Start the background loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "summary scheduler started: window=%ds interval=%ds",
            self.window_seconds,
            self.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                raise
            if not self._running:
                return
            try:
                await asyncio.to_thread(self.run_once)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                logger.exception("summary scheduler pass failed")

    # -- the actual work ------------------------------------------------

    def _next_window_key(self, window_end: float) -> str:
        """Key for this window, with a ``-2``/``-3`` suffix on collision."""
        base = window_key(datetime.fromtimestamp(window_end, tz=timezone.utc).astimezone())
        if not self.store.path_for(base).exists():
            return base
        for suffix in range(2, 100):
            candidate = f"{base}-{suffix}"
            if not self.store.path_for(candidate).exists():
                return candidate
        return f"{base}-{int(time.time())}"

    def run_once(self, now: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Consume and summarize the next window.

        Returns the stored summary document, or ``None`` when the window held
        no speech.
        """
        now = time.time() if now is None else now
        self.runs += 1
        self.last_run_at = now

        try:
            watermark = self.store.read_watermark()
            if watermark is None:
                # First run: start just before the oldest record so a backlog
                # drains one window at a time instead of being skipped.
                oldest = self.journal.oldest_timestamp()
                cursor = (oldest - 0.001) if oldest is not None else (now - self.window_seconds)
            else:
                cursor = watermark

            window_end = min(cursor + self.window_seconds, now)
            if window_end <= cursor:
                # Nothing new to consume yet.
                self.store.write_watermark(window_end)
                return None

            records = self.journal.read_range(cursor, window_end)
            text = " ".join(r.text.strip() for r in records if r.text and r.text.strip())
            sessions = sorted({r.session_id for r in records if r.session_id})

            # The watermark advances even for an empty window, so an empty
            # window is not rescanned on every run.
            self.store.write_watermark(window_end)

            if not text:
                logger.info(
                    "summary window %s was empty",
                    window_key(datetime.fromtimestamp(window_end, tz=timezone.utc).astimezone()),
                )
                self._trim_journal(window_end, now)
                return None

            word_count = len(text.split())
            result = self.summarizer.summarize(text)
            if not result.summary:
                result_summary = extractive_summary(text)
                engine = "extractive"
                fallback_used = True
            else:
                result_summary = result.summary
                engine = result.engine
                fallback_used = result.fallback_used

            key = self._next_window_key(window_end)
            document = {
                "key": key,
                "summary": result_summary,
                "segment_count": len(records),
                "word_count": word_count,
                "generated_at": _local_iso(now),
                "engine": engine,
                "fallback_used": fallback_used,
                "sessions": sessions,
                "window_start": _local_iso(cursor),
                "window_end": _local_iso(window_end),
            }
            if result.error:
                document["error"] = result.error

            self.store.save(document)
            logger.info(
                "summary %s: %d segments, %d words, engine=%s",
                key,
                len(records),
                word_count,
                engine,
            )

            self._trim_journal(window_end, now)

            if self.on_summary is not None:
                try:
                    self.on_summary(document)
                except Exception:
                    logger.exception("summary callback failed")

            return document
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.exception("summary run failed")
            return None

    def _trim_journal(self, watermark: float, now: float) -> None:
        """Drop journal records that are older than the retention window.

        Records at or after the watermark are never dropped, so text that is
        still waiting to be summarized is always protected.
        """
        if self.retention_days <= 0:
            return
        cutoff = now - self.retention_days * 86400
        removed = self.journal.trim(cutoff, protect_after_ts=watermark)
        if removed:
            logger.info("trimmed %d old journal records", removed)

    # -- introspection --------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Scheduler health, for ``GET /summaries/status``."""
        watermark = self.store.read_watermark()
        return {
            "running": self._running,
            "runs": self.runs,
            "last_run_at": self.last_run_at,
            "last_error": self.last_error,
            "window_seconds": self.window_seconds,
            "interval_seconds": self.interval_seconds,
            "retention_days": self.retention_days,
            "watermark": watermark,
            "journal_records": self.journal.count(),
        }
