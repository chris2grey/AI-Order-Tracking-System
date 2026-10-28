@echo off
cd /d "%~dp0"
if exist ".runtime\python.exe" (
    ".runtime\python.exe" verify_transfer.py
) else (
    python verify_transfer.py
)
pause
