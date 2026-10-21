This is exactly how I would write it for a coding agent. It's written as a real engineering PRD rather than a prompt.

---

# Product Requirements Document (PRD)

## Project Name

**LifeLog Backend v1.0**

---

# 1. Objective

Develop the complete backend system for a real-time lecture transcription platform.

The system consists of:

- ESP32-S3 Sense firmware
- FastAPI backend running locally on a laptop
- WebSocket broadcasting layer

The Android application and web frontend already exist and are **not** part of this implementation.

The backend must receive raw audio from the ESP32 over Wi-Fi, save recordings, perform speech transcription, and broadcast transcript updates over WebSockets.

---

# 2. System Architecture

```
                     WiFi

                      │

                      ▼

             ESP32-S3 Sense
          (Digital Microphone)

                      │

          Binary PCM Stream

                      │

                      ▼

        FastAPI Server (Laptop)

 ┌─────────────────────────────────────┐

 │ Audio Receiver                      │

 │ Audio Session Manager               │

 │ WAV Builder                         │

 │ Faster-Whisper                      │

 │ Transcript Manager                  │

 │ WebSocket Broadcast Server          │

 │ Local Storage                       │

 └─────────────────────────────────────┘

              │               │

              ▼               ▼

      Transcript Store   WebSocket Clients
```

---

# 3. Scope

Implement:

✅ ESP32 firmware

✅ FastAPI backend

✅ Audio pipeline

✅ WAV generation

✅ Faster-Whisper transcription

✅ WebSocket broadcast

Do NOT implement

- Android UI
- Web UI
- Authentication
- Cloud deployment
- User accounts
- Database persistence beyond local storage (SQLite is optional for metadata)

---

# 4. Functional Requirements

---

## ESP32 Responsibilities

The firmware shall

Initialize microphone.

Connect to Wi-Fi.

Reconnect automatically.

Capture microphone continuously.

Capture audio as

16 kHz

16-bit

Mono PCM

Stream binary audio continuously.

Never perform transcription.

Never perform MP3 encoding.

Never block microphone capture while networking.

Support reconnect after server restart.

---

## Laptop Responsibilities

Receive audio.

Create recording sessions.

Store audio.

Convert PCM to WAV.

Perform transcription.

Maintain transcript.

Broadcast transcript.

Save final transcript.

---

# 5. Audio Configuration

Microphone

PDM

Sample Rate

16000 Hz

Channels

1

Bit Depth

16-bit

Encoding

Signed PCM

Chunk Size

2048 bytes

Chunk Duration

64 ms

---

# 6. Firmware Architecture

```
Firmware

│

├── main.cpp

├── Config

├── WifiManager

├── MicManager

├── RingBuffer

├── AudioStreamer

└── Logger
```

---

# 7. Firmware Tasks

## Task 1

Microphone

Priority High

Responsibilities

Read microphone continuously.

Write PCM into ring buffer.

Never perform networking.

---

## Task 2

Network

Priority Medium

Responsibilities

Read ring buffer.

Send packets.

Reconnect automatically.

---

## Task 3

Heartbeat

Priority Low

Every

5 seconds

Send

```
Device alive

Uptime

RSSI

Free heap
```

---

# 8. Transport Protocol

Use **WebSocket**.

Reason

Persistent connection.

Low latency.

Binary frames.

No repeated HTTP overhead.

Endpoint

```
ws://SERVER_IP:8000/audio
```

Binary only.

Never Base64.

---

# 9. Packet Format

Each WebSocket binary frame

```
uint32 sequence

uint64 timestamp

uint16 payload_length

PCM payload
```

Little endian.

---

# 10. FastAPI Backend

Project Structure

```
server/

app/

    main.py

    config.py

    websocket.py

    receiver.py

    recorder.py

    whisper_engine.py

    session_manager.py

    broadcaster.py

    storage.py

    models.py

uploads/

audio/

transcripts/

logs/
```

---

# 11. FastAPI Endpoints

## WebSocket

```
/audio
```

ESP32 connects.

Uploads binary PCM.

---

## WebSocket

```
/events
```

Android/Web connects.

Receives transcript updates.

---

## REST

```
GET /health
```

Returns

```
OK
```

---

## REST

```
GET /status
```

Returns

Current recording

Connected clients

Connected ESP32

Recording duration

Queue size

CPU usage

---

# 12. Audio Session

When ESP32 connects

Create

```
Session

↓

session_id

↓

recording.wav

↓

transcript.txt
```

If ESP32 disconnects

Close session.

Flush remaining buffers.

Save transcript.

---

# 13. Audio Storage

Directory

```
uploads/audio/

YYYY-MM-DD/

session_uuid.wav
```

---

# 14. WAV Builder

Incoming PCM

↓

Append

↓

WAV Header

↓

Update Header Length

↓

Close correctly

The resulting WAV file must be playable in VLC/Audacity.

---

# 15. Transcription

Use

**faster-whisper**

Small model initially.

Configurable.

Transcribe incrementally.

Do NOT wait until recording finishes.

---

# 16. Transcript Manager

Maintain

Current transcript

Current timestamp

Segments

Each transcription result

↓

Append

↓

Broadcast

↓

Store

---

# 17. WebSocket Broadcast

Whenever new transcript text arrives

Broadcast JSON

```
{
    "session":"abc",

    "timestamp":123.42,

    "text":"The professor is explaining binary trees."
}
```

Broadcast immediately.

No polling.

---

# 18. Local Storage

Save

```
recording.wav

transcript.txt

metadata.json
```

metadata.json

```
Session ID

Start Time

End Time

Duration

Average Latency

Packet Loss

Words

Characters
```

---

# 19. Logging

Console

File

Examples

```
ESP32 Connected

Recording Started

Packet Received

Transcribed 2.3 seconds

Broadcast Sent

Recording Finished
```

---

# 20. Error Handling

Recover automatically from

WiFi loss

WebSocket disconnect

Whisper failure

Invalid packet

Corrupt frame

Partial packet

Client disconnect

Server restart

Never crash.

---

# 21. Performance Targets

Audio latency

<200 ms

WebSocket latency

<50 ms

Transcript delay

<2 s

RAM

Efficient

CPU

Reasonable

---

# 22. Coding Standards

Python

3.12

FastAPI

AsyncIO

Type hints

Pydantic

No global mutable state

Dependency injection where appropriate

Logging module

Proper exception hierarchy

---

# 23. Future Compatibility

Architecture must allow later addition of

- SD card recording on ESP32
- OTA firmware updates
- Multiple ESP32 devices
- Multiple simultaneous lecture sessions
- PostgreSQL instead of local metadata
- Speaker diarization
- LLM summarization
- Cloud deployment
- Authentication
- End-to-end encryption
- HTTPS/WSS
- Mobile push notifications

without major architectural changes.

---

# 24. Deliverables

### Firmware

```
firmware/

main.cpp

Config.h

MicManager.*

WifiManager.*

AudioStreamer.*

RingBuffer.*

Logger.*
```

---

### Backend

```
server/

app/

main.py

receiver.py

recorder.py

whisper_engine.py

websocket.py

session_manager.py

storage.py

config.py

models.py

requirements.txt

README.md
```

---

# 25. Definition of Done

The implementation is complete when:

- ESP32 boots and automatically connects to Wi-Fi.
- ESP32 establishes a WebSocket connection to the FastAPI server.
- PCM audio streams continuously from the ESP32.
- The server reconstructs a valid WAV recording.
- Faster-Whisper transcribes the audio incrementally.
- New transcript segments are broadcast immediately over a WebSocket endpoint.
- Audio (`recording.wav`), transcript (`transcript.txt`), and session metadata are saved locally.
- The system recovers gracefully from temporary network interruptions without requiring a restart.

---

## One additional recommendation

For the WebSocket protocol, define a simple message envelope now (including protocol version, device ID, and message type) even if you only have one ESP32 today. That small upfront design choice will make it much easier to support multiple devices, firmware updates, and future features without breaking compatibility.