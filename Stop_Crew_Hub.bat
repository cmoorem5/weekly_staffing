@echo off
REM Stops the hidden Crew Hub / Staffing Django server started by the
REM desktop shortcut. Safe to run any time; only touches manage.py runserver.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*manage.py*runserver*' }; " ^
  "if (-not $procs) { Write-Host 'Crew Hub server is not running.' } " ^
  "else { $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Stopped Crew Hub server, PID ' + $_.ProcessId) } }"
echo.
pause
