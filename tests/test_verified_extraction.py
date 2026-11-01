import unittest
from unittest.mock import Mock

import numpy as np

from web.voiceprint.extraction import VerifiedSpeakerBeam, create_extractor, validate_target_controls


class VerifiedExtractionTests(unittest.TestCase):
    def setUp(self):
        self.reference = np.full(192, 2, np.float32)
        self.cue = np.full(512, 3, np.float32)
        self.source = (.02*np.sin(np.arange(32000)*.031)).astype(np.float32)
        self.output = self.source*.8
        self.beam = Mock(device='cuda')
        self.beam.extract.return_value = self.output
        self.verifier = Mock()
        self.model = VerifiedSpeakerBeam(self.beam, self.verifier, self.reference)
        self.verifier.reset_mock()

    def test_matching_target_is_preserved_without_secondary_suppression(self):
        self.verifier.embed.return_value = self.reference
        output = self.model.extract(self.source, self.cue)
        np.testing.assert_array_equal(output, self.output)
        self.verifier.extract.assert_not_called()
        self.assertEqual(self.model.verification_state()['suppressedWindows'], 0)

    def test_configured_attenuation_changes_only_confirmed_unwanted_output(self):
        options={'suppressionDb':12}
        model=VerifiedSpeakerBeam(self.beam,self.verifier,self.reference,options=options)
        options['suppressionDb']=48
        self.verifier.embed.return_value=-self.reference
        self.verifier.extract.return_value=self.source*.02
        np.testing.assert_array_equal(model.extract(self.source,self.cue),self.output)
        np.testing.assert_allclose(model.extract(self.source,self.cue),self.output*10**(-12/20),rtol=1e-6)

    def test_identity_threshold_changes_when_secondary_verification_is_requested(self):
        unit=self.reference/np.linalg.norm(self.reference)
        orthogonal=np.zeros(192,np.float32)
        orthogonal[:2]=[1/np.sqrt(2),-1/np.sqrt(2)]
        vector=unit*.45+orthogonal*np.sqrt(1-.45**2)
        self.verifier.embed.return_value=vector
        self.model.extract(self.source,self.cue)
        self.verifier.extract.assert_not_called()
        strict=VerifiedSpeakerBeam(self.beam,self.verifier,self.reference,options={'identityThreshold':.5})
        self.verifier.reset_mock()
        self.verifier.embed.return_value=vector
        self.verifier.extract.return_value=self.source*.02
        strict.extract(self.source,self.cue)
        self.verifier.extract.assert_called_once()

    def test_disabling_short_word_protection_runs_identity_checks(self):
        model=VerifiedSpeakerBeam(self.beam,self.verifier,self.reference,options={'shortWordProtection':False})
        source=np.zeros(24000,np.float32)
        source[8000:11200]=self.source[:3200]
        self.beam.extract.return_value=source*.8
        self.verifier.reset_mock()
        self.verifier.embed.return_value=-self.reference
        self.verifier.extract.return_value=np.zeros_like(source)
        model.extract(source,self.cue)
        output=model.extract(source,self.cue)
        np.testing.assert_allclose(output,source*.008,atol=1e-8)
        self.assertEqual(self.verifier.embed.call_count,2)

    def test_invalid_controls_are_rejected(self):
        for value in (None,[],{'suppressionDb':True},{'suppressionDb':float('nan')},
                      {'suppressionDb':60},{'identityThreshold':.9},{'shortWordProtection':1},{'unknown':3}):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'invalid_target_controls'):
                validate_target_controls(value)

    def test_other_voice_requires_both_identity_and_absence_evidence(self):
        self.verifier.embed.return_value = -self.reference
        self.verifier.extract.return_value = self.source*.02
        first = self.model.extract(self.source, self.cue)
        np.testing.assert_array_equal(first, self.output)
        output = self.model.extract(self.source, self.cue)
        np.testing.assert_allclose(output, self.output*.01)
        self.assertEqual(self.model.verification_state()['suppressedWindows'], 1)
        recorded_source, recorded_reference = self.verifier.extract.call_args.args
        np.testing.assert_array_equal(recorded_source, self.source)
        np.testing.assert_array_equal(recorded_reference, self.reference)

    def test_uncertain_identity_keeps_target_when_separator_detects_presence(self):
        self.verifier.embed.return_value = -self.reference
        self.verifier.extract.return_value = self.source*.3
        output = self.model.extract(self.source, self.cue)
        np.testing.assert_array_equal(output, self.output)
        self.assertEqual(self.model.verification_state()['suppressedWindows'], 0)

    def test_quiet_speech_is_not_muted_by_a_level_gate(self):
        source = self.source*.01
        self.beam.extract.return_value = source.copy()
        output = self.model.extract(source, self.cue)
        np.testing.assert_array_equal(output, source)
        self.verifier.embed.assert_not_called()
        self.verifier.extract.assert_not_called()

    def test_verification_failure_never_returns_unverified_audio(self):
        self.verifier.embed.side_effect = RuntimeError('verification failed')
        with self.assertRaisesRegex(RuntimeError, 'verification failed'):
            self.model.extract(self.source, self.cue)

    def test_brief_utterance_is_not_rejected_for_insufficient_identity(self):
        source = np.zeros(24000, np.float32)
        source[8000:11200] = self.source[:3200]
        self.beam.extract.return_value = source*.8
        self.verifier.embed.return_value = -self.reference
        self.verifier.extract.return_value = np.zeros_like(source)
        output = self.model.extract(source, self.cue)
        np.testing.assert_array_equal(output, source*.8)
        self.verifier.embed.assert_not_called()
        self.verifier.extract.assert_not_called()
        self.assertEqual(self.model.verification_state()['shortUtteranceWindows'], 1)

    def test_spaced_other_speaker_words_do_not_count_as_one_brief_utterance(self):
        source = np.zeros(24000, np.float32)
        source[1600:3200] = self.source[:1600]
        source[16000:17600] = self.source[:1600]
        self.beam.extract.return_value = source*.8
        self.verifier.embed.return_value = -self.reference
        self.verifier.extract.return_value = np.zeros_like(source)
        self.model.extract(source, self.cue)
        output = self.model.extract(source, self.cue)
        np.testing.assert_allclose(output, source*.008, atol=1e-8)
        self.assertEqual(self.model.verification_state()['suppressedWindows'], 1)

    def test_a_positive_voice_check_resets_absence_confirmation(self):
        self.verifier.embed.return_value = -self.reference
        self.verifier.extract.return_value = self.source*.02
        self.model.extract(self.source, self.cue)
        self.verifier.embed.return_value = self.reference
        self.model.extract(self.source, self.cue)
        self.verifier.embed.return_value = -self.reference
        output = self.model.extract(self.source, self.cue)
        np.testing.assert_array_equal(output, self.output)
        self.assertEqual(self.model.verification_state()['suppressedWindows'], 0)

    def test_paired_embeddings_are_never_interchanged(self):
        profiles = Mock()
        profiles.embedding.return_value = self.reference
        self.beam.condition.return_value = self.cue
        model, cue = create_extractor('verified', profiles, self.verifier, self.beam)
        self.assertIsInstance(model, VerifiedSpeakerBeam)
        np.testing.assert_array_equal(cue, self.cue)
        np.testing.assert_array_equal(model.reference, self.reference)
        self.verifier.embed.return_value = self.reference
        model.extract(self.source, cue)
        passed_source, passed_cue = self.beam.extract.call_args.args
        np.testing.assert_array_equal(passed_source, self.source)
        np.testing.assert_array_equal(passed_cue, self.cue)
        np.testing.assert_array_equal(self.reference, np.full(192, 2, np.float32))


if __name__ == '__main__':
    unittest.main()
