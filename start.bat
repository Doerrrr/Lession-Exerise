@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Dictation
set "PYTHONIOENCODING=utf-8"

set "PY="
if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if defined PY goto :run

where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :run
goto :no_python

:run
echo.
echo [Dictation] Starting. The browser will open http://127.0.0.1:8765
echo [Dictation] Close this window or press Ctrl+C to stop the server.
echo.
"%PY%" -u server.py --open
echo.
echo [Dictation] Server stopped.
pause
exit /b 0

:no_python
echo.
echo [Dictation] Python 3 was not found.
echo Install Python 3.9+ or launch from the DSH environment.
echo.
pause
exit /b 1
