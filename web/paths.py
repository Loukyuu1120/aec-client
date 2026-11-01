"""Writable per-user state for installed builds; source runs keep local state."""
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def data_directory():
    override = os.environ.get('AEC_STUDIO_DATA_DIR')
    if override:
        return Path(override).resolve()
    if os.environ.get('AEC_STUDIO_INSTALLED') == '1':
        local = os.environ.get('LOCALAPPDATA')
        if not local:
            raise RuntimeError('LOCALAPPDATA is required for an installed build')
        return Path(local)/'AECStudio'/'data'
    return ROOT/'data'
