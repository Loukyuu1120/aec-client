"""Native launcher with a persistent startup log for pythonw."""
from pathlib import Path
import os
import sys
import traceback

root = Path(__file__).resolve().parent
os.chdir(root)
if sys.platform == 'win32':
    import ctypes
    port = os.environ.get('AEC_STUDIO_PORT', '8767')
    mutex_name = 'Local\\AECStudioPersonalVoice' + ('-'+port if port != '8767' else '')
    _instance = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == 183:
        if '--background' not in sys.argv:
            try:
                import urllib.request
                request = urllib.request.Request('http://127.0.0.1:'+port+'/api/app/show',
                    data=b'{}', headers={'Content-Type':'application/json'}, method='POST')
                urllib.request.urlopen(request,timeout=3).close()
            except Exception:
                pass
        raise SystemExit(0)
from web.paths import data_directory
data = data_directory()
data.mkdir(parents=True, exist_ok=True)
log = (data / 'app.log').open('a', encoding='utf-8', buffering=1)
sys.stdout = sys.stderr = log
try:
    from web.gui import main
    raise SystemExit(main())
except Exception:
    traceback.print_exc()
    if sys.platform == 'win32' and '--background' not in sys.argv:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            '启动失败，请查看项目 data/app.log。\nStartup failed. See data/app.log.',
            'AEC Studio', 0x10)
    raise
