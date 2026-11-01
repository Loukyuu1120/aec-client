"""User-started local comparison; no output streams or audio files."""
import json
import io
import threading
import time
import secrets
import uuid
import soundfile as sf
from datetime import datetime, timezone
import numpy as np
from ..audio import AudioRunner
from ..chain import Chain, PROFILES
from ..startup import validate_audio_preferences
from .stream import TargetStream, DELAY
from .extraction import EXTRACTION_MODELS, create_extractor, validate_target_controls


def short_drop_metrics(source, extracted):
    """Locate strong 200 ms attenuation during target-only speech, not words."""
    frames = min(len(source),len(extracted))//3200
    if not frames:
        return {'voicedFrames':0,'weakFrames':0,'longestWeakMs':0}
    a = np.asarray(source[:frames*3200]).reshape(frames,3200)
    b = np.asarray(extracted[:frames*3200]).reshape(frames,3200)
    level = np.sqrt(np.mean(a*a,axis=1))
    threshold = max(.0005,float(np.percentile(level,90))*.1)
    voiced = level>threshold
    weak = voiced & (np.sqrt(np.mean(b*b,axis=1)) < level*.15)
    longest = current = 0
    for item in weak:
        current = current+1 if item else 0
        longest = max(longest,current)
    return {'voicedFrames':int(voiced.sum()),'weakFrames':int(weak.sum()),'longestWeakMs':longest*200}


class VoiceDiagnostic:
    def __init__(self, profiles, encoder, beam, data):
        self.profiles, self.encoder, self.beam, self.data = profiles, encoder, beam, data
        self.status = 'idle'
        self.thread = None
        self.elapsed = 0.
        self.countdown = 0
        self.level = 0.
        self.output_level = 0.
        self.error = ''
        self.report = None
        self.kind, self.duration, self.save_audio = 'single', 12, False
        self.extraction_model = 'speakerbeam'
        self.audio_directory, self.audio_token = None, None
        self.preview_audio = {}
        self.audio_preferences = {'profile':1,'nsBackend':'webrtc','micGain':1.0}
        self.target_controls = validate_target_controls({})

    def state(self):
        return {'status':self.status, 'active':bool(self.thread and self.thread.is_alive()),
                'elapsed':round(self.elapsed,1), 'countdown':self.countdown,
                'level':float(self.level), 'outputLevel':float(self.output_level), 'error':self.error, 'report':self.report,
                'kind':self.kind,'duration':self.duration,'extractionModel':self.extraction_model,
                'phase':('you','other','both')[min(2,int(self.elapsed//12))] if self.kind=='duet' else 'you'}

    def start(self, mic, ref, kind='single', save_audio=False, extraction_model='speakerbeam', audio_preferences=None, target_controls=None):
        if self.state()['active']:
            raise ValueError('enrollment_busy')
        if kind not in ('single','duet'):
            raise ValueError('invalid_diagnostic')
        if extraction_model not in EXTRACTION_MODELS:
            raise ValueError('invalid_extraction_model')
        audio_preferences = dict({'profile':1,'nsBackend':'webrtc','micGain':1.0},
                                 **validate_audio_preferences({} if audio_preferences is None else audio_preferences))
        target_controls = validate_target_controls({} if target_controls is None else target_controls)
        self.status, self.error, self.report, self.elapsed = 'preparing', '', None, 0.
        self.level, self.output_level = 0., 0.
        self.extraction_model = extraction_model
        self.audio_preferences = audio_preferences
        self.target_controls = target_controls
        self.kind, self.duration, self.save_audio = kind, 36 if kind=='duet' else 12, bool(save_audio)
        self.audio_directory, self.audio_token = None, None
        self.preview_audio = {}
        self.thread = threading.Thread(target=self._run,args=(mic,ref),daemon=True)
        self.thread.start()

    def _run(self, mic, ref):
        chain, runner = None, None
        try:
            self.encoder.load()
            extractor, cue = create_extractor(self.extraction_model, self.profiles, self.encoder, self.beam, options=self.target_controls)
            reference = self.profiles.embedding()
            reference = reference / (np.linalg.norm(reference)+1e-12)
            chain = Chain(self.encoder.checkpoint.parents[1])
            if not chain.set_engine(PROFILES[self.audio_preferences['profile']][1]):
                raise RuntimeError(chain.last_error)
            if not chain.set_ns_backend(self.audio_preferences['nsBackend']):
                raise RuntimeError(chain.last_error)
            chain.mic_gain = self.audio_preferences['micGain']
            live_target, live_final = [], []
            raw, front, far = [], [], []
            def target_factory():
                stream = TargetStream(extractor,cue)
                original_target = stream.process
                def observe_target(frame):
                    front.append(np.asarray(frame,np.float32).copy())
                    result = original_target(frame)
                    live_target.append(result.copy())
                    return result
                stream.process = observe_target
                return stream
            chain.target_factory = target_factory
            if not chain.start():
                raise RuntimeError(chain.last_error)
            target_delay = chain.target_stream.delay
            original_frame = chain.process_frame
            def observe_final(m,r):
                raw.append(m.astype(np.float32)/32768.)
                far.append(r.astype(np.float32)/32768.)
                result = original_frame(m,r)
                live_final.append(result.astype(np.float32)/32768.)
                return result
            chain.process_frame = observe_final
            self.status = 'countdown'
            for n in (3,2,1):
                self.countdown = n
                time.sleep(1)
            self.countdown = 0
            runner = AudioRunner(chain,mic,ref,0,capture_only=True)
            runner.start()
            self.status = 'capturing'
            begin = time.monotonic()
            while time.monotonic()-begin < self.duration:
                self.elapsed = time.monotonic()-begin
                self.level = runner.in_rms
                self.output_level = chain.stage_levels.get('output', 0.)
                time.sleep(.05)
            runner.stop()
            runner = None
            self.status = 'analyzing'
            captured = sum(len(block) for block in raw)
            # Drain the actual production pipeline in real time, into memory
            # only. This includes delayed output for the last spoken words.
            for _ in range(320):
                chain.process_frame(np.zeros(128,np.int16),np.zeros(128,np.int16))
                time.sleep(.008)
            x, a, r = (np.concatenate(parts)[:captured] for parts in (raw,front,far))
            t = np.concatenate(live_target)[target_delay:target_delay+captured]
            final = np.concatenate(live_final)[target_delay:target_delay+captured]
            templates = None
            if self.kind == 'duet':
                own_sample, other_sample = x[32000:192000], x[224000:384000]
                if min(np.std(own_sample),np.std(other_sample)) > .001:
                    own, other = self.encoder.embed(own_sample), self.encoder.embed(other_sample)
                    templates = (own/(np.linalg.norm(own)+1e-12),other/(np.linalg.norm(other)+1e-12))
            rows = []
            for start in range(0,len(x)-31999,32000):
                before, after = x[start:start+32000], a[start:start+32000]
                if np.std(before)<.001:
                    continue
                rv, av = self.encoder.embed(before), self.encoder.embed(after)
                output = extractor.extract(after,cue)
                row = {'second':start/16000,'rawRms':float(np.std(before)),
                    'finalRms':float(np.std(final[start:start+32000])),
                    'aecRms':float(np.std(after)),'speakerRms':float(np.std(r[start:start+32000])),
                    'rawIdentity':float(rv@reference/(np.linalg.norm(rv)+1e-12)),
                    'aecIdentity':float(av@reference/(np.linalg.norm(av)+1e-12)),
                    'targetRatio':float(np.std(output)/(np.std(after)+1e-12)),
                    'streamTargetRatio':float(np.std(t[start:start+32000])/(np.std(after)+1e-12)),
                    'finalRatio':float(np.std(final[start:start+32000])/(np.std(after)+1e-12))}
                if self.kind == 'duet':
                    row['phase'] = ('you','other','both')[min(2,int(start//192000))]
                    if templates is not None:
                        unit = rv/(np.linalg.norm(rv)+1e-12)
                        row['rawToYou'],row['rawToOther'] = [float(unit@v) for v in templates]
                        result = t[start:start+32000]
                        if np.std(result) > .0002:
                            out_vector = self.encoder.embed(result)
                            out_vector /= np.linalg.norm(out_vector)+1e-12
                            row['outputToYou'],row['outputToOther'] = [float(out_vector@v) for v in templates]
                rows.append(row)
            self.report = {'windows':rows,'samples':len(x),'outputDevicesOpened':0,
                           'insufficientInput':len(rows)<2,'reportVersion':3,
                           'createdUtc':datetime.now(timezone.utc).isoformat(),
                           'resampling':'enrollment-matched-antialias',
                           'profileId':self.profiles.current()['id'],
                           'extractionModel':self.extraction_model,'processor':extractor.device,
                           'targetBufferMs':target_delay/16,
                           'streamError':chain.target_stream.error,'chainError':chain.last_error}
            self.report['parameters'] = {'extractionModel':self.extraction_model,
                'processor':self.encoder.processor, 'sampleRate':16000,
                'audioPreferences':dict(self.audio_preferences), 'targetControls':dict(self.target_controls)}
            self.report['kind'] = self.kind
            self.report['shortDrops'] = short_drop_metrics(a[32000:192000], t[32000:192000])
            if self.kind == 'duet':
                # Ignore the first two seconds of each phase to allow a human
                # to react to the prompt. These are signal metrics, not word accuracy.
                measured = [row for row in rows if row['second']%12>=2]
                own = [row for row in measured if row['phase']=='you']
                other = [row for row in measured if row['phase']=='other']
                together = [row for row in measured if row['phase']=='both' and 'outputToYou'in row]
                self.report['duetSummary'] = {
                    'yourSoloWindows':len(own),'otherSoloWindows':len(other),
                    'yourNearMutedWindows':sum(row['streamTargetRatio']<.15 for row in own),
                    'yourMedianRatio':float(np.median([row['streamTargetRatio'] for row in own])) if own else None,
                    'otherMedianAttenuationDb':float(-20*np.log10(np.median([row['streamTargetRatio']for row in other])+1e-12)) if other else None,
                    'overlapVoicePreference':float(np.median([row['outputToYou']-row['outputToOther']for row in together])) if together else None,
                    'templateCosine':float(templates[0]@templates[1]) if templates is not None else None,
                    'overlapIdentityWindows':len(together)}
                self.report['duetSummary']['yourShortDrops'] = short_drop_metrics(a[32000:192000],t[32000:192000])
            preview = {}
            for name, samples in (('raw', x), ('processed', final)):
                buffer = io.BytesIO()
                sf.write(buffer, samples, 16000, format='WAV', subtype='PCM_16')
                preview[name] = buffer.getvalue()
            self.preview_audio = preview
            self.audio_token = secrets.token_urlsafe(24)
            self.report['audio'] = {name:f'/api/diagnostics/audio/{name}?token={self.audio_token}' for name in preview}
            self.report['audioTemporary'] = not self.save_audio
            if self.save_audio:
                self.audio_directory = self.data/'duet-audio'/str(uuid.uuid4())
                self.audio_directory.mkdir(parents=True)
                sf.write(self.audio_directory/'raw.wav',x,16000,subtype='PCM_16')
                sf.write(self.audio_directory/'processed.wav',final,16000,subtype='PCM_16')
            self.data.mkdir(exist_ok=True)
            (self.data/'last-voice-diagnostic.json').write_text(json.dumps(self.report,indent=2),'utf-8')
            self.status = 'complete'
        except Exception as e:
            self.error, self.status = str(e), 'error'
        finally:
            if runner is not None:
                runner.stop()
            if chain is not None:
                chain.stop()
            self.level = 0.
            self.output_level = 0.
