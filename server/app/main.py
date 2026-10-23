"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket

# Local secrets must be loaded before anything else, because modules that
# read the environment (the Google OAuth helpers) are imported below. A real
# environment variable always wins over the file.
from .dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .broadcaster import Broadcaster
from .config import settings_from_env
from .logging_config import setup_logging
from .session_manager import SessionManager
from .vector_store import vector_store as _vector_store_init
from .storage import Storage
from .summary_scheduler import SummaryScheduler
from .summary_store import SummaryStore
from .summarizer import Summarizer
from .transcript_log import TranscriptLog
from .websocket_audio import AudioWebSocket
from .websocket_events import EventsWebSocket
from .database import init_db
from . import google_auth, google_calendar, google_gmail, google_indexer
from .llm import chat as llm_chat
from .vector_store import vector_store
import json, time, os, uuid, asyncio, threading
from datetime import datetime, timezone
from typing import Optional
from fastapi import Form, UploadFile, File, Query, HTTPException

settings = settings_from_env()
setup_logging(settings.log_level, Path(settings.base_dir) / "logs")

app = FastAPI(title="LifeLog Backend", version="1.0.0")

@app.on_event("startup")
def _startup():
    init_db()

    if os.getenv("LIFELOG_PREWARM", "1").strip().lower() not in ("1", "true", "yes", "on"):
        logging.getLogger("lifelog.main").info("model pre-warm disabled by configuration")
        return

    def _prewarm_chroma():
        """Warm ChromaDB in the background.

        The first call downloads the ~80 MB embedding model, which can take
        many minutes. Doing it on a background thread keeps the HTTP server
        responsive instead of blocking startup.
        """
        try:
            _vector_store_init._initialize()
            _vector_store_init.search("warmup", top_k=1)
            logging.getLogger("lifelog.main").info("ChromaDB pre-warmed")
        except Exception:
            logging.getLogger("lifelog.main").exception("ChromaDB pre-warm failed")

    def _prewarm_whisper():
        """Warm the Whisper model in the background for the same reason."""
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

    threading.Thread(target=_prewarm_chroma, daemon=True).start()
    threading.Thread(target=_prewarm_whisper, daemon=True).start()

session_manager = SessionManager()
storage = Storage(settings.storage_path)
broadcaster = Broadcaster()

# ---- Rolling-window summarizer ----
journal = TranscriptLog(storage.journal_path)
summary_store = SummaryStore(storage.summary_dir)
summary_webhook_url = os.getenv("LIFELOG_SUMMARY_WEBHOOK_URL", "").strip()


def _push_summary_webhook(document: dict) -> None:
    """POST a new summary to the configured webhook.

    A webhook failure is logged and swallowed - it must never break the
    summary itself.
    """
    if not summary_webhook_url:
        return
    try:
        import httpx

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(summary_webhook_url, json=document)
            resp.raise_for_status()
        logging.getLogger("lifelog.main").info("summary pushed to the webhook")
    except Exception:
        logging.getLogger("lifelog.main").exception("summary webhook push failed")


summary_summarizer = Summarizer(
    model=settings.summary_model,
    api_key=os.getenv("OPENROUTER_API_KEY", ""),
    daily_budget=settings.summary_daily_request_budget,
)
summary_scheduler = SummaryScheduler(
    journal=journal,
    store=summary_store,
    summarizer=summary_summarizer,
    window_seconds=settings.summary_window_seconds,
    interval_seconds=settings.summary_interval_seconds,
    retention_days=settings.summary_retention_days,
    on_summary=_push_summary_webhook,
)

audio_ws = AudioWebSocket(settings, session_manager, storage, broadcaster, journal=journal)
events_ws = EventsWebSocket(broadcaster)


@app.on_event("startup")
async def _start_scheduler():
    """Start the hourly summarization job."""
    if not os.getenv("LIFELOG_SUMMARY_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on"):
        logging.getLogger("lifelog.main").info("summary scheduler disabled by configuration")
        return
    await summary_scheduler.start()
    if not summary_summarizer.api_key:
        logging.getLogger("lifelog.main").warning(
            "no OPENROUTER_API_KEY - the summarizer will use the offline extractive fallback"
        )


@app.on_event("shutdown")
async def _stop_scheduler():
    await summary_scheduler.stop()
    summary_summarizer.close()
    if google_sync_task is not None:
        google_sync_task.cancel()
        try:
            await google_sync_task
        except (asyncio.CancelledError, Exception):
            pass


async def _google_sync_loop():
    """Keep the calendar and important mail fresh in the memory timeline.

    Only runs when Google is connected, and it never raises: a Google outage
    must not take the server down.
    """
    interval = max(60, int(os.getenv("GOOGLE_SYNC_INTERVAL_SECONDS", "900")))
    while True:
        try:
            await asyncio.sleep(interval)
            if not google_auth.get_credentials():
                continue
            await asyncio.to_thread(google_indexer.sync_all, True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger("lifelog.main").exception("google sync failed")


google_sync_task: Optional[asyncio.Task] = None


@app.on_event("startup")
async def _start_google_sync():
    global google_sync_task
    if os.getenv("GOOGLE_SYNC_ENABLED", "1").strip().lower() not in ("1", "true", "yes", "on"):
        return
    google_sync_task = asyncio.create_task(_google_sync_loop())


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


# ---- Rolling-window summaries (plain HTTP, no WebSocket) ----
# Route order matters: /summaries/latest and /summaries/status are registered
# before /summaries/{key}. Reorder them and FastAPI will match
# /summaries/status as key="status" and return {"summary": null}.


@app.get("/summaries")
async def api_summaries():
    """All stored summaries, oldest first."""
    return {"summaries": summary_store.all()}


@app.get("/summaries/latest")
async def api_summary_latest():
    """The single most recent summary.

    Returns {"summary": null} when there is nothing yet, so a polling client
    does not have to special-case errors.
    """
    return {"summary": summary_store.latest()}


@app.get("/summaries/status")
async def api_summary_status():
    """Summarizer health and remaining daily budget."""
    status = summary_scheduler.status()
    status.update(summary_summarizer.status())
    status["webhook_configured"] = bool(summary_webhook_url)
    return status


@app.post("/summaries/run")
async def api_summary_run():
    """Trigger a pass immediately instead of waiting for the timer.

    This calls the LLM for the next window, so it is an expensive operation.
    Do not call it on a timer from a phone.
    """
    document = await asyncio.to_thread(summary_scheduler.run_once)
    return {"summary": document, "ran": document is not None}


@app.get("/summaries/{key}")
async def api_summary(key: str):
    """One window by key, e.g. /summaries/2026-10-22T0139."""
    document = summary_store.load(key)
    if document is None:
        raise HTTPException(404, f"no summary for key '{key}'")
    return document


# ---- Google OAuth ----
@app.get("/auth/google")
async def auth_google(
    scope: str = Query("calendar"),
    mobile: int = 0,
    email: str = Query(""),
    choose_account: int = 1,
):
    """Start the OAuth flow.

    By default the Google "choose an account" page is shown, so the user can
    pick which Gmail to use. Pass ``choose_account=0`` to skip it, or
    ``email=you@gmail.com`` to preselect one account.
    """
    scopes = scope.split(",") if "," in scope else [scope]
    if not google_auth.is_configured():
        # Redirecting to Google with an empty client_id just produces an
        # "Missing required parameter: client_id" error page. Say what is
        # actually wrong instead.
        raise HTTPException(
            503,
            "Google OAuth is not configured: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET "
            "and GOOGLE_REDIRECT_URI must be set (server/.env or the environment).",
        )
    url = google_auth.build_auth_url(
        scopes,
        mobile=bool(mobile),
        email=email,
        choose_account=bool(choose_account),
    )
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
    c = await asyncio.to_thread(google_auth.get_credentials)
    if not c:
        return {
            "connected": False,
            "email": None,
            "scopes": [],
            "target_email": google_auth.configured_account_email() or None,
        }
    return {
        "connected": True,
        "email": c.get("email"),
        "scopes": (c.get("scopes") or "").split(),
        "target_email": google_auth.configured_account_email() or c.get("email"),
    }


@app.post("/auth/google/disconnect")
async def auth_google_disconnect():
    google_auth.delete_credentials()
    return {"success": True}


@app.post("/api/privacy/revoke-google")
async def revoke_google():
    google_auth.delete_credentials()
    return {"success": True}


# ---- Google Calendar ----
# Every Google call below runs in a worker thread. These endpoints do
# blocking HTTP to Google, and doing that on the event loop stalls the whole
# server - the app polls these, and a slow Google call would freeze every
# other request.
@app.get("/api/google/calendars")
async def api_calendars():
    calendars = await asyncio.to_thread(google_calendar.list_calendars)
    return {"calendars": calendars}


@app.get("/api/google/events")
async def api_events(calendar_id: str = "primary", time_min: str = None, time_max: str = None):
    try:
        events = await asyncio.to_thread(
            google_calendar.list_events, calendar_id, time_min, time_max
        )
    except google_calendar.GoogleApiError as e:
        raise HTTPException(e.status_code or 502, e.body or str(e))
    return {"events": events}


@app.post("/api/google/events")
async def api_create_event(body: dict):
    ev = await asyncio.to_thread(
        google_calendar.create_event,
        body.get("summary"), body.get("start"), body.get("end"),
        body.get("description"), body.get("location"), body.get("calendar_id", "primary"),
    )
    return {"success": True, "event": ev}


@app.delete("/api/google/events/{event_id}")
async def api_delete_event(event_id: str, calendar_id: str = "primary"):
    ok = await asyncio.to_thread(google_calendar.delete_event, event_id, calendar_id)
    return {"success": ok}


@app.get("/api/google/freebusy")
async def api_freebusy(time_min: str, time_max: str):
    freebusy = await asyncio.to_thread(google_calendar.freebusy, time_min, time_max)
    return {"freebusy": freebusy}


# ---- Gmail ----
@app.get("/api/google/messages")
async def api_messages(query: str = None, max_results: int = 10):
    """Mail, newest first. Defaults to the latest mail.

    Pass ``query`` to narrow it down, e.g. ``query=is:important``.
    """
    messages = await asyncio.to_thread(google_gmail.list_messages, query, max_results)
    return {"messages": messages}


@app.get("/api/google/messages/important")
async def api_important(max_results: int = 10, unread_only: int = 1):
    """The app's mail feed - the latest mail, newest first.

    This route used to return only the mail Gmail flags as important, which
    was not what the app wanted. It now returns the latest mail, the same as
    ``/api/google/messages`` and ``/api/google/messages/recent``.

    For the subset Gmail itself flags as important, use
    ``/api/google/messages/flagged``.
    """
    msgs = await asyncio.to_thread(google_gmail.list_recent, max_results)
    return {"messages": msgs}


@app.get("/api/google/messages/flagged")
async def api_flagged_messages(max_results: int = 10, unread_only: int = 1):
    """The messages Gmail flags as important. Unread only by default."""
    msgs = await asyncio.to_thread(
        google_gmail.list_important, max_results, bool(unread_only)
    )
    return {"messages": msgs}


@app.get("/api/google/messages/recent")
async def api_recent_messages(max_results: int = 10):
    """The latest mail, newest first."""
    msgs = await asyncio.to_thread(google_gmail.list_recent, max_results)
    return {"messages": msgs}


@app.get("/api/google/messages/{message_id}")
async def api_message(message_id: str):
    message = await asyncio.to_thread(google_gmail.get_message, message_id)
    return {"message": message}


@app.get("/api/google/profile")
async def api_profile():
    profile = await asyncio.to_thread(google_gmail.profile)
    return {"profile": profile}


@app.post("/api/google/index-emails")
async def api_index_emails(limit: int = 20):
    """Index the latest mail into the memory timeline."""
    result = await asyncio.to_thread(google_indexer.sync_recent_emails, limit)
    return {"emails": result.get("emails", 0), "result": result}


# ---- Google sync into the memory timeline ----
@app.post("/api/google/sync")
async def api_google_sync(
    force: int = 0,
    days_ahead: int = 14,
    days_back: int = 1,
    email_limit: int = 20,
):
    """Pull calendar events and important mail into the memory timeline.

    This is throttled unless ``force=1`` is passed, so it is safe to call on
    a dashboard poll.
    """
    result = await asyncio.to_thread(
        google_indexer.sync_all,
        bool(force),
        days_ahead,
        days_back,
        email_limit,
    )
    return {"result": result, **google_indexer.last_sync()}


@app.get("/api/google/sync/status")
async def api_google_sync_status():
    return google_indexer.last_sync()


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
    if d.get("category") == "email":
        d = _add_gmail_fields(d)
    return d


def _add_gmail_fields(d: dict) -> dict:
    """Give an email memory the shape of a Gmail message.

    Clients that already know how to read a Gmail message look for
    ``payload.headers[].name == "Subject"``. Without that they render the
    mail with no title at all.
    """
    people = d.get("people") or []
    sender = ""
    if people:
        sender = people[0]
        if len(people) > 1:
            sender = f"{people[0]} <{people[1]}>"
    received = d.get("starts_at")
    date_header = ""
    if received:
        date_header = datetime.fromtimestamp(
            received, tz=timezone.utc
        ).astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    d["subject"] = d.get("summary") or ""
    d["snippet"] = d.get("transcript") or ""
    d["from"] = sender
    d["date"] = date_header
    d["internalDate"] = str(int(received * 1000)) if received else ""
    d["labelIds"] = ["IMPORTANT", "INBOX"] if d.get("is_important") else ["INBOX"]
    d["id"] = (d.get("external_id") or "").split(":")[-1]
    d["payload"] = {
        "mimeType": "text/plain",
        "headers": [
            {"name": "Subject", "value": d["subject"]},
            {"name": "From", "value": sender},
            {"name": "To", "value": ""},
            {"name": "Date", "value": date_header},
        ],
    }
    return d


@app.get("/api/memories")
async def api_memories(
    limit: int = 20,
    offset: int = 0,
    category: str = None,
    important: int = None,
    source: str = None,
    q: str = None,
    upcoming: int = 0,
    days: int = 14,
    order: str = "new",
):
    """List stored memories - the single timeline of the user's life.

    It holds what was spoken (category ``memory``/``task``/``note``), what is
    coming up (category ``event``) and the latest mail (category ``email``).

    Filters:
      * ``category`` - ``memory`` | ``task`` | ``note`` | ``event`` | ``email``
      * ``important`` - 1 for important items only
      * ``source``   - ``llm`` | ``google_calendar`` | ``gmail``
      * ``upcoming`` - 1 to return only events still running in the next ``days``
      * ``q``        - free-text filter over the summary and transcript

    Ordering:
      * ``order=new`` (default) - newest added first
      * ``order=recent`` - newest by actual time (when the mail arrived, when
        the event is, or when the memory was created)
    """
    from .database import get_conn
    where = "1=1"
    params: list = []
    if category:
        where += " AND category=?"
        params.append(category)
    if important is not None:
        where += " AND is_important=?"
        params.append(1 if important else 0)
    if source:
        where += " AND source=?"
        params.append(source)
    if upcoming:
        # An event is "upcoming" while it has not finished yet. A meeting that
        # started an hour ago is still coming up, and an all-day event that
        # began at midnight is still going on until tomorrow.
        where += " AND ends_at IS NOT NULL AND ends_at >= ?"
        params.append(time.time())
        where += " AND starts_at IS NOT NULL AND starts_at <= ?"
        params.append(time.time() + days * 86400)
    if q:
        where += " AND (summary LIKE ? OR transcript LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])

    if order == "recent":
        sort = "COALESCE(starts_at, created_at) DESC, id DESC"
    elif order == "oldest":
        sort = "COALESCE(starts_at, created_at) ASC, id ASC"
    else:
        sort = "id DESC"

    conn = get_conn()
    # The total has to match the filter, otherwise pagination is wrong.
    total = conn.execute(
        f"SELECT COUNT(*) c FROM memories WHERE {where}", params
    ).fetchone()["c"]
    rows = conn.execute(
        f"SELECT * FROM memories WHERE {where} ORDER BY {sort} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return {"memories": [_row_to_dict(r) for r in rows], "total": total}


@app.get("/api/upcoming")
async def api_upcoming(days: int = 14, limit: int = 50):
    """What is coming up: calendar events ordered by start time.

    Only events starting within the next ``days`` are returned, and events
    in the past are left out. This is the view for "what is on today and
    over the next few days".
    """
    from .database import get_conn
    now = time.time()
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM memories
           WHERE category='event' AND starts_at IS NOT NULL AND ends_at IS NOT NULL
             AND ends_at >= ? AND starts_at <= ?
           ORDER BY starts_at ASC LIMIT ?""",
        (now, now + days * 86400, limit),
    ).fetchall()
    conn.close()
    events = [_row_to_dict(r) for r in rows]
    return {
        "events": events,
        "count": len(events),
        "days": days,
        "next": events[0] if events else None,
    }


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


@app.get("/api/search")
async def api_search_get(query: str = Query(...), threshold: float = 0.3, top_k: int = 10):
    """GET alias for POST /api/search, for clients that cannot send a body."""
    return await api_search(query, threshold, top_k)


# ---- Upload audio + AI pipeline ----
@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    """Transcribe an uploaded audio file and run the AI judgement on it.

    Accepts a WAV file or raw 16-bit PCM. Anything that cannot be decoded
    gets a readable error instead of a 500.
    """
    from .audio_decode import AudioDecodeError, decode_to_pcm
    from .whisper_engine import WhisperEngine

    content = await file.read()

    # A text file is not audio. Catch it here rather than feeding it to the
    # decoder as raw PCM.
    content_type = (file.content_type or "").lower()
    if content_type.startswith("text/") or (file.filename or "").lower().endswith(
        (".txt", ".json", ".md", ".csv", ".xml", ".html")
    ):
        raise HTTPException(400, f"'{file.filename}' is not an audio file")

    try:
        pcm = decode_to_pcm(content)
    except AudioDecodeError as e:
        raise HTTPException(400, str(e))

    engine = WhisperEngine(
        model_name=settings.whisper_model,
        device="cpu",
        compute_type="int8",
        vad_filter=False,
    )
    result = await asyncio.to_thread(engine.transcribe, pcm, 16000)
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
    conn_upcoming = conn.execute(
        """SELECT COUNT(*) c FROM memories
           WHERE category='event' AND starts_at IS NOT NULL AND ends_at IS NOT NULL
             AND ends_at >= ? AND starts_at <= ?""",
        (time.time(), time.time() + 14 * 86400),
    ).fetchone()["c"]
    conn.close()
    return {
        "memories": mems,
        "important": important,
        "actions": acts,
        "pending_actions": pending,
        "by_category": by_category,
        "upcoming": conn_upcoming,
        "tags": [],
        "sentiment": {},
    }


@app.get("/api/dashboard/daily-briefing")
async def api_daily_briefing():
    """Morning summary: recent memories, open tasks, what's coming up, and
    important mail.

    The calendar and mail entries are the same rows that appear in
    ``/api/memories``, so the timeline and the briefing never disagree.
    """
    from .database import get_conn
    now = time.time()
    conn = get_conn()
    mems = [_row_to_dict(r) for r in conn.execute(
        "SELECT * FROM memories ORDER BY id DESC LIMIT 5"
    ).fetchall()]
    tasks = [dict(r) for r in conn.execute(
        "SELECT * FROM actions WHERE status='pending' ORDER BY "
        "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id DESC LIMIT 10"
    ).fetchall()]
    upcoming = [_row_to_dict(r) for r in conn.execute(
        """SELECT * FROM memories
           WHERE category='event' AND starts_at IS NOT NULL AND ends_at IS NOT NULL
             AND ends_at >= ? AND starts_at <= ?
           ORDER BY starts_at ASC LIMIT 10""",
        (now, now + 14 * 86400),
    ).fetchall()]
    recent_mail = [_row_to_dict(r) for r in conn.execute(
        """SELECT * FROM memories
           WHERE category='email'
           ORDER BY starts_at DESC LIMIT 10"""
    ).fetchall()]
    conn.close()
    return {
        "memories": mems,
        "tasks": tasks,
        "upcoming": upcoming,
        # "important_emails" is kept under its old name so existing clients
        # keep working, but it now holds the latest mail, not the important
        # subset. "emails" is the clearer name for the same list.
        "important_emails": recent_mail,
        "emails": recent_mail,
        "calendar": upcoming,
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