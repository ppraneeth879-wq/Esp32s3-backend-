# Architecture

LifeLog streams PCM audio from an ESP32-S3 Sense over WebSocket to a FastAPI
backend. The backend reconstructs WAV files, transcribes incrementally with
Faster-Whisper, and broadcasts transcript updates to Android/Web clients.

See protocol/ for the wire format.