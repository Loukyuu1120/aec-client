"""Release physical capture while the selected virtual input has no consumers."""
from pathlib import Path
import sys
import threading
import time
from .startup import start_saved_processing


class CaptureMonitor:
    def __init__(self):
        self.watcher = None
        self.output_name = None

    def read(self, output_name):
        if sys.platform != 'win32':
            return {'ok':False,'error':'usage_monitor_not_supported','activeSessions':0}
        try:
            if self.watcher is None or self.output_name != output_name:
                self.close()
                import clr
                library = Path(__file__).resolve().parents[1]/'resources/AECStudioAudio.dll'
                clr.AddReference(str(library))
                from AECStudio.Interop import CaptureUsage
                self.watcher = CaptureUsage(output_name)
                self.output_name = output_name
            snapshot = self.watcher.Read()
            return {'ok':bool(snapshot.Ok),'activeSessions':int(snapshot.ActiveSessions),
                    'processIds':[int(value) for value in snapshot.ProcessIds] if snapshot.ProcessIds is not None else [],
                    'clients':[str(value) for value in snapshot.Clients] if snapshot.Clients is not None else [],
                    'error':str(snapshot.Error or '')}
        except Exception as error:
            return {'ok':False,'error':str(error),'activeSessions':0}

    def close(self):
        if self.watcher is not None:
            self.watcher.Dispose()
        self.watcher, self.output_name = None, None


class MicrophoneDemand:
    def __init__(self, server, settings, profiles, busy, monitor=None, clock=time.monotonic):
        self.server, self.settings, self.profiles, self.busy = server, settings, profiles, busy
        self.monitor = monitor or CaptureMonitor()
        self.clock = clock
        self.armed = False
        self.status = 'stopped'
        self.error = ''
        self.consumers = 0
        self.process_ids = []
        self.clients = []
        self.requested_at = 0.
        self.last_consumer = 0.
        self.error_since = None
        self.thread = None
        self.wakeup = threading.Event()

    def state(self):
        return {'enabled':bool(self.settings['onDemandMicrophone']),'armed':self.armed,
                'status':self.status,'error':self.error,'activeSessions':self.consumers,
                'consumerProcessIds':list(self.process_ids),'captureActive':bool(self.server.chain.running),
                'clients':list(self.clients),
                'releaseGraceSeconds':3}

    def start(self):
        with self.server.runner_lock:
            if self.busy():
                return {'ok':False,'error':'enrollment_busy'}
            if self.settings['mode'] == 'target' and not self.profiles.current():
                return {'ok':False,'error':'enrollment_required'}
            if not self.settings['onDemandMicrophone']:
                self.armed = False
                self.status = 'continuous'
                return self.server.start_audio()
            if self.server.prefs.get('listenToSelf'):
                self.armed = False
                self.status = 'monitoring'
                return self.server.start_audio()
            if self.armed:
                return {'ok':True,'waitingForConsumer':not self.server.chain.running}
            self.armed = True
            self.status, self.error = 'waiting', ''
            self.requested_at = self.clock()
            self.last_consumer = self.requested_at
            self.error_since = None
            self._ensure_thread()
            self.wakeup.set()
            return {'ok':True,'waitingForConsumer':not self.server.chain.running}

    def stop(self):
        with self.server.runner_lock:
            self.armed = False
            self.status, self.error = 'stopped', ''
            self.consumers, self.process_ids = 0, []
            self.clients = []
            self.monitor.close()
            return self.server.stop_audio()

    def _ensure_thread(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._work,daemon=True,name='microphone-demand')
            self.thread.start()

    def _work(self):
        while True:
            self.wakeup.wait(1)
            self.wakeup.clear()
            if not self.armed:
                continue
            try:
                self.tick()
            except Exception as error:
                with self.server.runner_lock:
                    self.error = str(error)
                    self.status = 'error'
                    self.armed = False

    def _start_audio(self):
        if self.server.chain.running:
            return
        previous = self.status
        self.status = 'preparing'
        result = start_saved_processing(self.server,self.settings,bool(self.profiles.current()),starter=self.server.start_audio)
        if not result.get('ok'):
            self.armed = False
            self.status = 'error'
            self.error = result.get('error','audio_start_failed')
        else:
            self.status = previous

    def tick(self):
        with self.server.runner_lock:
            if not self.armed or self.busy():
                return
            output = self.server.prefs.get('out')
            output_name = self.settings.get('devices',{}).get('out','')
            if output is None:
                self.armed, self.status, self.error = False, 'error', 'saved_devices_unavailable'
                return
            usage = self.monitor.read(output_name)
            self.consumers = usage.get('activeSessions',0)
            self.process_ids = usage.get('processIds',[])
            self.clients = usage.get('clients',[])
            now = self.clock()
            if not usage.get('ok'):
                self.error = usage.get('error','usage_monitor_unavailable')
                if self.error_since is None:
                    self.error_since = now
                if now-self.error_since < 5:
                    self.status = 'checking_active' if self.server.chain.running else 'checking'
                    return
                if self.server.chain.running:
                    self.server.stop_audio()
                self.status = 'monitor_unavailable'
                return
            self.error = ''
            self.error_since = None
            if self.consumers:
                self.last_consumer = now
                self.status = 'processing'
                self._start_audio()
            elif self.server.chain.running and now-self.last_consumer < 3:
                self.status = 'releasing'
            else:
                if self.server.chain.running:
                    self.server.stop_audio()
                self.status = 'waiting'
