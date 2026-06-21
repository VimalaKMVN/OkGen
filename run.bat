@echo off
REM OkGen launcher for Windows. Double-click to set up (first run) and start.
REM Installs fully offline from the bundled packages in vendor\wheels.
setlocal
cd /d "%~dp0"

REM Pick a Python launcher.
where py >nul 2>nul && (set "PY=py") || (set "PY=python")

if not exist ".venv\Scripts\python.exe" (
  echo Creating environment ^(first run only^)...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo.
    echo ERROR: Could not create the environment. Is Python 3.9-3.13 installed and on PATH?
    echo Get it from https://www.python.org/downloads/ ^(check "Add Python to PATH"^).
    pause
    exit /b 1
  )
)

REM Make sure the dependencies are actually installed (handles a half-set-up venv).
".venv\Scripts\python.exe" -c "import flask, openpyxl, yaml" >nul 2>nul
if errorlevel 1 (
  echo Installing OkGen ^(offline, from bundled packages^)...
  ".venv\Scripts\python.exe" -m pip install --quiet --no-index --find-links "vendor\wheels" flask openpyxl pyyaml
  if errorlevel 1 (
    echo.
    echo ERROR: Offline install failed. Your Python version may not be covered by
    echo the bundled packages ^(they cover Python 3.9-3.13, 64-bit^). Send your
    echo Python version to the OkGen distributor:
    ".venv\Scripts\python.exe" --version
    pause
    exit /b 1
  )
)

set "PYTHONPATH=%CD%\src"
echo Starting OkGen. Microsoft Edge will open at http://127.0.0.1:8000
echo Close this window ^(or press Ctrl+C^) to stop.
REM Open Microsoft Edge explicitly (not the default file handler).
start "" msedge "http://127.0.0.1:8000" || start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m okgen.cli serve
pause
