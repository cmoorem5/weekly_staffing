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
REM staffing.db may live outside this folder (BMF_STAFFING_DATA_DIR in .env),
REM so ask the app where it is; the line above is the fallback.
set "PYEXE=%LOCALAPPDATA%\BMFStaffing\venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
set "PATHFILE=%TEMP%\crew_hub_dbpath.txt"
"%PYEXE%" -m staffing_tool.paths staffing > "%PATHFILE%" 2>nul
if not errorlevel 1 for /f "usebackq delims=" %%P in ("%PATHFILE%") do set "DEST=%%P"
del "%PATHFILE%" >nul 2>&1

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

