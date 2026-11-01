"""Paired extractors, with conservative target-absence verification."""
import numpy as np


EXTRACTION_MODELS = ('speakerbeam', 'ps4', 'verified')
TARGET_DEFAULTS = {'suppressionDb':40., 'identityThreshold':.35, 'shortWordProtection':True}


def validate_target_controls(value):
    if not isinstance(value, dict) or any(key not in TARGET_DEFAULTS for key in value):
        raise ValueError('invalid_target_controls')
    result = dict(TARGET_DEFAULTS, **value)
    if type(result['shortWordProtection']) is not bool:
        raise ValueError('invalid_target_controls')
    for key, lower, upper in (('suppressionDb',12,48),('identityThreshold',.2,.55)):
        if type(result[key]) not in (int,float) or not np.isfinite(result[key]) or not lower <= result[key] <= upper:
            raise ValueError('invalid_target_controls')
        result[key] = float(result[key])
    return result


def rms(audio):
    return float(np.sqrt(np.mean(np.asarray(audio, np.float64) ** 2)))


class VerifiedSpeakerBeam:
    supports_overlap = False
    stream_right = 0
    stream_left = 16000
    stream_delay = 24000

    def __init__(self, beam, verifier, reference, adaptation=None, options=None):
        self.beam = beam
        self.adaptation = adaptation
        self.options = validate_target_controls({} if options is None else options)
        self.verifier = verifier
        self.device = beam.device
        self.reference = np.array(reference, dtype=np.float32, copy=True)
        if self.reference.shape != (192,) or not np.isfinite(self.reference).all():
            raise ValueError('invalid_embedding')
        norm = float(np.linalg.norm(self.reference))
        if norm <= 1e-8:
            raise ValueError('invalid_embedding')
        self.reference_unit = self.reference / norm
        self.speaker_similarity = None
        self.presence_ratio = None
        self.suppressed_windows = 0
        self.short_utterance_windows = 0
        self.speech_ms = 0
        self.utterance_span_ms = 0
        self.absence_confirmations = 0
        verifier.load()
        verifier.extract(np.zeros(32000, np.float32), self.reference)
        verifier.embed(np.zeros(32000, np.float32))

    def extract(self, audio, cue):
        if self.adaptation:
            self.reference = self.adaptation.reference().copy()
            self.reference_unit = self.reference / np.linalg.norm(self.reference)
        output = self.beam.extract(audio, cue)
        self.speaker_similarity = None
        self.presence_ratio = None
        self.speech_ms = 0
        self.utterance_span_ms = 0
        input_level = rms(audio)
        if input_level <= .0005 or rms(output) <= .0002:
            self.absence_confirmations = 0
            return output
        length = len(audio)//160*160
        if not length:
            return output
        levels = np.sqrt(np.mean(np.asarray(audio[:length], np.float32).reshape(-1, 160)**2, axis=1))
        threshold = max(.0003, float(np.percentile(levels, 95))*.15)
        active = np.flatnonzero(levels > threshold)
        self.speech_ms = int(len(active)*10)
        self.utterance_span_ms = int((active[-1]-active[0]+1)*10) if len(active) else 0
        if self.options['shortWordProtection'] and self.speech_ms <= 400 and self.utterance_span_ms <= 650:
            self.short_utterance_windows += 1
            self.absence_confirmations = 0
            return output
        vector = np.asarray(self.verifier.embed(output), np.float32)
        norm = float(np.linalg.norm(vector))
        if vector.shape != (192,) or not np.isfinite(vector).all() or norm <= 1e-8:
            raise ValueError('invalid_embedding')
        self.speaker_similarity = float(vector @ self.reference_unit / norm)
        if self.speaker_similarity >= self.options['identityThreshold']:
            self.absence_confirmations = 0
            if self.adaptation:
                self.adaptation.observe(audio, vector, self.speech_ms, self.verifier)
            return output
        target = self.verifier.extract(audio, self.reference)
        self.presence_ratio = rms(target) / input_level
        if not np.isfinite(self.presence_ratio):
            raise ValueError('invalid_model_output')
        if self.presence_ratio < .15:
            self.absence_confirmations += 1
            if self.absence_confirmations >= 2:
                self.suppressed_windows += 1
                return output * np.float32(10**(-self.options['suppressionDb']/20))
        else:
            self.absence_confirmations = 0
        return output

    def verification_state(self):
        return {'adaptation': self.adaptation.state() if self.adaptation else None,
                'speakerSimilarity': self.speaker_similarity,
                'presenceRatio': self.presence_ratio,
                'suppressedWindows': self.suppressed_windows,
                'shortUtteranceWindows': self.short_utterance_windows,
                'speechMs': self.speech_ms,
                'utteranceSpanMs': self.utterance_span_ms,
                'absenceConfirmations': self.absence_confirmations}


def create_extractor(kind, profiles, encoder, beam, adaptation=None, options=None):
    if kind not in EXTRACTION_MODELS:
        raise ValueError('invalid_extraction_model')
    if kind == 'ps4':
        encoder.load()
        return encoder, profiles.embedding()
    cue = beam.condition(profiles)
    if kind == 'verified':
        return VerifiedSpeakerBeam(beam, encoder, profiles.embedding(), adaptation, options), cue
    return beam, cue
