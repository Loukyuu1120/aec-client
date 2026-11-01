import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from web.paths import ROOT, data_directory


class DataPathTests(unittest.TestCase):
    def test_source_build_keeps_existing_local_profiles(self):
        with patch.dict(os.environ, {'AEC_STUDIO_DATA_DIR':'','AEC_STUDIO_INSTALLED':'0'}):
            self.assertEqual(data_directory(), ROOT/'data')

    def test_installed_build_keeps_profiles_outside_program_files(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'AEC_STUDIO_DATA_DIR':'','AEC_STUDIO_INSTALLED':'1','LOCALAPPDATA':directory}):
                self.assertEqual(data_directory(), Path(directory)/'AECStudio'/'data')

    def test_explicit_data_directory_is_respected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'AEC_STUDIO_DATA_DIR':directory,'AEC_STUDIO_INSTALLED':'1'}):
                self.assertEqual(data_directory(), Path(directory).resolve())


if __name__ == '__main__':
    unittest.main()
