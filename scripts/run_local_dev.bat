@echo off
REM Same as run_local.bat but with --reload for development.
REM Note: on Windows the uvicorn reloader can hang when the process is
REM started detached from a console - use run_local.bat for long-running
REM sessions and restart manually after changes.

setlocal
cd /d "%~dp0..\server"

if not exist logs mkdir logs

echo ============================================================
echo  LifeLog backend (dev, auto-reload) - http://127.0.0.1:8000
echo ============================================================

set LIFELOG_ENV=production
set LIFELOG_DB_PATH=lifelog.db

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --log-level info
