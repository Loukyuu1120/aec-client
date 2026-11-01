"""Stateful antialiasing for capture, matching enrollment's polyphase filter.

At the actual 48 -> 16 kHz ratio, the FIR coefficients equal scipy's default
resample_poly coefficients. Causal streaming adds 30 input samples (0.625 ms)
of fixed delay, instead of registration's zero-phase alignment.
"""
import math
import numpy as np
from scipy.signal import firwin, lfilter
from .dsp import LinearResampler


class CaptureResampler:
    def __init__(self, step):
        self.step = float(step)
        self.resampler = LinearResampler(step)
        self.taps = None
        if self.step > 1.:
            half = int(math.ceil(10 * self.step))
            self.taps = firwin(2*half+1,1./self.step,window=('kaiser',5.))
            self.state = np.zeros(len(self.taps)-1)

    def __call__(self, audio):
        x = np.asarray(audio,np.float32)
        if self.taps is not None and x.size:
            x, self.state = lfilter(self.taps,[1.],x,zi=self.state)
            x = x.astype(np.float32)
        return self.resampler(x)
