@echo off
title Streamlit HELLO test (should stay open)
cd /d "%~dp0"
set "PYTHON=C:\Users\pjone\miniconda3\envs\flightdash\python.exe"
set PYTHONUNBUFFERED=1

echo If this window closes right after "started", Streamlit is still broken — re-run FIX_STREAMLIT_OLD_VERSION.bat
echo Open in browser: http://127.0.0.1:8505
echo.
start "Streamlit hello" cmd /k ""%PYTHON%" -m streamlit hello --server.address 127.0.0.1 --server.port 8505 --server.headless true --server.fileWatcherType none"
echo A second window should open — use that one.
timeout /t 3 >nul
