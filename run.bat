@echo off
cd /d "%~dp0"
echo Starting Local Transcriber...
echo.
echo  On THIS computer:                 http://localhost:8765
echo  On your iPhone / iPad / laptop    http://YOUR-PC-IP:8765
echo  (same Wi-Fi), use this PC's IP shown below:
echo.
ipconfig | findstr /i "IPv4"
echo.
echo Keep this window open while you use it. Close it to stop.
start "" http://localhost:8765
".venv\Scripts\python.exe" -m uvicorn app:app --host 0.0.0.0 --port 8765
pause
