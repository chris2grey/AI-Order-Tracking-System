@echo off
setlocal
cd /d "%~dp0"
set "runner="
if exist ".runtime\python.exe" (
    set "runner=.runtime\python.exe"
)
if not defined runner if exist ".venv\Scripts\python.exe" (
    set "runner=.venv\Scripts\python.exe"
)
if not defined runner (
    echo See README.md to install the project Python environment first.
    goto done
)
if not exist ".test-temp" mkdir ".test-temp"
set "TEMP=%CD%\.test-temp"
set "TMP=%TEMP%"
"%runner%" manage.py test tracker --verbosity 1
:done
pause
