@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating NFL virtual environment...
  py -m venv .venv
  if errorlevel 1 pause & exit /b 1
)

echo Installing NFL app libraries...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements-app.txt

echo.
echo NFL environment is ready.
echo You can now double-click "Launch NFL Stats App.bat".
pause
