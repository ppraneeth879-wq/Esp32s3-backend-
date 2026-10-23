"""LLM-based importance assessment and structured extraction from speech.

The pipeline is: raw speech -> LLM judgement of "is this worth keeping?" ->
structured payload (memory / task / note / ignore) -> persistence.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .llm import chat, chat_json

logger = logging.getLogger("lifelog.extraction")

# Categories a speech segment can resolve to.
CATEGORY_TASK = "task"
CATEGORY_MEMORY = "memory"
CATEGORY_NOTE = "note"
CATEGORY_IGNORE = "ignore"

VALID_CATEGORIES = {CATEGORY_TASK, CATEGORY_MEMORY, CATEGORY_NOTE, CATEGORY_IGNORE}

# Explicit, unambiguous intent markers. Deliberately narrow: a phrase only
# counts when it is the *subject* of the clause, not a passing mention.
TASK_HINTS = (
    "i need to",
    "i have to",
    "i must ",
    "i've got to",
    "i have got to",
    "remind me to",
    "remind me ",
    "don't forget to",
    "dont forget to",
    "make sure to",
    "remember to",
    "i'll ",
    "i will ",
    "we need to",
    "i need ",
    "need to call",
    "need to email",
    "need to send",
    "need to book",
    "need to buy",
    "need to pick",
    "have to call",
    "have to email",
    "have to send",
    "have to book",
    "have to buy",
    "call me back",
    "call them back",
    "follow up on",
    "follow up with",
    "book the ",
    "buy the ",
    "pick up the ",
    "send the ",
    "email the ",
    "pay the ",
    "schedule the ",
    "take care of",
    "deadline is",
    "due by",
    "due tomorrow",
    "due today",
    "due friday",
    "due monday",
)

# Words that negate a nearby intent marker, e.g. "I should never order that".
NEGATIONS = ("never", "not ", "n't", "no longer", "don't want", "dont want")

# Explicit, durable-fact markers.
MEMORY_HINTS = (
    "my name is",
    "my birthday is",
    "my birthday's",
    "i live in",
    "i live at",
    "i work at",
    "i work for",
    "i'm from",
    "i am from",
    "i prefer",
    "i always ",
    "i never ",
    "i don't like",
    "i do not like",
    "i hate",
    "i love",
    "we decided",
    "the plan is",
    "remember that",
    "note that",
    "my wife",
    "my husband",
    "my son",
    "my daughter",
    "my mother",
    "my father",
    "my brother",
    "my sister",
    "my boss",
    "my manager",
    "my doctor",
    "my therapist",
    "my car",
    "my bike",
    "my flight",
    "my hotel",
    "my dentist",
    "i'm allergic",
    "im allergic",
    "i am allergic",
    "my password",
    "my account",
    "my number is",
    "my address is",
)

# Filler that should never be treated as content on its own.
FILLER_PHRASES = (
    "mm hmm",
    "mm-hmm",
    "yeah no",
    "you know what i mean",
    "or whatever",
    "anyway",
)

SYSTEM_PROMPT = """You are the analysis engine for "LifeLog", a personal memory system fed by continuous speech from a wearable device.

Analyse the SPEECH below and decide what, if anything, is worth persisting.

Reply with a single JSON object and nothing else. No prose, no markdown fences.

Schema:
{
  "important": boolean,
  "importance": number between 0 and 1,
  "category": "memory" | "note" | "ignore",
  "summary": "one sentence describing the content",
  "reason": "short justification for the category chosen",
  "sentiment": "positive" | "neutral" | "negative",
  "topics": [string],
  "tags": [string],
  "people": [string],
  "dates": [string]
}

Decision rules:
- "memory": a durable fact, preference, decision, plan, relationship fact, or an event worth recalling days later.
- "note": mildly useful context, but not durable enough to be a memory.
- "ignore": small talk, filler, greetings, repeated statements, emotional venting with no content, or empty/noise.
- "important" is true when the category is "memory" and the importance is at least 0.5.
- Be strict and literal. Never invent anything that was not said.
- "people": names of people mentioned. Use [] if none.
- "dates": any dates or times mentioned, as spoken ("tomorrow", "next Friday", "2026-10-20"). Use [] if none.
- Only ever use information that is present in the speech. Never speculate.
- If the speech is empty, meaningless, or contains no content, return category "ignore" and importance 0."""

# Task extraction is opt-in. The automated pipeline does not create tasks:
# the hourly rolling-window summarizer is what turns speech into something
# actionable, and auto-generated tasks were noisy.
TASK_SCHEMA_PROMPT = """
Schema (extended):
{
  "important": boolean,
  "importance": number between 0 and 1,
  "category": "task" | "memory" | "note" | "ignore",
  "summary": "one sentence describing the content",
  "reason": "short justification for the category chosen",
  "sentiment": "positive" | "neutral" | "negative",
  "topics": [string],
  "tags": [string],
  "people": [string],
  "dates": [string],
  "tasks": [{"title": string, "priority": "high" | "medium" | "low", "due_date": string|null}],
  "action_items": [string]
}

Extra rules for "tasks":
- "task": the speaker commits to doing something, sets a deadline or a reminder, or states a problem that needs follow-up. Each entry in "tasks" must be one concrete, self-contained imperative statement ("Email Sarah about the invoice", not "Sarah thing").
- Only add a task when the speech clearly expresses an intent to DO something."""


def build_system_prompt(create_tasks: bool = False) -> str:
    """The prompt for the model, with or without the task schema."""
    if create_tasks:
        return SYSTEM_PROMPT + TASK_SCHEMA_PROMPT
    return SYSTEM_PROMPT


@dataclass
class ExtractionResult:
    """Structured judgement about one speech segment."""

    important: bool = False
    importance: float = 0.0
    category: str = CATEGORY_IGNORE
    summary: str = ""
    reason: str = ""
    sentiment: str = "neutral"
    topics: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    source: str = "llm"

    @property
    def is_actionable(self) -> bool:
        return self.category == CATEGORY_TASK or bool(self.tasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "important": self.important,
            "importance": self.importance,
            "category": self.category,
            "summary": self.summary,
            "reason": self.reason,
            "sentiment": self.sentiment,
            "topics": self.topics,
            "tags": self.tags,
            "people": self.people,
            "dates": self.dates,
            "tasks": self.tasks,
            "action_items": self.action_items,
            "source": self.source,
        }


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that models sometimes wrap JSON in."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t, flags=re.IGNORECASE)
    t = re.sub(r"```$", "", t)
    return t.strip()


def parse_json_object(text: str) -> Optional[dict[str, Any]]:
    """Best-effort extraction of a JSON object from a model response."""
    if not text:
        return None
    candidate = _strip_fences(text)
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # Fall back to the outermost {...} span.
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(candidate[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        out = []
        for v in value:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
            elif v is not None and not isinstance(v, (dict, list)):
                out.append(str(v).strip())
        return out
    return []


def _normalise_tasks(value: Any) -> list[dict[str, Any]]:
    """Coerce the model's "tasks" field into a list of task dicts."""
    out: list[dict[str, Any]] = []
    if not value:
        return out
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return out
    for item in value:
        if isinstance(item, str):
            title = item.strip()
            if title:
                out.append({"title": title, "priority": "medium", "due_date": None})
        elif isinstance(item, dict):
            title = str(item.get("title") or item.get("text") or item.get("task") or "").strip()
            if not title:
                continue
            priority = str(item.get("priority") or "medium").lower()
            if priority not in ("high", "medium", "low"):
                priority = "medium"
            due = item.get("due_date") or item.get("due") or None
            out.append(
                {
                    "title": title,
                    "priority": priority,
                    "due_date": str(due) if due else None,
                }
            )
    return out


def _build_result(data: dict[str, Any], source: str = "llm") -> ExtractionResult:
    category = str(data.get("category") or CATEGORY_IGNORE).strip().lower()
    if category not in VALID_CATEGORIES:
        category = CATEGORY_IGNORE

    try:
        importance = float(data.get("importance", 0.0))
    except (TypeError, ValueError):
        importance = 0.0
    importance = max(0.0, min(1.0, importance))

    tasks = _normalise_tasks(data.get("tasks"))
    action_items = _as_list(data.get("action_items"))

    sentiment = str(data.get("sentiment") or "neutral").strip().lower()
    if sentiment not in ("positive", "neutral", "negative"):
        sentiment = "neutral"

    important = bool(data.get("important", False))
    if category in (CATEGORY_TASK, CATEGORY_MEMORY):
        important = True
    if importance >= 0.5:
        important = True

    return ExtractionResult(
        important=important,
        importance=importance,
        category=category,
        summary=str(data.get("summary") or "").strip(),
        reason=str(data.get("reason") or "").strip(),
        sentiment=sentiment,
        topics=_as_list(data.get("topics")),
        tags=_as_list(data.get("tags")),
        people=_as_list(data.get("people")),
        dates=_as_list(data.get("dates")),
        tasks=tasks,
        action_items=action_items,
        source=source,
    )


def _has_intent(text: str, hints: tuple[str, ...]) -> bool:
    """True when a hint appears and is not immediately negated."""
    for hint in hints:
        idx = text.find(hint)
        if idx == -1:
            continue
        window = text[idx : idx + len(hint) + 20]
        if any(neg in window for neg in NEGATIONS):
            continue
        return True
    return False


def _is_filler(text: str) -> bool:
    """True when the text is pure conversational filler."""
    stripped = text.strip(" .!?,")
    if not stripped:
        return True
    words = stripped.split()
    if len(words) <= 3:
        low = stripped.lower()
        return low in ("mm hmm", "yeah", "ok", "okay", "sure", "yep", "nope", "right", "alright", "got it")
    return False


def heuristic_extract(transcript: str) -> ExtractionResult:
    """Offline fallback used when the LLM is unavailable.

    Deliberately conservative: it only fires on unambiguous intent phrasing
    and requires a minimum amount of substance, so that filler and small talk
    are classified as "ignore" rather than being turned into fake tasks.
    """
    raw = (transcript or "").strip()
    if not raw:
        return ExtractionResult(category=CATEGORY_IGNORE, source="heuristic")

    text = raw.lower()

    # Strip filler that carries no intent.
    for filler in FILLER_PHRASES:
        text = text.replace(filler, " ")

    # Too short to judge anything.
    if len(text.split()) < 5 or _is_filler(text):
        return ExtractionResult(
            important=False,
            importance=0.0,
            category=CATEGORY_IGNORE,
            summary=raw[:200],
            reason="heuristic: too short to contain content",
            source="heuristic",
        )

    has_task = _has_intent(text, TASK_HINTS)
    has_memory = _has_intent(text, MEMORY_HINTS)

    if has_task:
        # Split into candidate task sentences, keep only the ones that carry
        # the intent marker.
        sentences = [s.strip() for s in re.split(r"[.!?]+", raw) if s.strip()]
        tasks = []
        for s in sentences:
            if len(s.split()) < 5:
                continue
            if _has_intent(s.lower(), TASK_HINTS):
                tasks.append({"title": s, "priority": "medium", "due_date": None})
        if not tasks:
            tasks = [{"title": raw[:200], "priority": "medium", "due_date": None}]
        return ExtractionResult(
            important=True,
            importance=0.6,
            category=CATEGORY_TASK,
            summary=(tasks[0]["title"])[:300],
            reason="heuristic: explicit task intent detected",
            tasks=tasks[:3],
            action_items=[t["title"] for t in tasks[:3]],
            source="heuristic",
        )

    if has_memory:
        return ExtractionResult(
            important=True,
            importance=0.55,
            category=CATEGORY_MEMORY,
            summary=raw[:300],
            reason="heuristic: durable fact detected",
            source="heuristic",
        )

    return ExtractionResult(
        important=False,
        importance=0.1,
        category=CATEGORY_IGNORE,
        summary=raw[:300],
        reason="heuristic: no task or durable fact detected",
        source="heuristic",
    )


def extract(
    transcript: str,
    model: str = "",
    threshold: float = 0.4,
    create_tasks: bool = False,
) -> Optional[ExtractionResult]:
    """Run the LLM over a speech segment and return a structured judgement.

    Falls back to :func:`heuristic_extract` when every model fails.
    """
    text = (transcript or "").strip()
    if not text:
        return None

    messages = [
        {"role": "system", "content": build_system_prompt(create_tasks)},
        {"role": "user", "content": f"SPEECH:\n{text}"},
    ]

    data = chat_json(messages, model=model or None, temperature=0.1)
    if data is None:
        logger.warning("extraction: LLM unavailable, using heuristic")
        result = heuristic_extract(text)
    else:
        result = _build_result(data, source="llm")

    if not create_tasks:
        # The automated pipeline does not create tasks.
        result.tasks = []
        result.action_items = []
        if result.category == CATEGORY_TASK:
            result.category = CATEGORY_MEMORY

    # Below the threshold, downgrade to a plain note so it is still searchable
    # but does not show up as an important item.
    if result.important and result.importance < threshold and result.category == CATEGORY_MEMORY:
        result.category = CATEGORY_NOTE
        result.important = False
        result.reason = (result.reason + " (below importance threshold)").strip()

    return result
