"""Inspect and clean the memory store."""

import sqlite3
import sys

conn = sqlite3.connect("lifelog.db")
conn.row_factory = sqlite3.Row

print("--- llm rows with event/email category (suspect test pollution) ---")
for r in conn.execute(
    "SELECT id, summary, source, category, external_id, location "
    "FROM memories WHERE source='llm' AND category IN ('event','email')"
).fetchall():
    print(" ", dict(r))

print()
print("--- genuine google rows ---")
for r in conn.execute(
    "SELECT id, summary, source, category, external_id, starts_at, location, status "
    "FROM memories WHERE source IN ('gmail','google_calendar') ORDER BY id"
).fetchall():
    print(" ", dict(r))

print()
print("--- spoken memories ---")
for r in conn.execute(
    "SELECT id, summary, source, category FROM memories "
    "WHERE source='llm' AND category IN ('memory','task','note') ORDER BY id"
).fetchall():
    print(" ", dict(r))

if "--clean" in sys.argv:
    cur = conn.execute(
        "DELETE FROM memories WHERE source='llm' AND category IN ('event','email')"
    )
    conn.commit()
    print()
    print(f"removed {cur.rowcount} polluted rows")

conn.close()
