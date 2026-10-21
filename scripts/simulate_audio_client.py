"""Simulated audio client for backend validation."""

import asyncio
import struct
import uuid
import websockets

DEVICE_ID = uuid.uuid4()
SERVER = "ws://127.0.0.1:8000/audio"


def build_packet(seq, ts, payload):
    header = struct.pack("<4sBBH16sIQI", b"LLV1", 1, 0x01, 0, DEVICE_ID.bytes, seq, ts, len(payload))
    return header + payload


async def main():
    async with websockets.connect(SERVER) as ws:
        pcm = b"\x00\x00" * 1024
        for i in range(5):
            pkt = build_packet(i, i * 64, pcm)
            await ws.send(pkt)
            await asyncio.sleep(0.064)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())