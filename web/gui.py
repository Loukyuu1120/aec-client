"""Native-window launcher — runs the aec-web server
in-process and opens it in a pywebview window
(Edge WebView2 on Windows; no browser needed).

Two modes, picked automatically:
  own    — no server running (first click of
           start.bat): preload the models, run
           uvicorn in a background thread, open
           the window once it answers. Closing the
            window stops the server — unless the
            Appearance tab's "minimize to system
            tray" is ticked (default), which hides
            the window to the tray instead (the
            server keeps running; the tray icon
            brings it back). With the tray on, the
            console hides once the page loads —
            only the window is visible.
  attach — a server is already running (second
           click): just open the window; closing it
           leaves the server up.

Fallbacks to the default browser whenever pywebview
or the WebView2 runtime is missing, so the GUI never
depends on a single engine.

Run:  python -m web.gui   (from the repo root)
"""

import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
import argparse
import subprocess
from pathlib import Path

PORT = int(os.environ.get('AEC_STUDIO_PORT', '8767'))
URL = f"http://127.0.0.1:{PORT}/"
TITLE = "AEC Studio · 声纹语音工作台"

_server = None  # uvicorn.Server, when this process owns one
_window = None  # pywebview window, once opened
_tray = None    # pystray.Icon, once the tray is on
_quitting = False
_force_tray = False
_owns_server = False
_open_window_event = threading.Event()
_quit_event = threading.Event()
_tray_lock = threading.Lock()


def _startup_options():
    try:
        with urllib.request.urlopen(f'{URL}api/app/startup', timeout=3) as response:
            return json.loads(response.read())['settings']
    except Exception:
        return {}


def _post(path, body=None):
    request = urllib.request.Request(URL.rstrip('/')+path,
        data=json.dumps(body or {}).encode('utf-8'),
        headers={'Content-Type':'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read())


def _auto_start_processing():
    for attempt in range(20):
        if _quit_event.is_set():
            return
        try:
            result = _post('/api/app/start-saved')
            if result.get('ok'):
                return
            if result.get('error') != 'saved_devices_unavailable':
                print('Automatic processing not started: '+str(result.get('error')), flush=True)
                return
        except Exception as error:
            print('Automatic processing attempt: '+str(error), flush=True)
        if _quit_event.wait(3):
            return


def _background_window():
    global _force_tray
    _force_tray = True
    if not _start_tray():
        return False
    if _window is not None:
        _window.hide()
    _console_minimize()
    return True


def _restart_application():
    executable = Path(__file__).resolve().parents[1]/'AECStudio.exe'
    if not executable.is_file():
        return False
    subprocess.Popen([str(executable),'--wait-for-pid',str(os.getpid()),'--show'],
                     cwd=str(executable.parent),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    _tray_quit()
    return True


def _wait_for_open():
    while not _quit_event.is_set():
        if _open_window_event.wait(.25):
            _open_window_event.clear()
            return True
    return False


def _shutdown():
    global _quitting
    _quitting = True
    _quit_event.set()
    if _owns_server:
        try:
            from . import server
            server.stop()
        except Exception as error:
            print('Audio shutdown: '+str(error), flush=True)
        if _server is not None:
            _server.should_exit = True
    if _tray is not None:
        try:
            _tray.stop()
        except Exception:
            pass


def _tray_toggle_processing(icon=None, item=None):
    def toggle():
        try:
            with urllib.request.urlopen(URL+'api/state', timeout=3) as response:
                state = json.loads(response.read())
                running = state.get('running') or state.get('onDemandArmed')
            result = _post('/api/stop' if running else '/api/app/start-saved')
            if not result.get('ok') and _tray is not None:
                _tray.notify(str(result.get('error')), TITLE)
        except Exception as error:
            print('Tray processing: '+str(error), flush=True)
    threading.Thread(target=toggle, daemon=True, name='tray-processing').start()


def _port_answers():
    try:
        urllib.request.urlopen(URL, timeout=1)
        return True
    except Exception:
        return False


def _prefs():
    """The server's prefs (the tray tick lives
    there); works in both own and attach mode."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/api/prefs",
                timeout=1) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _serve():
    """Start the local server; inference models load on first use."""
    global _server
    from .server import app, chain, board
    from .soundboard import decoders_available

    try:
        n = len(board.scan())
        print(f"Soundboard: dir={board.display_dir} "
              f"decoders={decoders_available()} loaded={n}",
              flush=True)
    except Exception as e:
        print(f"Soundboard: scan failed ({e})", flush=True)
    import uvicorn
    # Server.run() skips signal installation when
    # not on the main thread (uvicorn does this
    # itself), so a background thread is safe.
    # Log at warning: the tray watcher polls
    # /api/prefs every second and the meters
    # stream over WebSocket — at info, every
    # request floods the console. Errors still
    # appear.
    _server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1",
                       port=PORT, workers=1, log_level="warning"))
    _server.run()


def _console_minimize():
    """While the window is trayed, hide the
    console completely — no taskbar button at
    all, only the tray icon is visible, like
    any tray app. (SW_SHOW brings it back,
    scrollback intact.)"""
    if sys.platform != "win32":
        return
    if "WT_SESSION" in os.environ:
        # Inside Windows Terminal the visible
        # window belongs to wt.exe and holds the
        # user's own tabs - never touch it. The
        # console here is headless (nothing to
        # hide), so say so instead.
        print("[-] running inside Windows Terminal - "
              "its window stays (it holds your "
              "tabs). Double-click start.bat for "
              "the hidden, tray-only experience, "
              "or minimize the terminal yourself.",
              flush=True)
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        user32.ShowWindow(hwnd, 0)  # SW_HIDE
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            0x0080 | 0x0001 | 0x0002 | 0x0010)
        # SWP_HIDEWINDOW|SWP_NOSIZE|SWP_NOMOVE
        # |SWP_NOACTIVATE
    except Exception:
        pass


def _console_restore():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.kernel32.GetConsoleWindow(),
            5)  # SW_SHOW
    except Exception:
        pass


def _set_window_icon():
    """Point the native window (and console) at app.ico via WM_SETICON.

    pywebview exposes no icon parameter, so the window otherwise shows
    python.exe's icon in the taskbar. A background thread finds every
    top-level window of this process carrying our title (not just the
    first — attach-mode can hold two, and WebView2 init can recreate
    the window after first paint) and sets window + class icons, big
    and small, from web/ui/favicon.ico, then re-applies once after
    init settles in case init reset them. Fail-open: any error (or a
    missing file) leaves the Python icon in place. The icon handles
    stay alive for the life of the process (never destroyed).
    Windows only.
    """
    if sys.platform != "win32":
        return
    here = os.path.dirname(os.path.abspath(__file__))
    ico = os.path.join(here, "ui", "favicon.ico")
    if not os.path.isfile(ico):
        ico = os.path.join(here, "..", "resources", "app.ico")
    ico = os.path.abspath(ico)
    if not os.path.isfile(ico):
        return

    def worker():
        try:
            import ctypes
            user32 = ctypes.windll.user32
            WM_SETICON, ICON_SMALL, ICON_BIG = 0x80, 0, 1
            GCLP_HICON, GCLP_HICONSM = -14, -34
            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010

            try:
                h_big = user32.LoadImageW(
                    None, ico, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
                h_small = user32.LoadImageW(
                    None, ico, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            except Exception:
                return
            if not h_big and not h_small:
                return

            def set_icon(hwnd):
                try:
                    if h_big:
                        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
                    if h_small:
                        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
                    try:
                        if h_big:
                            user32.SetClassLongPtrW(hwnd, GCLP_HICON, h_big)
                        if h_small:
                            user32.SetClassLongPtrW(hwnd, GCLP_HICONSM, h_small)
                    except Exception:
                        pass
                except Exception:
                    pass

            def apply_all():
                try:
                    EnumProc = ctypes.WINFUNCTYPE(
                        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                    me, hits = os.getpid(), []

                    def cb(hwnd, _):
                        try:
                            pid = ctypes.c_ulong()
                            user32.GetWindowThreadProcessId(
                                hwnd, ctypes.byref(pid))
                            if pid.value != me:
                                return True
                            if not user32.IsWindowVisible(hwnd):
                                return True
                            n = user32.GetWindowTextLengthW(hwnd)
                            if n <= 0:
                                return True
                            buf = ctypes.create_unicode_buffer(n + 1)
                            user32.GetWindowTextW(hwnd, buf, n + 1)
                            if TITLE in buf.value:
                                set_icon(hwnd)
                                hits.append(hwnd)
                        except Exception:
                            pass
                        return True

                    user32.EnumWindows(EnumProc(cb), None)
                    return len(hits)
                except Exception:
                    # EnumWindows unavailable — fall back to title lookup.
                    try:
                        hwnd = user32.FindWindowW(None, TITLE)
                        if hwnd:
                            set_icon(hwnd)
                            return 1
                    except Exception:
                        pass
                return 0

            # Wait for the window (webview.start blocks the main thread),
            # then re-apply once after init settles (recreation resets it).
            for _ in range(120):  # ~30 s grace
                if apply_all():
                    break
                time.sleep(0.25)
            time.sleep(6.0)
            apply_all()
            # The console window too (visible when the tray is off),
            # so both taskbar buttons match instead of mixing icons.
            # Note: conhost owns this window and may ignore WM_SETICON —
            # if its button keeps the Python icon, tick "Minimize to
            # system tray" (default on) to hide the console entirely.
            try:
                con = ctypes.windll.kernel32.GetConsoleWindow()
                if con:
                    set_icon(con)
            except Exception:
                pass
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True, name="window-icon").start()


def _tray_icon():
    """App icon (web/ui/favicon.ico, a copy of resources/app.ico)
    when available — same brand as the desktop exe/tray — else the
    drawn green-square fallback, so the tray never depends on an
    image asset being present."""
    from PIL import Image
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "ui", "tray.png"),
                 os.path.join(here, "ui", "favicon.ico"),
                 os.path.join(here, "..", "resources", "app.ico")):
        try:
            if os.path.isfile(cand):
                import warnings
                # app.ico is multi-resolution: its directory sizes
                # disagree with the embedded PNG, so Pillow warns
                # (not fails) on load. Silence exactly that warning —
                # real load errors still raise into the fallback below.
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message="Image was not the expected size",
                        category=UserWarning,
                        module="PIL\\.IcoImagePlugin")
                    img = Image.open(cand)
                    img.load()
                return img.convert("RGBA").resize((64, 64), Image.LANCZOS)
        except Exception:
            pass
    from PIL import ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14,
                        fill=(31, 111, 67, 255))
    d.rectangle([18, 25, 25, 39], fill=(255, 255, 255, 255))
    d.rectangle([29, 18, 36, 46], fill=(255, 255, 255, 255))
    d.rectangle([40, 27, 47, 37], fill=(255, 255, 255, 255))
    return img


def _start_tray():
    global _tray
    with _tray_lock:
        if _tray is not None:
            _tray.visible = True
            return True
        try:
            import pystray
            ready = threading.Event()
            failures = []
            _tray = pystray.Icon(
                'aec-studio', _tray_icon(), TITLE,
                pystray.Menu(
                    pystray.MenuItem('打开工作台 / Open', _tray_show, default=True),
                    pystray.MenuItem('开始或停止处理 / Start or stop', _tray_toggle_processing),
                    pystray.MenuItem('退出 / Exit', _tray_quit)))

            def setup(icon):
                icon.visible = True
                ready.set()

            def run():
                try:
                    tray.run(setup=setup)
                except Exception as error:
                    failures.append(error)
                    ready.set()
            tray = _tray
            threading.Thread(target=run, daemon=True, name='tray').start()
            if not ready.wait(3) or failures:
                print('Tray unavailable: '+str(failures), flush=True)
                try:
                    tray.stop()
                except Exception:
                    pass
                _tray = None
                return False
            return True
        except Exception as error:
            print('Tray unavailable: '+str(error), flush=True)
            _tray = None
            return False


def _tray_show(icon=None, item=None):
    global _force_tray
    _force_tray = False
    if _window is not None:
        _window.show()
    else:
        _open_window_event.set()


def _tray_quit(icon=None, item=None):
    global _quitting
    _quitting = True
    _quit_event.set()
    if _tray is not None:
        _tray.visible = False
    if _tray is not None:
        _tray.stop()
    if _window is not None:  # pause must be seen
        _window.destroy()


def _on_closing():
    """Returning False cancels the close (pywebview
    window.events.closing): hide to the tray instead
    of quitting when the Appearance tab asks for it."""
    if _quitting:
        return True
    if _prefs().get("trayEnabled"):
        if _background_window():
            return False
    return True


def _watch_prefs():
    """The tray tick lives in the server's prefs; show
    or remove the tray icon as it changes. Polling (not
    a callback) so it works in attach mode too, where
    the server runs in another process."""
    while not _quitting:
        try:
            want = _force_tray or bool(_prefs().get("trayEnabled"))
        except Exception:
            want = False
        if want and _tray is None:
            _start_tray()
        elif _tray is not None and _tray.visible != want:
            _tray.visible = want
        time.sleep(1.0)


def main(argv=None):
    global _window, _owns_server, _force_tray
    parser = argparse.ArgumentParser()
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--auto-start', action='store_true')
    parser.add_argument('--no-auto-start', action='store_true')
    parser.add_argument('--show', action='store_true')
    arguments = parser.parse_args(argv)
    if sys.platform == "win32":
        # Own taskbar identity: without this the taskbar button is
        # labeled "Python 3.12 (64-bit)" (python.exe's version resource)
        # and grouped with any other Python window — no WM_SETICON can
        # change that. Must run before any window exists. Fail-open.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "AECStudio.Desktop")
        except Exception:
            pass
    owned = not _port_answers()
    _owns_server = owned
    server_thread = None
    if owned:
        server_thread = threading.Thread(
            target=_serve, daemon=True, name="server")
        server_thread.start()
        # The window opens only once the server
        # answers (models are preloaded first).
        for _ in range(120):  # ~30 s grace
            if _port_answers():
                break
            time.sleep(0.25)
        else:
            print("[-] ERROR - server did not start "
                  "(see the messages above).", flush=True)
            return 1
    else:
        print("[-] attached to the running server - "
              "close the window to exit.", flush=True)

    options = _startup_options()
    hidden = not arguments.show and (arguments.background or options.get('startInBackground', False))
    automatic = (arguments.auto_start or options.get('autoStartProcessing', False)) and not arguments.no_auto_start
    if owned:
        from .server import app
        app.state.background_handler = _background_window
        app.state.quit_handler = _tray_quit
        app.state.show_handler = _tray_show
        app.state.restart_handler = _restart_application
        app.state.desktop_status = lambda: {'windowCreated':_window is not None,'trayAvailable':_tray is not None}
    if automatic and owned:
        threading.Thread(target=_auto_start_processing, daemon=True, name='auto-processing').start()
    _force_tray = bool(hidden)
    if hidden and _start_tray():
        _console_minimize()
        if not _wait_for_open():
            _shutdown()
            if server_thread is not None:
                server_thread.join(timeout=3)
            return 0
    hide_timer = None
    try:
        import webview
        _window = webview.create_window(
            TITLE, URL, width=1320, height=940,
            min_size=(1000, 760), resizable=True)
        _window.events.closing += _on_closing
        if _prefs().get("trayEnabled"):
            # App-like: once the page loads, the
            # console hides (no taskbar button) —
            # only the native window is visible.
            # (Tray off keeps the console for logs
            # and Ctrl+C.) The timer is a fallback
            # in case the loaded event never fires.
            _window.events.loaded += (
                lambda *a: _console_minimize())
            hide_timer = threading.Timer(
                4.0, _console_minimize)
            hide_timer.daemon = True
            hide_timer.start()
        threading.Thread(target=_watch_prefs, daemon=True,
                         name="prefs").start()
        # Runtime window icon (taskbar + title bar): pywebview has no
        # icon parameter, so a watcher thread sets WM_SETICON once the
        # window exists. Fail-open — Python's icon stays on any error.
        _set_window_icon()
        webview.start()
    except Exception as e:
        if hide_timer is not None:
            hide_timer.cancel()
        # No pywebview / no WebView2 runtime —
        # fall back to the default browser.
        print(f"[-] native window unavailable ({e}); "
              "opening the default browser.", flush=True)
        try:
            webbrowser.open(URL)
        except Exception:
            pass  # headless / no browser - the server
                  # is up; open the URL manually
        if owned:
            # Serve in the foreground (Ctrl+C stops).
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass
        return 0

    # Window closed.
    if owned:
        _shutdown()
        if server_thread is not None:
            server_thread.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
