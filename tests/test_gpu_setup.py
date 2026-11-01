import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from web.gpu_runtime import GpuRuntimeManager


READY={'ok':True,'cudaAvailable':True,'cudaVersion':'12.8','torch':'2.9.1+cu128','torchaudio':'2.9.1+cu128'}
CPU={'ok':True,'cudaAvailable':False,'torch':'2.9.1+cpu','torchaudio':'2.9.1+cpu'}


class GpuSetupTests(unittest.TestCase):
    def test_working_gpu_runtime_never_downloads_or_reinstalls(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=GpuRuntimeManager(directory,Path(directory)/'data')
            with patch('web.gpu_runtime.probe_runtime',return_value=READY), \
                 patch('web.gpu_runtime.driver_information',return_value={'available':True}), \
                 patch.object(manager,'_download') as download,patch.object(manager,'_remember') as remember:
                manager._install()
            self.assertEqual(manager.state()['status'],'ready')
            download.assert_not_called()
            remember.assert_not_called()

    def test_existing_compatible_components_are_reused_without_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=GpuRuntimeManager(directory,Path(directory)/'data')
            existing=Path(directory)/'existing'
            with patch('web.gpu_runtime.probe_runtime',side_effect=[CPU,READY]), \
                 patch('web.gpu_runtime.driver_information',return_value={'available':True}), \
                 patch('web.gpu_runtime.candidate_sites',return_value=[existing]), \
                 patch.object(manager,'_download') as download:
                manager._install()
            self.assertEqual(manager.state()['status'],'restart_required')
            pointer=json.loads((manager.data/'gpu-runtime.json').read_text())
            self.assertEqual(pointer['site'],str(existing.resolve()))
            download.assert_not_called()

    def test_missing_driver_produces_guidance_instead_of_installing_cuda(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=GpuRuntimeManager(directory,Path(directory)/'data')
            with patch('web.gpu_runtime.probe_runtime',return_value=CPU), \
                 patch('web.gpu_runtime.driver_information',return_value={'available':False}), \
                 patch.object(manager,'_download') as download:
                manager._install()
            self.assertEqual(manager.state()['status'],'driver_required')
            download.assert_not_called()

    def test_failed_download_hash_is_never_activated(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=GpuRuntimeManager(directory,Path(directory)/'data')
            response=io.BytesIO(b'not a trusted package')
            response.headers={'Content-Length':'21'}
            with patch('web.gpu_runtime.urllib.request.urlopen',return_value=response):
                with self.assertRaisesRegex(ValueError,'checksum'):
                    manager._download('test.whl','https://example.invalid/test.whl','0'*64,0)
            self.assertFalse((manager.cache/'test.whl').exists())

    def test_gpu_package_cannot_extract_outside_its_staging_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel=Path(directory)/'test.whl'
            with zipfile.ZipFile(wheel,'w') as archive:
                archive.writestr('../outside.py','bad')
            target=Path(directory)/'staging'
            target.mkdir()
            with self.assertRaisesRegex(ValueError,'archive_path'):
                GpuRuntimeManager._extract(wheel,target)
            self.assertFalse((Path(directory)/'outside.py').exists())


if __name__=='__main__':
    unittest.main()
