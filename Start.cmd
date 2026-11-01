@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please run Setup.cmd first.
  pause
  exit /b 1
)
start "AEC Studio" ".venv\Scripts\pythonw.exe" launch.py
