@echo off
title Streamlit SMOKE TEST (minimal app)
set "APP_DIR=C:\Users\pjone\TheFlightWall_OSS-main\TheFlightWall_OSS-main\streamlit_dashboard"
set "PYTHON=C:\Users\pjone\miniconda3\envs\flightdash\python.exe"
set PYTHONUNBUFFERED=1
cd /d "%APP_DIR%"
echo If this works in the browser but RUN_STREAMLIT.bat does not, app.py is crashing the server.
echo Open: http://127.0.0.1:8502
echo.
"%PYTHON%" -m streamlit run "%APP_DIR%\smoke_app.py" --server.address 127.0.0.1 --server.port 8502 --server.fileWatcherType none
echo Smoke test ENDED.
pause
