"""Configuration loading for LifeLog backend."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class Settings:
    def __init__(self, data: dict[str, Any], base_dir: Path):
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
        # Insight pipeline (speech -> memory/task)
        self.extraction_enabled: bool = bool(data.get("extraction_enabled", True))
        self.extraction_model: str = data.get("extraction_model", "")
        self.importance_threshold: float = float(data.get("importance_threshold", 0.4))
        self.extraction_min_chars: int = int(data.get("extraction_min_chars", 80))
        self.extraction_max_wait_seconds: float = float(data.get("extraction_max_wait_seconds", 25))
        self.base_dir = base_dir

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
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
        base_dir = Path.cwd()
    # server root is parent of app/
    server_root = Path(__file__).resolve().parent.parent
    config_path = server_root / "config" / f"{env}.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Settings(data, base_dir=server_root)


def settings_from_env() -> Settings:
    env = os.getenv("LIFELOG_ENV", "development")
    return load_settings(env)