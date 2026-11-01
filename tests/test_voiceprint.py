import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np
from web.voiceprint.model import TargetModel
from web.voiceprint.profiles import Profiles, prepare_audio
from web.voiceprint.stream import TargetStream, HOP, DELAY


class FakeModel:
    device = 'cpu'
    def embed(self, audio):
        return np.full(192, 2, dtype=np.float32)
    def extract(self, audio, embedding):
        return np.asarray(audio) * float(embedding[0])


class EnrollmentTests(unittest.TestCase):
    def test_localized_mapper_is_not_a_microphone(self):
        from web.audio import _is_pseudo
        for name in ('Microsoft 声音映射器 - Input', '主声音捕获驱动程序', 'Microsoft Sound Mapper - Input'):
            self.assertTrue(_is_pseudo(name))
        self.assertFalse(_is_pseudo('麦克风 (USB Condenser Microphone)'))

    def test_silent_microphone_stops_early_without_replacing_profile(self):
        from web.voiceprint.recording import EnrollmentRecorder
        from types import SimpleNamespace
        from unittest.mock import Mock
        stream = Mock()
        audio = Mock()
        audio.get_device_info_by_index.return_value = {'name':'USB Microphone','defaultSampleRate':48000,'maxInputChannels':1}

        def open_capture(**kwargs):
            stream.start_stream.side_effect = lambda: kwargs['stream_callback'](bytes(6*48000*2), 6*48000, {}, 0)
            return stream

        audio.open.side_effect = open_capture
        profiles = SimpleNamespace(model=SimpleNamespace(load=lambda:None), enroll=Mock())
        recorder = EnrollmentRecorder(profiles)
        with patch('pyaudiowpatch.PyAudio', return_value=audio):
            recorder._record(27, 60, 'Test')
        self.assertEqual(recorder.error, 'microphone_silent')
        self.assertLess(recorder.elapsed, 7)
        profiles.enroll.assert_not_called()
        stream.close.assert_called()
        audio.terminate.assert_called()

    def test_enrollment_preserves_callback_samples_and_sample_rate(self):
        from web.voiceprint.recording import EnrollmentRecorder
        from types import SimpleNamespace
        from unittest.mock import Mock
        source = (np.sin(np.arange(30*48000)*.021)*8000).astype(np.int16)
        stream = Mock()
        audio = Mock()
        audio.get_device_info_by_index.return_value = {'name':'USB Microphone','defaultSampleRate':48000,'maxInputChannels':1}

        def open_capture(**kwargs):
            self.assertFalse(kwargs['start'])
            self.assertEqual(kwargs['frames_per_buffer'], 128)
            stream.start_stream.side_effect = lambda: kwargs['stream_callback'](source.tobytes(), len(source), {}, 0)
            return stream

        audio.open.side_effect = open_capture
        profiles = SimpleNamespace(model=SimpleNamespace(load=lambda:None), enroll=Mock())
        recorder = EnrollmentRecorder(profiles)
        with patch('pyaudiowpatch.PyAudio', return_value=audio):
            recorder._record(27, 30, 'Test')
        self.assertEqual(recorder.status, 'complete')
        recorded, rate, name = profiles.enroll.call_args.args
        np.testing.assert_array_equal(recorded, source.astype(np.float32)/32768)
        self.assertEqual((rate, name), (48000, 'Test'))
        stream.read.assert_not_called()
        stream.stop_stream.assert_called_once()
        stream.close.assert_called_once()

    def test_enrollment_rejects_callback_format_mismatch(self):
        from web.voiceprint.recording import EnrollmentRecorder
        from types import SimpleNamespace
        from unittest.mock import Mock
        stream = Mock()
        audio = Mock()
        audio.get_device_info_by_index.return_value = {'name':'USB Microphone','defaultSampleRate':48000,'maxInputChannels':1}

        def open_capture(**kwargs):
            stream.start_stream.side_effect = lambda: kwargs['stream_callback'](bytes(128*4), 128, {}, 0)
            return stream

        audio.open.side_effect = open_capture
        profiles = SimpleNamespace(model=SimpleNamespace(load=lambda:None), enroll=Mock())
        recorder = EnrollmentRecorder(profiles)
        with patch('pyaudiowpatch.PyAudio', return_value=audio):
            recorder._record(27, 30, 'Test')
        self.assertEqual(recorder.error, 'microphone_format_mismatch')
        profiles.enroll.assert_not_called()
        stream.close.assert_called_once()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profiles = Profiles(self.temp.name, FakeModel())
        # Only a deterministic test signal; it never becomes a user profile.
        self.audio = (.05 * np.sin(np.arange(30*16000)*.031)).astype(np.float32)

    def tearDown(self):
        self.temp.cleanup()

    def test_duration_and_silence_rejected(self):
        for x in (np.zeros(30*16000), self.audio[:16000], np.tile(self.audio, 3)):
            with self.assertRaises(ValueError):
                prepare_audio(x, 16000)

    def test_preserves_conditioning_scale_and_reads_local_profile(self):
        profile = self.profiles.enroll(self.audio, 16000, 'Test')
        self.assertEqual(profile['dimensions'], 192)
        self.assertTrue(np.all(self.profiles.embedding() == 2))
        self.assertEqual(self.profiles.current()['id'], profile['id'])

    def test_failed_new_enrollment_preserves_old_profile(self):
        old = self.profiles.enroll(self.audio, 16000)
        with self.assertRaises(ValueError):
            self.profiles.enroll(np.zeros_like(self.audio), 16000)
        self.assertEqual(self.profiles.current()['id'], old['id'])

    def test_saving_failure_does_not_switch_active_profile(self):
        old = self.profiles.enroll(self.audio, 16000)
        with patch('web.voiceprint.profiles.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.profiles.enroll(self.audio, 16000)
        self.assertEqual(self.profiles.current()['id'], old['id'])

    def test_different_encoder_profile_is_not_accepted(self):
        p = self.profiles.enroll(self.audio, 16000)
        path = Path(self.temp.name) / p['id'] / 'profile.json'
        p['modelId'] = 'speechbrain-ecapa-192'
        path.write_text(json.dumps(p), 'utf-8')
        self.assertIsNone(self.profiles.current())

    def test_explicit_gpu_never_silently_falls_back(self):
        model = TargetModel()
        model._hardware = {'cudaAvailable': False}
        with self.assertRaisesRegex(ValueError, 'cuda_unavailable'):
            model.configure('cuda')
        self.assertEqual(model.processor, 'auto')


class StreamTests(unittest.TestCase):
    def test_gpu_overlap_keeps_samples_aligned_and_does_not_fade_each_block(self):
        model = FakeModel()
        model.device = 'cuda'
        stream = TargetStream(model, np.full(192,.5,np.float32))
        signal = (.1 + np.arange(64000,dtype=np.float32) / 1000000)
        feed = np.pad(signal,(0,DELAY+16000))
        output = []
        try:
            for offset in range(0,len(feed),128):
                output.append(stream.process(feed[offset:offset+128]))
                if offset % 2048 == 0:
                    time.sleep(.003)
            result = np.concatenate(output)
            self.assertEqual(stream.error,'')
            self.assertTrue(stream.state()['overlap'])
            np.testing.assert_allclose(result[DELAY+128:DELAY+len(signal)],signal[128:] * .5,rtol=1e-6)
        finally:
            stream.close()

    def test_target_mode_does_not_gate_quiet_verified_output(self):
        from web.chain import Chain
        from types import SimpleNamespace
        c = Chain()
        c.dtln = SimpleNamespace()
        c.running = True
        c.engine = 'off'
        c.ns_backend = 'off'
        c.target_stream = SimpleNamespace(process=lambda x:x)
        quiet = np.full(160, 40, np.int16)
        with patch.object(c, '_hpf_run', side_effect=lambda x:x), patch.object(c, '_pres_run', side_effect=lambda x:x), patch.object(c, '_exp_run', side_effect=AssertionError('quiet target must not be gated')):
            for _ in range(100):
                self.assertTrue(np.all(c.process_frame(quiet, np.zeros_like(quiet)) == quiet))

    def test_target_chain_failure_does_not_return_raw_microphone(self):
        from web.chain import Chain
        from types import SimpleNamespace
        c = Chain()
        c.dtln = SimpleNamespace()
        c.running = True
        c.engine = 'off'
        c.target_stream = SimpleNamespace(process=lambda x: (_ for _ in ()).throw(RuntimeError('failed')))
        self.assertTrue(np.all(c.process_frame(np.full(160,1000,np.int16), np.zeros(160,np.int16)) == 0))

    def test_condition_is_used_and_ordered_after_initial_delay(self):
        stream = TargetStream(FakeModel(), np.ones(192, np.float32) * .5)
        output = []
        try:
            for i in range((DELAY+HOP)//160):
                output.append(stream.process(np.full(160, .2, np.float32)))
                # Give the deterministic worker CPU; no real-time device is opened.
                if (i+1) % 100 == 0:
                    time.sleep(.02)
            y = np.concatenate(output)
            self.assertTrue(np.all(y[:DELAY] == 0))
            self.assertAlmostEqual(float(np.median(y[DELAY:])), .1, places=5)
            self.assertEqual(stream.error, '')
        finally:
            stream.close()

    def test_shorter_delay_preserves_samples_across_a_partial_frame(self):
        model = FakeModel()
        model.device = 'cuda'
        model.stream_right = 0
        model.stream_delay = 24000
        stream = TargetStream(model, np.ones(192, np.float32)*.5)
        output = []
        try:
            for offset in range(0, 40000, 128):
                size = min(128, 40000-offset)
                output.append(stream.process(np.full(size, .2, np.float32)))
                if offset % 5120 == 0:
                    time.sleep(.01)
            result = np.concatenate(output)
            self.assertEqual(len(result), 40000)
            self.assertTrue(np.all(result[:24000] == 0))
            np.testing.assert_allclose(result[24080:], .1, atol=1e-6)
            self.assertEqual(stream.state()['bufferMs'], 1500)
            self.assertEqual(stream.error, '')
        finally:
            stream.close()

    def test_model_failure_never_passes_mixture(self):
        model = FakeModel()
        model.extract = lambda *args: (_ for _ in ()).throw(RuntimeError('bad model'))
        stream = TargetStream(model, np.ones(192, np.float32))
        try:
            for _ in range(100):
                self.assertTrue(np.all(stream.process(np.ones(160, np.float32)) == 0))
            time.sleep(.03)
            self.assertIn('tse_processing_failed', stream.error)
            self.assertTrue(np.all(stream.process(np.ones(160, np.float32)) == 0))
        finally:
            stream.close()


if __name__ == '__main__':
    unittest.main()
