@echo off
REM Run the LifeLog backend natively (no Docker) on http://127.0.0.1:8000
REM
REM   scripts\run_local.bat        - stable run (no auto-reload)
REM   scripts\run_local_dev.bat    - same, with --reload for development
REM
REM Keeps the same behaviour as the Docker deployment:
REM   - LIFELOG_ENV=production  (config\production.yaml)
REM   - SQLite at server\lifelog.db
REM   - ChromaDB at server\data\chromadb
REM   - uploads / logs under server\

setlocal
cd /d "%~dp0..\server"

if not exist logs mkdir logs

echo ============================================================
echo  LifeLog backend - http://127.0.0.1:8000
echo  API docs - http://127.0.0.1:8000/docs
echo  Stop with Ctrl+C
echo ============================================================

set LIFELOG_ENV=production
set LIFELOG_DB_PATH=lifelog.db

REM Bind to 0.0.0.0, not 127.0.0.1.
REM The ESP32 connects to ws://<this-machine-lan-ip>:8000/audio, so the
REM server has to be reachable on the LAN interface as well as localhost.
REM (Docker bound 0.0.0.0 too, which is why the device worked there.)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info
