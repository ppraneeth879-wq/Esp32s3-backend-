"""WAV writer for streaming PCM audio."""

from __future__ import annotations

import struct
import wave
from pathlib import Path
from typing import BinaryIO


class WAVWriter:
    """Wraps a WAV file, appending PCM chunks and fixing up the header on close."""

    def __init__(
        self,
        path: Path,
        sample_rate: int = 16000,
        channels: int = 1,
        bit_depth: int = 16,
    ):
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.channels = channels
        self.bit_depth = bit_depth
        self._file: BinaryIO | None = None
        self._data_bytes = 0
        self._opened = False

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("wb")
        # Write placeholder header (44 bytes); fix up on close.
        self._file.write(b"\x00" * 44)
        self._file.flush()
        self._opened = True

    def write(self, pcm: bytes) -> None:
        if not self._opened or self._file is None:
            raise RuntimeError("WAVWriter not opened")
        self._file.write(pcm)
        self._data_bytes += len(pcm)
        self._file.flush()

    def close(self) -> None:
        if not self._opened or self._file is None:
            return
        self._file.close()
        # Rewrite header with correct sizes.
        self._fix_header()
        self._file = None
        self._opened = False

    def _fix_header(self) -> None:
        byte_rate = self.sample_rate * self.channels * (self.bit_depth // 8)
        block_align = self.channels * (self.bit_depth // 8)
        with self.path.open("r+b") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + self._data_bytes))
            f.write(b"WAVE")
            f.write(b"fmt ")
            f.write(struct.pack("<IHHIIHH", 16, 1, self.channels, self.sample_rate, byte_rate, block_align, self.bit_depth))
            f.write(b"data")
            f.write(struct.pack("<I", self._data_bytes))
            f.flush()

    @property
    def data_bytes(self) -> int:
        return self._data_bytes

    @property
    def is_open(self) -> bool:
        return self._opened