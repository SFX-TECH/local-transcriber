@echo off
REM One-time setup: creates the Python environment and installs everything.
REM Re-run this only if the .venv folder is missing or broken.
cd /d "%~dp0"

echo Creating Python 3.11 environment...
py -3.11 -m venv .venv
if errorlevel 1 (
  echo Could not find Python 3.11 via the "py" launcher. Install Python 3.11 from python.org and re-run.
  pause
  exit /b 1
)

echo Installing dependencies (this downloads ~1.5 GB the first time)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo Setup complete. Double-click run.bat to start the app.
pause
