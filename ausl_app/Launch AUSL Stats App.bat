@echo off
setlocal
cd /d "%~dp0"
if exist "..\.venv\Scripts\python.exe" (
  "..\.venv\Scripts\python.exe" "src\ausl_stats_app.py"
) else (
  py "src\ausl_stats_app.py"
)
if errorlevel 1 pause
