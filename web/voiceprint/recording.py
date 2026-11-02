"""Explicitly started microphone-only enrollment. Never opens an output stream."""
import threading
import time
import queue
import numpy as np
from .profiles import prepare_audio


class EnrollmentRecorder:
    def __init__(self, profiles):
        self.profiles = profiles
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.thread = None
        self.status = 'idle'
        self.seconds = 0
        self.elapsed = 0.
        self.error = ''
        self.level = 0.
        self.preview = None
        self.device_name = ''
        self.peak = 0.
        self.speech_seconds = 0.

    def state(self):
        return {'status': self.status, 'seconds': self.seconds, 'elapsed': round(self.elapsed, 1),
                'error': self.error, 'level': float(self.level), 'quality': self.preview,
                'deviceName': self.device_name, 'peak': float(self.peak),
                'speechSeconds': round(self.speech_seconds, 1),
                'active': bool(self.thread and self.thread.is_alive())}

    def start(self, device, seconds, name):
        if seconds not in (30, 45, 60):
            raise ValueError('duration_30_60')
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('enrollment_busy')
            self.cancel_event.clear()
            self.seconds, self.elapsed, self.error = seconds, 0., ''
            self.preview = None
            self.device_name, self.peak, self.speech_seconds = '', 0., 0.
            self.status = 'preparing'
            self.thread = threading.Thread(target=self._record, args=(int(device), seconds, name),
                                           name='voice-enrollment', daemon=True)
            self.thread.start()

    def _record(self, device, seconds, name):
        import pyaudiowpatch as pa
        audio, stream = None, None
        try:
            # Model preparation finishes BEFORE the recording countdown starts.
            self.profiles.model.load()
            if self.cancel_event.is_set():
                self.status = 'cancelled'
                return
            audio = pa.PyAudio()
            info = audio.get_device_info_by_index(device)
            self.device_name = info.get('name', '')
            from ..audio import _is_virtual_cable, _is_pseudo
            if info.get('isLoopbackDevice') or info.get('maxInputChannels', 0) < 1:
                raise ValueError('select_microphone')
            if _is_virtual_cable(self.device_name) or _is_pseudo(self.device_name):
                raise ValueError('select_physical_microphone')
            rate = int(info['defaultSampleRate'])
            captured = queue.Queue(maxsize=512)
            capture_errors = []

            def on_audio(in_data, frame_count, time_info, status):
                try:
                    if status & pa.paInputOverflow:
                        raise ValueError('microphone_overflow')
                    samples = np.frombuffer(in_data, dtype=np.int16).copy()
                    if len(samples) != frame_count:
                        raise ValueError('microphone_format_mismatch')
                    captured.put_nowait(samples)
                except queue.Full:
                    if not capture_errors:
                        capture_errors.append('microphone_overflow')
                except Exception as error:
                    if not capture_errors:
                        capture_errors.append(str(error))
                return None, pa.paContinue

            from ..audio_formats import open_pcm_stream
            stream, rate, channels = open_pcm_stream(audio, pa, device, on_audio,
                                                     capture=True, frames_per_buffer=128)
            self.status = 'recording'
            stream.start_stream()
            chunks, count = [], 0
            last_audio = time.monotonic()
            while count < rate * seconds and not self.cancel_event.is_set():
                if capture_errors:
                    raise ValueError(capture_errors[0])
                try:
                    samples = captured.get(timeout=.25)
                except queue.Empty:
                    if time.monotonic() - last_audio >= 6:
                        raise ValueError('microphone_silent')
                    continue
                last_audio = time.monotonic()
                frame = samples[:rate * seconds - count].astype(np.float32) / 32768
                chunks.append(frame)
                count += len(frame)
                self.elapsed = count / rate
                self.level = np.sqrt(np.mean(frame * frame))
                self.peak = max(self.peak, float(np.max(np.abs(frame))))
                if self.level > .0005:
                    self.speech_seconds += len(frame) / rate
                if self.elapsed >= 6 and self.peak < .0003:
                    raise ValueError('microphone_silent')
            if capture_errors:
                raise ValueError(capture_errors[0])
            stream.stop_stream()
            stream.close()
            stream = None
            if self.cancel_event.is_set():
                self.status = 'cancelled'
                return
            self.status = 'extracting'
            x = np.concatenate(chunks)
            _, self.preview = prepare_audio(x, rate)
            self.profiles.enroll(x, rate, name)
            self.status = 'complete'
        except Exception as e:
            self.error = str(e)
            self.status = 'error'
        finally:
            if stream is not None:
                stream.close()
            if audio is not None:
                audio.terminate()
            self.level = 0.

    def cancel(self):
        # Cancellation is accepted only before extraction commits a profile.
        if self.status in ('extracting', 'complete'):
            raise ValueError('enrollment_finishing')
        self.cancel_event.set()
