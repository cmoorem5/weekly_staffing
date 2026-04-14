@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe bmf_staffing\manage.py runserver
) else (
  python bmf_staffing\manage.py runserver
)
pause
