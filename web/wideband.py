"""Native 48 kHz clean calls, with no unprocessed high-frequency splice."""
import numpy as np
from pywebrtc_audio import EchoCanceller, NoiseSuppressor


class WidebandProcessor:
    def __init__(self, engine='aec3', noise=True):
        if engine not in ('aec3', 'off'):
            raise ValueError('wideband_requires_aec3')
        self.echo = EchoCanceller(sample_rate=48000) if engine == 'aec3' else None
        self.noise = NoiseSuppressor(sample_rate=48000, level=1) if noise else None

    def process(self, mic, ref, gain=1.):
        x = np.asarray(mic, np.float32) / 32768. * gain
        r = np.asarray(ref, np.float32) / 32768.
        if len(x) != 480 or r.shape != x.shape:
            raise ValueError('wideband_frame_must_be_10ms')
        if self.echo is not None:
            x = self.echo.process(x, r)
        if self.noise is not None:
            x = self.noise.process(x)
        return np.clip(np.rint(x * 32768.), -32768, 32767).astype(np.int16)


class NarrowbandNs:
    """Bridge the legacy 128-sample pump to exact WebRTC 160-sample frames."""
    def __init__(self):
        self.ns = NoiseSuppressor(sample_rate=16000, level=1)
        self.pending = np.empty(0, np.float32)
        self.ready = np.zeros(160, np.float32)

    def process(self, frame):
        self.pending = np.concatenate((self.pending, frame))
        while len(self.pending) >= 160:
            block, self.pending = self.pending[:160], self.pending[160:]
            self.ready = np.concatenate((self.ready, self.ns.process(block)))
        out, self.ready = self.ready[:len(frame)].copy(), self.ready[len(frame):]
        return out
