@echo off
REM Stops the hidden Crew Hub / Staffing server started by the desktop
REM shortcut. Safe to run any time; only touches the Crew Hub waitress
REM server (or a leftover manage.py runserver from older versions).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*waitress*bmf_staffing.wsgi*' -or $_.CommandLine -like '*manage.py*runserver*' }; " ^
  "if (-not $procs) { Write-Host 'Crew Hub server is not running.' } " ^
  "else { $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Stopped Crew Hub server, PID ' + $_.ProcessId) } }"
echo.
pause
