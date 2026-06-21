@echo off
REM OkGen launcher for Windows. Double-click to set up (first run) and start.
setlocal
cd /d "%~dp0"

REM Pick a Python launcher.
where py >nul 2>nul && (set PY=py) || (set PY=python)

if not exist ".venv\Scripts\python.exe" (
  echo Creating environment ^(first run only^)...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo.
    echo ERROR: Could not create the environment. Is Python 3.9+ installed and on PATH?
    echo Download it from https://www.python.org/downloads/ ^(check "Add Python to PATH"^).
    pause
    exit /b 1
  )
  echo Installing OkGen...
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -e .
)

echo Starting OkGen. A browser will open at http://127.0.0.1:8000
echo Close this window (or press Ctrl+C) to stop.
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m okgen.cli serve
pause
