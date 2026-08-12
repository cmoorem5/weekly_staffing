@echo off
setlocal
title Crew Hub - Setup / Update
cd /d "%~dp0"

echo ==================================================
echo  Crew Hub - one-time setup / update
echo  Safe to re-run any time: it pulls code, installs
echo  dependencies, backs up both databases, applies
echo  migrations, and collects static files.
echo ==================================================
echo.

REM --- 1) Download the latest code ---------------------------------
set DO_PULL=1
where git >nul 2>nul || set DO_PULL=0
if not exist ".git" set DO_PULL=0
if "%DO_PULL%"=="1" (
  echo [1/8] Downloading the latest code from GitHub...
  git pull --ff-only
  if errorlevel 1 (
    echo       Pull failed - continuing with the code already on disk.
    echo       Fix with: git stash  then re-run this script.
  )
) else (
  echo [1/8] Skipping download - git or the .git folder was not found.
)
echo.

REM --- 2) Python virtual environment --------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo [2/8] Creating the Python virtual environment...
  py -3.12 -m venv .venv 2>nul || python -m venv .venv
) else (
  echo [2/8] Virtual environment already exists.
)
set "PYEXE=.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo.

REM --- 3) Dependencies ----------------------------------------------
echo [3/8] Installing / updating dependencies...
"%PYEXE%" -m pip install --upgrade pip --quiet
"%PYEXE%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
echo.

REM --- 4) Settings file ---------------------------------------------
echo [4/8] Checking the settings file...
if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo       Created .env from .env.example - edit it later to turn on
  echo       real email sending or PostgreSQL. Defaults work as-is.
)

REM --- 5) Database backup, before any migration runs ----------------
REM Migrations change the schema, so both databases are copied to
REM archive\ first. Timestamped, never overwritten, and never pruned:
REM only staffing_autobackup_*.db is auto-pruned (staffing_tool\db_backup.py),
REM so these survive until deleted by hand.
REM The Crew Hub copy keeps the .sqlite3 extension on purpose - the
REM dashboard's restore picker globs archive\*.db, and a Crew Hub database
REM must never be offered as a staffing.db to restore.
echo [5/8] Backing up databases to archive\...
if not exist "archive" mkdir "archive" >nul 2>&1
for /f %%I in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMdd_HHmmss\")"') do set "TS=%%I"
if not defined TS (
  echo       ERROR: could not build a timestamp - refusing to skip the backup.
  goto :fail
)
if exist "bmf_staffing\db.sqlite3" (
  copy /Y "bmf_staffing\db.sqlite3" "archive\crew_hub_%TS%.sqlite3" >nul
  if errorlevel 1 goto :fail
  echo       Crew Hub schedules, rotations, payroll, time off
  echo         -^> archive\crew_hub_%TS%.sqlite3
) else (
  echo       No Crew Hub database yet - nothing to back up ^(first run^).
)
if exist "staffing.db" (
  copy /Y "staffing.db" "archive\staffing_%TS%.db" >nul
  if errorlevel 1 goto :fail
  echo       Staffing KPI database
  echo         -^> archive\staffing_%TS%.db
)
findstr /I /R "^DJANGO_DB_ENGINE=post" ".env" >nul 2>&1
if not errorlevel 1 (
  echo.
  echo       NOTE: .env points Crew Hub at PostgreSQL, which this script
  echo             cannot back up. Take a pg_dump before continuing if you
  echo             have not already.
)
echo.

REM --- 6) Database migrations ---------------------------------------
echo [6/8] Applying database migrations...
"%PYEXE%" bmf_staffing\manage.py migrate
if errorlevel 1 goto :fail
echo.

REM --- 7) Static files (served by whitenoise under waitress) --------
echo [7/8] Collecting static files...
"%PYEXE%" bmf_staffing\manage.py collectstatic --noinput
if errorlevel 1 goto :fail
echo.

REM --- 8) First admin account ---------------------------------------
REM The count goes through a temp file instead of a backquoted command.
REM cmd strips the outer quotes off a backquoted command that both starts
REM and ends with one, so the old inline form ran a mangled path and
REM failed with "The system cannot find the path specified".
REM Two more guards on the output itself: -v 0 drops Django's shell
REM auto-import banner ("N objects imported automatically"), and the
REM CREWHUB_USERS= marker means no stray line can be read as the count.
set "USERCOUNT="
set "COUNTFILE=%TEMP%\crew_hub_usercount.txt"
del "%COUNTFILE%" >nul 2>&1
"%PYEXE%" bmf_staffing\manage.py shell -v 0 -c "from django.contrib.auth import get_user_model; print('CREWHUB_USERS=' + str(get_user_model().objects.count()))" > "%COUNTFILE%" 2>nul
if exist "%COUNTFILE%" (
  for /f "usebackq tokens=2 delims==" %%c in (`findstr /B /C:"CREWHUB_USERS=" "%COUNTFILE%"`) do set "USERCOUNT=%%c"
  del "%COUNTFILE%" >nul 2>&1
)
if "%USERCOUNT%"=="0" (
  echo [8/8] No login accounts exist yet. Create the first admin account:
  echo.
  "%PYEXE%" bmf_staffing\manage.py createsuperuser
) else (
  if defined USERCOUNT (
    echo [8/8] Login accounts already exist - skipping admin creation.
  ) else (
    echo [8/8] Could not check accounts. If you need an admin login, run:
    echo       %PYEXE% bmf_staffing\manage.py createsuperuser
  )
)

echo.
echo ==================================================
echo  Done. Start Crew Hub from the desktop shortcut.
echo  No shortcut yet?  Run: Create Desktop Shortcut.bat
echo ==================================================
echo.
pause
exit /b 0

:fail
echo.
echo Something failed - read the messages above, then re-run this script.
pause
exit /b 1
