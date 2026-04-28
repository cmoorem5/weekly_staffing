@echo off
setlocal EnableExtensions

REM Restores staffing.db from a specified archive file.
REM Usage:
REM   Restore_Staffing_DB.bat archive\staffing_YYYYMMDD_HHMMSS.db

cd /d "%~dp0"

if "%~1"=="" (
  echo Usage: %~nx0 ^<path-to-archive-db^>
  echo Example: %~nx0 archive\\staffing_20260427_153012.db
  exit /b 1
)

set "SRC=%~1"
set "DEST=%~dp0staffing.db"

if not exist "%SRC%" (
  echo ERROR: Archive file not found: "%SRC%"
  exit /b 1
)

echo This will overwrite "%DEST%"
choice /C YN /N /M "Continue? [Y/N] "
if errorlevel 2 exit /b 0

copy /Y "%SRC%" "%DEST%" >nul
if errorlevel 1 (
  echo ERROR: Restore failed.
  exit /b 1
)

echo Restored "%DEST%" from "%SRC%"
exit /b 0

