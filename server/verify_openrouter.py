"""Call the live OpenRouter API with the configured key.

    cd server
    python verify_openrouter.py

Shows which models in the fallback chain are reachable right now and what the
daily budget looks like.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.dotenv import load_dotenv
from app.summarizer import (
    DEFAULT_MODEL,
    ROUTER_FALLBACK_MODEL,
    Summarizer,
    extractive_summary,
    normalize_whitespace,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

SPEECH = (
    "Okay so I need to remember to email Sarah back about the invoice tomorrow morning. "
    "And I have to call the dentist to reschedule my appointment for next week. "
    "My wife Priya's birthday is on the fourteenth of March."
)


def main() -> int:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        print("no OPENROUTER_API_KEY in the environment or server/.env")
        return 1

    model = os.getenv("LIFELOG_SUMMARY_MODEL", DEFAULT_MODEL)
    summarizer = Summarizer(model=model, api_key=api_key, daily_budget=50)

    print(f"primary model : {model}")
    print(f"router fallback: {ROUTER_FALLBACK_MODEL}")
    print(json.dumps(summarizer.status(), indent=2))
    print()

    text = normalize_whitespace(SPEECH)
    print(f"input ({len(text)} chars): {text}\n")

    start = time.time()
    result = summarizer.summarize(text)
    elapsed = time.time() - start

    print(f"engine        : {result.engine}")
    print(f"fallback_used : {result.fallback_used}")
    print(f"elapsed       : {elapsed:.1f}s")
    if result.error:
        print(f"error         : {result.error}")
    print("\n--- summary ---")
    print(result.summary)
    print("----------------")

    print("\nextractive fallback for comparison:")
    print(extractive_summary(text))

    print(f"\napi requests used today: {summarizer.requests_today}")
    print(f"budget remaining      : {summarizer.budget_remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
