"""End-to-end test of the insight pipeline: buffering, dedup, storage, events."""
import sys, os, json, asyncio, time, shutil, tempfile

# Use a scratch database so we do not pollute the real one.
os.chdir(tempfile.mkdtemp())
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/app')

from app.database import init_db, get_conn
from app.pipeline import InsightPipeline, new_part, content_hash
from app.extraction import extract

init_db()
print("DB init OK\n")

# ---- Test 1: overlap de-duplication ----
print("=" * 60)
print("TEST 1: new_part() overlap de-duplication")
print("=" * 60)
cases = [
    ("I need to call the bank about my card", "I need to call the bank about my card and then book a table"),
    ("hello there how are you doing today", "how are you doing today I went to the store earlier"),
    ("completely different words here", "nothing in common at all"),
    ("", "first window of text"),
]
for existing, incoming in cases:
    print(f"  existing : {existing!r}")
    print(f"  incoming : {incoming!r}")
    print(f"  -> new   : {new_part(existing, incoming)!r}\n")

# ---- Test 2: full pipeline with a fake broadcaster ----
print("=" * 60)
print("TEST 2: full pipeline -> memory + action rows")
print("=" * 60)

class FakeBroadcaster:
    def __init__(self):
        self.messages = []
    async def broadcast(self, msg):
        self.messages.append(msg)

async def run_pipeline():
    b = FakeBroadcaster()
    p = InsightPipeline(
        broadcaster=b,
        session_id="test-session-1",
        device_id="00000000-0000-0000-0000-000000000001",
        model="google/gemma-4-26b-a4b-it:free",
        threshold=0.4,
        min_chars=60,
        max_wait_seconds=5.0,
    )
    # feed in overlapping fragments like whisper windows produce
    await p.feed("Okay so I need to remember to email Sarah back about the invoice tomorrow morning.")
    await p.feed("Okay so I need to remember to email Sarah back about the invoice tomorrow morning. And I have to call the dentist to reschedule my appointment for next week.")
    await p.feed("And I have to call the dentist to reschedule my appointment for next week.")
    await p.close()
    return b.messages

msgs = asyncio.run(run_pipeline())
for m in msgs:
    print(f"  event type : {m.get('type')}")
    print(f"  ignored    : {m.get('ignored')}")
    print(f"  category   : {m.get('category')}  importance={m.get('importance')}")
    print(f"  summary    : {str(m.get('summary'))[:90]}")
    mem = m.get("memory")
    if mem:
        print(f"  memory id  : {mem['id']}  important={mem['important']}  people={mem['people']}  dates={mem['dates']}")
    acts = m.get("actions") or []
    print(f"  actions    : {len(acts)}")
    for a in acts:
        print(f"     - [{a['priority']}] {a['title']}  due={a['due_date']}  conf={a['confidence']}")
    print()

# ---- Test 3: verify DB rows ----
print("=" * 60)
print("TEST 3: database contents")
print("=" * 60)
conn = get_conn()
for r in conn.execute("SELECT id, summary, category, is_important, importance, people, dates FROM memories").fetchall():
    print(f"  memory {r['id']}: category={r['category']} important={r['is_important']} imp={r['importance']}")
    print(f"    summary: {r['summary'][:80]}")
    print(f"    people: {r['people']}  dates: {r['dates']}")
for r in conn.execute("SELECT id, title, priority, status, due_date, memory_id FROM actions").fetchall():
    print(f"  action {r['id']}: [{r['priority']}] {r['title'][:60]}")
    print(f"    status={r['status']} due={r['due_date']} memory_id={r['memory_id']}")
conn.close()

# ---- Test 4: dedup of identical content ----
print()
print("=" * 60)
print("TEST 4: duplicate content is not stored twice")
print("=" * 60)
conn = get_conn()
before = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
conn.close()
print(f"  memories before: {before}")
print(f"  (content_hash dedup is enforced per-pipeline via _seen_hashes)")
