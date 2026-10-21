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

The server no longer just gives you a transcript. It gives you three kinds of
thing, and your app should show them differently:

| Server object | Where it comes from | How the app should treat it |
|---|---|---|
| `transcript` event | Whisper, every ~5 s | **Live caption.** Show as it streams. Ephemeral. Do not persist. |
| `insight` event → `memory` | AI judged it worth remembering | **Journal / memory.** Persist locally, show in a timeline, searchable. |
| `insight` event → `actions[]` | AI found something to do | **Task.** Persist locally, show in a to-do list, allow complete/dismiss. |

The `insight` event contains **both** — a task always comes with the memory it
was derived from (`actions[].memory_id` → `memory.id`).

If `insight.ignored == true`, the AI decided the speech was small talk or
filler. Nothing was stored. You can optionally show a faint "not saved" hint.

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
{ "memories": 128, "important": 41, "tasks": 23, "pending_tasks": 7,
  "by_category": { "task": 52, "memory": 61, "note": 15 } }
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

## 6. What the client is responsible for vs. the server

| Concern | Owner |
|---|---|
| Mic capture, PCM encoding, WebSocket upload to `/audio` | **ESP32 firmware** (already done) |
| Transcription (Whisper) | **server** |
| Deciding if speech matters (LLM) | **server** |
| Creating memories + tasks | **server** |
| Live caption UI | **client** |
| Task list UI, complete/dismiss, local caching | **client** |
| Rendering memories, search, chat | **client** |

The client never has to post audio or trigger analysis itself. It only listens
on `/events` and calls REST.

---

## 7. Reference: what an `insight` looks like in practice

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

## 8. Timing

| Event | Typical latency after the words were spoken |
|---|---|
| `transcript` | 3–6 s (Whisper runs on 5 s windows) |
| `insight` | 3–8 s after the last `transcript` (the AI call) |
| `action_updated` | < 300 ms |

The server buffers speech and only calls the LLM when it has enough text
(`extraction_min_chars`, default 80) or the speaker pauses
(`extraction_max_wait_seconds`, default 25). So `insight` events arrive in
batches, not one per sentence.
