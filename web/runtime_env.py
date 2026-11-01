"""Activate an explicitly configured, compatible per-user GPU runtime."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys

from .paths import data_directory


DLL_HANDLES = []


def probe_runtime(site=None, timeout=45):
    site = site or os.environ.get('AEC_STUDIO_GPU_RUNTIME')
    script = Path(__file__).with_name('runtime_probe_child.py')
    executable = Path(sys.executable)
    if executable.name.lower() in ('pythonw.exe', 'aec studio.exe') and executable.with_name('python.exe').is_file():
        executable = executable.with_name('python.exe')
    command = [str(executable), str(script)]
    if site:
        command.extend(('--site', str(site)))
    environment = dict(os.environ, AEC_STUDIO_SKIP_GPU_BOOTSTRAP='1', PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                            env=environment, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    lines = result.stdout.strip().splitlines()
    if not lines:
        return {'ok': False, 'error': result.stderr.strip()[-1500:] or 'runtime_probe_failed'}
    try:
        return json.loads(lines[-1])
    except ValueError:
        return {'ok': False, 'error': 'runtime_probe_failed'}


def activate_configured_runtime():
    if os.environ.get('AEC_STUDIO_INSTALLED') != '1' or os.environ.get('AEC_STUDIO_SKIP_GPU_BOOTSTRAP') == '1':
        return
    if sys.platform == 'win32' and ctypes.windll.shell32.IsUserAnAdmin():
        return
    pointer = data_directory()/'gpu-runtime.json'
    try:
        configuration = json.loads(pointer.read_text(encoding='utf-8'))
        site = Path(configuration['site']).resolve()
        if not (site/'torch/__init__.py').is_file():
            return
        result = probe_runtime(site)
        if not result.get('ok') or not result.get('cudaAvailable'):
            return
        if not str(result.get('torch', '')).startswith('2.9.1+'):
            return
        sys.path.insert(0, str(site))
        library = site/'torch/lib'
        if library.is_dir():
            os.environ['PATH'] = str(library) + os.pathsep + os.environ.get('PATH', '')
            if hasattr(os, 'add_dll_directory'):
                DLL_HANDLES.append(os.add_dll_directory(str(library)))
        os.environ['AEC_STUDIO_GPU_RUNTIME'] = str(site)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return
