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

## 2. Memories (the timeline of your life)

A *memory* is everything worth seeing in one list:

| `category` | What it is | `source` |
|---|---|---|
| `memory` | something worth remembering that was spoken | `llm` |
| `task` | something worth remembering that was spoken | `llm` |
| `note` | mildly useful context that was spoken | `llm` |
| `event` | a **calendar event** - what is coming up | `google_calendar` |
| `email` | an **important** email - what needs attention | `gmail` |

Raw speech is never stored unless the model says it is worth keeping. The
calendar and important mail are pulled in by a background sync (every 15
minutes by default) so the timeline is complete without the client doing
anything.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/memories` | `?limit=20&offset=0&category=task&important=1&source=gmail&q=text&upcoming=1&days=14&order=new` |
| GET | `/api/memories/{id}` | single memory |
| PATCH | `/api/memories/{id}` | correct a memory: `summary`, `category`, `importance`, `is_important`, `sentiment`, `tags`, `topics` |
| DELETE | `/api/memories/{id}` | deletes the memory and its tasks |
| GET | `/api/upcoming` | `?days=14&limit=50` - calendar events ordered by start time |

**Filters**

| Parameter | Effect |
|---|---|
| `category` | `memory` \| `task` \| `note` \| `event` \| `email` |
| `important` | `1` for important items only |
| `source` | `llm` \| `google_calendar` \| `gmail` |
| `upcoming` | `1` to return only events still running in the next `days` |
| `q` | free-text filter over the summary and transcript |

**Ordering**

| `order` | Effect |
|---|---|
| `new` (default) | newest added first |
| `recent` | newest by actual time - when the mail arrived, when the event is, or when the memory was created |
| `oldest` | the reverse of `recent` |

Use `order=recent` for the mail list so the newest email is at the top.

**`GET /api/upcoming`** - "what is on today and over the next few days":
```json
{
  "events": [
    { "id": 42, "summary": "Team standup", "category": "event",
      "source": "google_calendar", "starts_at": 1792755600.0,
      "ends_at": 1792759200.0, "location": "Room 3", "status": "confirmed",
      "link": "https://calendar.google.com/event?eid=…",
      "people": ["sarah@example.com"], "dates": ["2026-10-23T10:00:00+0530"] }
  ],
  "count": 1,
  "days": 14,
  "next": { "…": "the soonest event" }
}
```

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
  "session_id": "4624c177-bc5e-43ce-9101-0c6d0ef17844",
  "external_id": "",
  "starts_at": null,
  "ends_at": null,
  "location": "",
  "status": "",
  "link": ""
}
```

For a calendar event `category` is `event` and `starts_at`/`ends_at` hold the
epoch times, with `location`, `status` and `link` filled in. For an email
`category` is `email`, `starts_at` is when it arrived, and `link` opens it in
Gmail. `external_id` is the Google id, so re-syncing updates an entry in
place instead of creating a duplicate.

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

## 6. Rolling-window summaries

Every hour the server takes all transcript segments recorded since the last
pass, sends them to an LLM, and stores a summary as a JSON document on disk.
The transcript files are **kept** — summarizing never deletes anything.

All summary endpoints are plain HTTP. **Do not use the WebSocket for
summaries** — `/events` carries live transcript events only.

| Method | Path | Purpose |
|---|---|---|
| GET | `/summaries` | All stored summaries, oldest first |
| GET | `/summaries/latest` | The single most recent summary |
| GET | `/summaries/status` | Summarizer health and remaining daily budget |
| GET | `/summaries/{key}` | One window by key, e.g. `/summaries/2026-10-22T0139` |
| POST | `/summaries/run` | Trigger a pass immediately instead of waiting for the timer |

**Route order matters.** `/summaries/latest` and `/summaries/status` are
registered *before* `/summaries/{key}`. If they are reordered, FastAPI matches
`/summaries/status` as `key="status"` and returns `{"summary": null}`.

**`GET /summaries/latest`** — the endpoint the app should poll.
```json
{
  "summary": {
    "key": "2026-10-22T1329",
    "summary": "- Emailed Sarah about the invoice\n- Called the dentist to reschedule",
    "segment_count": 42,
    "word_count": 1830,
    "generated_at": "2026-10-22T13:29:17+0530",
    "engine": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "fallback_used": false,
    "sessions": ["9b9ed6e3-…", "1a8e4c02-…"],
    "window_start": "2026-10-22T12:29:17+0530",
    "window_end": "2026-10-22T13:29:17+0530"
  }
}
```
When no summary exists yet it returns `{"summary": null}` — **not** a 404, so
the app can poll without special-casing errors.

`engine` always names the engine that **actually** produced the text. If the
API was unavailable it is `"extractive"` and `fallback_used` is `true`. A
summary claiming an LLM ran when it did not would hide a broken configuration.

The `summary` field contains literal `\n` separated bullets — split on `\n`
and strip the leading `- `.

**`GET /summaries/status`**
```json
{
  "running": true, "runs": 12, "last_run_at": 1792657757.0, "last_error": "",
  "window_seconds": 3600, "interval_seconds": 3600, "retention_days": 30,
  "watermark": 1792657757.0, "journal_records": 42,
  "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
  "router_fallback": "openrouter/free",
  "api_configured": true,
  "daily_budget": 50, "requests_today": 3, "budget_remaining": 47,
  "max_input_chars": 100000,
  "webhook_configured": false
}
```

**`POST /summaries/run`** → `{"summary": {...}, "ran": true}` or
`{"summary": null, "ran": false}` when the window was empty.

> This calls the LLM for the next window, so it is an expensive operation.
> Do not call it on a timer from a phone.

### On-disk layout
```
server/uploads/
├── audio/<date>/<session_uuid>/
│   ├── recording.wav        # original audio
│   ├── transcript.txt       # full session transcript
│   └── metadata.json
├── journal.jsonl            # append-only, one JSON object per segment
└── summaries/
    ├── _state.json          # the rolling-window watermark (internal)
    ├── 2026-10-22T1329.json # one file per summarized window
    └── 2026-10-22T1429.json
```

Window keys are **local time**, formatted `YYYY-MM-DDTHHMM`. No `:` so the
keys are valid filenames on every platform, and they sort lexicographically.

---

## 7. Google Calendar / Gmail

| Method | Path | Notes |
|---|---|---|
| GET | `/auth/google?scope=calendar,gmail&mobile=1` | starts OAuth, redirects to Google |
| GET | `/auth/google/callback?code=…&state=…` | OAuth redirect target |
| GET | `/auth/google/status` | `{"connected": true, "email": "…", "scopes": [...], "target_email": "…"}` |
| POST | `/auth/google/disconnect` | revokes stored credentials |
| GET | `/api/google/calendars` | list calendars |
| GET | `/api/google/events?calendar_id=primary&time_min=…&time_max=…` | list events (ISO 8601) |
| POST | `/api/google/events` | `{"summary","start","end","description","location","calendar_id"}` |
| DELETE | `/api/google/events/{event_id}?calendar_id=primary` | delete an event |
| GET | `/api/google/freebusy?time_min=…&time_max=…` | free/busy blocks |
| GET | `/api/google/messages?query=…&max_results=10` | **important mail by default** |
| GET | `/api/google/messages/important?max_results=10` | important + unread |
| GET | `/api/google/messages/recent?max_results=10` | recent mail, important or not |
| GET | `/api/google/messages/{message_id}` | full message |
| GET | `/api/google/profile` | Gmail profile |
| POST | `/api/google/sync?force=1` | pull calendar + important mail into the timeline |
| GET | `/api/google/sync/status` | when the last sync ran |

### Mail: the latest

`GET /api/google/messages` shows the **latest mail**, newest first. The
timeline shows the latest mail too - important mail is not singled out.

```
GET /api/google/messages                      # latest mail
GET /api/google/messages/recent               # the same, as a named route
GET /api/google/messages?query=is:important   # narrow it down
GET /api/google/messages/important            # what Gmail flags as important
```

An email in the timeline is flagged `is_important` only when **Gmail itself**
flags it, so the flag is a truthful signal rather than a filter on what is
worth seeing.

A message with no subject and no body is not stored at all. An event with no
title and no description is not stored at all. An event that is deleted in
Google is removed from the timeline on the next sync.

### Time format

The Calendar API only accepts RFC 3339 (`2026-10-22T00:00:00+05:30`). The
server repairs the common near-misses - a missing `:SS`, a missing offset, or
a bare date - and drops a bound it cannot understand. A Google error is
returned as a clean error response with Google's own message, never a 500.

OAuth redirect URI to register in Google Cloud Console:
`https://pouch-lark-series.ngrok-free.dev/auth/google/callback`

### The account chooser

`GET /auth/google` shows the Google **"choose an account"** page by default,
so the user picks which Gmail to use.

| Parameter | Effect |
|---|---|
| `choose_account=1` | **default** — show the account chooser |
| `choose_account=0` | skip it, use the already signed-in account |
| `email=you@gmail.com` | pre-select that account in the chooser |

This is `prompt=consent select_account`. With only `prompt=consent`, Google
skips the chooser whenever the browser is already signed in and goes straight
to the consent screen for whichever account is active — with no way back.
`GOOGLE_ACCOUNT_EMAIL` in `server/.env` is also honoured as a `login_hint`,
but only when explicitly set. The account that is *already connected* is
never used as a hint, for the same reason.

Only one account is connected at a time; connecting another replaces it.

---

## 8. WebSocket `/events` (clients)

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

## 9. Device WebSocket `/audio`

The ESP32 connects here (LAN only, not through ngrok).
It sends binary LifeLog packets (`protocol/protocol.md`) containing 16-bit PCM
mono @ 16 kHz. The server transcribes, analyses and stores automatically.
No client action is required — the device is fully autonomous.
