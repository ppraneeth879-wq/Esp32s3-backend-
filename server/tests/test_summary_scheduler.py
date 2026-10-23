"""Tests for the rolling-window scheduler."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.summary_scheduler import SummaryScheduler
from app.summary_store import SummaryStore
from app.summarizer import Summarizer, SummaryResult
from app.transcript_log import TranscriptLog


class _StubSummarizer:
    """Records what it was asked to summarize, never touches the network."""

    def __init__(self, engine: str = "stub"):
        self.engine = engine
        self.calls = []

    def summarize(self, text: str) -> SummaryResult:
        self.calls.append(text)
        return SummaryResult(
            summary="- stub summary", engine=self.engine, fallback_used=False
        )


def _make(tmp_path: Path, **kwargs):
    journal = TranscriptLog(tmp_path / "journal.jsonl")
    store = SummaryStore(tmp_path / "summaries")
    summarizer = _StubSummarizer()
    scheduler = SummaryScheduler(
        journal=journal,
        store=store,
        summarizer=summarizer,
        window_seconds=kwargs.get("window_seconds", 3600),
        interval_seconds=kwargs.get("interval_seconds", 3600),
        retention_days=kwargs.get("retention_days", 30),
    )
    return scheduler, journal, store, summarizer


def _fill(journal: TranscriptLog, start_ts: float, count: int, gap: float = 10.0):
    for i in range(count):
        journal.append_text(
            text=f"segment {i} of speech",
            session_id="s1",
            device_id="d1",
            timestamp=start_ts + i * gap,
        )


# ---------------------------------------------------------------- basics


def test_first_run_summarizes_the_first_window(tmp_path: Path):
    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=3600)
    _fill(journal, 1000.0, 5)
    doc = scheduler.run_once(now=2000.0)
    assert doc is not None
    assert doc["segment_count"] == 5
    assert doc["engine"] == "stub"
    assert doc["fallback_used"] is False
    assert store.latest() is not None


def test_window_key_is_derived_from_the_window_end(tmp_path: Path):
    from datetime import datetime

    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=3600)
    _fill(journal, 1000.0, 3)
    doc = scheduler.run_once(now=1500.0)
    expected = datetime.fromtimestamp(1500.0).strftime("%Y-%m-%dT%H%M")
    assert doc["key"] == expected


def test_summary_document_has_the_documented_shape(tmp_path: Path):
    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=3600)
    _fill(journal, 1000.0, 4)
    doc = scheduler.run_once(now=1500.0)
    for field in (
        "key",
        "summary",
        "segment_count",
        "word_count",
        "generated_at",
        "engine",
        "fallback_used",
        "sessions",
        "window_start",
        "window_end",
    ):
        assert field in doc, field
    assert doc["sessions"] == ["s1"]


# ------------------------------------------------------------ idempotency


def test_scheduler_does_not_resummarize_a_record(tmp_path: Path):
    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=3600)
    _fill(journal, 1000.0, 5)
    scheduler.run_once(now=1500.0)
    assert len(summarizer.calls) == 1

    # A second pass over the same journal must not redo the same work.
    scheduler.run_once(now=2000.0)
    assert len(summarizer.calls) == 1
    assert len(store.all()) == 1


def test_a_record_is_never_summarized_twice_across_windows(tmp_path: Path):
    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=100)
    _fill(journal, 1000.0, 10, gap=20.0)  # spans 1000 -> 1180
    scheduler.run_once(now=1500.0)  # window [1000, 1100)
    scheduler.run_once(now=1600.0)  # window [1100, 1200)
    scheduler.run_once(now=1700.0)  # window [1200, 1300)

    # Every record must appear in exactly one summary.
    seen = []
    for doc in store.all():
        seen.append(doc["segment_count"])
    assert sum(seen) == 10
    assert len(summarizer.calls) == 2


def test_the_watermark_advances_even_for_an_empty_window(tmp_path: Path):
    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=3600)
    _fill(journal, 1000.0, 2)
    scheduler.run_once(now=1500.0)
    watermark = store.read_watermark()
    assert watermark == 1500.0

    # No new records, but the watermark still moves forward.
    scheduler.run_once(now=2000.0)
    assert store.read_watermark() == 2000.0
    assert len(summarizer.calls) == 1


# ---------------------------------------------------------------- backlog


def test_a_backlog_drains_one_window_at_a_time(tmp_path: Path):
    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=3600)
    # Three hours of backlog.
    _fill(journal, 1000.0, 30, gap=360.0)
    scheduler.run_once(now=20000.0)
    first = store.latest()
    assert first["segment_count"] < 30  # only one window was consumed
    assert store.read_watermark() == 1000.0 - 0.001 + 3600


def test_the_backlog_is_fully_drained_over_several_passes(tmp_path: Path):
    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=3600)
    _fill(journal, 1000.0, 30, gap=360.0)
    for _ in range(10):
        scheduler.run_once(now=20000.0)
    total = sum(d["segment_count"] for d in store.all())
    assert total == 30


# ---------------------------------------------------------------- restart


def test_the_watermark_survives_a_restart(tmp_path: Path):
    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=3600)
    _fill(journal, 1000.0, 5)
    scheduler.run_once(now=1500.0)
    assert len(summarizer.calls) == 1

    # A brand new scheduler on the same directory, as after a restart.
    scheduler2, journal2, store2, summarizer2 = _make(tmp_path, window_seconds=3600)
    scheduler2.run_once(now=2000.0)
    assert len(summarizer2.calls) == 0
    assert len(store2.all()) == 1


# ----------------------------------------------------------------- keys


def test_same_minute_keys_do_not_collide(tmp_path: Path):
    # Two windows that both hold content and both end inside the same
    # minute, so the second key would collide with the first.
    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=10)
    for ts in (1000.0, 1005.0, 1010.0, 1015.0):
        journal.append_text(text=f"line {ts}", session_id="s1", timestamp=ts)
    doc1 = scheduler.run_once(now=1020.0)
    doc2 = scheduler.run_once(now=1020.0)
    assert doc1 is not None
    assert doc2 is not None
    assert doc1["key"] != doc2["key"]
    assert doc2["key"].startswith(doc1["key"] + "-")
    assert len(store.all()) == 2


# ------------------------------------------------------------- reporting


def test_the_engine_field_names_the_engine_that_actually_answered(tmp_path: Path):
    journal = TranscriptLog(tmp_path / "journal.jsonl")
    store = SummaryStore(tmp_path / "summaries")
    summarizer = _StubSummarizer(engine="extractive")
    scheduler = SummaryScheduler(journal, store, summarizer, window_seconds=3600)
    _fill(journal, 1000.0, 2)
    doc = scheduler.run_once(now=1500.0)
    assert doc["engine"] == "extractive"


def test_status_reports_the_watermark_and_journal_size(tmp_path: Path):
    scheduler, journal, store, summarizer = _make(tmp_path, window_seconds=3600)
    _fill(journal, 1000.0, 3)
    scheduler.run_once(now=1500.0)
    status = scheduler.status()
    assert status["journal_records"] == 3
    assert status["watermark"] == 1500.0
    assert status["window_seconds"] == 3600
