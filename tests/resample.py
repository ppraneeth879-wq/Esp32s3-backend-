"""Resample a WAV file to 16kHz mono 16-bit using the stdlib audioop module."""
import audioop, sys, wave

src, dst = sys.argv[1], sys.argv[2]
w = wave.open(src, "rb")
nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
data = w.readframes(n)
w.close()

print(f"source: channels={nch} sampwidth={sw} rate={sr} frames={n}")

if sw != 2:
    data = audioop.lin2lin(data, sw, 2)
    sw = 2
if nch != 1:
    # Trim to a whole number of frames before downmixing.
    frame_size = sw * nch
    data = data[: (len(data) // frame_size) * frame_size]
    data = audioop.tomono(data, sw, 0.5, 0.5)
if sr != 16000:
    data, _ = audioop.ratecv(data, sw, 1, sr, 16000, None)
    sr = 16000

out = wave.open(dst, "wb")
out.setnchannels(1)
out.setsampwidth(2)
out.setframerate(sr)
out.writeframes(data)
out.close()
print(f"{src} -> {dst}: {sr}Hz mono 16-bit, {len(data)/32000:.2f}s")
