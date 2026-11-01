# Windows personal installer

Installer 0.11.2 checks the application's lifetime mutex at startup and again before installation. Interactive confirmation sends a local graceful-exit request and waits up to ten seconds for the mutex to disappear. Cancellation, failed exit and silent installation with a running application do not proceed with file replacement. It never terminates arbitrary Python processes. The embedded application runtime remains version 0.11.0.

Build with PowerShell 7 and the system .NET Framework C# compiler. `scripts/build-windows-installer.ps1` accepts explicit download, work, output and Inno Setup compiler directories. Use `-StageOnly` to validate the isolated runtime before compiling the installer.

Pinned inputs:

- Official Python 3.12.10 x64 embeddable ZIP.
- Official PyTorch and torchaudio 2.9.1 CUDA 12.8 Windows CPython 3.12 wheels.
- Unmodified official VB-CABLE Driver Pack45, including all original files.
- Signed Microsoft VC++ x64 redistributable and WebView2 x64 standalone installer.
- Inno Setup 7.1.1 x64 compiler.

The staging process excludes source `data/`, caches and personal voiceprints. It preserves dependency license metadata. The installer explicitly excludes `model.ckpt`, even when that publisher-hosted model has been fetched into staging for local tests. `install_model.py` obtains that fixed checkpoint from its original publisher and checks SHA-256 before making it available. PS4's publisher declares Apache-2.0.

`AECStudio.exe` is an unsigned Windows launcher for the bundled interpreter. It sets the per-user data path and the private runtime DLL search path without changing the machine environment. `--self-test --deep --output FILE` performs an isolated import/GPU inference probe without opening audio streams.

`AECStudioDriver.exe --check` is read-only. `--install PATH_TO_INF` uses standard SetupAPI/NewDev APIs for the exact signed `VBAudioVACWDM` hardware ID, creates a root device only if absent, and waits for the original capture/render endpoints. It does not modify the INF/CAT/SYS files, audio defaults, security settings, or existing driver registrations. If installation of a newly created devnode fails, only that new registration is removed. It returns 3010 only when Windows explicitly reports a reboot requirement. Never run driver-install tests against an existing user's audio setup merely to validate a build.

The build-host check covered an already installed device and verified its Microsoft hardware compatibility catalog signature. First installation on a clean system has not been exercised on that host. Treat this as a personal trial build pending clean-target testing, not a signed enterprise deployment.

Source runs retain project-local data. Installed runs write to `%LOCALAPPDATA%\AECStudio\data`. Uninstall leaves that directory and the potentially shared VB-CABLE device intact.
