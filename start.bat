@echo off
chcp 65001 >nul 2>&1
setlocal
pushd "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Punch Craft Jianying - Web UI

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  [ERROR] .venv\Scripts\python.exe not found.
    echo  Make sure the whole punch-craft-jianying folder is intact.
    echo.
    pause
    popd
    endlocal
    exit /b 1
)

".venv\Scripts\python.exe" "gui.py"
echo.
echo  Server stopped. Press any key to close this window.
pause >nul
popd
endlocal
