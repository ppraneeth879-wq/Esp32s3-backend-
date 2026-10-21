"""Recorder: accumulates PCM and drives WAVWriter."""

from __future__ import annotations

from .wav_writer import WAVWriter


class Recorder:
    def __init__(self, wav_writer: WAVWriter):
        self.wav_writer = wav_writer
        self.bytes_written = 0

    def start(self) -> None:
        self.wav_writer.open()

    def write(self, pcm: bytes) -> None:
        self.wav_writer.write(pcm)
        self.bytes_written += len(pcm)

    def stop(self) -> None:
        self.wav_writer.close()