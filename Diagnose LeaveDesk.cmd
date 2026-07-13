@echo off
cd /d "%~dp0"
echo LeaveDesk diagnostic
echo =====================
echo.
echo Folder:
cd
echo.
echo Python:
"C:\Users\LohWeeHui\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" --version
echo.
echo Checking app syntax...
"C:\Users\LohWeeHui\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m py_compile app.py leave_rules.py
if errorlevel 1 goto failed
echo Syntax OK.
echo.
echo Starting LeaveDesk on http://127.0.0.1:8000
echo Keep this window open.
echo.
"C:\Users\LohWeeHui\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py
goto stopped

:failed
echo.
echo App did not pass startup checks.
pause
exit /b 1

:stopped
echo.
echo LeaveDesk stopped.
echo If the browser says ERR_CONNECTION_REFUSED, this window must stay open while you use the app.
pause
