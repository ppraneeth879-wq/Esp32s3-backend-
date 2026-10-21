"""Simulate an ESP32 streaming audio over the WebSocket and verify the
insight pipeline fires and reaches /events clients.
"""
import asyncio, json, struct, sys, wave, os, time
import websockets

WAV = sys.argv[1] if len(sys.argv) > 1 else "/tmp/speech_test.wav"
SERVER = "ws://127.0.0.1:8000"
DEVICE_ID = "00000000-0000-0000-0000-000000000001"


def build_packet(ptype, seq, payload=b"", device_hex="00000000000000000000000000000001"):
    header = bytearray(40)
    header[0:4] = b"LLV1"
    header[4] = 1              # protocol version
    header[5] = ptype          # packet type
    header[6:8] = b"\x00\x00"  # reserved
    header[8:24] = bytes.fromhex(device_hex)
    header[24:28] = struct.pack("<I", seq)
    header[28:36] = struct.pack("<Q", int(time.time() * 1000))
    header[36:40] = struct.pack("<I", len(payload))
    return bytes(header) + payload


async def main():
    # 1. Listen on /events
    events = []
    async with websockets.connect(f"{SERVER}/events") as evt:
        print("connected to /events")

        async def listen():
            try:
                async for msg in evt:
                    events.append(json.loads(msg))
                    t = events[-1].get("type")
                    if t == "transcript":
                        print(f"  [event] transcript: {events[-1].get('text','')[:100]!r}")
                    elif t == "insight":
                        m = events[-1]
                        print(f"  [event] INSIGHT category={m.get('category')} "
                              f"importance={m.get('importance')} ignored={m.get('ignored')}")
                        print(f"           summary: {str(m.get('summary'))[:100]}")
                        mem = m.get("memory")
                        if mem:
                            print(f"           memory id={mem['id']} people={mem.get('people')} dates={mem.get('dates')}")
                        for a in m.get("actions") or []:
                            print(f"           TASK [{a['priority']}] {a['title']} due={a.get('due_date')}")
                    else:
                        print(f"  [event] {t}")
            except Exception:
                pass

        listener = asyncio.create_task(listen())

        # 2. Load the wav and stream it as 16-bit mono 16kHz PCM
        w = wave.open(WAV, "rb")
        if w.getframerate() != 16000:
            print(f"WARNING: wav is {w.getframerate()}Hz")
        pcm = w.readframes(w.getnframes())
        w.close()
        print(f"streaming {len(pcm)} bytes of PCM ({len(pcm)/32000:.1f}s)")

        # 3. Connect as the device and stream in 2048-byte chunks (like the firmware)
        async with websockets.connect(f"{SERVER}/audio") as audio:
            seq = 0
            CHUNK = 2048
            for i in range(0, len(pcm), CHUNK):
                chunk = pcm[i : i + CHUNK]
                await audio.send(build_packet(0x01, seq, chunk))
                seq += 1
                await asyncio.sleep(0.02)  # roughly realtime
            # send a heartbeat
            await audio.send(build_packet(0x02, seq))
            print("stream complete, waiting for pipeline...")

        # 4. Wait for the insight
        for _ in range(40):
            await asyncio.sleep(1)
            if any(e.get("type") == "insight" for e in events):
                break

        listener.cancel()

    print(f"\ntotal events received: {len(events)}")
    print(f"event types: {[e.get('type') for e in events]}")


asyncio.run(main())
