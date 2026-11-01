import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
from web.voiceprint.adaptation import VoiceAdaptation, reset_adaptation


class AdaptationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        (self.directory / 'profile').mkdir()
        self.base = np.zeros(192, np.float32)
        self.base[0] = 4.
        self.profiles = SimpleNamespace(directory=self.directory, embedding=lambda:self.base,
                                        current=lambda:{'id':'profile'})
        self.enabled = True
        self.adaptation = VoiceAdaptation(self.profiles, lambda:self.enabled)
        self.adaptation.next_check = 0.
        self.voice = self.base.copy()
        self.voice[1] = .7
        self.verifier = SimpleNamespace(embed=Mock(return_value=self.voice))
        self.audio = np.full(32000, .02, np.float32)

    def tearDown(self):
        self.temporary.cleanup()

    def observe(self, speech=1500):
        self.adaptation.observe(self.audio, self.voice, speech, self.verifier)

    def test_short_speech_and_paused_learning_do_not_run_extra_inference(self):
        self.observe(400)
        self.enabled = False
        self.observe()
        self.verifier.embed.assert_not_called()
        self.assertFalse(self.adaptation.path.exists())

    def test_trusted_speech_persists_only_vectors_and_preserves_scale(self):
        self.observe()
        self.assertEqual(self.adaptation.updates, 1)
        self.assertAlmostEqual(np.linalg.norm(self.adaptation.vector), 4., places=5)
        self.assertEqual(set(json.loads(self.adaptation.path.read_text())), {'version','vector','updates'})
        np.testing.assert_array_equal(self.profiles.embedding(), self.base)
        reloaded = VoiceAdaptation(self.profiles, lambda:True)
        np.testing.assert_allclose(reloaded.vector, self.adaptation.vector)
        self.enabled = False
        np.testing.assert_array_equal(self.adaptation.reference(), self.base)

    def test_other_speaker_and_mixture_are_rejected(self):
        self.voice[:] = 0
        self.voice[1] = 4
        self.observe()
        self.verifier.embed.assert_not_called()
        self.voice[:] = self.base
        self.verifier.embed.return_value = np.roll(self.base, 1)
        self.adaptation.next_check = 0
        self.observe()
        self.assertEqual(self.adaptation.updates, 0)

    def test_borderline_identity_waits_without_spending_an_extra_check(self):
        self.voice[:] = 0
        self.voice[0] = .64
        self.voice[1] = np.sqrt(1-.64**2)
        self.observe()
        self.verifier.embed.assert_not_called()
        self.assertEqual(self.adaptation.next_check, 0.)

    def test_cooldown_limits_work_and_drift_is_bounded(self):
        self.voice[1] = 2.5
        for index in range(200):
            with patch('web.voiceprint.adaptation.time.monotonic', return_value=index*31.+1):
                self.observe()
                self.observe()
        self.assertLessEqual(self.verifier.embed.call_count, 200)
        self.assertGreaterEqual(self.adaptation.state()['anchorSimilarity'], .98)

    def test_storage_failure_does_not_change_reference_or_raise(self):
        with patch('web.voiceprint.adaptation.os.replace', side_effect=OSError):
            self.observe()
        self.assertEqual(self.adaptation.updates, 0)
        self.assertEqual(self.adaptation.error, 'OSError')
        np.testing.assert_array_equal(self.adaptation.reference(), self.base)

    def test_reset_removes_learning_and_reloads_enrollment(self):
        self.observe()
        reset_adaptation(self.profiles)
        fresh = VoiceAdaptation(self.profiles, lambda:True)
        self.assertEqual(fresh.updates, 0)
        np.testing.assert_array_equal(fresh.reference(), self.base)
