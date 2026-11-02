"""User-approved GPU framework setup; never installs a system CUDA Toolkit."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import urllib.request
import uuid
import zipfile

from .runtime_env import probe_runtime


GPU_PACKAGES = (
    ('torch-2.9.1+cu128-cp312-cp312-win_amd64.whl',
     'https://download.pytorch.org/whl/cu128/torch-2.9.1%2Bcu128-cp312-cp312-win_amd64.whl',
     '3a01f0b64c10a82d444d9fd06b3e8c567b1158b76b2764b8f51bfd8f535064b0'),
    ('torchaudio-2.9.1+cu128-cp312-cp312-win_amd64.whl',
     'https://download.pytorch.org/whl/cu128/torchaudio-2.9.1%2Bcu128-cp312-cp312-win_amd64.whl',
     '88896c7bfa486102439fab6c85ac834176617e9c06eb0be9074c07ee1183b47d'),
)


def driver_information():
    report = {'available': False, 'cudaVersion': None, 'gpuName': None,
              'toolkitDetected': bool(os.environ.get('CUDA_PATH') and Path(os.environ['CUDA_PATH']).is_dir())}
    if sys.platform != 'win32':
        return report
    try:
        library = Path(os.environ['WINDIR'])/'System32/nvcuda.dll'
        cuda = ctypes.WinDLL(str(library))
        version = ctypes.c_int()
        count = ctypes.c_int()
        if cuda.cuInit(0) != 0 or cuda.cuDriverGetVersion(ctypes.byref(version)) != 0:
            return report
        if cuda.cuDeviceGetCount(ctypes.byref(count)) != 0 or count.value < 1:
            return report
        name = ctypes.create_string_buffer(256)
        cuda.cuDeviceGetName(name, len(name), 0)
        report.update({'available': version.value >= 12000, 'cudaVersion': version.value,
                       'gpuName': name.value.decode('utf-8', errors='replace')})
    except (OSError, KeyError, AttributeError):
        pass
    return report


def compatible_runtime(report):
    return (report.get('ok') is True and report.get('cudaAvailable') is True
            and str(report.get('torch', '')).split('+')[0] == '2.9.1'
            and str(report.get('torchaudio', '')).split('+')[0] == '2.9.1')


def candidate_sites(root, data):
    candidates = [Path(data).parent/'gpu-runtime', Path(root)/'python/Lib/site-packages']
    for variable in ('VIRTUAL_ENV', 'CONDA_PREFIX', 'PYTHONHOME'):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable])/'Lib/site-packages')
    if os.environ.get('LOCALAPPDATA'):
        candidates.append(Path(os.environ['LOCALAPPDATA'])/'Programs/Python/Python312/Lib/site-packages')
    if sys.platform == 'win32':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r'Software\Python\PythonCore\3.12\InstallPath') as key:
                    location = winreg.QueryValueEx(key, '')[0]
                    candidates.append(Path(location)/'Lib/site-packages')
            except OSError:
                pass
    seen = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            version = (candidate/'torch/version.py').read_text(encoding='utf-8')
        except OSError:
            continue
        if re.search(r'cuda(?:\s*:\s*[^=\n]+)?\s*=\s*[\'"][0-9]', version):
            yield candidate


class GpuRuntimeManager:
    def __init__(self, root, data, cache=None):
        self.root = Path(root)
        self.data = Path(data)
        self.cache = Path(cache) if cache else self.data.parent/'gpu-downloads'
        self.thread = None
        self.lock = threading.RLock()
        self.existing_site = None
        self.details = {'status': 'unchecked', 'action': 'check', 'progress': 0,
                        'error': '', 'needsRestart': False, 'driver': {}}

    def state(self):
        with self.lock:
            return dict(self.details, active=bool(self.thread and self.thread.is_alive()))

    def _update(self, **values):
        with self.lock:
            self.details.update(values)

    def inspect(self):
        self.existing_site = None
        self._update(status='checking', action='none', error='')
        current = probe_runtime()
        driver = driver_information()
        self._update(driver=driver, runtime=current)
        if compatible_runtime(current):
            self._update(status='ready', action='none', cudaVersion=current.get('cudaVersion'), progress=100)
            return self.state()
        if not driver['available']:
            self._update(status='driver_required', action='driver')
            return self.state()
        for candidate in candidate_sites(self.root, self.data):
            report = probe_runtime(candidate)
            if compatible_runtime(report):
                self.existing_site = candidate
                try:
                    pointer = json.loads((self.data/'gpu-runtime.json').read_text(encoding='utf-8'))
                    if Path(pointer['site']).resolve() == candidate:
                        self._update(status='restart_required', action='none', needsRestart=True,
                                     cudaVersion=report.get('cudaVersion'), progress=100)
                        return self.state()
                except (OSError,ValueError,KeyError):
                    pass
                self._update(status='available', action='reuse', cudaVersion=report.get('cudaVersion'))
                return self.state()
        self._update(status='available', action='install')
        return self.state()

    def check_async(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.state()
            self.thread = threading.Thread(target=self._check, daemon=True, name='gpu-check')
            self.thread.start()
        return self.state()

    def _check(self):
        try:
            self.inspect()
        except Exception as error:
            self._update(status='error', action='check', error=str(error))

    def _remember(self, site):
        self.data.mkdir(parents=True, exist_ok=True)
        temporary = self.data/'gpu-runtime.tmp'
        temporary.write_text(json.dumps({'site': str(Path(site).resolve()), 'version': '2.9.1'}), encoding='utf-8')
        os.replace(temporary, self.data/'gpu-runtime.json')
        self._update(status='restart_required', action='none', needsRestart=True, progress=100)

    def install_async(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('gpu_setup_busy')
            self.thread = threading.Thread(target=self._install, daemon=True, name='gpu-setup')
            self.thread.start()
        return self.state()

    def _download(self, name, url, expected, package_index):
        self.cache.mkdir(parents=True, exist_ok=True)
        target = self.cache/name
        if target.is_file():
            with target.open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() == expected:
                    self._update(progress=(package_index+1)*40)
                    return target
        temporary = target.with_suffix(target.suffix+'.part')
        request = urllib.request.Request(url, headers={'User-Agent':'AECStudio-GpuSetup/0.11.7'})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as output:
            total = int(response.headers.get('Content-Length', 0))
            copied = 0
            while True:
                block = response.read(1024*1024)
                if not block:
                    break
                output.write(block)
                copied += len(block)
                progress = package_index*40 + (copied/total*40 if total else 0)
                self._update(progress=round(progress, 1), downloadedBytes=copied, totalBytes=total)
        with temporary.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
                raise ValueError('gpu_download_checksum_failed')
        os.replace(temporary, target)
        return target

    @staticmethod
    def _extract(wheel, destination):
        destination = destination.resolve()
        with zipfile.ZipFile(wheel) as archive:
            for item in archive.infolist():
                target = (destination/item.filename).resolve()
                if not target.is_relative_to(destination):
                    raise ValueError('invalid_gpu_archive_path')
            archive.extractall(destination)

    def _remove_staging(self, directory):
        directory = directory.resolve()
        parent = self.data.parent.resolve()
        if not directory.is_relative_to(parent) or not directory.name.startswith('gpu-runtime.staging-'):
            raise ValueError('invalid_gpu_staging_path')
        if directory.exists():
            shutil.rmtree(directory)

    def _install(self):
        staging = None
        try:
            inspection = self.inspect()
            if inspection['status'] == 'ready':
                return
            if inspection['status'] == 'restart_required':
                return
            if inspection['action'] == 'reuse':
                self._remember(self.existing_site)
                return
            if inspection['action'] != 'install':
                self._update(error='nvidia_driver_required')
                return
            self._update(status='installing', action='none', progress=0, error='')
            staging = self.data.parent/('gpu-runtime.staging-'+uuid.uuid4().hex)
            staging.mkdir(parents=True)
            for index, (name, url, expected) in enumerate(GPU_PACKAGES):
                wheel = self._download(name, url, expected, index)
                self._extract(wheel, staging)
            self._update(progress=90)
            report = probe_runtime(staging, timeout=120)
            if not compatible_runtime(report):
                raise ValueError('gpu_runtime_validation_failed: '+str(report.get('error', 'GPU unavailable')))
            target = self.data.parent/'gpu-runtime'
            if target.exists():
                report = probe_runtime(target)
                if compatible_runtime(report):
                    self._remove_staging(staging)
                    staging = None
                    self._remember(target)
                    return
                raise ValueError('gpu_runtime_folder_needs_repair')
            staging.rename(target)
            staging = None
            self._remember(target)
        except Exception as error:
            self._update(status='error', action='check', error=str(error))
        finally:
            if staging and staging.exists():
                self._remove_staging(staging)
