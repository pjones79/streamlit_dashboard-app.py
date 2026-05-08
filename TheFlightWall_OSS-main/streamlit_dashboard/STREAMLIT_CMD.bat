@echo off
REM Launched from RUN_STREAMLIT.bat — keeps an interactive console (fixes instant exit on some PCs).
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

set "PYTHON=C:\Users\pjone\miniconda3\envs\flightdash\python.exe"

if not exist "%PYTHON%" (
    echo Fix PYTHON= in this file. Not found: %PYTHON%
    pause
    exit /b 1
)

echo.
echo When you see: Uvicorn server started...
echo wait 5 seconds, then open: http://127.0.0.1:8501
echo.
echo Leave THIS window open. Ctrl+C to stop.
echo ============================================================
echo.

"%PYTHON%" -m streamlit run "%~dp0app.py" --server.address 127.0.0.1 --server.port 8501 --server.fileWatcherType none --server.headless true

echo.
echo ============================================================
echo Streamlit stopped. Read any messages above.
echo If it stopped right after "Uvicorn server started", run FIX_STREAMLIT_OLD_VERSION.bat
echo ============================================================
pause
