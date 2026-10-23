"""Prove the watermark survives a restart with no duplicate work.

    cd server
    python verify_restart.py

Simulates a crash between two summarization passes: the journal, the store
and the scheduler are rebuilt from scratch, and every record must still be
summarized exactly once.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.summary_scheduler import SummaryScheduler
from app.summary_store import SummaryStore
from app.summarizer import SummaryResult
from app.transcript_log import TranscriptLog

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


class _StubSummarizer:
    """Never touches the network; records what it was asked to do."""

    def __init__(self, calls: list | None = None):
        # The call list is shared across "restarts" so it survives them.
        self.calls = calls if calls is not None else []

    def summarize(self, text: str) -> SummaryResult:
        self.calls.append(text)
        return SummaryResult(summary="- stub summary", engine="stub")


def build(workdir: Path, calls: list | None = None):
    """Build a fresh scheduler, as a restarted server would."""
    journal = TranscriptLog(workdir / "journal.jsonl")
    store = SummaryStore(workdir / "summaries")
    summarizer = _StubSummarizer(calls)
    scheduler = SummaryScheduler(
        journal=journal, store=store, summarizer=summarizer, window_seconds=3600
    )
    return scheduler, journal, store, summarizer


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="lifelog-restart-"))
    print(f"workdir: {workdir}\n")

    # Seed the journal with 12 segments spread over an hour.
    calls: list[str] = []
    scheduler, journal, store, summarizer = build(workdir, calls)
    for i in range(12):
        journal.append_text(
            text=f"segment {i} of the speech",
            session_id="s1",
            device_id="d1",
            timestamp=1000.0 + i * 300,
        )
    print(f"seeded 12 journal records\n")

    # Pass 1.
    doc1 = scheduler.run_once(now=5000.0)
    print(f"pass 1: key={doc1['key']} segments={doc1['segment_count']}")
    watermark = store.read_watermark()
    print(f"        watermark={watermark}")

    # "Restart": everything is rebuilt from disk.
    print("\n--- restart ---")
    scheduler, journal, store, summarizer = build(workdir, calls)
    print(f"watermark after restart: {store.read_watermark()}")
    assert store.read_watermark() == watermark, "the watermark must survive"

    # Pass 2 must not redo the first window.
    doc2 = scheduler.run_once(now=5000.0)
    print(f"pass 2: summary={'yes' if doc2 else 'no (nothing new)'}")

    # Pass 3 with a later clock drains the next window.
    doc3 = scheduler.run_once(now=9000.0)
    print(f"pass 3: key={doc3['key'] if doc3 else None} segments={doc3['segment_count'] if doc3 else 0}")

    # Every record must be summarized exactly once.
    total = sum(d["segment_count"] for d in store.all())
    print(f"\nsummaries stored : {len(store.all())}")
    print(f"records covered  : {total}")
    print(f"api calls made   : {len(calls)}")

    ok = True
    if total != 12:
        print(f"{RED}FAIL: expected 12 records summarized, got {total}{RESET}")
        ok = False
    else:
        print(f"{GREEN}OK: every record was summarized exactly once{RESET}")

    if len(calls) != len(store.all()):
        print(f"{RED}FAIL: expected one API call per summary{RESET}")
        ok = False
    else:
        print(f"{GREEN}OK: one API call per summary, no wasted requests{RESET}")

    if calls and "segment" not in calls[0]:
        print(f"{RED}FAIL: a summary did not contain the journal text{RESET}")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
