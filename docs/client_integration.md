# Client Integration Guide (Android + Web)

Everything below is from the **server's point of view** — what the server
exposes, what it emits, and what a client must do.

Base URL (public, use this from a phone or a deployed website):

```
https://pouch-lark-series.ngrok-free.dev
```

- REST: `https://pouch-lark-series.ngrok-free.dev/api/...`
- Events (WebSocket): `wss://pouch-lark-series.ngrok-free.dev/events`
- Device audio (WebSocket): `ws://<esp32-lan-ip>:8000/audio` — the ESP32 only
  talks to the LAN, it does **not** go through ngrok.

Full endpoint reference: [`api.md`](api.md).
Event JSON schemas: `protocol/schemas/events.json`.

---

## 1. The data model the client must present

The server gives you **one timeline** (`/api/memories`) that mixes
everything, plus a couple of specialised views. Your app should tell them
apart by `category` and `source`:

| Server object | Where it comes from | How the app should treat it |
|---|---|---|
| `transcript` event | Whisper, every ~5 s | **Live caption.** Show as it streams. Ephemeral. Do not persist. |
| `insight` event → `memory` | AI judged it worth remembering | **Journal / memory.** Persist locally, show in a timeline, searchable. |
| `insight` event → `actions[]` | AI found something to do | **Task.** Persist locally, show in a to-do list, allow complete/dismiss. |
| `category: "event"` | **Google Calendar** | **Coming up.** Show the title and the start time. |
| `category: "email"` | **important Gmail** | **Needs attention.** Show the subject and sender; tap opens it in Gmail. |

The `insight` event contains **both** — a task always comes with the memory it
was derived from (`actions[].memory_id` → `memory.id`).

If `insight.ignored == true`, the AI decided the speech was small talk or
filler. Nothing was stored. You can optionally show a faint "not saved" hint.

### The timeline, in the app

```
GET /api/memories?limit=50
```
One list. Render each row by `category`:
- `event` → `summary` + a formatted `starts_at`
- `email` → `summary` (the subject) + `people` (the sender); `link` opens it in Gmail
- everything else → `summary`, with `transcript` underneath if you want the raw words

**"Coming up" panel:**
```
GET /api/upcoming?days=14
```
The next two weeks of calendar events, soonest first. `next` is the very next
one - good for a "today" header on the home screen.

**"Latest mail" panel:**
```
GET /api/memories?category=email&limit=20&order=recent
```
`order=recent` sorts by when each mail actually arrived, so the newest is at
the top. (The default `order=new` sorts by when the server added it, which
is not the same thing.)

**"Needs attention" panel** - only the mail Gmail itself flagged:
```
GET /api/memories?category=email&important=1&limit=20
```

### Mail

`GET /api/google/messages` returns the **latest mail**, newest first.
`GET /api/google/messages/important` is the subset Gmail flags as important.

### Calendar time format

Send RFC 3339 with seconds: `2026-10-23T00:00:00+05:30`. The server repairs
the common near-misses (a missing `:SS`, a missing offset, a bare date), but
sending it properly is better.

---

## 2. Minimal client flow

```
1. Open WSS /events
2. On "device_connected"  -> show "recording" indicator
3. On "transcript"        -> append to a live caption bubble
4. On "insight":
       if (ignored)                 -> optionally show a subtle "not saved" hint
       else -> add insight.memory to the memory list
           -> add insight.actions[] to the task list
5. On "action_updated"    -> update that task in place (someone completed it)
```

Everything else (list, filter, search, complete a task) is plain REST.

---

## 3. REST calls your app needs

### Bootstrap / home screen
```http
GET /api/stats
```
```json
{ "memories": 128, "important": 41, "actions": 23, "pending_tasks": 7,
  "upcoming": 4,
  "by_category": { "task": 52, "memory": 61, "note": 15, "event": 9, "email": 15 } }
```

### The timeline
```http
GET /api/memories?limit=50
GET /api/memories?category=event          # calendar events only
GET /api/memories?category=email           # important emails only
GET /api/memories?upcoming=1&days=14       # events in the next 14 days
GET /api/upcoming?days=14                  # the same, ordered by start time
```

### Task list
```http
GET /api/actions?status=pending&limit=50
```
Returns `{ "actions": [...], "total": 23, "pending": 7 }`.

Action fields: `id`, `title`, `description`, `priority` (`high|medium|low`),
`status` (`pending|completed|dismissed`), `due_date` (free text, e.g.
`"tomorrow morning"` — **not** an ISO date; render it verbatim or normalise
client-side), `confidence` (0..1), `memory_id`, `created_at`, `completed_at`.

### Complete a task
```http
PATCH /api/actions/{id}
Content-Type: application/json

{ "status": "completed" }
```
Returns the updated action and also pushes an `action_updated` event, so a task
completed on the web shows up instantly on the phone.

### Dismiss / reschedule
```http
PATCH /api/actions/{id}
{ "status": "dismissed" }        or   { "priority": "high" }   or   { "due_date": "2026-10-20" }
```

### Memory timeline
```http
GET /api/memories?limit=50&offset=0
```
Optional filters: `category=task|memory|note`, `important=1|0`, `q=<text search>`.

### Semantic search
```http
POST /api/search?query=who%20do%20I%20owe%20money%20to&threshold=0.3&top_k=10
```
Note: `POST`, not `GET`. Returns `{ "results": [...], "query": "...", "count": n }`.

### Ask your memory
```http
POST /api/chat
Content-Type: application/json

{ "question": "what did I say I need to do about the invoice?" }
```
Returns `{ "answer": "...", "sources": [...], "context_used": true }`.
`sources[]` are the recalled memories — render them as tappable citations.

### Daily briefing (nice home-screen widget)
```http
GET /api/dashboard/daily-briefing
```
Returns `{ "memories": [...], "tasks": [...], "calendar": [...], "important_emails": [...] }`.

---

## 4. Android (Kotlin)

### WebSocket — the events feed

Use OkHttp's `WebSocket`. Reconnect with exponential backoff (the server
tolerates reconnects; a fresh connection re-syncs via REST).

```kotlin
val client = OkHttpClient.Builder()
    .readTimeout(0, TimeUnit.MILLISECONDS)   // keep the socket open
    .build()

val req = Request.Builder()
    .url("wss://pouch-lark-series.ngrok-free.dev/events")
    .build()

val ws = client.newWebSocket(req, object : WebSocketListener() {
    override fun onMessage(ws: WebSocket, text: String) {
        val gson = Gson()
        val map = gson.fromJson(text, Map::class.java)
        when (map["type"]) {
            "transcript" -> {
                val t = map["text"] as String
                runOnUiThread { liveCaption.append(t) }
            }
            "insight" -> {
                val ignored = map["ignored"] as Boolean
                if (ignored) {
                    runOnUiThread { showNotSavedHint() }
                } else {
                    val mem = map["memory"] as Map<*, *>
                    val acts = map["actions"] as List<*>
                    runOnUiThread {
                        memoryDao.insert(mem.toMemory())
                        acts.forEach { taskDao.insert(it.toTask()) }
                    }
                }
            }
            "action_updated" -> {
                val act = map["action"] as Map<*, *>
                runOnUiThread { taskDao.markDone(act["id"] as Int) }
            }
        }
    }

    override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
        // schedule reconnect
    }
})
```

### REST with Retrofit

```kotlin
interface LifeLogApi {
    @GET("api/stats")            suspend fun stats(): Stats
    @GET("api/actions")          suspend fun actions(@Query("status") status: String? = "pending"): ActionList
    @GET("api/memories")         suspend fun memories(@Query("limit") limit: Int = 50): MemoryList
    @HTTP(method = "PATCH", path = "api/actions/{id}", hasBody = true)
    suspend fun updateAction(@Path("id") id: Int, @Body body: Map<String, String>): Action
    @POST("api/chat")            suspend fun chat(@Body body: Map<String, String>): ChatReply
    @POST("api/search")          suspend fun search(@Query("query") q: String): SearchReply
}
```

Base URL: `https://pouch-lark-series.ngrok-free.dev/`

### Notes for Android
- ngrok's free tier shows an interstitial page for **browser** requests. It is
  generally not applied to plain API calls, but if you hit an HTML response,
  add a header `ngrok-skip-browser-warning: true` to every request. In OkHttp:
  ```kotlin
  .addInterceptor { chain ->
      chain.proceed(chain.request().newBuilder()
          .addHeader("ngrok-skip-browser-warning", "true").build())
  }
  ```
- Do **not** connect the phone to `/audio`. That endpoint is the ESP32's.
- Offline behaviour: cache `memory` and `action` rows locally. The
  `action_updated` event is the only cross-device sync signal, so re-fetch
  `/api/actions` when the socket reconnects.
- `due_date` is free text from speech, e.g. `"tomorrow morning"` or
  `"next week"`. It is not a parseable date. Show it as-is, or run your own
  natural-language parser if you want a calendar entry.

### Google OAuth on Android
Open `https://pouch-lark-series.ngrok-free.dev/auth/google?scope=calendar,gmail&mobile=1`.
The server redirects back to `lifelog://oauth?google=connected&email=…`.
Register that scheme in your `AndroidManifest.xml` and handle the intent.

---

## 5. Web (React / plain JS)

### WebSocket
```js
const ws = new WebSocket("wss://pouch-lark-series.ngrok-free.dev/events");
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  switch (m.type) {
    case "transcript":   liveCaption.push(m.text); break;
    case "insight":
      if (m.ignored) { flash("Not saved"); }
      else {
        memories.unshift(m.memory);
        m.actions.forEach(a => tasks.unshift(a));
      }
      break;
    case "action_updated": patchTask(m.action); break;
  }
};
```

### REST
```js
const API = "https://pouch-lark-series.ngrok-free.dev";

const stats   = await fetch(`${API}/api/stats`).then(r => r.json());
const actions = await fetch(`${API}/api/actions?status=pending`).then(r => r.json());
const memories= await fetch(`${API}/api/memories?limit=50`).then(r => r.json());

await fetch(`${API}/api/actions/1`, {
  method: "PATCH",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ status: "completed" }),
});

const search = await fetch(`${API}/api/search?query=${encodeURIComponent(q)}`, { method: "POST" }).then(r => r.json());
const chat   = await fetch(`${API}/api/chat`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ question: "what do I owe money for?" }),
}).then(r => r.json());
```

### Notes for the web
- `POST /api/search` — a GET will return 405.
- `GET /api/memories` and `GET /api/actions` return a **wrapper object**
  (`{memories: [...], total: n}`), not a bare array. `GET /api/memories/{id}`
  returns the object directly.
- Because the server is behind ngrok, every call has ~50–200 ms of extra
  latency. Prefer one `GET /api/dashboard/daily-briefing` call for the home
  screen over several round trips.
- Auth: there is currently **no** auth on the API. Do not expose the ngrok URL
  publicly without adding a token check first.

---

## 6. Rolling-window summaries (new)

Once an hour the server summarizes everything spoken since the last pass. The
app polls one endpoint:

```http
GET /summaries/latest
```
```json
{ "summary": { "key": "2026-10-22T1329", "summary": "- Emailed Sarah …\n- Called the dentist …",
               "engine": "…", "fallback_used": false, "window_end": "…" } }
```

**Polling rules:**

| Situation | What to do |
|---|---|
| `{"summary": null}` | No summary yet. Not an error. Keep polling. |
| `summary.key` == the last key you stored | Nothing new. Do nothing. |
| `summary.key` is new | Show the new summary, store the key. |
| `summary.fallback_used == true` | The LLM was unavailable; this is an extractive summary. Still worth showing, but you may want to label it. |

The `summary` field contains literal `\n` separated bullets — split on `\n`
and strip the leading `- `.

`window_end` is the more useful field to display, because the window is
rolling and not aligned to the clock.

**Do not use the WebSocket for summaries.** `/events` carries live transcript
events only. Summaries are HTTP by design.

**Optional push:** set `LIFELOG_SUMMARY_WEBHOOK_URL` and the server POSTs the
summary JSON the moment it is generated. The body is the same JSON that
`GET /summaries/latest` returns under its `summary` key.

**Budget:** `GET /summaries/status` shows `requests_today` and
`budget_remaining`. The OpenRouter free tier allows 50 free-model requests per
day per key; at one window per hour that is 24/day, which fits. When the
budget runs out the server stops calling the API and uses the offline
extractive fallback rather than wasting requests on 429s.

---

## 7. Google OAuth — choosing which account

**The account chooser is shown by default**, so the user can pick which Gmail
to use. This is `prompt=consent select_account`: with only `prompt=consent`,
Google skips the chooser whenever the browser is already signed in and drops
straight into the consent screen for whichever account happens to be active —
with no way back to pick a different one.

```
GET /auth/google?scope=calendar,gmail&mobile=1
```

| Parameter | Effect |
|---|---|
| `choose_account=1` | **default** — show the account chooser |
| `choose_account=0` | skip the chooser, use the already signed-in account |
| `email=you@gmail.com` | pre-select that account in the chooser |

`GOOGLE_ACCOUNT_EMAIL` in `server/.env` is also honoured as a `login_hint`,
but only when you explicitly set it — the account that is *already connected*
is never used as a hint, because that would skip the chooser and leave no way
to switch accounts.

> **Do not cache the auth URL.** Google shows the chooser because the request
> asks for it, not because of anything cached. A URL built before the
> credentials were loaded fails with
> `invalid_request: Missing required parameter: client_id`.

`GET /auth/google/status` reports which account is connected:
```json
{ "connected": true, "email": "you@gmail.com", "scopes": ["…"], "target_email": "you@gmail.com" }
```

Only one account is connected at a time. Connecting a different one replaces
the stored credentials.

---

## 8. What the client is responsible for vs. the server

| Concern | Owner |
|---|---|
| Mic capture, PCM encoding, WebSocket upload to `/audio` | **ESP32 firmware** (already done) |
| Transcription (Whisper) | **server** |
| Deciding if a memory is worth keeping (LLM) | **server** |
| Storing memories | **server** |
| Pulling calendar events + important mail into the timeline | **server** (background sync every 15 min) |
| Hourly rolling-window summary | **server** |
| Live caption UI | **client** |
| Task list UI, complete/dismiss, local caching | **client** |
| Rendering the timeline, search, chat | **client** |
| Polling and displaying summaries | **client** |

The client never has to post audio or trigger analysis itself. It only listens
on `/events` and calls REST.

The calendar and mail sync runs on the server every 15 minutes
(`GOOGLE_SYNC_INTERVAL_SECONDS`). You do not need to call it. To force it:
```
POST /api/google/sync?force=1
```

> **Tasks are no longer auto-generated.** The server used to create a task for
> every utterance the LLM judged actionable, which produced a lot of noise.
> That is switched off (`extraction_create_actions: false`). The hourly
> summary is now the mechanism for turning speech into something actionable.
> `GET /api/actions` still works and manual tasks can still be created with
> `POST /api/actions` — the list is simply empty until something is added.

---

## 9. Reference: what an `insight` looks like in practice

Live from the ESP32, speaking *"I need to remember to email Sarah back about
the invoice tomorrow morning"*:

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

---

## 10. Timing

| Event | Typical latency after the words were spoken |
|---|---|
| `transcript` | 3–6 s (Whisper runs on 5 s windows) |
| `insight` | 3–8 s after the last `transcript` (the AI call) |
| `action_updated` | < 300 ms |

The server buffers speech and only calls the LLM when it has enough text
(`extraction_min_chars`, default 80) or the speaker pauses
(`extraction_max_wait_seconds`, default 25). So `insight` events arrive in
batches, not one per sentence.
