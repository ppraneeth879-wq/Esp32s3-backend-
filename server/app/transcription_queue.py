"""Transcription queue: accumulates PCM into windows and dispatches to engine."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Callable, Optional

from .transcription_engine import TranscriptionEngine, TranscriptResult


class TranscriptionQueue:
    def __init__(
        self,
        engine: TranscriptionEngine,
        window_bytes: int,
        on_result: Callable[[TranscriptResult], None],
        sample_rate: int = 16000,
    ):
        self.engine = engine
        self.window_bytes = window_bytes
        self.on_result = on_result
        self.sample_rate = sample_rate
        self._buffer = bytearray()
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._seen_text: str = ""

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        # flush remainder
        await self._transcribe_buffer(final=True)

    async def enqueue(self, pcm: bytes) -> None:
        async with self._lock:
            self._buffer.extend(pcm)
        if len(self._buffer) >= self.window_bytes:
            await self._transcribe_buffer()

    async def _transcribe_buffer(self, final: bool = False) -> None:
        async with self._lock:
            if not self._buffer:
                return
            data = bytes(self._buffer)
            if not final:
                # keep overlap? simple: consume window
                self._buffer = bytearray()
            else:
                self._buffer = bytearray()
        if len(data) < 3200:  # skip tiny
            return
        try:
            result = await asyncio.to_thread(self.engine.transcribe, data, self.sample_rate)
        except Exception:
            return
        new_text = result.text
        # Only send NEW segments: diff against seen
        if new_text and new_text != self._seen_text:
            # naive new-segment extraction
            if new_text.startswith(self._seen_text):
                delta = new_text[len(self._seen_text):].strip()
            else:
                delta = new_text
            self._seen_text = new_text
            if delta:
                seg_result = TranscriptResult(
                    segments=[__import__("app.transcription_engine", fromlist=["TranscriptSegment"]).TranscriptSegment(text=delta, is_final=final)],
                    language=result.language,
                )
                try:
                    self.on_result(seg_result)
                except Exception:
                    pass

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(0.5)
            async with self._lock:
                enough = len(self._buffer) >= self.window_bytes
            if enough:
                await self._transcribe_buffer()