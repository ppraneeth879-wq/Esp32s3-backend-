"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket

from .broadcaster import Broadcaster
from .config import settings_from_env
from .logging_config import setup_logging
from .session_manager import SessionManager
from .vector_store import vector_store as _vector_store_init
from .storage import Storage
from .websocket_audio import AudioWebSocket
from .websocket_events import EventsWebSocket
from .database import init_db
from . import google_auth, google_calendar, google_gmail
from .llm import chat as llm_chat
from .vector_store import vector_store
import json, time, os, uuid, asyncio, threading
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass
from fastapi import Form, UploadFile, File, Query, HTTPException

settings = settings_from_env()
setup_logging(settings.log_level, Path(settings.base_dir) / "logs")

app = FastAPI(title="LifeLog Backend", version="1.0.0")

@app.on_event("startup")
def _startup():
    init_db()
    # Pre-initialize ChromaDB + trigger embedding model download at boot
    # so the first request doesn't block for minutes.
    try:
        _vector_store_init._initialize()
        _vector_store_init.search("warmup", top_k=1)
    except Exception:
        pass
    # Load the Whisper model in the background so the first spoken utterance
    # is not delayed by a multi-minute model download, while the HTTP server
    # stays available immediately.
    def _prewarm_whisper():
        try:
            from .whisper_engine import WhisperEngine
            engine = WhisperEngine(
                model_name=settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                vad_filter=settings.whisper_vad_filter,
            )
            engine._load()
            logging.getLogger("lifelog.main").info(
                "Whisper model '%s' pre-warmed", settings.whisper_model
            )
        except Exception:
            logging.getLogger("lifelog.main").exception("Whisper pre-warm failed")

    threading.Thread(target=_prewarm_whisper, daemon=True).start()

session_manager = SessionManager()
storage = Storage(settings.storage_path)
broadcaster = Broadcaster()

audio_ws = AudioWebSocket(settings, session_manager, storage, broadcaster)
events_ws = EventsWebSocket(broadcaster)


@app.get("/health")
async def health():
    return {"status": "OK"}


@app.get("/status")
async def status():
    active = session_manager.active_session()
    return {
        "connected_clients": broadcaster.client_count,
        "connected_devices": 1 if active else 0,
        "recording": active is not None and active.state.value == "RECORDING",
        "active_session": active.session_id if active else None,
        "queue_size": 0,
    }


@app.websocket("/audio")
async def audio_endpoint(ws: WebSocket):
    await audio_ws.handle(ws)


@app.websocket("/events")
async def events_endpoint(ws: WebSocket):
    await events_ws.handle(ws)


# ---- Google OAuth ----
@app.get("/auth/google")
async def auth_google(scope: str = Query("calendar"), mobile: int = 0):
    scopes = scope.split(",") if "," in scope else [scope]
    url = google_auth.build_auth_url(scopes, mobile=bool(mobile))
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url)


@app.get("/auth/google/callback")
async def auth_google_callback(code: str, state: str = ""):
    tok = google_auth.exchange_code(code)
    # get email
    email = ""
    try:
        import httpx
        r = httpx.get("https://www.googleapis.com/oauth2/v2/userinfo",
                      headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=30)
        if r.status_code == 200:
            email = r.json().get("email", "")
    except Exception:
        pass
    google_auth.store_credentials(tok, email=email)
    if state == "mobile":
        from fastapi.responses import RedirectResponse
        return RedirectResponse(f"lifelog://oauth?google=connected&email={email}")
    frontend = os.getenv("FRONTEND_URL", "")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"{frontend}?google=connected&email={email}")


@app.get("/auth/google/status")
async def auth_google_status():
    c = google_auth.get_credentials()
    if not c:
        return {"connected": False, "email": None, "scopes": []}
    return {"connected": True, "email": c.get("email"), "scopes": (c.get("scopes") or "").split()}


@app.post("/auth/google/disconnect")
async def auth_google_disconnect():
    google_auth.delete_credentials()
    return {"success": True}


@app.post("/api/privacy/revoke-google")
async def revoke_google():
    google_auth.delete_credentials()
    return {"success": True}


# ---- Google Calendar ----
@app.get("/api/google/calendars")
async def api_calendars():
    return {"calendars": google_calendar.list_calendars()}


@app.get("/api/google/events")
async def api_events(calendar_id: str = "primary", time_min: str = None, time_max: str = None):
    return {"events": google_calendar.list_events(calendar_id, time_min, time_max)}


@app.post("/api/google/events")
async def api_create_event(body: dict):
    ev = google_calendar.create_event(
        body.get("summary"), body.get("start"), body.get("end"),
        body.get("description"), body.get("location"), body.get("calendar_id", "primary"))
    return {"success": True, "event": ev}


@app.delete("/api/google/events/{event_id}")
async def api_delete_event(event_id: str, calendar_id: str = "primary"):
    ok = google_calendar.delete_event(event_id, calendar_id)
    return {"success": ok}


@app.get("/api/google/freebusy")
async def api_freebusy(time_min: str, time_max: str):
    return {"freebusy": google_calendar.freebusy(time_min, time_max)}


# ---- Gmail ----
@app.get("/api/google/messages")
async def api_messages(query: str = None, max_results: int = 10):
    return {"messages": google_gmail.list_messages(query, max_results)}


@app.get("/api/google/messages/important")
async def api_important(max_results: int = 10):
    msgs = google_gmail.list_messages("is:important is:unread", max_results)
    return {"messages": msgs}


@app.get("/api/google/messages/{message_id}")
async def api_message(message_id: str):
    return {"message": google_gmail.get_message(message_id)}


@app.get("/api/google/profile")
async def api_profile():
    return {"profile": google_gmail.profile()}


@app.post("/api/google/index-emails")
async def api_index_emails(limit: int = 20):
    msgs = google_gmail.list_messages(max_results=limit)
    return {"emails": msgs, "count": len(msgs)}


# ---- Memories ----
_JSON_LIST_COLUMNS = ("tags", "topics", "tasks", "people", "dates", "action_items")


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k in _JSON_LIST_COLUMNS:
        try:
            d[k] = json.loads(d[k]) if d.get(k) else []
        except Exception:
            d[k] = []
    if "is_important" in d:
        d["is_important"] = bool(d["is_important"])
    return d


@app.get("/api/memories")
async def api_memories(
    limit: int = 20,
    offset: int = 0,
    category: str = None,
    important: int = None,
    q: str = None,
):
    """List stored memories. Filter by category, importance, or free text."""
    from .database import get_conn
    sql = "SELECT * FROM memories WHERE 1=1"
    params: list = []
    if category:
        sql += " AND category=?"
        params.append(category)
    if important is not None:
        sql += " AND is_important=?"
        params.append(1 if important else 0)
    if q:
        sql += " AND (summary LIKE ? OR transcript LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    total = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
    conn.close()
    return {"memories": [_row_to_dict(r) for r in rows], "total": total}


@app.get("/api/memories/{mid}")
async def api_memory(mid: int):
    from .database import get_conn
    conn = get_conn()
    row = conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    return _row_to_dict(row)


@app.patch("/api/memories/{mid}")
async def api_update_memory(mid: int, body: dict):
    """Let a client correct or dismiss a memory."""
    from .database import get_conn
    allowed = {"summary", "category", "importance", "is_important", "sentiment", "tags", "topics"}
    sets, params = [], []
    for k, v in body.items():
        if k not in allowed:
            continue
        if k == "is_important":
            v = 1 if v else 0
        if k in ("tags", "topics"):
            v = json.dumps(v if isinstance(v, list) else [str(v)])
        sets.append(f"{k}=?")
        params.append(v)
    if not sets:
        raise HTTPException(400, "no updatable fields supplied")
    params.append(mid)
    conn = get_conn()
    conn.execute(f"UPDATE memories SET {','.join(sets)} WHERE id=?", params)
    conn.commit()
    row = conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    return _row_to_dict(row)


@app.delete("/api/memories/{mid}")
async def api_delete_memory(mid: int):
    from .database import get_conn
    conn = get_conn()
    conn.execute("DELETE FROM actions WHERE memory_id=?", (mid,))
    conn.execute("DELETE FROM memories WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.post("/api/search")
async def api_search(query: str = Query(...), threshold: float = 0.3, top_k: int = 10):
    results = await asyncio.to_thread(vector_store.search, query, threshold, top_k)
    return {"results": results, "query": query, "count": len(results)}


# ---- Upload audio + AI pipeline ----
@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    import tempfile, os
    from .whisper_engine import WhisperEngine
    content = await file.read()
    # save temp
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp.write(content)
    tmp.close()
    # transcribe
    engine = WhisperEngine(model_name=settings.whisper_model, device="cpu", compute_type="int8", vad_filter=False)
    # if wav, strip header
    pcm = content
    if content[:4] == b"RIFF":
        pcm = content[44:]
    result = engine.transcribe(pcm, 16000)
    transcript = result.text

    # Run the same AI judgement the live path uses.
    from .extraction import extract
    from .pipeline import content_hash, store_actions, store_memory

    extracted = extract(
        transcript,
        model=settings.extraction_model,
        threshold=settings.importance_threshold,
    )
    if extracted is None:
        return {
            "success": True,
            "transcript": transcript,
            "extracted": None,
            "memory": None,
            "actions": [],
            "ignored": True,
        }

    memory = None
    actions = []
    if extracted.category != "ignore":
        memory = store_memory(
            extracted,
            transcript,
            session_id="upload",
            content_hash_value=content_hash(extracted.summary or transcript),
        )
        actions = store_actions(extracted, memory["id"] if memory else None, session_id="upload")

    return {
        "success": True,
        "transcript": transcript,
        "extracted": extracted.to_dict(),
        "memory": memory,
        "actions": actions,
        "ignored": extracted.category == "ignore",
    }


# ---- Chat ----
@app.post("/api/chat")
async def api_chat(body: dict):
    """Answer a question using the stored memories (semantic recall + LLM)."""
    question = body.get("question", "") or body.get("query", "")
    if not question.strip():
        raise HTTPException(400, "question is required")
    from .pipeline import ask_llm, summarise_memories
    context = await asyncio.to_thread(summarise_memories, question, 6)
    answer = await asyncio.to_thread(ask_llm, question, context)
    if not answer:
        answer = "No relevant memory found."
    try:
        results = await asyncio.to_thread(vector_store.search, question, 0.0, 6)
    except Exception:
        results = []
    return {"answer": answer, "sources": results, "context_used": bool(context)}


# ---- Dashboard ----
@app.get("/api/dashboard/summary")
async def api_dashboard_summary():
    from .database import get_conn
    conn = get_conn()
    mems = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
    important = conn.execute(
        "SELECT COUNT(*) c FROM memories WHERE is_important=1"
    ).fetchone()["c"]
    acts = conn.execute("SELECT COUNT(*) c FROM actions").fetchone()["c"]
    pending = conn.execute(
        "SELECT COUNT(*) c FROM actions WHERE status='pending'"
    ).fetchone()["c"]
    by_category = {
        r["category"]: r["c"]
        for r in conn.execute(
            "SELECT category, COUNT(*) c FROM memories GROUP BY category"
        ).fetchall()
    }
    conn.close()
    return {
        "memories": mems,
        "important": important,
        "actions": acts,
        "pending_actions": pending,
        "by_category": by_category,
        "tags": [],
        "sentiment": {},
    }


@app.get("/api/dashboard/daily-briefing")
async def api_daily_briefing():
    """Morning summary: recent memories, open tasks, calendar, important mail."""
    from .database import get_conn
    conn = get_conn()
    mems = [_row_to_dict(r) for r in conn.execute(
        "SELECT * FROM memories ORDER BY id DESC LIMIT 5"
    ).fetchall()]
    tasks = [dict(r) for r in conn.execute(
        "SELECT * FROM actions WHERE status='pending' ORDER BY "
        "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id DESC LIMIT 10"
    ).fetchall()]
    conn.close()
    cal = []
    try:
        cal = google_calendar.list_events(time_min=None, time_max=None)[:5]
    except Exception:
        cal = []
    important = []
    try:
        important = google_gmail.list_messages("is:important is:unread", 5)
    except Exception:
        important = []
    return {
        "memories": mems,
        "tasks": tasks,
        "calendar": cal,
        "important_emails": important,
        "actions": tasks,
    }


# ---- Actions / Tasks ----
@app.get("/api/actions")
async def api_actions(
    status: str = None,
    priority: str = None,
    limit: int = 50,
    offset: int = 0,
):
    """List tasks extracted from speech. Filter by status or priority."""
    from .database import get_conn
    sql = "SELECT * FROM actions WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if priority:
        sql += " AND priority=?"
        params.append(priority)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    total = conn.execute("SELECT COUNT(*) c FROM actions").fetchone()["c"]
    pending = conn.execute(
        "SELECT COUNT(*) c FROM actions WHERE status='pending'"
    ).fetchone()["c"]
    conn.close()
    return {"actions": [dict(r) for r in rows], "total": total, "pending": pending}


@app.get("/api/actions/{aid}")
async def api_action(aid: int):
    from .database import get_conn
    conn = get_conn()
    row = conn.execute("SELECT * FROM actions WHERE id=?", (aid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    return dict(row)


@app.post("/api/actions")
async def api_create_action(body: dict):
    """Manually create a task."""
    from .database import get_conn
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO actions
           (memory_id, title, description, action_type, priority, status, confidence,
            due_date, source, session_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            body.get("memory_id"),
            body.get("title"),
            body.get("description") or body.get("title"),
            body.get("action_type", "task"),
            body.get("priority", "medium"),
            body.get("status", "pending"),
            body.get("confidence", 1.0),
            body.get("due_date"),
            body.get("source", "manual"),
            body.get("session_id", ""),
            time.time(),
        ),
    )
    conn.commit()
    aid = cur.lastrowid
    row = conn.execute("SELECT * FROM actions WHERE id=?", (aid,)).fetchone()
    conn.close()
    return dict(row)


@app.patch("/api/actions/{aid}")
async def api_update_action(aid: int, body: dict):
    """Update a task: mark it done, dismiss it, change priority, reschedule."""
    from .database import get_conn
    allowed = {"title", "description", "priority", "status", "due_date", "action_type"}
    sets, params = [], []
    for k, v in body.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        params.append(v)
    if "status" in body and body["status"] in ("completed", "done"):
        sets.append("completed_at=?")
        params.append(time.time())
    if not sets:
        raise HTTPException(400, "no updatable fields supplied")
    params.append(aid)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE actions SET {','.join(sets)} WHERE id=?", params)
    conn.commit()
    row = conn.execute("SELECT * FROM actions WHERE id=?", (aid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    updated = dict(row)
    # notify live clients so the app updates without polling
    try:
        await broadcaster.broadcast({"type": "action_updated", "action": updated})
    except Exception:
        pass
    return updated


@app.delete("/api/actions/{aid}")
async def api_delete_action(aid: int):
    from .database import get_conn
    conn = get_conn()
    conn.execute("DELETE FROM actions WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    return {"success": True}


# ---- Analytics ----
@app.get("/api/stats")
async def api_stats():
    """Headline counters for the app home screen."""
    from .database import get_conn
    conn = get_conn()
    mems = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
    important = conn.execute(
        "SELECT COUNT(*) c FROM memories WHERE is_important=1"
    ).fetchone()["c"]
    tasks = conn.execute("SELECT COUNT(*) c FROM actions").fetchone()["c"]
    pending = conn.execute(
        "SELECT COUNT(*) c FROM actions WHERE status='pending'"
    ).fetchone()["c"]
    by_category = {
        r["category"]: r["c"]
        for r in conn.execute(
            "SELECT category, COUNT(*) c FROM memories GROUP BY category"
        ).fetchall()
    }
    conn.close()
    return {
        "memories": mems,
        "important": important,
        "tasks": tasks,
        "pending_tasks": pending,
        "by_category": by_category,
    }