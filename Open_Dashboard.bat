@echo off
setlocal

REM Open staffing dashboard in browser. Starts Django only if port 8000 is closed.

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$c = $null; " ^
  "try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8000); $c.Close(); exit 0 } catch { exit 1 }"

if errorlevel 1 (
  echo Django not detected on port 8000. Starting server...
  if exist ".venv\\Scripts\\python.exe" (
    start \"BMF Staffing - Django\" /MIN /D \"%~dp0\" .venv\\Scripts\\python.exe bmf_staffing\\manage.py runserver
  ) else (
    start \"BMF Staffing - Django\" /MIN /D \"%~dp0\" python bmf_staffing\\manage.py runserver
  )

  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$deadline = (Get-Date).AddSeconds(90); " ^
    "while ((Get-Date) -lt $deadline) { " ^
    "  try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8000); $c.Close(); exit 0 } catch { Start-Sleep -Milliseconds 300 } " ^
    "}; " ^
    "Write-Host 'Django did not open port 8000 within 90s - check the server window for errors.'; exit 1"
)

start "" "http://127.0.0.1:8000/staffing-dashboard/"
exit /b 0

