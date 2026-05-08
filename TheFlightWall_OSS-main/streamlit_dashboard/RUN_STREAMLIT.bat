@echo off
title Flight Dashboard — launcher
REM Windows fix: double-clicking a .bat sometimes gives Python no real console stdin; Streamlit then quits
REM right after "Uvicorn server started". We start Streamlit in a proper cmd /k session instead.

cd /d "%~dp0"

if not exist "%~dp0app.py" (
    echo app.py not found next to this script. Folder: %~dp0
    pause
    exit /b 1
)

echo Starting Flight Dashboard in a new console window...
echo KEEP THAT NEW WINDOW OPEN while you use the browser.
echo.
echo Then open in Chrome or Edge:  http://127.0.0.1:8501
echo.

start "Flight Dashboard — keep open" cmd /k "%~dp0STREAMLIT_CMD.bat"

echo.
echo The server runs in the other window titled "Flight Dashboard".
echo You can close THIS window if you want.
timeout /t 5 >nul
