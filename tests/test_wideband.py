import unittest
import numpy as np
from unittest.mock import Mock, patch
from web.wideband import WidebandProcessor, NarrowbandNs
from web.chain import Chain
from web.audio import AudioRunner


class WidebandTests(unittest.TestCase):
    def test_capture_only_never_opens_output_or_monitor(self):
        audio = Mock()
        audio.get_device_info_by_index.return_value = {'defaultSampleRate':48000}
        runner = AudioRunner(Chain(),1,2,0,monitor_idx=3,capture_only=True)
        with patch('web.audio.pa_patch.PyAudio',return_value=audio):
            try:
                runner.start()
                self.assertEqual(audio.open.call_count,2)
                self.assertTrue(all(call.kwargs.get('input') for call in audio.open.call_args_list))
                self.assertFalse(any(call.kwargs.get('output') for call in audio.open.call_args_list))
                self.assertTrue(all(call.kwargs['frames_per_buffer'] == 480 for call in audio.open.call_args_list))
            finally:
                runner.stop()

    def test_native_path_preserves_10khz_tone_without_resampling(self):
        signal = np.rint(5000 * np.sin(np.arange(480) * 2 * np.pi * 10000 / 48000)).astype(np.int16)
        processor = WidebandProcessor('off', False)
        np.testing.assert_array_equal(processor.process(signal, np.zeros_like(signal)), signal)

    def test_aec_and_ns_accept_real_48khz_frames(self):
        processor = WidebandProcessor('aec3', True)
        for _ in range(20):
            result = processor.process(np.zeros(480,np.int16), np.zeros(480,np.int16))
            self.assertEqual(result.shape, (480,))
            self.assertEqual(result.dtype, np.int16)

    def test_audio_runner_uses_native_rate_and_frame_size(self):
        chain = Chain()
        chain.sample_rate = 48000
        chain.engine = 'off'
        chain.ns_backend = 'off'
        self.assertTrue(chain.start())
        try:
            runner = AudioRunner(chain, 1, 2, 3)
            self.assertEqual(runner.sample_rate, 48000)
            self.assertEqual(runner.frame_size, 480)
            self.assertEqual(chain.state()['sampleRate'],48000)
            self.assertEqual(chain.process_frame(np.zeros(480,np.int16),np.zeros(480,np.int16)).shape,(480,))
        finally:
            chain.stop()

    def test_narrowband_ns_bridges_128_frames_without_length_drift(self):
        processor = NarrowbandNs()
        for _ in range(100):
            self.assertEqual(processor.process(np.zeros(128,np.float32)).shape,(128,))


if __name__ == '__main__':
    unittest.main()
