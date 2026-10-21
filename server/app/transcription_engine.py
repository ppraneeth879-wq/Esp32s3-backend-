"""Transcription engine abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class TranscriptSegment:
    text: str
    start: float = 0.0
    end: float = 0.0
    is_final: bool = True


@dataclass
class TranscriptResult:
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str = ""

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()


class TranscriptionEngine(Protocol):
    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> TranscriptResult:
        ...