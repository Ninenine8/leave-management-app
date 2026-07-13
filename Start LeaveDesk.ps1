$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host ""
Write-Host "Starting LeaveDesk..." -ForegroundColor Cyan
Write-Host ""
Write-Host "Keep this PowerShell window open while using the app." -ForegroundColor Yellow
Write-Host "Open: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host ""

& "C:\Users\LohWeeHui\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py

Write-Host ""
Write-Host "LeaveDesk stopped. Press Enter to close this window."
Read-Host
