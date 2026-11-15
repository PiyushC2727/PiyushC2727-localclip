@echo off
title LocalClip — AI Video Clipper
cls
echo ======================================================================
echo                     LocalClip — AI Video Clipper
echo ======================================================================
echo.
echo Starting localclip web server...
echo URL: http://127.0.0.1:8765
echo (A browser window will open automatically)
echo.

set PY_EXE=

if exist ".venv\Scripts\python.exe" (
    set "PY_EXE=.venv\Scripts\python.exe"
)

if not defined PY_EXE (
    where uv >nul 2>nul
    if %errorlevel% equ 0 (
        uv run python server.py %*
        goto done
    )
)

if not defined PY_EXE (
    set "PY_EXE=python"
)

"%PY_EXE%" server.py %*

:done
echo.
pause
