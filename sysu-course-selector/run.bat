@echo off
setlocal EnableExtensions
cd /d "%~dp0"

py -3 main.py
if not errorlevel 1 exit /b 0

python main.py
if not errorlevel 1 exit /b 0

echo.
echo Failed to start. Install Python 3 and run:
echo pip install -r requirements.txt
pause
