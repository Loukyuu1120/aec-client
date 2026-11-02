# Windows personal installer

The installer checks the application's lifetime mutex at startup and again before installation. Interactive confirmation sends a local graceful-exit request and waits up to ten seconds for the mutex to disappear. Cancellation, failed exit and silent installation with a running application do not proceed with file replacement. It never terminates arbitrary Python processes. The installer and application runtime are version 0.11.6.

Build with PowerShell 7 and the system .NET Framework C# compiler. `scripts/build-windows-installer.ps1` accepts explicit download, work, output and Inno Setup compiler directories. Use `-StageOnly` to validate the isolated runtime before compiling the installer.

The `Windows Release` GitHub Actions workflow prepares a clean Python 3.12.10 environment, downloads pinned CPU PyTorch wheels and the PS4 checkpoint, validates public build inputs, runs regression tests and builds on Windows Server 2022. It uses the runner's installed Inno Setup compiler (6.5 or newer). Microsoft redistributables are checked for valid signatures before packaging. SpeakerBeam weights and personal data remain excluded.

Push a tag matching `AppVersion` in `AECStudio.iss` (for example `v0.11.6`) to build and publish the installer, checksum and dependency list. The workflow also supports manual builds without publishing; enable the publish input to create a Release. Existing Releases are never overwritten. Build artifacts are retained for 14 days. Version or test failures prevent publication.

Version 0.11.6 negotiates PCM channel count and rate for enrollment, capture and output without changing system endpoint settings. Multichannel input is averaged into mono and mono output is duplicated into the negotiated output channels. The driver helper queries active render and capture endpoints through the bundled CoreAudio bridge, so localized VB-CABLE names work and the 16-channel auxiliary port is excluded. The same matching rule is used by on-demand capture; multiple matching capture endpoints remain an explicit error.

Installer 0.11.5 waits up to 60 seconds after driver binding for audio endpoints and checks Configuration Manager for `CM_PROB_NEED_RESTART`. Code 3 means the driver exists but endpoints are unavailable; it completes file installation with a warning and disables automatic launch. It does not reinstall an existing driver or assume a restart will fix every failure. Driver checks and results are logged to `%PROGRAMDATA%\AECStudio\driver-install.log`. A healthy existing-driver check was verified locally; first installation on the affected computer still requires confirmation.

`languages/ChineseSimplified.isl` is preserved from the upstream Inno Setup `is-7_1_1` source distribution, with its translator attribution. Keeping the translation in the repository avoids relying on optional language files on hosted runners.

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
