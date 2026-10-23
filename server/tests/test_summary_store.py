"""Tests for the summary store and the rolling-window watermark."""

from __future__ import annotations

from pathlib import Path

from app.summary_store import SummaryStore


def test_save_and_load(tmp_path: Path):
    store = SummaryStore(tmp_path / "summaries")
    doc = {"key": "2026-10-22T0139", "summary": "- did a thing", "engine": "test"}
    store.save(doc)
    loaded = store.load("2026-10-22T0139")
    assert loaded is not None
    assert loaded["summary"] == "- did a thing"
    assert loaded["engine"] == "test"


def test_load_missing_key_returns_none(tmp_path: Path):
    store = SummaryStore(tmp_path / "summaries")
    assert store.load("nope") is None


def test_all_returns_oldest_first(tmp_path: Path):
    store = SummaryStore(tmp_path / "summaries")
    store.save({"key": "2026-10-22T0200", "generated_at": "2026-10-22T02:39:17+0530"})
    store.save({"key": "2026-10-22T0100", "generated_at": "2026-10-22T01:39:17+0530"})
    store.save({"key": "2026-10-22T0300", "generated_at": "2026-10-22T03:39:17+0530"})
    keys = [d["key"] for d in store.all()]
    assert keys == ["2026-10-22T0100", "2026-10-22T0200", "2026-10-22T0300"]


def test_latest_returns_the_newest(tmp_path: Path):
    store = SummaryStore(tmp_path / "summaries")
    store.save({"key": "2026-10-22T0100", "generated_at": "2026-10-22T01:39:17+0530"})
    store.save({"key": "2026-10-22T0200", "generated_at": "2026-10-22T02:39:17+0530"})
    assert store.latest()["key"] == "2026-10-22T0200"


def test_latest_is_none_when_empty(tmp_path: Path):
    store = SummaryStore(tmp_path / "summaries")
    assert store.latest() is None


def test_state_file_is_not_listed_as_a_summary(tmp_path: Path):
    store = SummaryStore(tmp_path / "summaries")
    store.save({"key": "2026-10-22T0100", "generated_at": "x"})
    store.write_watermark(1234.5)
    keys = [d["key"] for d in store.all()]
    assert keys == ["2026-10-22T0100"]


def test_watermark_round_trip(tmp_path: Path):
    store = SummaryStore(tmp_path / "summaries")
    assert store.read_watermark() is None
    store.write_watermark(1700000000.25)
    assert store.read_watermark() == 1700000000.25


def test_watermark_survives_a_new_store_instance(tmp_path: Path):
    store = SummaryStore(tmp_path / "summaries")
    store.write_watermark(999.0)
    reopened = SummaryStore(tmp_path / "summaries")
    assert reopened.read_watermark() == 999.0


def test_delete_summary(tmp_path: Path):
    store = SummaryStore(tmp_path / "summaries")
    store.save({"key": "2026-10-22T0100", "generated_at": "x"})
    assert store.delete("2026-10-22T0100") is True
    assert store.load("2026-10-22T0100") is None
