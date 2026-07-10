@echo off
cd /d "%~dp0"
echo Starting LeaveDesk...
echo.
echo Keep this window open while using the app.
echo Open http://127.0.0.1:8000 in your browser.
echo.
"C:\Users\LohWeeHui\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py
echo.
echo LeaveDesk stopped. Press any key to close this window.
pause >nul
