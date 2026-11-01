"""Transactional, local-only profiles; a failed enrollment preserves the old one."""
from pathlib import Path
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from math import gcd
from .model import MODEL_ID

RATE = 16000


def prepare_audio(audio, rate):
    x = np.asarray(audio, dtype=np.float32)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if x.ndim != 1 or not np.isfinite(x).all():
        raise ValueError('invalid_audio')
    if rate < 8000 or rate > 192000:
        raise ValueError('invalid_sample_rate')
    if not 29.95 <= len(x) / rate <= 60.05:
        raise ValueError('duration_30_60')
    if rate != RATE:
        common = gcd(int(rate), RATE)
        x = resample_poly(x, RATE // common, int(rate) // common).astype(np.float32)
    x = x[:60 * RATE]
    if np.mean(np.abs(x) >= .995) > .01:
        raise ValueError('recording_clipped')
    frames = x[:len(x) // 1600 * 1600].reshape(-1, 1600)
    levels = np.sqrt(np.mean(frames * frames, axis=1))
    # Adaptive floor allows quiet microphones; silence is rejected, never boosted.
    floor = max(.0005, float(np.percentile(levels, 90)) * .12)
    voiced = float(np.count_nonzero(levels > floor) * .1)
    if voiced < 12:
        raise ValueError('insufficient_speech')
    return x, {'duration': len(x) / RATE, 'speechSeconds': round(voiced, 1),
               'rms': float(np.sqrt(np.mean(x * x))), 'peak': float(np.max(np.abs(x)))}


class Profiles:
    def __init__(self, directory, model):
        self.directory = Path(directory)
        self.model = model

    def current(self):
        try:
            ident = json.loads((self.directory / 'active.json').read_text('utf-8'))['id']
            # IDs cannot traverse outside the profile store.
            uuid.UUID(ident)
            profile = json.loads((self.directory / ident / 'profile.json').read_text('utf-8'))
            if profile['modelId'] != MODEL_ID:
                return None
            return profile
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def embedding(self):
        p = self.current()
        if not p:
            raise ValueError('enrollment_required')
        v = np.load(self.directory / p['id'] / 'embedding.npy', allow_pickle=False)
        if v.shape != (192,) or not np.isfinite(v).all():
            raise ValueError('invalid_embedding')
        return v

    def enroll(self, audio, rate, name='我的声音'):
        x, quality = prepare_audio(audio, rate)
        excerpts = [x[i:i+48000] for i in range(0, len(x) - 47999, 48000)
                    if np.sqrt(np.mean(x[i:i+48000] ** 2)) >= .001]
        if len(excerpts) < 4:
            raise ValueError('insufficient_speech')
        vectors = np.stack([self.model.embed(a) for a in excerpts])
        units = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-8)
        center = np.mean(units, axis=0)
        center /= max(float(np.linalg.norm(center)), 1e-8)
        consistency = float(np.median(units @ center))
        if consistency < .55:
            raise ValueError('inconsistent_speaker')
        # The trained separator expects RAW ECAPA scale, not a unit vector.
        vector = np.mean(vectors, axis=0).astype(np.float32)
        profile = {'id': str(uuid.uuid4()), 'name': str(name).strip()[:48] or '我的声音',
                   'modelId': MODEL_ID, 'encoder': 'WeSpeaker ECAPA-TDNN (PS4 paired)',
                   'dimensions': 192, 'created': datetime.now(timezone.utc).isoformat(),
                   'consistency': round(consistency, 4), **quality}
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / profile['id']
        destination.mkdir()
        try:
            sf.write(destination / 'reference.wav', x, RATE, subtype='PCM_16')
            np.save(destination / 'embedding.npy', vector, allow_pickle=False)
            (destination / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2), 'utf-8')
            tmp = self.directory / f'active-{profile["id"]}.tmp'
            tmp.write_text(json.dumps({'id': profile['id']}), 'utf-8')
            os.replace(tmp, self.directory / 'active.json')
        except Exception:
            shutil.rmtree(destination)
            raise
        return profile
