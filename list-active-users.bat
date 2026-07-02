@echo off
setlocal enableextensions
title OkGen - Active user sessions

rem ---------------------------------------------------------------------------
rem  Lists everyone currently logged in to this machine (local + RDP sessions)
rem  so you can identify and reach out to whoever left the app running.
rem
rem  Runs WITHOUT administrator rights. The window stays open at the end so you
rem  can read it; press any key (or Ctrl+C) to close.
rem ---------------------------------------------------------------------------

echo.
echo === Active user sessions on this machine ===
echo.

quser 2>nul
if errorlevel 1 (
    echo   (quser unavailable - showing session list via qwinsta instead)
    echo.
    qwinsta 2>nul
)

echo.
echo   USERNAME    = who is logged in ^(reach out to them^)
echo   STATE       = Active ^(working^) or Disc ^(disconnected but still running^)
echo   LOGON TIME  = when they signed in
echo.
echo ----------------------------------------------------------------------
echo This window will stay open. Press any key to close it, or Ctrl+C.
pause >nul
