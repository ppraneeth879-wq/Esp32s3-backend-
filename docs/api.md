# LifeLog API Specification

Base URL (LAN): `http://<server-ip>:8000`
Base URL (public): `https://pouch-lark-series.ngrok-free.dev`

All responses are JSON. All list endpoints return a wrapper object with a
`total` count so clients can paginate.

---

## 1. System

| Method | Path | Returns |
|---|---|---|
| GET | `/health` | `{"status":"OK"}` |
| GET | `/status` | live session info (see below) |
| GET | `/api/stats` | headline counters for the home screen |
| GET | `/api/dashboard/summary` | memories / important / tasks counters + category breakdown |
| GET | `/api/dashboard/daily-briefing` | recent memories + open tasks + calendar + important mail |

**`GET /status`**
```json
{
  "connected_clients": 2,
  "connected_devices": 1,
  "recording": true,
  "active_session": "0f9c…",
  "queue_size": 0
}
```

**`GET /api/stats`**
```json
{
  "memories": 128,
  "important": 41,
  "tasks": 23,
  "pending_tasks": 7,
  "by_category": { "task": 52, "memory": 61, "note": 15 }
}
```

---

## 2. Memories (the things worth remembering)

A *memory* is created by the AI when spoken content is judged important.
Raw speech is never stored as a memory unless the model says it is worth keeping.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/memories` | `?limit=20&offset=0&category=task&important=1&q=text` |
| GET | `/api/memories/{id}` | single memory |
| PATCH | `/api/memories/{id}` | correct a memory: `summary`, `category`, `importance`, `is_important`, `sentiment`, `tags`, `topics` |
| DELETE | `/api/memories/{id}` | deletes the memory and its tasks |

**Memory object**
```json
{
  "id": 12,
  "vector_id": "ede14083-38b3-4ce9-b875-d2aaf55e9e5c",
  "transcript": "Ok, so I need to remember to email Sarah back about the invoice tomorrow morning.",
  "summary": "The speaker plans to email Sarah back about the invoice tomorrow morning.",
  "sentiment": "neutral",
  "tags": ["reminder", "tomorrow morning"],
  "topics": ["invoice", "email follow-up"],
  "tasks": ["Email Sarah back about the invoice"],
  "people": ["Sarah"],
  "dates": ["tomorrow morning"],
  "action_items": ["Email Sarah back about the invoice tomorrow morning."],
  "created_at": 1792421520.774209,
  "is_important": true,
  "importance": 0.9,
  "category": "task",
  "reason": "It is an explicit task with a named person and a time-sensitive deadline.",
  "source": "llm",
  "session_id": "4624c177-bc5e-43ce-9101-0c6d0ef17844"
}
```

`category` is one of:

| Category | Meaning |
|---|---|
| `task` | the speaker committed to doing something → a task row is also created |
| `memory` | a durable fact, decision, preference or event worth recalling later |
| `note` | mildly useful context, stored and searchable but not surfaced as important |
| `ignore` | small talk / filler / repetition → **not stored**, only broadcast |

---

## 3. Tasks / Actions

| Method | Path | Notes |
|---|---|---|
| GET | `/api/actions` | `?status=pending&priority=high&limit=50&offset=0` |
| GET | `/api/actions/{id}` | single task |
| POST | `/api/actions` | create manually |
| PATCH | `/api/actions/{id}` | update: `status` (`pending`/`completed`/`dismissed`), `priority`, `title`, `due_date` |
| DELETE | `/api/actions/{id}` | delete a task |

**`GET /api/actions`**
```json
{
  "actions": [
    {
      "id": 1,
      "memory_id": 1,
      "title": "Email Sarah back about the invoice",
      "description": "Email Sarah back about the invoice",
      "action_type": "task",
      "priority": "high",
      "status": "pending",
      "confidence": 0.9,
      "due_date": "tomorrow morning",
      "source": "llm",
      "session_id": "4624c177-…",
      "created_at": 1792421521.2,
      "completed_at": null
    }
  ],
  "total": 23,
  "pending": 7
}
```

**`PATCH /api/actions/1`**
```json
{ "status": "completed" }
```
Response is the updated action. A `action_updated` event is also broadcast on
`/events` so every connected client refreshes.

---

## 4. Search & Chat

| Method | Path | Body / Query |
|---|---|---|
| POST | `/api/search` | `?query=…&threshold=0.3&top_k=10` — semantic search over memories |
| POST | `/api/chat` | `{"question": "who do I need to email?"}` — recall + LLM answer |

**`POST /api/chat`**
```json
{
  "answer": "You need to email Sarah back about the invoice.",
  "sources": [
    {
      "text": "The speaker plans to email Sarah back about the invoice tomorrow morning.\nOk, so I need to …",
      "metadata": {
        "category": "task",
        "important": true,
        "importance": 0.9,
        "sentiment": "neutral",
        "tags": "reminder,tomorrow morning",
        "topics": "invoice,email follow-up",
        "people": "Sarah",
        "memory_id": 1
      },
      "similarity": 0.41
    }
  ],
  "context_used": true
}
```

`POST /api/search` returns `{"results": [...], "query": "...", "count": n}` with the
same `results` shape.

---

## 5. Upload (offline / fallback path)

| Method | Path | Body |
|---|---|---|
| POST | `/api/upload` | multipart form field `file` = a WAV file |

Runs the exact same pipeline as the live path: transcribe → AI judge → store.

```json
{
  "success": true,
  "transcript": "OK, so I need to remember to email Sarah back about the invoice …",
  "extracted": {
    "important": true,
    "importance": 0.9,
    "category": "task",
    "summary": "The speaker plans to email Sarah about an invoice tomorrow morning …",
    "reason": "The speaker states two concrete commitments with timing …",
    "sentiment": "neutral",
    "topics": ["invoice correspondence", "dental appointment"],
    "tags": ["email", "Sarah", "invoice"],
    "people": ["Sarah"],
    "dates": ["tomorrow morning", "next week"],
    "tasks": [{"title": "Email Sarah back about the invoice", "priority": "high", "due_date": "tomorrow morning"}],
    "action_items": [],
    "source": "llm"
  },
  "memory": { "id": 3, "...": "..." },
  "actions": [ { "id": 3, "title": "Email Sarah back about the invoice", "priority": "high" } ],
  "ignored": false
}
```

---

## 6. Google Calendar / Gmail

| Method | Path | Notes |
|---|---|---|
| GET | `/auth/google?scope=calendar,gmail&mobile=0` | starts OAuth, redirects to Google |
| GET | `/auth/google/callback?code=…&state=…` | OAuth redirect target |
| GET | `/auth/google/status` | `{"connected": true, "email": "…", "scopes": [...]}` |
| POST | `/auth/google/disconnect` | revokes stored credentials |
| GET | `/api/google/calendars` | list calendars |
| GET | `/api/google/events?calendar_id=primary&time_min=…&time_max=…` | list events (ISO 8601) |
| POST | `/api/google/events` | `{"summary","start","end","description","location","calendar_id"}` |
| DELETE | `/api/google/events/{event_id}?calendar_id=primary` | delete an event |
| GET | `/api/google/freebusy?time_min=…&time_max=…` | free/busy blocks |
| GET | `/api/google/messages?query=…&max_results=10` | list mail |
| GET | `/api/google/messages/important?max_results=10` | important + unread |
| GET | `/api/google/messages/{message_id}` | full message |
| GET | `/api/google/profile` | Gmail profile |
| POST | `/api/google/index-emails?limit=20` | index mail into the memory store |

OAuth redirect URI to register in Google Cloud Console:
`https://pouch-lark-series.ngrok-free.dev/auth/google/callback`

---

## 7. WebSocket `/events` (clients)

Connect to `wss://pouch-lark-series.ngrok-free.dev/events`.
Every message is a JSON object with a `type` field.
Schema: `protocol/schemas/events.json`.

| `type` | When | Key fields |
|---|---|---|
| `device_connected` | ESP32 opened `/audio` | `device_id`, `device_name`, `firmware_version` |
| `recording_started` | session opened | `session_id`, `started_at_ms` |
| `transcript` | every ~5s of speech | `text`, `sequence`, `timestamp_ms`, `is_final` |
| `insight` | the AI judged a chunk of speech | `ignored`, `important`, `importance`, `category`, `summary`, `memory`, `actions` |
| `action_updated` | a task was completed/edited | `action` |
| `recording_finished` | session closed | `duration_ms` |
| `error` | server-side failure | `code`, `message` |
| `status` | periodic telemetry | `connected_clients`, `recording`, `queue_size` |

### The `insight` event — the important one

```json
{
  "type": "insight",
  "session_id": "4624c177-bc5e-43ce-9101-0c6d0ef17844",
  "device_id": "00000000-0000-0000-0000-000000000001",
  "reason": "size",
  "ignored": false,
  "important": true,
  "importance": 0.9,
  "category": "task",
  "summary": "The speaker plans to email Sarah back about the invoice tomorrow morning.",
  "detail": "It is an explicit task with a named person and a time-sensitive deadline.",
  "memory": {
    "id": 1,
    "vector_id": "ede14083-38b3-4ce9-b875-d2aaf55e9e5c",
    "summary": "The speaker plans to email Sarah back about the invoice tomorrow morning.",
    "transcript": "Ok, so I need to remember to email Sarah back about the invoice tomorrow morning.",
    "category": "task",
    "important": true,
    "importance": 0.9,
    "sentiment": "neutral",
    "tags": ["reminder", "tomorrow morning"],
    "topics": ["invoice", "email follow-up"],
    "people": ["Sarah"],
    "dates": ["tomorrow morning"],
    "reason": "It is an explicit task with a named person and a time-sensitive deadline.",
    "created_at": 1792421520.774209
  },
  "actions": [
    {
      "id": 1,
      "title": "Email Sarah back about the invoice",
      "description": "Email Sarah back about the invoice",
      "action_type": "task",
      "priority": "high",
      "status": "pending",
      "confidence": 0.9,
      "due_date": "tomorrow morning",
      "memory_id": 1
    }
  ]
}
```

When the AI decides the speech is not worth keeping, the event still fires but
`ignored` is `true`, `memory` is `null` and `actions` is `[]`. Use this to show
a subtle "not saved" indicator rather than an error.

---

## 8. Device WebSocket `/audio`

The ESP32 connects here (LAN only, not through ngrok).
It sends binary LifeLog packets (`protocol/protocol.md`) containing 16-bit PCM
mono @ 16 kHz. The server transcribes, analyses and stores automatically.
No client action is required — the device is fully autonomous.
