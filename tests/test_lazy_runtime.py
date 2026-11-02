import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from web.voiceprint.model import TargetModel


class LazyRuntimeTests(unittest.TestCase):
    def test_idle_polling_and_clean_processing_do_not_import_neural_runtimes(self):
        script = """
import json
import sys
import numpy as np
from fastapi.testclient import TestClient
from web import server
client = TestClient(server.app)
for attempt in range(10):
    response = client.get('/api/studio/state')
    assert response.status_code == 200
assert response.json()['hardware']['checked'] is False
assert client.post('/api/studio/settings',json={'processor':'cpu'}).status_code == 200
server.chain.sample_rate = 48000
server.chain.ns_backend = 'webrtc'
assert server.chain.set_engine('aec3')
assert server.chain.start()
for attempt in range(10):
    frame = server.chain.process_frame(np.zeros(480,np.int16),np.zeros(480,np.int16))
    assert frame.shape == (480,)
server.chain.stop()
from web.resampling import CaptureResampler
assert CaptureResampler(1)(np.zeros(480,np.float32)).shape == (480,)
heavy = [name for name in ('torch','onnxruntime','ai_edge_litert','scipy') if name in sys.modules]
print(json.dumps({'heavy':heavy}))
"""
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, AEC_STUDIO_DATA_DIR=directory, PYTHONUTF8='1')
            result = subprocess.run([sys.executable, '-c', script],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                capture_output=True, text=True, encoding='utf-8', timeout=60,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]),{'heavy':[]})

    def test_hardware_uses_isolated_probe_report_without_importing_torch(self):
        report = {'ok':True, 'cudaAvailable':True, 'torch':'2.9.1+cu128',
                  'cudaVersion':'12.8', 'gpuName':'Example GPU'}
        model = TargetModel()
        with patch.dict(sys.modules, {'torch':None}):
            actual = model.hardware(report)
        self.assertTrue(actual['checked'])
        self.assertTrue(actual['cudaAvailable'])
        self.assertEqual(actual['gpuName'],'Example GPU')

    def test_cuda_selection_still_rejects_incompatible_runtime(self):
        model = TargetModel()
        with patch.dict(sys.modules, {'torch':None}), \
             patch('web.runtime_env.probe_runtime',return_value={'ok':True,'cudaAvailable':False}):
            with self.assertRaisesRegex(ValueError,'cuda_unavailable'):
                model.configure('cuda')

    def test_dtln_still_loads_and_processes_when_selected(self):
        from web.chain import Chain
        import numpy as np
        chain = Chain(model_dir=str(Path(__file__).resolve().parents[1]/'models'))
        self.assertTrue(chain.set_engine('dtln'))
        self.assertTrue(chain.start(),chain.last_error)
        try:
            self.assertTrue(chain.dtln.ready)
            self.assertTrue(chain.ns.ready)
            result = chain.process_frame(np.zeros(128,np.int16),np.zeros(128,np.int16))
            self.assertEqual(result.shape,(128,))
        finally:
            chain.stop()

    def test_narrowband_webrtc_does_not_construct_unused_dtln_models(self):
        from web.chain import Chain
        import numpy as np
        chain = Chain()
        chain.ns_backend = 'webrtc'
        with patch('web.chain.DtlnAec',side_effect=AssertionError('unused AEC model')), \
             patch('web.chain.DtlnNs',side_effect=AssertionError('unused NS model')):
            self.assertTrue(chain.set_engine('aec3'))
            self.assertTrue(chain.start(),chain.last_error)
            try:
                self.assertIsNone(chain.dtln)
                self.assertIsNone(chain.ns)
                for attempt in range(10):
                    result=chain.process_frame(np.zeros(128,np.int16),np.zeros(128,np.int16))
                    self.assertEqual(result.shape,(128,))
            finally:
                chain.stop()


if __name__ == '__main__':
    unittest.main()
