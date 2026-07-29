@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python src\nfl_stats_app.py
if errorlevel 1 pause
