"""PS4/WeSep adapter. The ECAPA and separator MUST share the same checkpoint.

SpeechBrain ECAPA-192 and WeSpeaker ECAPA-192 are different vector spaces:
equal dimensions do not make their embeddings interchangeable.
"""
from pathlib import Path
import hashlib
import threading
import time
import os
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT = ROOT / 'models/ps4/checkpoint_epoch037.pt'
REVISION = '8feb7a1b3da1264659f155c42bfb40a5be88d730'
MODEL_ID = f'PS4-WeSpeaker-ECAPA192:{REVISION}'


class TargetModel:
    def __init__(self, checkpoint=CHECKPOINT):
        self.checkpoint = Path(checkpoint)
        self.model = None
        self.lock = threading.RLock()
        self.inference_ms = 0.0
        self.device = 'cpu'
        self.processor = 'auto'
        self._hardware = None

    def hardware(self, runtime=None):
        if self._hardware is None and runtime and runtime.get('ok'):
            self._hardware = {'cudaAvailable':bool(runtime.get('cudaAvailable')),
                              'torchVersion':runtime.get('torch'), 'cudaVersion':runtime.get('cudaVersion'),
                              'gpuName':runtime.get('gpuName'), 'checked':True}
        if self._hardware is None:
            torch = sys.modules.get('torch')
            if torch is None:
                return {'cudaAvailable':False, 'torchVersion':None, 'cudaVersion':None,
                        'gpuName':None, 'toolkitVersion':None, 'checked':False}
            available = torch.cuda.is_available()
            self._hardware = {'cudaAvailable': available, 'torchVersion': torch.__version__,
                              'cudaVersion': torch.version.cuda,
                              'gpuName': torch.cuda.get_device_name(0) if available else None, 'checked':True}
            # The installed toolkit and the runtime bundled with PyTorch are independent.
            locations = [Path(os.environ.get('CUDA_PATH', '.')),
                         Path(os.environ.get('LOCALAPPDATA', '.')) / 'NVIDIA/CUDA/v13.4']
            self._hardware['toolkitVersion'] = None
            for location in locations:
                try:
                    import json
                    data = json.loads((location / 'redistribution-manifest.json').read_text('utf-8'))
                    self._hardware['toolkitVersion'] = data['release_label']
                    break
                except (OSError, ValueError, KeyError):
                    pass
        return dict(self._hardware)

    def configure(self, processor):
        if processor not in ('auto', 'cpu', 'cuda'):
            raise ValueError('invalid_processor')
        if processor == 'cuda':
            hardware = self.hardware()
            if hardware.get('checked') is False:
                from ..runtime_env import probe_runtime
                hardware = self.hardware(probe_runtime())
            if not hardware['cudaAvailable']:
                raise ValueError('cuda_unavailable')
        with self.lock:
            if self.processor != processor:
                self.model = None
                self.processor = processor
                torch = sys.modules.get('torch')
                if torch is not None and torch.cuda.is_available():
                    torch.cuda.empty_cache()

    def load(self):
        with self.lock:
            if self.model is not None:
                return
            if not self.checkpoint.exists():
                raise FileNotFoundError('model_missing')
            import torch
            from vendor.ps4.inference import build_model
            torch.set_num_threads(2)
            if self.processor == 'cuda' and not torch.cuda.is_available():
                raise ValueError('cuda_unavailable')
            self.device = ('cuda' if self.processor != 'cpu' and torch.cuda.is_available()
                           else 'cpu')
            m = build_model(torch.device(self.device))
            # Reject partial/mismatched checkpoints; never run untrained layers.
            ckpt = torch.load(self.checkpoint, map_location='cpu', weights_only=True)
            m.load_state_dict(ckpt['model'], strict=True)
            m.eval()
            # Encode enrollment once; forward then consumes the saved ECAPA vector.
            m.joint_training = False
            self.torch = torch
            self.model = m

    def embed(self, audio):
        self.load()
        t = self.torch
        with self.lock, t.inference_mode():
            x = t.as_tensor(np.asarray(audio, np.float32)[None], device=self.device)
            features = (self.model.spk_encoder(self.model.preEmphasis(x)) + 1e-8).log()
            features -= features.mean(dim=-1, keepdim=True)
            vector = self.model.spk_model(features.permute(0, 2, 1))
            if isinstance(vector, tuple):
                vector = vector[-1]
            vector = vector.detach().cpu().numpy().reshape(-1).astype(np.float32)
        if vector.shape != (192,) or not np.isfinite(vector).all():
            raise ValueError('invalid_embedding')
        return vector

    def extract(self, audio, embedding):
        self.load()
        x = np.asarray(audio, np.float32)
        v = np.asarray(embedding, np.float32)
        if x.ndim != 1 or len(x) < 512 or v.shape != (192,):
            raise ValueError('invalid_model_input')
        if not np.isfinite(x).all() or not np.isfinite(v).all():
            raise ValueError('invalid_model_input')
        t = self.torch
        started = time.perf_counter()
        with self.lock, t.inference_mode():
            y, _ = self.model(t.as_tensor(x[None], device=self.device),
                              t.as_tensor(v[None], device=self.device))
            result = y[0].detach().cpu().numpy().astype(np.float32)
        self.inference_ms = (time.perf_counter() - started) * 1000
        if result.shape != x.shape or not np.isfinite(result).all():
            raise ValueError('invalid_model_output')
        return result

    def checksum(self):
        with self.checkpoint.open('rb') as f:
            return hashlib.file_digest(f, 'sha256').hexdigest()
