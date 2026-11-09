@echo off
setlocal EnableExtensions

REM Copies staffing.db to archive/ with a timestamped filename.

cd /d "%~dp0"

set "SRC=%~dp0staffing.db"
REM staffing.db may live outside this folder (BMF_STAFFING_DATA_DIR in .env),
REM so ask the app where it is; the line above is the fallback.
set "PYEXE=%LOCALAPPDATA%\BMFStaffing\venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
set "PATHFILE=%TEMP%\crew_hub_dbpath.txt"
"%PYEXE%" -m staffing_tool.paths staffing > "%PATHFILE%" 2>nul
if not errorlevel 1 for /f "usebackq delims=" %%P in ("%PATHFILE%") do set "SRC=%%P"
del "%PATHFILE%" >nul 2>&1
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

