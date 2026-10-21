"""Test the extraction module against the real LLM with realistic speech samples."""
import sys, json, time

sys.path.insert(0, '/app')
sys.path.insert(0, '/app/app')

from app.extraction import extract, parse_json_object, heuristic_extract, SYSTEM_PROMPT
from app.llm import chat

SAMPLES = [
    ("TASK", "Okay so I need to remember to email Sarah back about the invoice tomorrow morning, it's already overdue. And I have to call the dentist to reschedule my appointment for next week."),
    ("MEMORY", "My wife Priya's birthday is on the 14th of March, and we usually book a table at that Italian place near the park. Also, I am allergic to shellfish so I should never order the linguine."),
    ("SMALLTALK", "Yeah no that's totally fine, I'll just grab it later then. Hey did you see what she said about the party on Friday? I think it was like eight o'clock or something. Oh and the weather is crazy today man."),
    ("TASK2", "Remind me to book the flight to Berlin before the end of the week, the earlier one is much cheaper."),
    ("NOISE", "Mm hmm. Yeah. Okay. Sure."),
]

print("=" * 70)
print("Testing LLM extraction with", len(SAMPLES), "samples")
print("Model:", "google/gemma-4-26b-a4b-it:free")
print("=" * 70)

for label, speech in SAMPLES:
    t0 = time.time()
    r = extract(speech, model="google/gemma-4-26b-a4b-it:free", threshold=0.4)
    dt = time.time() - t0
    print(f"\n[{label}] ({dt:.1f}s)")
    print(f"  speech: {speech[:80]}...")
    if r is None:
        print("  -> None")
        continue
    print(f"  category   : {r.category}")
    print(f"  important  : {r.important}  (importance {r.importance:.2f})")
    print(f"  summary    : {r.summary[:100]}")
    print(f"  reason     : {r.reason[:90]}")
    print(f"  people     : {r.people}")
    print(f"  dates      : {r.dates}")
    print(f"  tasks      : {[(t['title'][:50], t['priority'], t['due_date']) for t in r.tasks]}")

print("\n" + "=" * 70)
print("Testing heuristic fallback")
print("=" * 70)
for label, speech in SAMPLES:
    r = heuristic_extract(speech)
    print(f"[{label}] category={r.category} important={r.important} tasks={len(r.tasks)}")
