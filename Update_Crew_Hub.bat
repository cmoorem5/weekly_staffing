@echo off
setlocal
title Crew Hub - Setup / Update
cd /d "%~dp0"

echo ==================================================
echo  Crew Hub - one-time setup / update
echo  Safe to re-run any time: it only pulls code,
echo  installs dependencies, applies migrations, and
echo  collects static files.
echo ==================================================
echo.

REM --- 1) Download the latest code ---------------------------------
set DO_PULL=1
where git >nul 2>nul || set DO_PULL=0
if not exist ".git" set DO_PULL=0
if "%DO_PULL%"=="1" (
  echo [1/6] Downloading the latest code from GitHub...
  git pull --ff-only
  if errorlevel 1 (
    echo       Pull failed - continuing with the code already on disk.
    echo       Fix with: git stash  then re-run this script.
  )
) else (
  echo [1/6] Skipping download - git or the .git folder was not found.
)
echo.

REM --- 2) Python virtual environment --------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo [2/6] Creating the Python virtual environment...
  py -3.12 -m venv .venv 2>nul || python -m venv .venv
) else (
  echo [2/6] Virtual environment already exists.
)
set "PYEXE=.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
echo.

REM --- 3) Dependencies ----------------------------------------------
echo [3/6] Installing / updating dependencies...
"%PYEXE%" -m pip install --upgrade pip --quiet
"%PYEXE%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
echo.

REM --- 4) Settings file + database migrations -----------------------
if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo       Created .env from .env.example - edit it later to turn on
  echo       real email sending or PostgreSQL. Defaults work as-is.
)
echo [4/6] Applying database migrations...
"%PYEXE%" bmf_staffing\manage.py migrate
if errorlevel 1 goto :fail
echo.

REM --- 5) Static files (served by whitenoise under waitress) --------
echo [5/6] Collecting static files...
"%PYEXE%" bmf_staffing\manage.py collectstatic --noinput
if errorlevel 1 goto :fail
echo.

REM --- 6) First admin account ---------------------------------------
set "USERCOUNT="
for /f "usebackq delims=" %%c in (`"%PYEXE%" bmf_staffing\manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.count())"`) do set "USERCOUNT=%%c"
if "%USERCOUNT%"=="0" (
  echo [6/6] No login accounts exist yet. Create the first admin account:
  echo.
  "%PYEXE%" bmf_staffing\manage.py createsuperuser
) else (
  if defined USERCOUNT (
    echo [6/6] Login accounts already exist - skipping admin creation.
  ) else (
    echo [6/6] Could not check accounts. If you need an admin login, run:
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
