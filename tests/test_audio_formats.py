import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
from web.audio_formats import open_pcm_stream
from web.audio import _find_cable_input, _is_cable_input
from web.voiceprint.recording import EnrollmentRecorder


class AudioFormatTests(unittest.TestCase):
    def backend(self, maximum=2):
        audio = Mock()
        audio.get_device_info_by_index.return_value = {
            'name':'Microphone array', 'maxInputChannels':maximum,
            'maxOutputChannels':maximum, 'defaultSampleRate':48000}
        return audio, SimpleNamespace(paInt16=8)

    def test_stereo_only_capture_downmixes_without_changing_duration(self):
        audio, module = self.backend()
        received = []
        def open_stream(**parameters):
            if parameters['channels'] == 1:
                raise OSError(-9998, 'Invalid number of channels')
            return SimpleNamespace(callback=parameters['stream_callback'])
        audio.open.side_effect = open_stream
        stream, rate, channels = open_pcm_stream(audio, module, 3,
            lambda data, frames, timing, status: (received.append(data), 0), capture=True)
        stream.callback(np.array([[30000, 30000], [1000, 3000]], np.int16).tobytes(), 2, {}, 0)
        np.testing.assert_array_equal(np.frombuffer(received[0], np.int16), [30000, 2000])
        self.assertEqual((rate, channels), (48000, 2))

    def test_stereo_output_duplicates_mono_in_frame_order(self):
        audio, module = self.backend()
        def open_stream(**parameters):
            if parameters['channels'] == 1:
                raise OSError(-9998, 'Invalid number of channels')
            return SimpleNamespace(callback=parameters['stream_callback'])
        audio.open.side_effect = open_stream
        stream, rate, channels = open_pcm_stream(audio, module, 3,
            lambda *args: (np.array([100, -200], np.int16).tobytes(), 0), capture=False)
        data, status = stream.callback(None, 2, {}, 0)
        np.testing.assert_array_equal(np.frombuffer(data, np.int16), [100, 100, -200, -200])

    def test_default_rate_can_fall_back_to_supported_rate(self):
        audio, module = self.backend()
        audio.get_device_info_by_index.return_value['defaultSampleRate'] = 96000
        def open_stream(**parameters):
            if parameters['rate'] != 48000:
                raise ValueError('Invalid sample rate', -9997)
            return Mock()
        audio.open.side_effect = open_stream
        stream, rate, channels = open_pcm_stream(audio, module, 3, Mock(), capture=True)
        self.assertEqual(rate, 48000)

    def test_access_and_device_errors_do_not_retry_other_formats(self):
        audio, module = self.backend()
        audio.open.side_effect = OSError(-9985, 'Device unavailable')
        with self.assertRaises(OSError):
            open_pcm_stream(audio, module, 3, Mock(), capture=True)
        self.assertEqual(audio.open.call_count, 1)

    def test_stereo_enrollment_preserves_real_sample_count(self):
        audio, module = self.backend()
        source = (np.sin(np.arange(30 * 48000) * .021) * 8000).astype(np.int16)
        stream = Mock()
        def open_stream(**parameters):
            if parameters['channels'] == 1:
                raise OSError(-9998, 'Invalid number of channels')
            stereo = np.repeat(source[:, None], 2, axis=1)
            stream.start_stream.side_effect = lambda: parameters['stream_callback'](stereo.tobytes(), len(source), {}, 0)
            return stream
        audio.open.side_effect = open_stream
        profiles = SimpleNamespace(model=SimpleNamespace(load=lambda:None), enroll=Mock())
        recorder = EnrollmentRecorder(profiles)
        with patch('pyaudiowpatch.PyAudio', return_value=audio):
            recorder._record(3, 30, 'Test')
        self.assertEqual(recorder.status, 'complete', recorder.error)
        recorded, rate, name = profiles.enroll.call_args.args
        np.testing.assert_array_equal(recorded, source.astype(np.float32) / 32768)
        self.assertEqual(rate, 48000)

    def test_localized_standard_cable_wins_over_sixteen_channel_port(self):
        devices = [{'index':7, 'name':'CABLE In 16 Ch (VB-Audio Virtual Cable)'},
                   {'index':8, 'name':'扬声器 (VB-Audio Virtual Cable)'}]
        self.assertEqual(_find_cable_input(devices), 8)
        self.assertIsNone(_find_cable_input(devices[:1]))
        self.assertTrue(_is_cable_input('Speakers (VB-Audio Virtual Cable)'))
        self.assertFalse(_is_cable_input('CABLE Output (VB-Audio Virtual Cable)'))


if __name__ == '__main__':
    unittest.main()
