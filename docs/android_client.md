# Android Client (Transcript Streaming)

There is no Android app in this repository (out of scope per PRD). To receive
streaming transcripts, connect a WebSocket client to:

```
ws://<server-host>:8000/events
```

Each message is JSON per `protocol/schemas/events.json` (transcript type).

Example (Kotlin, OkHttp):

```kotlin
val client = OkHttpClient()
val request = Request.Builder().url("ws://192.168.1.100:8000/events").build()
val ws = client.newWebSocket(request, object : WebSocketListener() {
    override fun onMessage(webSocket: WebSocket, text: String) {
        // parse JSON: type == "transcript" -> text field
    }
})
```

For a ready-made open-source reference, search "Android WebSocket OkHttp chat client" or
"Android STT websocket client" — any OkHttp/Retrofit WebSocket sample works; just point
it at `/events` and parse the transcript JSON.