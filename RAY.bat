@echo off
rem Launch RAY on Windows. Double-click this file or run it from a terminal.
setlocal
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul
if %errorlevel%==0 set "PY=py"

if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo RAY needs Python 3.10 or newer. Install it from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

"%PY%" main.py %*
if errorlevel 1 (
  echo.
  echo RAY exited with an error. If a package is missing, run:
  echo   "%PY%" -m pip install -r requirements.txt
  pause
)
endlocal
