"""Explicit per-user startup preferences and saved audio routing."""
from pathlib import Path
import sys

try:
    import winreg
except ImportError:
    winreg = None


RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'AECStudio'
STARTUP_FLAGS = ('startWithWindows', 'startInBackground', 'autoStartProcessing', 'closeToTray')


def launcher_path(root):
    return Path(root)/'AECStudio.exe'


def register_startup(enabled, root, background=False, auto_start=False):
    if sys.platform != 'win32' or winreg is None:
        raise ValueError('startup_not_supported')
    executable = launcher_path(root).resolve()
    if enabled and not executable.is_file():
        raise ValueError('installed_launcher_required')
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE|winreg.KEY_QUERY_VALUE) as key:
        if enabled:
            command = '"' + str(executable) + '"'
            if background:
                command += ' --background'
            if auto_start:
                command += ' --auto-start'
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                existing = winreg.QueryValueEx(key, RUN_NAME)[0]
                if existing.startswith('"'+str(executable)+'"') or existing == str(executable):
                    winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


def validate_audio_preferences(value):
    if not isinstance(value, dict):
        raise ValueError('invalid_audio_preferences')
    result = {}
    for key, item in value.items():
        if key == 'profile' and type(item) is int and item in range(4):
            result[key] = item
        elif key == 'nsBackend' and item in ('off', 'webrtc', 'dtln', 'hush'):
            result[key] = item
        elif key == 'micGain' and type(item) in (int, float) and .1 <= item <= 4:
            result[key] = float(item)
        else:
            raise ValueError('invalid_audio_preferences')
    return result


def start_saved_processing(server, settings, has_profile, starter=None):
    if server.chain.running:
        return {'ok': True, 'alreadyRunning': True}
    if settings.get('mode') == 'target' and not has_profile:
        return {'ok': False, 'error': 'enrollment_required'}
    saved = settings.get('devices', {})
    if not all(saved.get(key) for key in ('mic', 'ref', 'out')):
        return {'ok': False, 'error': 'saved_devices_unavailable'}
    from .audio import _is_virtual_cable
    if not _is_virtual_cable(saved['out']):
        return {'ok': False, 'error': 'saved_virtual_output_unavailable'}
    inventory = server.list_devices()
    resolved = {}
    for key, category in (('mic', 'capture'), ('ref', 'loopback'), ('out', 'playback')):
        matching = [device for device in inventory.get(category, ()) if device['name'] == saved[key]]
        if len(matching) != 1:
            return {'ok': False, 'error': 'saved_devices_unavailable'}
        resolved[key] = matching[0]['index']
    preferences = validate_audio_preferences(settings.get('audioPreferences', {}))
    with server.runner_lock:
        server.prefs.update(preferences)
        server.prefs.update(resolved)
        server.prefs['listenToSelf'] = False
        return (starter or server.start)()
