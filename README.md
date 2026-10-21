# LifeLog

Real-time lecture transcription platform.

- firmware/: ESP32-S3 Sense firmware (PlatformIO)
- server/: FastAPI backend
- protocol/: shared binary protocol spec
- docs/: architecture, API, state machine, targets
- scripts/: dev utilities
- docker/: containerization

## Quick start (backend)

```
cd server
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Run tests:
```
cd server
pytest
```