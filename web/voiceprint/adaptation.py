"""Conservative local adaptation of the verification cue, anchored to enrollment."""
import json
import os
import time
from pathlib import Path
import numpy as np


class VoiceAdaptation:
    def __init__(self, profiles, enabled):
        self.base = np.asarray(profiles.embedding(), np.float32).copy()
        self.scale = float(np.linalg.norm(self.base))
        self.anchor = self.base / self.scale
        self.vector = self.base.copy()
        self.path = Path(profiles.directory) / profiles.current()['id'] / 'adaptation.json'
        self.enabled = enabled
        self.updates = 0
        self.error = ''
        self.next_check = time.monotonic() + 5.
        self.last_update = 0.
        try:
            saved = json.loads(self.path.read_text('utf-8'))
            vector = np.asarray(saved['vector'], np.float32)
            if vector.shape == (192,) and np.isfinite(vector).all():
                norm = float(np.linalg.norm(vector))
                if norm > 1e-8 and float(vector @ self.anchor / norm) >= .98:
                    self.vector = vector / norm * self.scale
                    self.updates = max(0, int(saved['updates']))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def reference(self):
        return self.vector if self.enabled() else self.base

    def observe(self, audio, output_vector, speech_ms, verifier):
        if not self.enabled() or speech_ms < 1000:
            return
        now = time.monotonic()
        if now < self.next_check:
            return
        try:
            output = self._unit(output_vector)
            if float(output @ self.anchor) < .65:
                return
            self.next_check = now + 30.
            samples = np.asarray(audio, np.float32)
            if np.mean(np.abs(samples) >= .995) > .001:
                return
            raw = self._unit(verifier.embed(samples))
            if float(raw @ self.anchor) < .6 or float(raw @ output) < .94:
                return
            candidate = self._unit(self.vector / self.scale * .98 + raw * .02)
            if float(candidate @ self.anchor) < .98:
                return
            vector = candidate * self.scale
            payload = {'version': 1, 'vector': vector.tolist(), 'updates': self.updates + 1}
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload), 'utf-8')
            os.replace(temporary, self.path)
            self.vector = vector
            self.updates += 1
            self.last_update = now
            self.error = ''
        except Exception as error:
            self.error = type(error).__name__

    @staticmethod
    def _unit(vector):
        vector = np.asarray(vector, np.float32)
        norm = float(np.linalg.norm(vector))
        if vector.shape != (192,) or not np.isfinite(vector).all() or norm <= 1e-8:
            raise ValueError('invalid_embedding')
        return vector / norm

    def state(self):
        return {'enabled': bool(self.enabled()), 'updates': self.updates, 'error': self.error,
                'anchorSimilarity': round(float(self.vector @ self.anchor / self.scale), 5)}


def reset_adaptation(profiles):
    profile = profiles.current()
    if profile:
        (Path(profiles.directory) / profile['id'] / 'adaptation.json').unlink(missing_ok=True)
