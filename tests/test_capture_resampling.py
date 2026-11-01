import unittest
import numpy as np
from scipy.signal import resample_poly
from web.resampling import CaptureResampler
from web.dsp import LinearResampler


class CaptureResamplingTests(unittest.TestCase):
    def test_stream_matches_enrollment_filter_after_fixed_delay(self):
        signal=np.random.default_rng(4).normal(0,.1,48000).astype(np.float32)
        converter=CaptureResampler(3)
        actual=np.concatenate([converter(signal[i:i+128]) for i in range(0,len(signal),128)])
        expected=resample_poly(signal,1,3)
        # 30 samples at 48 kHz correspond to 10 samples at 16 kHz.
        n=min(len(actual)-10,len(expected))
        np.testing.assert_allclose(actual[10:10+n],expected[:n],atol=1e-6)

    def test_rejects_ultrasonic_signal_instead_of_aliasing_it_into_speech(self):
        signal=(.1*np.sin(2*np.pi*12000*np.arange(48000)/48000)).astype(np.float32)
        old=LinearResampler(3)(signal)[100:]
        new=CaptureResampler(3)(signal)[100:]
        self.assertLess(np.std(new),np.std(old)*.001)

    def test_native_48khz_is_unchanged(self):
        signal=np.random.default_rng(9).normal(size=480).astype(np.float32)
        np.testing.assert_array_equal(CaptureResampler(1)(signal),signal)

    def test_chunking_does_not_change_noninteger_conversion(self):
        signal=np.random.default_rng(8).normal(size=44100).astype(np.float32)
        converter=CaptureResampler(44100/16000)
        chunks=np.concatenate([converter(signal[i:i+128]) for i in range(0,len(signal),128)])
        whole=CaptureResampler(44100/16000)(signal)
        np.testing.assert_allclose(chunks,whole,atol=1e-6)


if __name__=='__main__':unittest.main()
