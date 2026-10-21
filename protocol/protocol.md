# LifeLog Binary Protocol

## Overview

LifeLog uses a versioned binary protocol over WebSocket. Every frame sent from an
ESP32 device to the FastAPI server (and any control frame in the reverse
direction) follows this envelope. The protocol is the single source of truth for
both firmware and backend.

## Transport

- WebSocket binary frames only. Never Base64, never JSON for audio.
- Endpoint (ESP32 -> server): `ws://<host>:<port>/audio`
- Endpoint (clients): `ws://<host>:<port>/events`

## Envelope (little-endian)

| Field            | Size  | Type     | Description                                  |
|------------------|-------|----------|----------------------------------------------|
| Magic Number     | 4     | bytes    | ASCII `LLV1`                                 |
| Protocol Version | 1     | uint8    | Currently `1`                                |
| Packet Type      | 1     | uint8    | See packet types                             |
| Reserved         | 2     | uint16   | Must be `0`; reserved for future use         |
| Device ID        | 16    | UUID     | Device UUID (binary, little-endian fields)   |
| Sequence Number  | 4     | uint32   | Monotonic per device, wraps not expected      |
| Timestamp        | 8     | uint64   | Milliseconds since device boot (or epoch)     |
| Payload Length   | 4     | uint32   | Length of payload in bytes                   |
| Payload          | N     | bytes    | Packet-type specific                         |

Total header size: 40 bytes.

## Magic Number

`LLV1` (0x4C 0x4C 0x56 0x31). The server MUST reject any frame whose magic
does not match, logging a warning and closing the connection on repeated
violations.

## Protocol Version

Starts at `1`. Backward-incompatible changes bump the major version. The server
may accept a range of versions; unknown versions are rejected.

## Packet Types

| Value | Name         | Direction        | Payload                                    |
|-------|--------------|------------------|--------------------------------------------|
| 0x01  | AUDIO        | device -> server | Raw PCM (16-bit, mono, 16 kHz, LE)         |
| 0x02  | HEARTBEAT    | device -> server | Telemetry JSON (see schemas/heartbeat.json) |
| 0x03  | DEVICE_INFO  | device -> server | Device metadata JSON                       |
| 0x04  | START_SESSION| either           | Optional session hint JSON                 |
| 0x05  | END_SESSION  | either           | Reason string (optional)                   |
| 0x06  | ERROR        | either           | Error code + message                       |

## Audio Format (Recording Format)

- Sample rate: 16000 Hz
- Channels: 1 (mono)
- Bit depth: 16-bit signed PCM
- Endianness: little-endian
- Container: WAV (PCM), written by server only
- Never MP3 on the device. Convert on server if needed.

## Latency Targets

- ESP32 -> server: < 100 ms
- Server -> Whisper: < 2 s
- Broadcast: < 50 ms
- End-to-end: < 3 s

## Versioning

All changes to this document must be reflected in both firmware (`PacketBuilder`)
and backend (`app/protocol.py`). Tests in both sides validate round-trip
serialization.