@echo off
title LocalClip (Phone / LAN Mode)
cls
echo ======================================================================
echo                LocalClip — Phone / LAN Mode
echo ======================================================================
echo.
echo Starting localclip on local network...
echo.

set PY_EXE=

if exist ".venv\Scripts\python.exe" (
    set "PY_EXE=.venv\Scripts\python.exe"
)

if not defined PY_EXE (
    where uv >nul 2>nul
    if %errorlevel% equ 0 (
        uv run python server.py --lan
        goto done
    )
)

if not defined PY_EXE (
    set "PY_EXE=python"
)

"%PY_EXE%" server.py --lan

:done
echo.
pause
