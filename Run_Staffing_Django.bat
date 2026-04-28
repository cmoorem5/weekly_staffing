@echo off
REM Re-launch minimized so the wait + browser step does not sit in a visible CMD window.
if /I not "%~1"=="MINIMIZED" (
  start "" /MIN "%~f0" MINIMIZED
  exit /b 0
)

cd /d "%~dp0"

REM Django dev server in its own minimized window (restore from taskbar to see logs / Ctrl+C).
if exist ".venv\Scripts\python.exe" (
  start "BMF Staffing - Django" /MIN /D "%~dp0" .venv\Scripts\python.exe bmf_staffing\manage.py runserver
) else (
  start "BMF Staffing - Django" /MIN /D "%~dp0" python bmf_staffing\manage.py runserver
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$deadline = (Get-Date).AddSeconds(90); " ^
  "while ((Get-Date) -lt $deadline) { " ^
  "  try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8000); $c.Close(); exit 0 } catch { Start-Sleep -Milliseconds 300 } " ^
  "}; " ^
  "Write-Host 'Django did not open port 8000 within 90s - check the server window for errors.'; exit 1"

if errorlevel 1 (
  echo.
  echo Opening the site anyway. If it does not load, wait a few seconds and press F5 to refresh.
  timeout /t 2 /nobreak >nul
)

start "" "http://127.0.0.1:8000/"

REM This minimized launcher window closes. Django stays minimized until you open it from the taskbar.
exit /b 0
