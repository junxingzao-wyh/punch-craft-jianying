@echo off
chcp 65001 >nul 2>&1
setlocal
pushd "%~dp0"
title Punch Craft Jianying - Install

echo.
echo  === punch-craft-jianying installer ===
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found in PATH.
    echo  Install Python 3.9+ from https://www.python.org/downloads/
    echo  During install, check "Add python.exe to PATH".
    echo.
    pause
    popd
    endlocal
    exit /b 1
)

if exist ".venv\Scripts\python.exe" goto HAVEVENV

echo  [1/3] Creating virtual environment...
python -m venv .venv
if errorlevel 1 goto FAIL
goto PIP

:HAVEVENV
echo  [1/3] Virtual environment already exists, skip.

:PIP
echo  [2/3] Upgrading pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple

echo  [3/3] Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto FAIL

echo.
echo  Done. Now double-click start.bat
echo.
pause
popd
endlocal
exit /b 0

:FAIL
echo.
echo  [ERROR] Install failed. Check your network, then try again.
echo  Or run manually:
echo    .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
popd
endlocal
exit /b 1
