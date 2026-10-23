"""Decode uploaded audio into 16-bit mono PCM at 16 kHz.

The Whisper engine wants exactly that format. A WAV file cannot simply have
its first 44 bytes chopped off: the header size depends on which optional
chunks (LIST, INFO, ...) the encoder wrote, so slicing 44 bytes can leave
header bytes in the audio and crash the decoder.
"""

from __future__ import annotations

import io
import wave
from typing import Optional

import numpy as np

TARGET_RATE = 16000
TARGET_CHANNELS = 1
TARGET_WIDTH = 2  # bytes per sample


class AudioDecodeError(ValueError):
    """The uploaded audio could not be decoded."""


def _to_int16(frames: bytes, sampwidth: int) -> np.ndarray:
    """Any PCM width to int16."""
    if sampwidth == 2:
        return np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if sampwidth == 1:
        # 8-bit WAV is unsigned.
        return (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) * 256.0
    if sampwidth == 4:
        return np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 65536.0
    if sampwidth == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        value = (raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16))
        value = np.where(value & 0x800000, value - 0x1000000, value)
        return value.astype(np.float32) / 256.0
    raise AudioDecodeError(f"unsupported sample width: {sampwidth} bytes")


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Linear resample. Good enough for speech recognition."""
    if source_rate == target_rate or samples.size == 0:
        return samples
    duration = samples.size / float(source_rate)
    target_length = max(1, int(round(duration * target_rate)))
    source_positions = np.linspace(0.0, samples.size - 1, num=samples.size)
    target_positions = np.linspace(0.0, samples.size - 1, num=target_length)
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


def decode_to_pcm(content: bytes, target_rate: int = TARGET_RATE) -> bytes:
    """Turn an uploaded file into 16-bit mono PCM at ``target_rate``.

    Accepts a WAV file or raw 16-bit PCM. Raises :class:`AudioDecodeError`
    with a readable message for anything else.
    """
    if not content:
        raise AudioDecodeError("the uploaded file is empty")

    if content[:4] != b"RIFF":
        # Raw PCM: assume 16-bit mono at the target rate.
        trimmed = content[: len(content) - (len(content) % 2)]
        if not trimmed:
            raise AudioDecodeError("the uploaded file is not valid audio")
        return trimmed

    try:
        with wave.open(io.BytesIO(content), "rb") as w:
            channels = w.getnchannels()
            sampwidth = w.getsampwidth()
            rate = w.getframerate()
            frames = w.readframes(w.getnframes())
    except wave.Error as e:
        raise AudioDecodeError(f"could not read the WAV file: {e}") from e

    if not frames:
        raise AudioDecodeError("the WAV file has no audio data")

    samples = _to_int16(frames, sampwidth)

    if channels > 1:
        usable = (samples.size // channels) * channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)

    samples = _resample(samples, rate, target_rate)

    clipped = np.clip(samples, -32768.0, 32767.0)
    return clipped.astype(np.int16).tobytes()


def describe(content: bytes) -> Optional[dict]:
    """Details of a WAV file, for logging."""
    if content[:4] != b"RIFF":
        return None
    try:
        with wave.open(io.BytesIO(content), "rb") as w:
            return {
                "channels": w.getnchannels(),
                "sample_width": w.getsampwidth(),
                "rate": w.getframerate(),
                "frames": w.getnframes(),
            }
    except wave.Error:
        return None
