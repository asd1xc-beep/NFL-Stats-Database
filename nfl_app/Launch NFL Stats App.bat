@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found.
  echo Run "Setup NFL Environment.bat" first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" src\nfl_stats_app.py
if errorlevel 1 pause
