"""Configuration loading for LifeLog backend."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# Every key a profile may contain. A profile with an unknown key is rejected
# so a typo fails fast at startup instead of being silently ignored.
KNOWN_KEYS = frozenset(
    {
        "host",
        "port",
        "whisper_model",
        "whisper_device",
        "whisper_compute_type",
        "whisper_beam_size",
        "whisper_vad_filter",
        "whisper_initial_prompt",
        "audio_window_seconds",
        "storage_dir",
        "log_level",
        "max_clients",
        "telemetry_interval_seconds",
        "extraction_enabled",
        "extraction_model",
        "importance_threshold",
        "extraction_min_chars",
        "extraction_max_wait_seconds",
        "extraction_create_actions",
        "summary_model",
        "summary_interval_seconds",
        "summary_window_seconds",
        "summary_retention_days",
        "summary_daily_request_budget",
    }
)


class ConfigError(ValueError):
    """Raised when a configuration profile is invalid."""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return raw.strip()


class Settings:
    def __init__(self, data: dict[str, Any], base_dir: Path):
        unknown = set(data) - KNOWN_KEYS
        if unknown:
            raise ConfigError(
                "unknown configuration key(s): " + ", ".join(sorted(unknown))
            )

        self.host: str = data.get("host", "0.0.0.0")
        self.port: int = int(data.get("port", 8000))
        self.whisper_model: str = data.get("whisper_model", "large-v3-turbo")
        self.whisper_device: str = data.get("whisper_device", "cpu")
        self.whisper_compute_type: str = data.get("whisper_compute_type", "int8")
        self.whisper_beam_size: int = int(data.get("whisper_beam_size", 5))
        self.whisper_vad_filter: bool = bool(data.get("whisper_vad_filter", True))
        self.whisper_initial_prompt: str = data.get("whisper_initial_prompt", "")
        self.audio_window_seconds: int = int(data.get("audio_window_seconds", 5))
        self.storage_dir: str = data.get("storage_dir", "uploads")
        self.log_level: str = data.get("log_level", "INFO")
        self.max_clients: int = int(data.get("max_clients", 100))
        self.telemetry_interval_seconds: int = int(data.get("telemetry_interval_seconds", 5))
        # Insight pipeline (speech -> memory)
        self.extraction_enabled: bool = bool(data.get("extraction_enabled", True))
        self.extraction_model: str = data.get("extraction_model", "")
        self.importance_threshold: float = float(data.get("importance_threshold", 0.4))
        self.extraction_min_chars: int = int(data.get("extraction_min_chars", 80))
        self.extraction_max_wait_seconds: float = float(data.get("extraction_max_wait_seconds", 25))
        # Auto-generated tasks are off by default.
        self.extraction_create_actions: bool = bool(data.get("extraction_create_actions", False))

        # Rolling-window summarizer. Environment variables override the file.
        self.summary_model: str = _env_str(
            "LIFELOG_SUMMARY_MODEL", data.get("summary_model", "nvidia/nemotron-3-ultra-550b-a55b:free")
        )
        self.summary_interval_seconds: int = _env_int(
            "LIFELOG_SUMMARY_INTERVAL", int(data.get("summary_interval_seconds", 3600))
        )
        self.summary_window_seconds: int = _env_int(
            "LIFELOG_SUMMARY_WINDOW", int(data.get("summary_window_seconds", 3600))
        )
        self.summary_retention_days: int = _env_int(
            "LIFELOG_SUMMARY_RETENTION_DAYS", int(data.get("summary_retention_days", 30))
        )
        self.summary_daily_request_budget: int = _env_int(
            "LIFELOG_SUMMARY_DAILY_BUDGET", int(data.get("summary_daily_request_budget", 50))
        )

        self.base_dir = base_dir

    @property
    def storage_path(self) -> Path:
        p = Path(_env_str("LIFELOG_STORAGE_DIR", self.storage_dir))
        if not p.is_absolute():
            p = self.base_dir / p
        return p

    @property
    def sample_rate(self) -> int:
        return 16000

    @property
    def channels(self) -> int:
        return 1

    @property
    def bit_depth(self) -> int:
        return 16

    @property
    def chunk_size(self) -> int:
        return 2048

    @property
    def audio_window_bytes(self) -> int:
        return self.sample_rate * (self.bit_depth // 8) * self.channels * self.audio_window_seconds


def load_settings(env: str = "development", base_dir: Path | None = None) -> Settings:
    if base_dir is None:
        # server root is parent of app/
        server_root = Path(__file__).resolve().parent.parent
    else:
        server_root = base_dir
    config_path = server_root / "config" / f"{env}.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Settings(data, base_dir=server_root)


def settings_from_env() -> Settings:
    """Load the profile named by ``LIFELOG_PROFILE`` (or ``LIFELOG_ENV``)."""
    profile = _env_str("LIFELOG_PROFILE", "") or _env_str("LIFELOG_ENV", "") or "development"
    return load_settings(profile)
