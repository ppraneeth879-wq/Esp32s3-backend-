"""Tests for decoding uploaded audio into the format Whisper wants."""

from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from app.audio_decode import AudioDecodeError, decode_to_pcm, describe


def _wav(channels=1, sampwidth=2, rate=16000, frames=1600) -> bytes:
    """A WAV file with `frames` frames per channel."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(b"\x11\x22" * (frames * channels * sampwidth // 2))
    return buf.getvalue()


def _wav_with_a_list_chunk() -> bytes:
    """A valid WAV whose header is longer than 44 bytes."""
    base = _wav()
    fmt_chunk = base[12:36]        # "fmt " + size + 16 bytes of fmt data
    audio = base[44:]
    list_chunk = b"LIST" + (6).to_bytes(4, "little") + b"INFO\x00\x00"
    # RIFF <size> WAVE <chunks...>
    out = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + fmt_chunk + list_chunk
    out += b"data" + len(audio).to_bytes(4, "little") + audio
    out = out[:4] + (len(out) - 8).to_bytes(4, "little") + out[8:]
    return out


def test_a_plain_wav_passes_through():
    pcm = decode_to_pcm(_wav())
    assert len(pcm) == 1600 * 2
    assert isinstance(pcm, bytes)


def test_a_wav_with_extra_header_chunks_is_decoded():
    """A WAV header is not always 44 bytes.

    Regression test: chopping 44 bytes off a file with a LIST chunk left
    header bytes in the audio and crashed the decoder.
    """
    content = _wav_with_a_list_chunk()
    assert len(content) > 44 + 1600 * 2
    pcm = decode_to_pcm(content)
    assert len(pcm) == 1600 * 2
    assert len(pcm) % 2 == 0


def test_stereo_is_mixed_down_to_mono():
    pcm = decode_to_pcm(_wav(channels=2, frames=1000))
    assert len(pcm) == 1000 * 2


def test_a_different_sample_rate_is_resampled():
    pcm = decode_to_pcm(_wav(rate=22050, frames=22050))
    # One second at 22050 Hz becomes one second at 16 kHz.
    assert abs(len(pcm) / 2 - 16000) < 100


def test_eight_bit_audio_is_accepted():
    pcm = decode_to_pcm(_wav(sampwidth=1, frames=1600))
    assert len(pcm) == 1600 * 2


def test_raw_pcm_is_accepted():
    pcm = decode_to_pcm(b"\x01\x02\x03\x04")
    assert pcm == b"\x01\x02\x03\x04"


def test_raw_pcm_with_an_odd_length_is_trimmed():
    pcm = decode_to_pcm(b"\x01\x02\x03")
    assert pcm == b"\x01\x02"
    assert len(pcm) % 2 == 0


def test_an_empty_file_is_rejected():
    with pytest.raises(AudioDecodeError):
        decode_to_pcm(b"")


def test_a_broken_wav_is_rejected_with_a_readable_message():
    with pytest.raises(AudioDecodeError) as exc:
        decode_to_pcm(b"RIFF" + b"\x00" * 20)
    assert "could not read the WAV file" in str(exc.value)


def test_describe_reports_the_wav_format():
    details = describe(_wav(channels=2, rate=44100))
    assert details["channels"] == 2
    assert details["rate"] == 44100
    assert describe(b"not a wav") is None
