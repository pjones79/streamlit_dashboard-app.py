@echo off
REM Run from this folder (double-click or run from cmd). Uses: python -m streamlit
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found in PATH. Install Python from https://www.python.org/downloads/
  echo Prefer the "Windows installer (64-bit)" x86-64 build unless you know you need ARM64.
  pause
  exit /b 1
)
echo Using:
python --version
python -m streamlit run app.py
pause
