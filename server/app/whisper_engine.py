"""Faster-Whisper transcription engine implementation."""

from __future__ import annotations

import io
import wave
from typing import Optional

from .transcription_engine import TranscriptResult, TranscriptSegment


class WhisperEngine:
    def __init__(
        self,
        model_name: str = "large-v3-turbo",
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
        vad_filter: bool = True,
        initial_prompt: str = "",
    ):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.initial_prompt = initial_prompt
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError("faster-whisper not installed") from e
        self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)

    def _pcm_to_wav_bytes(self, audio_bytes: bytes, sample_rate: int) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(audio_bytes)
        return buf.getvalue()

    def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> TranscriptResult:
        self._load()
        wav_bytes = self._pcm_to_wav_bytes(audio_bytes, sample_rate)
        # faster-whisper accepts file-like or path; use BytesIO via temp not needed, pass ndarray approach.
        import numpy as np

        # Convert PCM bytes to float32 array
        pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # --- Pre-processing: DC offset removal + amplitude guard ---
        # Remove DC bias (common with raw PDM mic output)
        dc = np.mean(pcm)
        if abs(dc) > 1e-6:
            pcm = pcm - dc
        # Guard against near-zero signal (silence / no clock)
        rms = float(np.sqrt(np.mean(pcm ** 2)))
        self._last_rms = rms
        kwargs = dict(
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            condition_on_previous_text=False,
            language="en",
        )
        if self.initial_prompt:
            kwargs["initial_prompt"] = self.initial_prompt
        segments, info = self._model.transcribe(pcm, **kwargs)
        out_segments = []
        for seg in segments:
            out_segments.append(
                TranscriptSegment(text=seg.text.strip(), start=seg.start, end=seg.end, is_final=True)
            )
        return TranscriptResult(segments=out_segments, language=getattr(info, "language", "") or "")