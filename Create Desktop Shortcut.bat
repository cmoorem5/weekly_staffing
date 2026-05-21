@echo off
REM One-time setup: BMF logo shortcut on your Windows desktop.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create_desktop_shortcut.ps1"
if errorlevel 1 (
  echo.
  echo Shortcut creation failed. See messages above.
  pause
  exit /b 1
)
echo.
pause
