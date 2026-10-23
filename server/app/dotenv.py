"""Minimal .env loader for local secrets.

A real environment variable always wins over a value in the file, so a shell
variable (or a Docker ``environment:`` entry) overrides ``server/.env``.

Deliberately dependency-free: the server must be able to start on a machine
where ``python-dotenv`` is not installed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

__all__ = ["parse_env_file", "load_env_file", "load_dotenv"]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a ``.env`` file into a dict. Malformed lines are skipped."""
    values: Dict[str, str] = {}
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return values

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_quotes(value.strip())
    return values


def load_env_file(path: Path, override: bool = False) -> Dict[str, str]:
    """Load ``path`` into ``os.environ``.

    Existing environment variables are preserved unless ``override=True``.
    Returns the values that were actually applied.
    """
    applied: Dict[str, str] = {}
    for key, value in parse_env_file(path).items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def load_dotenv(path: Optional[Path] = None, override: bool = False) -> Dict[str, str]:
    """Convenience wrapper that defaults to ``<repo>/server/.env``."""
    if path is None:
        # app/dotenv.py -> app/ -> server/
        path = Path(__file__).resolve().parent.parent / ".env"
    return load_env_file(Path(path), override=override)
