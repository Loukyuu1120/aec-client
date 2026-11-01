@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-studio.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\download-target-model.py
if errorlevel 1 goto failed
echo Setup complete. Run Start.cmd. NVIDIA users can run Setup-GPU.cmd.
pause
exit /b 0
:failed
echo Setup failed. Check the message above.
pause
exit /b 1
