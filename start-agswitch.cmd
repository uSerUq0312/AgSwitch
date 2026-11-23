@echo off
setlocal EnableExtensions
title AgSwitch

cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONLEGACYWINDOWSSTDIO=0"

set "ELECTRON_RUN_AS_NODE="

call :find_python
if not defined PY goto :no_python

echo Using interpreter: %PY%
echo.

"%PY%" "%ROOT%\app\main.py" %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo AgSwitch exited with code %RC%
    pause
)
endlocal
exit /b %RC%


:find_python
set "PY="
if exist "%ROOT%\runtime\python.exe" set "PY=%ROOT%\runtime\python.exe"
if not defined PY if exist "D:\dev\Python313\python.exe" set "PY=D:\dev\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "C:\Python313\python.exe" set "PY=C:\Python313\python.exe"
if not defined PY if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
if not defined PY (
    for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY set "PY=%%I"
)
if not defined PY (
    for /f "delims=" %%I in ('where python 2^>nul') do if not defined PY set "PY=%%I"
)
goto :eof


:no_python
echo [x] Python not found. Install Python 3.10+ first:
echo     https://www.python.org/downloads/windows/
echo.
echo If you already installed it, edit this file and set PY=^<path to python.exe^>
echo.
pause
endlocal
exit /b 1
