# Tests

Server unit tests live in `server/tests/` and run with pytest:

```bash
cd server
python -m pytest tests/ -q
```

## Pipeline / integration scripts (these here)

These exercise the **insight pipeline** (speech → Whisper → LLM judgement →
memory + task). They run *inside the container*, because that is where the
model, the API key and the database live.

| File | What it does |
|---|---|
| `make_speech.ps1` | Generates a speech WAV with the Windows SAPI TTS engine (test fixture). |
| `resample.py` | Converts any WAV to 16 kHz mono 16-bit PCM, which is what the server expects. |
| `test_extraction.py` | Sends sample sentences to the LLM and prints the judgement (category, importance, tasks, people, dates). |
| `test_pipeline.py` | End-to-end pipeline test on a scratch database: overlap de-duplication, memory + action creation, DB rows. |
| `test_live_ws.py` | Simulates the ESP32: streams a WAV over `ws://…/audio` and prints every event received on `/events`. |

## Running them

```bash
# 1. build the fixture
powershell -ExecutionPolicy Bypass -File tests\make_speech.ps1

# 2. copy into the container and resample to 16 kHz
docker cp tests\test_live_ws.py  espbackend-lifelog-server-1:/tmp/t.py
docker cp tests\resample.py     espbackend-lifelog-server-1:/tmp/r.py
docker cp tests\speech_test.wav espbackend-lifelog-server-1:/tmp/s.wav
docker exec espbackend-lifelog-server-1 python3 /tmp/r.py /tmp/s.wav /tmp/s16.wav

# 3. run
docker exec espbackend-lifelog-server-1 python3 /tmp/t.py /tmp/s16.wav
docker exec espbackend-lifelog-server-1 python3 /tmp/test_extraction.py
```

`test_live_ws.py` needs the `websockets` package inside the container:

```bash
docker exec espbackend-lifelog-server-1 pip install websockets
```

## Expected output

```
  [event] transcript: 'Ok, so I need to remember to email Sarah back about the invoice tomorrow morning.'
  [event] transcript: 'And I have to call the dentist to reschedule my appointment for next week.'
  [event] INSIGHT category=task importance=0.8 ignored=False
           memory id=1 people=['Sarah'] dates=['tomorrow morning']
           TASK [medium] Email Sarah back about the invoice due=tomorrow morning
  [event] INSIGHT category=task importance=0.78 ignored=False
           memory id=2 people=[] dates=['next week']
           TASK [medium] Call the dentist to reschedule the appointment for next week
```
