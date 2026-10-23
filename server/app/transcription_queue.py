"""Transcription queue: accumulates PCM into windows and dispatches to engine."""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from .transcription_engine import TranscriptionEngine, TranscriptResult, TranscriptSegment


class TranscriptionQueue:
    def __init__(
        self,
        engine: TranscriptionEngine,
        window_bytes: int,
        on_result: Callable[[TranscriptResult], None],
        sample_rate: int = 16000,
        channels: int = 1,
        bit_depth: int = 16,
    ):
        self.engine = engine
        self.window_bytes = window_bytes
        self.on_result = on_result
        self.sample_rate = sample_rate
        self.channels = channels
        self.bit_depth = bit_depth
        self._bytes_per_second = max(
            1, sample_rate * channels * (bit_depth // 8)
        )
        self._buffer = bytearray()
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._seen_text: str = ""
        # Audio-time bookkeeping: how far into the stream we are, so every
        # segment can be stamped with its real start/end offset.
        self._audio_offset = 0.0
        self._window_start = 0.0

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

    def _absolute_segments(self, result: TranscriptResult) -> list[TranscriptSegment]:
        """Re-base the engine's window-relative times onto the stream timeline."""
        out: list[TranscriptSegment] = []
        for seg in result.segments:
            out.append(
                TranscriptSegment(
                    text=seg.text,
                    start=self._window_start + seg.start,
                    end=self._window_start + seg.end,
                    is_final=seg.is_final,
                )
            )
        return out

    @staticmethod
    def _range_for_delta(
        segments: list[TranscriptSegment], delta: str
    ) -> tuple[float, float]:
        """The time range covered by the newly seen text."""
        if not segments:
            return 0.0, 0.0
        joined = " ".join(s.text for s in segments).strip()
        delta_start = max(0, len(joined) - len(delta))
        start = segments[0].start
        end = segments[-1].end
        cursor = 0
        for seg in segments:
            seg_len = len(seg.text)
            if cursor <= delta_start < cursor + seg_len:
                start = seg.start
                break
            cursor += seg_len + 1
        return start, end

    async def _transcribe_buffer(self, final: bool = False) -> None:
        async with self._lock:
            if not self._buffer:
                return
            data = bytes(self._buffer)
            self._buffer = bytearray()
            window_start = self._window_start
            self._audio_offset += len(data) / self._bytes_per_second
            self._window_start = self._audio_offset
        if len(data) < 3200:  # skip tiny
            return
        try:
            result = await asyncio.to_thread(self.engine.transcribe, data, self.sample_rate)
        except Exception:
            return
        new_text = result.text
        # Only send NEW segments: diff against seen
        if new_text and new_text != self._seen_text:
            if new_text.startswith(self._seen_text):
                delta = new_text[len(self._seen_text):].strip()
            else:
                delta = new_text
            self._seen_text = new_text
            if delta:
                absolute = self._absolute_segments(result)
                start, end = self._range_for_delta(absolute, delta)
                seg_result = TranscriptResult(
                    segments=[
                        TranscriptSegment(text=delta, start=start, end=end, is_final=final)
                    ],
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
