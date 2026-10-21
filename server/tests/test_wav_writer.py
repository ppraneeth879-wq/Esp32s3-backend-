import sys
import wave
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.wav_writer import WAVWriter


def test_wav_writer(tmp_path):
    p = tmp_path / "t.wav"
    w = WAVWriter(p, sample_rate=16000, channels=1, bit_depth=16)
    w.open()
    pcm = b"\x00\x00" * 100
    w.write(pcm)
    w.close()
    assert p.exists()
    with wave.open(str(p), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getnframes() == 100