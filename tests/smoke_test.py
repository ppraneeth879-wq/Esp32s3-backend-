"""Smoke-test every LifeLog REST endpoint against a running server."""
import json
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
client = httpx.Client(base_url=BASE, timeout=120.0)

results = []


def check(name, method, path, expect=200, **kw):
    try:
        r = client.request(method, path, **kw)
        ok = r.status_code == expect
        # The Windows console cannot print every Unicode character, and real
        # data (email subjects) contains plenty of them.
        body = r.text[:150].replace("\n", " ").encode("ascii", "replace").decode()
        results.append((ok, name, r.status_code, body))
    except Exception as e:
        results.append((False, name, "EXC", f"{type(e).__name__}: {e}"))


def _first_id(path: str) -> tuple[int, int]:
    """The id of the first row, and the status code it should return.

    Returns (id, 404) when the collection is empty.
    """
    try:
        r = client.get(path)
        payload = r.json()
    except Exception:
        return 0, 404
    rows = payload.get("memories") or payload.get("actions") or []
    if not rows:
        return 0, 404
    return int(rows[0]["id"]), 200


# --- system ---
check("GET  /health", "GET", "/health")
check("GET  /status", "GET", "/status")
check("GET  /api/stats", "GET", "/api/stats")
check("GET  /api/dashboard/summary", "GET", "/api/dashboard/summary")
check("GET  /api/dashboard/daily-briefing", "GET", "/api/dashboard/daily-briefing")

# --- memories ---
check("GET  /api/memories", "GET", "/api/memories?limit=5")
check("GET  /api/memories?important=1", "GET", "/api/memories?important=1")
check("GET  /api/memories?category=task", "GET", "/api/memories?category=task")
check("GET  /api/memories?category=bogus", "GET", "/api/memories?category=bogus")
_mid, _mcode = _first_id("/api/memories")
check("GET  /api/memories/{id}", "GET", f"/api/memories/{_mid}", expect=_mcode)
check("GET  /api/memories/999999", "GET", "/api/memories/999999", expect=404)

# --- actions ---
check("GET  /api/actions", "GET", "/api/actions")
check("GET  /api/actions?status=pending", "GET", "/api/actions?status=pending")
check("GET  /api/actions?status=bogus", "GET", "/api/actions?status=bogus")
_aid, _acode = _first_id("/api/actions")
check("GET  /api/actions/{id}", "GET", f"/api/actions/{_aid}", expect=_acode)
check("GET  /api/actions/999999", "GET", "/api/actions/999999", expect=404)

# --- summaries ---
check("GET  /summaries", "GET", "/summaries")
check("GET  /summaries/latest", "GET", "/summaries/latest")
check("GET  /summaries/status", "GET", "/summaries/status")
check("GET  /summaries/1999-01-01T0000", "GET", "/summaries/1999-01-01T0000", expect=404)

# --- search + chat ---
check("POST /api/search", "POST", "/api/search?query=invoice&threshold=0.0")
check("GET  /api/search", "GET", "/api/search?query=invoice&threshold=0.0")
check("POST /api/chat", "POST", "/api/chat", json={"question": "what do I need to do?"})

# --- google ---
check("GET  /auth/google/status", "GET", "/auth/google/status")
check("GET  /api/google/calendars", "GET", "/api/google/calendars")
check("GET  /api/google/profile", "GET", "/api/google/profile")
check("GET  /api/google/messages", "GET", "/api/google/messages?max_results=2")

# --- validation ---
# A missing question is rejected explicitly with 400 (not FastAPI's default 422).
check("POST /api/chat (no question)", "POST", "/api/chat", json={}, expect=400)

print(f"\n{'RESULT':<8} {'ENDPOINT':<34} {'CODE':<6} BODY")
print("-" * 110)
fails = 0
for ok, name, code, body in results:
    if not ok:
        fails += 1
    line = f"{'PASS' if ok else 'FAIL':<8} {name:<34} {str(code):<6} {body[:60]}"
    print(line.encode("ascii", "replace").decode())

print("-" * 110)
print(f"{len(results) - fails}/{len(results)} passed")
sys.exit(1 if fails else 0)
