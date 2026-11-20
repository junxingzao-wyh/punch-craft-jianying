@echo off
chcp 65001 >nul 2>&1
setlocal
pushd "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Punch Craft Jianying - Fix Audio

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  [ERROR] .venv\Scripts\python.exe not found.
    echo  Keep the whole punch-craft-jianying folder intact.
    echo.
    pause
    popd
    endlocal
    exit /b 1
)

echo.
echo  Quit JianYingPro COMPLETELY before running this.
echo  Otherwise JianYing will write the old broken file back when it exits.
echo.

echo  Drafts found:
".venv\Scripts\python.exe" fix_audio.py --list
echo.
set "DRAFT="
set /p "DRAFT=Draft name (press Enter = newest one): "
echo.

if "%DRAFT%"=="" (
    ".venv\Scripts\python.exe" fix_audio.py
) else (
    ".venv\Scripts\python.exe" fix_audio.py --draft "%DRAFT%"
)

echo.
echo  Done. Open JianYing and play the draft.
pause >nul
popd
endlocal
