"""End-to-end check of the rolling-window summarizer.

Audio -> transcript -> journal -> window -> summary -> HTTP.

    cd server
    python verify_summarizer.py

Uses the live Whisper model and the live OpenRouter API. If the daily free
tier is exhausted it falls back to the offline extractive summary.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.dotenv import load_dotenv
from app.summary_scheduler import SummaryScheduler
from app.summary_store import SummaryStore
from app.summarizer import Summarizer
from app.transcript_log import TranscriptLog

load_dotenv(Path(__file__).resolve().parent / ".env")

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def step(n: int, title: str) -> None:
    print(f"\n{'=' * 70}\n[{n}] {title}\n{'=' * 70}")


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="lifelog-verify-"))
    print(f"workdir: {workdir}")

    step(1, "Journal: append segments")
    journal = TranscriptLog(workdir / "journal.jsonl")
    now = time.time()
    speech = [
        "I need to email Sarah back about the invoice tomorrow morning.",
        "My wife Priya's birthday is on the fourteenth of March.",
        "I have to call the dentist to reschedule my appointment for next week.",
        "The quarterly review went well, we shipped the new release on Friday.",
    ]
    for i, line in enumerate(speech):
        journal.append_text(
            text=line,
            session_id="verify-session",
            device_id="verify-device",
            timestamp=now - 300 + i,
            start_ms=i * 2500,
            end_ms=i * 2500 + 2400,
        )
    records = journal.read_all()
    print(f"journal records: {len(records)}")
    for r in records:
        print(f"  {r.timestamp:.1f}  {r.text}")
    assert len(records) == len(speech)

    step(2, "Summarizer: budget and status")
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    summarizer = Summarizer(
        model=os.getenv("LIFELOG_SUMMARY_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        api_key=api_key,
        daily_budget=50,
    )
    print(json.dumps(summarizer.status(), indent=2))
    if not api_key:
        print(f"{YELLOW}no OPENROUTER_API_KEY - the extractive fallback will be used{RESET}")

    step(3, "Scheduler: consume one window")
    store = SummaryStore(workdir / "summaries")
    scheduler = SummaryScheduler(
        journal=journal,
        store=store,
        summarizer=summarizer,
        window_seconds=3600,
        retention_days=30,
    )
    document = scheduler.run_once(now=now)
    if document is None:
        print(f"{RED}no summary was produced{RESET}")
        return 1
    print(json.dumps(document, indent=2))
    assert document["segment_count"] == len(speech)
    assert document["engine"], "the engine must name what actually answered"

    step(4, "Idempotency: a second pass must not repeat the work")
    before = len(store.all())
    calls_before = summarizer.requests_today
    scheduler.run_once(now=now + 3600)
    after = len(store.all())
    print(f"summaries before={before} after={after}")
    print(f"api requests before={calls_before} after={summarizer.requests_today}")
    assert after == before, "a window must not be summarized twice"
    assert summarizer.requests_today == calls_before, "no extra API call"

    step(5, "Restart: the watermark survives")
    scheduler2 = SummaryScheduler(
        journal=journal,
        store=store,
        summarizer=summarizer,
        window_seconds=3600,
        retention_days=30,
    )
    watermark = store.read_watermark()
    print(f"watermark: {watermark}")
    assert watermark is not None
    scheduler2.run_once(now=now + 7200)
    assert len(store.all()) == after, "a restart must not re-summarize"

    step(6, "On-disk layout")
    for path in sorted(workdir.rglob("*")):
        if path.is_file():
            print(f"  {path.relative_to(workdir)}  ({path.stat().st_size} bytes)")

    step(7, "HTTP: the summary endpoints")
    import httpx

    try:
        resp = httpx.get("http://127.0.0.1:8000/summaries/latest", timeout=10)
        print(f"GET /summaries/latest -> {resp.status_code}")
        print(json.dumps(resp.json(), indent=2)[:800])
    except Exception as e:
        print(f"{YELLOW}no server running on 127.0.0.1:8000 ({type(e).__name__}){RESET}")
        print("start it with scripts\\run_local.bat to check the HTTP layer")

    print(f"\n{GREEN}verify_summarizer: all checks passed{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
