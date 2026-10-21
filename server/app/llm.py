"""LLM client using OpenRouter (OpenAI-compatible) with a resilient fallback chain.

Free OpenRouter models are shared-pool and frequently return HTTP 429. The
client therefore walks a chain of models, remembers which ones are cooling
down, and returns the first successful completion.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("lifelog.llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Ordered fallback chain. The first entry is the preferred model; the rest are
# tried in order when a model is rate-limited or errors.
DEFAULT_CHAIN: list[str] = [
    "google/gemma-4-26b-a4b-it:free",
    "minimax/minimax-m3.1-flash:free",
    "z-ai/glm-5.3-flash",
    "google/gemini-2.5-flash-lite",
    "openai/gpt-4o-mini",
]

# How long to skip a model after a given failure type.
COOLDOWN_RATE_LIMIT = 300.0   # 429: shared free pool is saturated
COOLDOWN_SERVER_ERROR = 60.0  # 5xx: transient
COOLDOWN_OTHER = 30.0

_cooldowns: dict[str, float] = {}
_lock = threading.Lock()


def _key() -> str:
    return os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("POOLSIDE_API_KEY", "")


def _url() -> str:
    if os.getenv("OPENROUTER_API_KEY"):
        return OPENROUTER_URL
    return OPENAI_URL


def _in_cooldown(model: str) -> bool:
    with _lock:
        until = _cooldowns.get(model, 0.0)
        if until and until > time.monotonic():
            return True
        if until:
            _cooldowns.pop(model, None)
        return False


def _set_cooldown(model: str, seconds: float) -> None:
    with _lock:
        _cooldowns[model] = time.monotonic() + seconds


def clear_cooldowns() -> None:
    """Reset the model cooldown table (used by tests and on demand)."""
    with _lock:
        _cooldowns.clear()


def _candidates(model: Optional[str]) -> list[str]:
    """Build the ordered list of models to try."""
    chain = [m for m in (os.getenv("LIFELOG_LLM_CHAIN") or "").split(",") if m.strip()]
    if not chain:
        chain = list(DEFAULT_CHAIN)
    if model:
        # The explicitly requested model goes first, then the rest of the chain.
        ordered = [model] + [m for m in chain if m != model]
    else:
        ordered = list(chain)
    return ordered


def _post(model: str, messages: list[dict], temperature: float, timeout: float,
          max_tokens: Optional[int] = None) -> tuple[bool, str, int]:
    """One HTTP attempt. Returns (ok, content_or_error, status_code)."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    try:
        resp = httpx.post(
            _url(),
            headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", 0
    if resp.status_code != 200:
        return False, resp.text[:300], resp.status_code
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        return False, f"bad response shape: {e}", resp.status_code
    if content is None:
        return False, "empty content", resp.status_code
    return True, str(content), resp.status_code


def chat(
    messages: list[dict],
    model: Optional[str] = None,
    temperature: float = 0.3,
    timeout: float = 60.0,
    max_tokens: Optional[int] = None,
) -> str:
    """Call the LLM, walking the fallback chain until one model succeeds.

    Returns the completion text, or an empty string if every model failed.
    """
    key = _key()
    if not key:
        return ""

    last_error = ""
    for candidate in _candidates(model):
        if _in_cooldown(candidate):
            continue
        ok, content, status = _post(candidate, messages, temperature, timeout, max_tokens)
        if ok:
            return content
        last_error = f"{candidate}: [{status}] {content}"
        if status == 429:
            _set_cooldown(candidate, COOLDOWN_RATE_LIMIT)
        elif status >= 500:
            _set_cooldown(candidate, COOLDOWN_SERVER_ERROR)
        elif status:
            _set_cooldown(candidate, COOLDOWN_OTHER)
        logger.warning("LLM model %s failed: [%s] %s", candidate, status, content[:200])

    if last_error:
        logger.error("LLM: all models failed. last error: %s", last_error[:300])
    return ""


def chat_json(
    messages: list[dict],
    model: Optional[str] = None,
    temperature: float = 0.1,
    timeout: float = 60.0,
) -> Optional[dict]:
    """Call the LLM and parse the reply as a JSON object.

    Tries each model in the chain and returns the first parseable object.
    """
    from .extraction import parse_json_object

    key = _key()
    if not key:
        return None

    for candidate in _candidates(model):
        if _in_cooldown(candidate):
            continue
        ok, content, status = _post(candidate, messages, temperature, timeout)
        if not ok:
            if status == 429:
                _set_cooldown(candidate, COOLDOWN_RATE_LIMIT)
            elif status >= 500:
                _set_cooldown(candidate, COOLDOWN_SERVER_ERROR)
            elif status:
                _set_cooldown(candidate, COOLDOWN_OTHER)
            logger.warning("LLM JSON model %s failed: [%s]", candidate, status)
            continue
        data = parse_json_object(content)
        if data is not None:
            return data
        logger.warning("LLM JSON model %s returned unparseable output", candidate)

    return None
