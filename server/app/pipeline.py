"""Insight pipeline: turns live speech into memories and tasks.

Responsibilities:
  * accumulate transcript fragments (with overlap de-duplication)
  * flush to the LLM on a size/time trigger so we do not call it per window
  * persist the judgement as a memory and/or task
  * broadcast the result to /events clients
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any, Optional

from .database import get_conn
from .extraction import (
    CATEGORY_IGNORE,
    CATEGORY_MEMORY,
    CATEGORY_NOTE,
    CATEGORY_TASK,
    ExtractionResult,
    extract,
)
from .llm import chat
from .vector_store import vector_store

logger = logging.getLogger("lifelog.pipeline")

# Categories that get persisted as a memory row.
STORED_CATEGORIES = {CATEGORY_TASK, CATEGORY_MEMORY, CATEGORY_NOTE}

_PUNCT = re.compile(r"[^\w\s]")


def _norm_word(w: str) -> str:
    return _PUNCT.sub("", w.lower())


def new_part(existing: str, incoming: str) -> str:
    """Return the part of `incoming` that is not already in `existing`.

    Whisper re-transcribes overlapping audio, so consecutive windows repeat
    their tail. We align on a word boundary and keep only the unseen remainder.
    """
    ex_words = existing.split()
    in_words = incoming.split()
    if not ex_words:
        return incoming
    if not in_words:
        return ""

    max_overlap = min(len(ex_words), len(in_words))
    for n in range(max_overlap, 0, -1):
        tail = [_norm_word(w) for w in ex_words[-n:]]
        head = [_norm_word(w) for w in in_words[:n]]
        if tail == head:
            return " ".join(in_words[n:])
    return incoming


def content_hash(text: str) -> str:
    """Stable hash of normalised text, used to avoid duplicate memories."""
    norm = " ".join(_norm_word(w) for w in (text or "").split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def store_memory(
    result: ExtractionResult,
    transcript: str,
    session_id: str = "",
    content_hash_value: str = "",
) -> Optional[dict[str, Any]]:
    """Persist a memory row + its vector embedding. Returns the memory dict."""
    vector_id = str(uuid.uuid4())
    created_at = time.time()
    h = content_hash_value or content_hash(result.summary or transcript)

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO memories
               (vector_id, transcript, summary, sentiment, tags, topics, tasks,
                people, dates, action_items, created_at, is_important, importance,
                category, reason, source, session_id, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                vector_id,
                transcript,
                result.summary,
                result.sentiment,
                json.dumps(result.tags),
                json.dumps(result.topics),
                json.dumps([t["title"] for t in result.tasks]),
                json.dumps(result.people),
                json.dumps(result.dates),
                json.dumps(result.action_items),
                created_at,
                1 if result.important else 0,
                result.importance,
                result.category,
                result.reason,
                result.source,
                session_id,
                h,
            ),
        )
        memory_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("failed to store memory")
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Searchable text: the summary carries the semantic meaning, the
    # transcript carries the literal wording.
    doc = f"{result.summary}\n{transcript}".strip()
    try:
        vector_store.add(
            vector_id,
            doc,
            {
                "category": result.category,
                "important": bool(result.important),
                "importance": result.importance,
                "sentiment": result.sentiment,
                "tags": ",".join(result.tags),
                "topics": ",".join(result.topics),
                "people": ",".join(result.people),
                "memory_id": memory_id,
            },
        )
    except Exception:
        logger.exception("vector store add failed for memory %s", vector_id)

    return {
        "id": memory_id,
        "vector_id": vector_id,
        "summary": result.summary,
        "transcript": transcript,
        "category": result.category,
        "important": bool(result.important),
        "importance": result.importance,
        "sentiment": result.sentiment,
        "tags": result.tags,
        "topics": result.topics,
        "people": result.people,
        "dates": result.dates,
        "reason": result.reason,
        "created_at": created_at,
    }


def store_actions(
    result: ExtractionResult,
    memory_id: Optional[int] = None,
    session_id: str = "",
) -> list[dict[str, Any]]:
    """Turn extracted tasks into rows in the actions table."""
    if not result.tasks:
        return []
    conn = get_conn()
    cur = conn.cursor()
    out: list[dict[str, Any]] = []
    try:
        for t in result.tasks:
            title = t.get("title", "").strip()
            if not title:
                continue
            confidence = max(0.0, min(1.0, result.importance or 0.5))
            cur.execute(
                """INSERT INTO actions
                   (memory_id, title, description, action_type, priority, status,
                    confidence, due_date, source, session_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    memory_id,
                    title,
                    title,
                    CATEGORY_TASK,
                    t.get("priority") or "medium",
                    "pending",
                    confidence,
                    t.get("due_date"),
                    result.source,
                    session_id,
                    time.time(),
                ),
            )
            out.append(
                {
                    "id": cur.lastrowid,
                    "title": title,
                    "description": title,
                    "action_type": CATEGORY_TASK,
                    "priority": t.get("priority") or "medium",
                    "status": "pending",
                    "confidence": confidence,
                    "due_date": t.get("due_date"),
                    "memory_id": memory_id,
                }
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("failed to store actions")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


class InsightPipeline:
    """Buffers speech for a session and turns it into memories/tasks."""

    def __init__(
        self,
        broadcaster,
        session_id: str,
        device_id: str = "",
        model: str = "",
        threshold: float = 0.4,
        min_chars: int = 80,
        max_wait_seconds: float = 25.0,
        enabled: bool = True,
        create_tasks: bool = False,
    ):
        self.broadcaster = broadcaster
        self.session_id = session_id
        self.device_id = device_id
        self.model = model
        self.threshold = threshold
        self.min_chars = min_chars
        self.max_wait_seconds = max_wait_seconds
        self.enabled = enabled
        # Auto-generated tasks are off by default. The hourly rolling-window
        # summarizer is the mechanism for turning speech into something
        # actionable; per-utterance task generation was noisy.
        self.create_tasks = create_tasks

        self._buffer = ""          # pending, not yet analysed
        self._analysed = ""        # everything already analysed
        self._last_flush = time.monotonic()
        self._lock = asyncio.Lock()
        self._closed = False
        self._seen_hashes: set[str] = set()
        self._pending_task: Optional[asyncio.Task] = None

    # -- public API -----------------------------------------------------

    async def feed(self, text: str) -> None:
        """Accept a new transcript fragment."""
        if not self.enabled or self._closed:
            return
        fragment = (text or "").strip()
        if not fragment:
            return

        async with self._lock:
            fresh = new_part(self._analysed, fragment)
            if not fresh:
                return
            self._buffer = (self._buffer + " " + fresh).strip()
            self._analysed = (self._analysed + " " + fresh).strip()
            # keep the analysis window bounded
            if len(self._analysed) > 4000:
                self._analysed = self._analysed[-2000:]
            should_flush = len(self._buffer) >= self.min_chars

        if should_flush:
            await self._drain()

    async def _drain(self) -> None:
        """Run the pending buffer through the LLM (fire-and-forget safe)."""
        async with self._lock:
            if not self._buffer:
                return
            text = self._buffer
            self._buffer = ""
            self._last_flush = time.monotonic()
        await self._analyse(text, "size")

    async def tick(self) -> None:
        """Periodic check: flush if the buffer has been waiting too long."""
        if not self.enabled or self._closed:
            return
        async with self._lock:
            has_text = bool(self._buffer)
            waited = time.monotonic() - self._last_flush
        if has_text and waited >= self.max_wait_seconds:
            await self._drain()

    async def close(self) -> None:
        """Flush whatever is left when the session ends."""
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            text = self._buffer
            self._buffer = ""
        if text.strip():
            await self._analyse(text, "session_end")

    # -- internals ------------------------------------------------------

    async def _analyse(self, text: str, reason: str) -> None:
        """Blocking work runs in a thread so the event loop stays responsive."""
        try:
            payload = await asyncio.to_thread(self._process, text, reason)
        except Exception:
            logger.exception("insight pipeline failed for session %s", self.session_id)
            return
        if not payload:
            return
        try:
            await self.broadcaster.broadcast(payload)
        except Exception:
            logger.exception("failed to broadcast insight for session %s", self.session_id)

    def _process(self, text: str, reason: str) -> Optional[dict[str, Any]]:
        """LLM analysis + persistence. Runs off the event loop."""
        result = extract(
            text,
            model=self.model,
            threshold=self.threshold,
            create_tasks=self.create_tasks,
        )
        if result is None:
            return None

        if result.category == CATEGORY_IGNORE:
            logger.info("insight[%s]: ignored (%s)", self.session_id, result.reason)
            return {
                "type": "insight",
                "session_id": self.session_id,
                "device_id": self.device_id,
                "reason": reason,
                "ignored": True,
                "important": False,
                "importance": result.importance,
                "category": result.category,
                "summary": result.summary,
                "memory": None,
                "actions": [],
            }

        if result.category not in STORED_CATEGORIES:
            result.category = CATEGORY_NOTE

        h = content_hash(result.summary or text)
        if h in self._seen_hashes:
            logger.info("insight[%s]: duplicate, skipped", self.session_id)
            return None
        self._seen_hashes.add(h)

        memory = store_memory(result, text, self.session_id, h)
        actions = (
            store_actions(result, memory["id"] if memory else None, self.session_id)
            if self.create_tasks
            else []
        )

        logger.info(
            "insight[%s]: category=%s importance=%.2f memories=1 actions=%d",
            self.session_id,
            result.category,
            result.importance,
            len(actions),
        )

        return {
            "type": "insight",
            "session_id": self.session_id,
            "device_id": self.device_id,
            "reason": reason,
            "ignored": False,
            "important": result.important,
            "importance": result.importance,
            "category": result.category,
            "summary": result.summary,
            "detail": result.reason,
            "memory": memory,
            "actions": actions,
        }


def summarise_memories(question: str, top_k: int = 6) -> str:
    """Semantic recall used by /api/chat. Blocking; call via asyncio.to_thread."""
    try:
        results = vector_store.search(question, threshold=0.0, top_k=top_k)
    except Exception:
        return ""
    parts = []
    for r in results:
        meta = r.get("metadata") or {}
        label = meta.get("category") or "memory"
        parts.append(f"[{label}] {r.get('text', '')}")
    return "\n".join(parts)


def ask_llm(question: str, context: str) -> str:
    """Answer a question using recalled memories as context. Blocking."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are the LifeLog assistant. You answer questions about the user's "
                "own life using the memories provided. If the memories do not contain "
                "the answer, say so plainly. Be concise."
            ),
        },
        {
            "role": "user",
            "content": f"Memories:\n{context or '(none)'}\n\nQuestion: {question}",
        },
    ]
    return chat(messages)
