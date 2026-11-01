"""Paired TD-SpeakerBeam inference. Never substitute ECAPA vectors for its cue.

The existing ECAPA enrollment remains intact. This extractor derives its own
512-dimensional learned conditioning vector from the SAME saved reference WAV.
"""
from pathlib import Path
import threading
import time
import numpy as np
import soundfile as sf
from .model import ROOT

CHECKPOINT = ROOT / 'models/speakerbeam/model.ckpt'
REVISION = '05b6c13640c95a9995b51ee5bc01ce86ad239834'
REPOSITORY = 'wyz/librimix_tse_clean_2mix_training_td_speakerbeam_16k'
FILENAME = 'exp/training_td_speakerbeam_16k/epoch92_step12347611_valid.loss.ckpt'


def build_model():
    import torch
    from vendor.speakerbeam.tcn import TemporalConvNet, TemporalConvNetInformed
    nn = torch.nn

    class Beam(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = nn.Module()
            self.encoder.conv1d = nn.Conv1d(1,256,32,stride=16,bias=False)
            self.decoder = nn.Module()
            self.decoder.convtrans1d = nn.ConvTranspose1d(256,1,32,stride=16,bias=False)
            self.extractor = nn.Module()
            common = dict(N=256,B=256,H=512,P=3,X=8,Sc=256,norm_type='gLN',
                          causal=False,pre_mask_nonlinear='prelu',mask_nonlinear='linear')
            self.extractor.tcn = TemporalConvNetInformed(**common,R=4,out_channel=None,
                i_adapt_layer=7,adapt_layer_type='mul',adapt_enroll_dim=256)
            self.extractor.auxiliary_net = TemporalConvNet(**common,R=1,C=1,out_channel=512)

        def embed(self, x):
            features = torch.relu(self.encoder.conv1d(x[:,None]))
            return self.extractor.auxiliary_net(features).squeeze(1).mean(-1)

        def forward(self, x, cue):
            features = torch.relu(self.encoder.conv1d(x[:,None]))
            mask = self.extractor.tcn(features, cue)
            return self.decoder.convtrans1d(features*mask,output_size=(x.shape[0],1,x.shape[-1])).squeeze(1)

    return Beam()


class SpeakerBeamModel:
    # CUDA uses overlapping windows. CPU keeps the longer hop to stay within
    # its compute budget on this machine; it must not promise CUDA throughput.
    supports_overlap = False

    def __init__(self):
        self.processor = 'auto'
        self.device = 'cpu'
        self.model = None
        self.lock = threading.RLock()
        self.inference_ms = 0.
        self._profile_id = None
        self._cue = None

    def configure(self, processor):
        if processor not in ('auto','cpu','cuda'):
            raise ValueError('invalid_processor')
        with self.lock:
            if self.processor != processor:
                self.model = None
                self.processor = processor

    def load(self):
        with self.lock:
            if self.model is not None:
                return
            if not CHECKPOINT.exists():
                raise FileNotFoundError('model_missing')
            import torch
            if self.processor == 'cuda' and not torch.cuda.is_available():
                raise ValueError('cuda_unavailable')
            self.device = 'cuda' if self.processor != 'cpu' and torch.cuda.is_available() else 'cpu'
            torch.set_num_threads(2)
            model = build_model()
            weights = torch.load(CHECKPOINT,map_location='cpu',weights_only=True)['state_dict']
            model.load_state_dict(weights,strict=True)
            self.model = model.to(self.device).eval()
            self.torch = torch

    def condition(self, profiles):
        profile = profiles.current()
        if not profile:
            raise ValueError('enrollment_required')
        self.load()
        with self.lock:
            if self._profile_id != profile['id']:
                audio, rate = sf.read(profiles.directory/profile['id']/'reference.wav',dtype='float32')
                if rate != 16000 or audio.ndim != 1:
                    raise ValueError('invalid_audio')
                with self.torch.inference_mode():
                    reference = self.torch.tensor(audio[None,:15*16000],device=self.device)
                    self._cue = self.model.embed(reference)[0].cpu().numpy()
                self._profile_id = profile['id']
            return self._cue.copy()

    def extract(self, audio, embedding):
        self.load()
        x, v = np.asarray(audio,np.float32), np.asarray(embedding,np.float32)
        if x.ndim != 1 or len(x) < 32 or v.shape != (512,) or not np.isfinite(x).all() or not np.isfinite(v).all():
            raise ValueError('invalid_model_input')
        started = time.perf_counter()
        with self.lock, self.torch.inference_mode():
            output = self.model(self.torch.tensor(x[None],device=self.device),
                                self.torch.tensor(v[None],device=self.device))[0].cpu().numpy()
        self.inference_ms = (time.perf_counter()-started)*1000
        if output.shape != x.shape or not np.isfinite(output).all():
            raise ValueError('invalid_model_output')
        return output
