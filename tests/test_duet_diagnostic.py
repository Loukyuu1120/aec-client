import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from fastapi.testclient import TestClient
from web import server
from web.voiceprint.diagnostic import VoiceDiagnostic, short_drop_metrics
from web.voiceprint.extraction import VerifiedSpeakerBeam


class DuetTests(unittest.TestCase):
    def test_short_drop_detects_two_hundred_ms_gaps(self):
        source=np.full(16000,.01,np.float32)
        output=source.copy()
        output[3200:9600]=0
        self.assertEqual(short_drop_metrics(source,output),{'voicedFrames':5,'weakFrames':2,'longestWeakMs':400})
        self.assertEqual(short_drop_metrics(source,source)['weakFrames'],0)

    def test_duet_defaults_to_no_recording_and_prompts_each_speaker(self):
        with tempfile.TemporaryDirectory() as directory:
            d=VoiceDiagnostic(Mock(),Mock(),Mock(),Path(directory))
            with patch('web.voiceprint.diagnostic.threading.Thread') as thread:
                thread.return_value.is_alive.return_value=False
                d.start(1,2,kind='duet')
            self.assertFalse(d.save_audio)
            self.assertEqual(d.duration,36)
            for elapsed,phase in [(0,'you'),(12.1,'other'),(24.1,'both')]:
                d.elapsed=elapsed
                self.assertEqual(d.state()['phase'],phase)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_saved_audio_requires_test_token_and_known_kind(self):
        client=TestClient(server.app)
        d=server.app.state.voice_diagnostic
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            (p/'raw.wav').write_bytes(b'RIFFtest')
            with patch.object(d,'audio_directory',p),patch.object(d,'audio_token','test-token'):
                self.assertEqual(client.get('/api/diagnostics/audio/raw').status_code,404)
                self.assertEqual(client.get('/api/diagnostics/audio/raw?token=wrong').status_code,404)
                self.assertEqual(client.get('/api/diagnostics/audio/private?token=test-token').status_code,404)
                result=client.get('/api/diagnostics/audio/raw?token=test-token')
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.content,b'RIFFtest')

    def test_memory_preview_needs_token_and_is_not_cached_or_written(self):
        client = TestClient(server.app)
        diagnostic = server.app.state.voice_diagnostic
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(diagnostic, 'preview_audio', {'processed':b'RIFF-memory-preview'}), \
                 patch.object(diagnostic, 'audio_token', 'memory-token'), \
                 patch.object(diagnostic, 'audio_directory', None):
                self.assertEqual(client.get('/api/diagnostics/audio/processed?token=wrong').status_code, 404)
                response = client.get('/api/diagnostics/audio/processed?token=memory-token')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b'RIFF-memory-preview')
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.headers['content-type'], 'audio/wav')
                self.assertEqual(client.get('/api/diagnostics/audio/raw?token=memory-token').status_code, 404)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_new_test_clears_previous_memory_preview(self):
        diagnostic = VoiceDiagnostic(Mock(), Mock(), Mock(), Path('unused'))
        diagnostic.preview_audio = {'processed':b'old-test'}
        diagnostic.audio_token = 'old-token'
        with patch('web.voiceprint.diagnostic.threading.Thread') as thread:
            thread.return_value.is_alive.return_value = False
            diagnostic.start(1, 2, kind='duet')
        self.assertEqual(diagnostic.preview_audio, {})
        self.assertIsNone(diagnostic.audio_token)

    def test_diagnostic_uses_selected_model_and_its_paired_reference(self):
        for selected in ('ps4', 'speakerbeam', 'verified'):
            with self.subTest(model=selected), tempfile.TemporaryDirectory() as directory:
                reference = np.full(192, 2, np.float32)
                beam_reference = np.full(512, 3, np.float32)
                profiles = Mock()
                profiles.embedding.return_value = reference
                encoder = Mock(checkpoint=Path(directory)/'models/ps4/model.pt', device='cuda')
                beam = Mock(device='cuda')
                beam.condition.return_value = beam_reference
                diagnostic = VoiceDiagnostic(profiles, encoder, beam, Path(directory))
                diagnostic.extraction_model = selected
                chain = Mock()

                def initialize_stream():
                    chain.target_stream = chain.target_factory()
                    raise RuntimeError('stop_before_capture')

                chain.start.side_effect = initialize_stream
                with patch('web.voiceprint.diagnostic.Chain', return_value=chain), \
                        patch('web.voiceprint.diagnostic.TargetStream') as stream, \
                        patch('web.voiceprint.diagnostic.AudioRunner') as runner:
                    diagnostic._run(1, 2)
                self.assertEqual(diagnostic.error, 'stop_before_capture')
                stream.assert_called_once()
                model, cue = stream.call_args.args
                if selected == 'verified':
                    self.assertIsInstance(model, VerifiedSpeakerBeam)
                    self.assertIs(model.beam, beam)
                    self.assertIs(model.verifier, encoder)
                    np.testing.assert_array_equal(model.reference, reference)
                else:
                    self.assertIs(model, encoder if selected == 'ps4' else beam)
                np.testing.assert_array_equal(cue, reference if selected == 'ps4' else beam_reference)
                np.testing.assert_array_equal(reference, np.full(192, 2, np.float32))
                if selected == 'ps4':
                    beam.condition.assert_not_called()
                runner.assert_not_called()
                chain.stop.assert_called_once()

    def test_invalid_diagnostic_model_does_not_start_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            diagnostic = VoiceDiagnostic(Mock(), Mock(), Mock(), Path(directory))
            with patch('web.voiceprint.diagnostic.threading.Thread') as thread:
                with self.assertRaisesRegex(ValueError, 'invalid_extraction_model'):
                    diagnostic.start(1, 2, extraction_model='invalid')
            thread.assert_not_called()
            self.assertEqual(diagnostic.status, 'idle')

    def test_selected_audio_parameters_reach_the_real_chain_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            encoder=Mock(checkpoint=Path(directory)/'models/ps4/model.pt',device='cpu')
            profiles=Mock()
            profiles.embedding.return_value=np.ones(192,np.float32)
            diagnostic=VoiceDiagnostic(profiles,encoder,Mock(),Path(directory))
            with patch('web.voiceprint.diagnostic.threading.Thread') as thread:
                thread.return_value.is_alive.return_value=False
                diagnostic.start(1,2,extraction_model='ps4',audio_preferences={'profile':3,'nsBackend':'off','micGain':1.5},target_controls={'suppressionDb':24})
            chain=Mock()
            chain.start.side_effect=RuntimeError('stop_before_capture')
            with patch('web.voiceprint.diagnostic.Chain',return_value=chain),patch('web.voiceprint.diagnostic.AudioRunner') as runner:
                diagnostic._run(1,2)
            chain.set_engine.assert_called_once_with('off')
            chain.set_ns_backend.assert_called_once_with('off')
            self.assertEqual(chain.mic_gain,1.5)
            self.assertEqual(diagnostic.target_controls['suppressionDb'],24)
            self.assertEqual(diagnostic.error,'stop_before_capture')
            runner.assert_not_called()


if __name__=='__main__':unittest.main()
