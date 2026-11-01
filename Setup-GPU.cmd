@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu130
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-studio.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\download-target-model.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "import torch; assert torch.cuda.is_available(), 'CUDA unavailable: check NVIDIA driver'; print(torch.cuda.get_device_name(0))"
if errorlevel 1 goto failed
echo GPU setup complete. Restart AEC Studio and choose a processor in Settings.
pause
exit /b 0
:failed
echo GPU setup failed. Check the message above.
pause
exit /b 1
