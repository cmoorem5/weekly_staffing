@echo off
setlocal EnableExtensions

REM Copies staffing.db to archive/ with a timestamped filename.

cd /d "%~dp0"

set "SRC=%~dp0staffing.db"
set "ARCHIVE_DIR=%~dp0archive"

if not exist "%SRC%" (
  echo ERROR: staffing.db not found at "%SRC%"
  exit /b 1
)

if not exist "%ARCHIVE_DIR%" (
  mkdir "%ARCHIVE_DIR%" >nul 2>&1
)

for /f %%I in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMdd_HHmmss\")"') do set "TS=%%I"
set "DEST=%ARCHIVE_DIR%\staffing_%TS%.db"

copy /Y "%SRC%" "%DEST%" >nul
if errorlevel 1 (
  echo ERROR: Backup failed.
  exit /b 1
)

echo Backed up to "%DEST%"
exit /b 0

