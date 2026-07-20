@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
    py -3 main.py
) else (
    python main.py
)

if errorlevel 1 (
    echo.
    echo 启动失败。请先安装 Python 3，并在本目录运行：
    echo pip install -r requirements.txt
    pause
)

endlocal
