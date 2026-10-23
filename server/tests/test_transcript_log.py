"""Tests for the append-only transcript journal."""

from __future__ import annotations

import json
from pathlib import Path

from app.transcript_log import TranscriptLog, TranscriptRecord, window_key


def _rec(tmp_path: Path, ts: float, text: str, session: str = "s1") -> TranscriptRecord:
    return TranscriptRecord(
        timestamp=ts,
        session_id=session,
        device_id="dev-1",
        text=text,
        start_ms=int(ts * 1000) % 100000,
        end_ms=int(ts * 1000) % 100000 + 1000,
    )


def test_append_and_read_all(tmp_path: Path):
    log = TranscriptLog(tmp_path / "journal.jsonl")
    log.append(_rec(tmp_path, 100.0, "first"))
    log.append(_rec(tmp_path, 200.0, "second"))
    records = log.read_all()
    assert len(records) == 2
    assert records[0].text == "first"
    assert records[1].text == "second"


def test_journal_is_append_only_and_one_line_per_record(tmp_path: Path):
    path = tmp_path / "journal.jsonl"
    log = TranscriptLog(path)
    log.append(_rec(tmp_path, 100.0, "first"))
    log.append(_rec(tmp_path, 200.0, "second"))
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)
        assert "timestamp" in obj
        assert "text" in obj


def test_read_range_is_half_open(tmp_path: Path):
    log = TranscriptLog(tmp_path / "journal.jsonl")
    for i in range(5):
        log.append(_rec(tmp_path, 100.0 + i, f"line {i}"))
    # [100, 103) -> timestamps 100, 101, 102
    records = log.read_range(100.0, 103.0)
    assert len(records) == 3
    assert records[0].text == "line 0"
    assert records[-1].text == "line 2"


def test_read_range_excludes_the_end_timestamp(tmp_path: Path):
    log = TranscriptLog(tmp_path / "journal.jsonl")
    log.append(_rec(tmp_path, 100.0, "a"))
    log.append(_rec(tmp_path, 200.0, "b"))
    records = log.read_range(100.0, 200.0)
    assert [r.text for r in records] == ["a"]


def test_oldest_and_newest_timestamp(tmp_path: Path):
    log = TranscriptLog(tmp_path / "journal.jsonl")
    assert log.oldest_timestamp() is None
    log.append(_rec(tmp_path, 300.0, "c"))
    log.append(_rec(tmp_path, 100.0, "a"))
    log.append(_rec(tmp_path, 200.0, "b"))
    assert log.oldest_timestamp() == 100.0
    assert log.newest_timestamp() == 300.0


def test_read_all_skips_corrupt_lines(tmp_path: Path):
    path = tmp_path / "journal.jsonl"
    log = TranscriptLog(path)
    log.append(_rec(tmp_path, 100.0, "good one"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
        fh.write("[1,2,3]\n")
    log.append(_rec(tmp_path, 200.0, "good two"))
    records = log.read_all()
    assert [r.text for r in records] == ["good one", "good two"]


def test_trim_removes_old_records(tmp_path: Path):
    log = TranscriptLog(tmp_path / "journal.jsonl")
    for i in range(10):
        log.append(_rec(tmp_path, 1000.0 + i * 100, f"line {i}"))
    # Timestamps 1000..1900. A cutoff of 1500 keeps 1500, 1600, ... 1900.
    removed = log.trim(before_ts=1500.0)
    assert removed == 5
    assert len(log.read_all()) == 5
    assert log.read_all()[0].timestamp == 1500.0


def test_trim_protects_records_awaiting_summarization(tmp_path: Path):
    log = TranscriptLog(tmp_path / "journal.jsonl")
    for i in range(10):
        log.append(_rec(tmp_path, 1000.0 + i * 100, f"line {i}"))
    # Everything at or after the watermark must survive.
    removed = log.trim(before_ts=9999.0, protect_after_ts=1000.0)
    assert removed == 0
    assert len(log.read_all()) == 10


def test_window_key_is_local_time_and_filename_safe():
    from datetime import datetime

    key = window_key(datetime(2026, 10, 22, 1, 39, 17))
    assert key == "2026-10-22T0139"
    assert ":" not in key
    assert key == window_key(datetime(2026, 10, 22, 1, 39, 59))


def test_window_keys_sort_chronologically():
    from datetime import datetime

    keys = [
        window_key(datetime(2026, 10, 22, 10, 0)),
        window_key(datetime(2026, 10, 22, 2, 0)),
        window_key(datetime(2026, 10, 21, 23, 59)),
    ]
    assert sorted(keys) == ["2026-10-21T2359", "2026-10-22T0200", "2026-10-22T1000"]
