@echo off
title DOWNGRADE Streamlit (fix instant exit on Windows)
echo.
echo Your PC starts Streamlit then quits right after "Uvicorn server started".
echo That is a known bad combo with some Streamlit 1.5x + Uvicorn builds on Windows.
echo.
echo This script will:
echo   1) Remove Streamlit from the "flightdash" env (Conda + pip)
echo   2) Install Streamlit 1.40.2 (uses Tornado; usually stays running)
echo.
echo CLOSE any running Streamlit windows first.
echo.
pause

set "CONDA=C:\Users\pjone\miniconda3\Scripts\conda.exe"
if not exist "%CONDA%" (
    echo Edit this file: set CONDA= to your miniconda3\Scripts\conda.exe
    pause
    exit /b 1
)

echo.
echo --- Removing old Streamlit (ignore errors if not installed) ---
"%CONDA%" remove -n flightdash streamlit --yes 2>nul
"%CONDA%" run -n flightdash python -m pip uninstall -y streamlit 2>nul

echo.
echo --- Installing Streamlit 1.40.2 ---
"%CONDA%" run -n flightdash python -m pip install --no-cache-dir "streamlit==1.40.2"

echo.
echo --- Installed version ---
"%CONDA%" run -n flightdash python -m streamlit version

echo.
echo DONE. Now:
echo   1) Double-click RUN_HELLO_TEST.bat  (should STAY open — close with Ctrl+C)
echo   2) If hello works, double-click RUN_STREAMLIT.bat
echo.
pause
