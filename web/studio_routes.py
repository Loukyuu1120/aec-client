"""Fork-specific UI and enrollment API, leaving upstream DSP engines intact."""
from pathlib import Path
import io
import json
import os
import threading
from urllib.parse import urlparse
from fastapi import Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
import soundfile as sf
from .voiceprint.model import TargetModel, CHECKPOINT
from .voiceprint.profiles import Profiles
from .voiceprint.recording import EnrollmentRecorder
from .voiceprint.stream import TargetStream
from .voiceprint.speakerbeam import SpeakerBeamModel, CHECKPOINT as BEAM_CHECKPOINT
from .voiceprint.diagnostic import VoiceDiagnostic
from .voiceprint.extraction import EXTRACTION_MODELS, create_extractor, validate_target_controls
from .voiceprint.adaptation import VoiceAdaptation, reset_adaptation
from .paths import data_directory
from .startup import STARTUP_FLAGS, launcher_path, register_startup, start_saved_processing, validate_audio_preferences
from .gpu_runtime import GpuRuntimeManager
from .microphone_demand import MicrophoneDemand

ROOT = Path(__file__).resolve().parents[1]
DATA = data_directory()


def install(server):
    app, chain, prefs = server.app, server.chain, server.prefs
    model = TargetModel()
    beam = SpeakerBeamModel()
    profiles = Profiles(DATA / 'profiles', model)
    recorder = EnrollmentRecorder(profiles)
    diagnostic = VoiceDiagnostic(profiles,model,beam,DATA)
    gpu_manager = GpuRuntimeManager(ROOT, DATA)
    startup_status = {'status':'idle','error':''}
    settings_lock = threading.RLock()
    settings = {'language': 'zh-CN', 'mode': 'clean', 'profileName': '我的声音', 'processor': 'auto',
                'devices': {}, 'cleanSampleRate': 48000, 'extractionModel': 'verified',
                'startWithWindows':False, 'startInBackground':False, 'autoStartProcessing':False,
                'closeToTray':False, 'adaptiveLearning':False,
                'onDemandMicrophone':os.name == 'nt',
                'targetControls':validate_target_controls({}),
                'audioPreferences':{'profile':1,'nsBackend':'webrtc','micGain':1.0}}
    try:
        stored = json.loads((DATA / 'studio-settings.json').read_text('utf-8'))
        settings.update({k: v for k, v in stored.items() if k in settings})
    except (OSError, ValueError, TypeError):
        pass
    if settings['mode'] not in ('clean', 'target'):
        settings['mode'] = 'clean'
    if settings['language'] not in ('zh-CN', 'en'):
        settings['language'] = 'zh-CN'
    if settings['processor'] not in ('auto', 'cpu', 'cuda'):
        settings['processor'] = 'auto'
    model.processor = settings['processor']
    beam.processor = settings['processor']
    if settings['extractionModel'] not in EXTRACTION_MODELS:
        settings['extractionModel'] = 'verified'
    if settings['cleanSampleRate'] not in (16000, 48000):
        settings['cleanSampleRate'] = 48000
    if not isinstance(settings['devices'], dict):
        settings['devices'] = {}
    for flag in (*STARTUP_FLAGS, 'adaptiveLearning', 'onDemandMicrophone'):
        if type(settings[flag]) is not bool:
            settings[flag] = flag == 'onDemandMicrophone' and os.name == 'nt'
    try:
        settings['audioPreferences'] = dict({'profile':1,'nsBackend':'webrtc','micGain':1.0}, **validate_audio_preferences(settings['audioPreferences']))
    except ValueError:
        settings['audioPreferences'] = {'profile':1,'nsBackend':'webrtc','micGain':1.0}
    try:
        settings['targetControls'] = validate_target_controls(settings['targetControls'])
    except ValueError:
        settings['targetControls'] = validate_target_controls({})
    # AEC3 is appropriate for loudspeaker double-talk, already installed locally.
    prefs['profile'] = 1
    prefs['nsBackend'] = 'webrtc'
    prefs.update(settings['audioPreferences'])
    prefs['trayEnabled'] = settings['closeToTray']

    def target_factory():
        if recorder.state()['active'] or diagnostic.state()['active']:
            raise ValueError('enrollment_busy')
        chain.sample_rate = 16000 if settings['mode'] == 'target' else settings['cleanSampleRate']
        if settings['mode'] != 'target':
            return None
        adaptation = VoiceAdaptation(profiles, lambda: settings['adaptiveLearning']) if settings['extractionModel'] == 'verified' else None
        extractor, cue = create_extractor(settings['extractionModel'], profiles, model, beam, adaptation, settings['targetControls'])
        return TargetStream(extractor, cue)

    chain.target_factory = target_factory
    demand = MicrophoneDemand(server, settings, profiles, lambda:recorder.state()['active'] or diagnostic.state()['active'])
    app.state.microphone_demand = demand

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        # Protect microphone-changing endpoints from cross-site requests.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and urlparse(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': 'origin_not_allowed'}, status_code=403)
        return await call_next(request)

    @app.get('/studio')
    def studio():
        return FileResponse(ROOT / 'web/ui/studio.html')

    @app.get('/api/studio/state')
    def studio_state():
        st = server.state()
        return {'settings': dict(settings), 'audio': st, 'enrollment': recorder.state(), 'diagnostic':diagnostic.state(),
                'profile': profiles.current(), 'modelAvailable':
                    (BEAM_CHECKPOINT.exists() and CHECKPOINT.exists()) if settings['extractionModel']=='verified' else
                    (BEAM_CHECKPOINT if settings['extractionModel']=='speakerbeam' else CHECKPOINT).exists(),
                'hardware': model.hardware(), 'activeProcessor': beam.device if beam.model else (model.device if model.model else None),
                'startup':dict(startup_status, supported=launcher_path(ROOT).is_file(),
                               backgroundAvailable=callable(getattr(app.state,'background_handler',None))),
                'gpuSetup':gpu_manager.state(), 'microphoneDemand':demand.state(),
                'selectedSampleRate': 16000 if settings['mode'] == 'target' else settings['cleanSampleRate'],
                'model': {'speakerbeam':'TD-SpeakerBeam · paired 512D cue',
                          'ps4':'PS4 · WeSep BSRNN + WeSpeaker ECAPA-TDNN',
                          'verified':'SpeakerBeam + PS4 target-absence verification'}[settings['extractionModel']],
                'targetBufferMs': 1500 if settings['extractionModel']=='verified' else 2000, 'version': '0.11.6'}

    @app.post('/api/voiceprint/reset-learning')
    def reset_learning():
        with server.runner_lock:
            if chain.running or demand.armed or recorder.state()['active'] or diagnostic.state()['active']:
                raise HTTPException(409, 'stop_before_change')
            reset_adaptation(profiles)
            return {'ok': True}

    @app.get('/api/app/startup')
    def startup_options():
        desktop = getattr(app.state,'desktop_status',lambda:{})()
        return {'settings':{key:settings[key] for key in STARTUP_FLAGS},'status':dict(startup_status),'desktop':desktop}

    @app.post('/api/app/start-saved')
    def start_saved():
        with server.runner_lock:
            if recorder.state()['active'] or diagnostic.state()['active']:
                raise HTTPException(409,'stop_before_enroll')
            result = start_saved_processing(server, settings, bool(profiles.current()))
            startup_status.update(status=('waitingForConsumer' if result.get('waitingForConsumer') else 'running') if result.get('ok') else 'waiting',error=result.get('error',''))
            return result

    @app.post('/api/app/background')
    def background():
        if recorder.state()['active'] or diagnostic.state()['active']:
            raise HTTPException(409,'stop_before_enroll')
        handler = getattr(app.state,'background_handler',None)
        if not callable(handler):
            raise HTTPException(409,'native_window_required')
        if handler() is False:
            raise HTTPException(409,'tray_unavailable')
        return {'ok':True}

    @app.post('/api/app/quit')
    def quit_app():
        handler = getattr(app.state,'quit_handler',None)
        if not callable(handler):
            raise HTTPException(409,'native_window_required')
        threading.Timer(.2,handler).start()
        return {'ok':True}

    @app.post('/api/app/show')
    def show_app():
        handler = getattr(app.state,'show_handler',None)
        if not callable(handler):
            raise HTTPException(409,'native_window_required')
        handler()
        return {'ok':True}

    @app.post('/api/app/restart')
    def restart_app():
        if recorder.state()['active'] or diagnostic.state()['active'] or gpu_manager.state()['active']:
            raise HTTPException(409,'stop_before_change')
        handler = getattr(app.state,'restart_handler',None)
        if not callable(handler) or not launcher_path(ROOT).is_file():
            raise HTTPException(409,'installed_launcher_required')
        threading.Timer(.3,handler).start()
        return {'ok':True}

    @app.get('/api/gpu/state')
    def gpu_state():
        return gpu_manager.state()

    @app.post('/api/gpu/check')
    def gpu_check():
        return gpu_manager.check_async()

    @app.post('/api/gpu/install')
    def gpu_install():
        with server.runner_lock:
            if chain.running or recorder.state()['active'] or diagnostic.state()['active']:
                raise HTTPException(409,'stop_before_change')
            try:
                return gpu_manager.install_async()
            except ValueError as error:
                raise HTTPException(409,str(error)) from error

    @app.post('/api/diagnostics/voice')
    def diagnose_voice(body: dict):
        with server.runner_lock:
            if chain.running or recorder.state()['active'] or diagnostic.state()['active']:
                raise HTTPException(409,'stop_before_enroll')
            devices = server.list_devices()
            mic, ref = body.get('mic'), body.get('ref')
            if not any(d['index']==mic for d in devices['capture']) or not any(d['index']==ref for d in devices['loopback']):
                raise HTTPException(422,'select_microphone')
            kind = body.get('kind','single')
            if kind not in ('single','duet'):
                raise HTTPException(422,'invalid_diagnostic')
            if demand.armed:
                demand.stop()
            diagnostic.start(mic,ref,kind=kind,save_audio=body.get('saveAudio') is True,
                             extraction_model=settings['extractionModel'],
                             audio_preferences=dict(settings['audioPreferences']),
                             target_controls=dict(settings['targetControls']))
        return diagnostic.state()

    @app.get('/api/diagnostics/audio/{kind}')
    def diagnostic_audio(kind: str, token: str=''):
        import secrets
        if kind not in ('raw','processed') or not diagnostic.audio_token or not secrets.compare_digest(token,diagnostic.audio_token):
            raise HTTPException(404,'not_found')
        preview = diagnostic.preview_audio.get(kind)
        if preview is not None:
            return Response(preview, media_type='audio/wav', headers={'Cache-Control':'no-store'})
        if diagnostic.audio_directory is None:
            raise HTTPException(404,'not_found')
        return FileResponse(diagnostic.audio_directory/(kind+'.wav'),media_type='audio/wav', headers={'Cache-Control':'no-store'})

    @app.post('/api/studio/settings')
    def save_settings(body: dict):
        with server.runner_lock, settings_lock:
            if any(k in body for k in ('mode', 'processor', 'cleanSampleRate', 'extractionModel', 'targetControls', 'audioPreferences','onDemandMicrophone')):
                if chain.running or demand.armed or recorder.state()['active'] or diagnostic.state()['active']:
                    raise HTTPException(409, 'stop_before_change')
                if 'mode' in body and body['mode'] not in ('clean', 'target'):
                    raise HTTPException(422, 'invalid_mode')
            if 'cleanSampleRate' in body and body['cleanSampleRate'] not in (16000, 48000):
                raise HTTPException(422, 'invalid_sample_rate')
            if 'extractionModel' in body and body['extractionModel'] not in EXTRACTION_MODELS:
                raise HTTPException(422, 'invalid_extraction_model')
            if 'devices' in body:
                if not isinstance(body['devices'], dict) or any(k not in ('mic','ref','out') or not isinstance(v,str) or len(v)>256 for k,v in body['devices'].items()):
                    raise HTTPException(422, 'invalid_devices')
            if 'language' in body and body['language'] not in ('zh-CN', 'en'):
                raise HTTPException(422, 'invalid_language')
            for flag in (*STARTUP_FLAGS, 'adaptiveLearning', 'onDemandMicrophone'):
                if flag in body and type(body[flag]) is not bool:
                    raise HTTPException(422,'invalid_startup_setting')
            if 'audioPreferences' in body:
                try:
                    body['audioPreferences'] = dict(settings['audioPreferences'], **validate_audio_preferences(body['audioPreferences']))
                except ValueError as error:
                    raise HTTPException(422,str(error)) from error
            if 'targetControls' in body:
                try:
                    body['targetControls'] = validate_target_controls(body['targetControls'])
                except ValueError as error:
                    raise HTTPException(422,str(error)) from error
            if 'processor' in body:
                try:
                    model.configure(body['processor'])
                    beam.configure(body['processor'])
                except ValueError as error:
                    raise HTTPException(422,str(error)) from error
            proposed = dict(settings, **{key:body[key] for key in body if key in settings})
            if any(key in body for key in ('startWithWindows','startInBackground','autoStartProcessing')):
                if settings['startWithWindows'] or proposed['startWithWindows'] or 'startWithWindows' in body:
                    try:
                        register_startup(proposed['startWithWindows'], ROOT,
                                         proposed['startInBackground'], proposed['autoStartProcessing'])
                    except (OSError,ValueError) as error:
                        raise HTTPException(409,'startup_registration_failed: '+str(error)) from error
            settings.update({k: body[k] for k in settings if k in body})
            if 'audioPreferences' in body:
                prefs.update(settings['audioPreferences'])
            prefs['trayEnabled'] = settings['closeToTray']
            settings['profileName'] = str(settings['profileName'])[:48]
            DATA.mkdir(exist_ok=True)
            temp = DATA / 'studio-settings.tmp'
            temp.write_text(json.dumps(settings, ensure_ascii=False), 'utf-8')
            os.replace(temp, DATA / 'studio-settings.json')
        return dict(settings)

    @app.post('/api/voiceprint/record')
    def record(body: dict):
        with server.runner_lock:
            if chain.running or diagnostic.state()['active']:
                raise HTTPException(409, 'stop_before_enroll')
            if demand.armed:
                demand.stop()
            try:
                device = int(body['device'])
                # Use the same filtered capture inventory as the audio UI.
                if not any(d['index'] == device for d in server.list_devices()['capture']):
                    raise ValueError('select_microphone')
                recorder.start(device, int(body.get('seconds', 45)), settings['profileName'])
            except (ValueError, KeyError, TypeError) as e:
                raise HTTPException(422, str(e)) from e
        return recorder.state()

    @app.post('/api/voiceprint/cancel')
    def cancel():
        try:
            recorder.cancel()
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return recorder.state()

    @app.post('/api/voiceprint/import')
    async def import_reference(request: Request):
        # WAV only, bounded before decoding; no server-side arbitrary paths.
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > 24 * 1024 * 1024:
                raise HTTPException(413, 'file_too_large')
        with server.runner_lock, recorder.lock:
            if chain.running or recorder.state()['active'] or diagnostic.state()['active']:
                raise HTTPException(409, 'stop_before_enroll')
            if demand.armed:
                demand.stop()
            try:
                with sf.SoundFile(io.BytesIO(chunks)) as f:
                    if f.format not in ('WAV', 'WAVEX') or f.channels > 2 or f.duration > 60.05:
                        raise ValueError('duration_30_60')
                    data, rate = f.read(dtype='float32'), f.samplerate
                recorder.status = 'extracting'
                recorder.error = ''
                # Keep import exclusion until the worker commits or fails.
                def import_work():
                    try:
                        profile = profiles.enroll(data, rate, settings['profileName'])
                        recorder.preview = profile
                        recorder.status = 'complete'
                    except Exception as e:
                        recorder.error = str(e)
                        recorder.status = 'error'
                recorder.thread = threading.Thread(target=import_work, daemon=True)
                recorder.thread.start()
            except Exception as e:
                raise HTTPException(422, str(e)) from e
        return {'ok': True}

    # Expose only for local regression tests and controlled shutdown.
    app.state.voice_profiles = profiles
    app.state.voice_recorder = recorder
    app.state.target_model = model
    app.state.beam_model = beam
    app.state.voice_diagnostic = diagnostic
    app.state.gpu_manager = gpu_manager
    app.state.startup_status = startup_status
