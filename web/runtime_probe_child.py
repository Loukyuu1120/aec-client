"""Isolated framework compatibility probe used before adopting a runtime."""
import argparse
import json
import os
from pathlib import Path
import sys


parser = argparse.ArgumentParser()
parser.add_argument('--site', type=Path)
args = parser.parse_args()
handles = []
if args.site:
    sys.path.insert(0, str(args.site.resolve()))
    library = args.site/'torch/lib'
    if library.is_dir() and hasattr(os, 'add_dll_directory'):
        handles.append(os.add_dll_directory(str(library)))
try:
    import torch
    import torchaudio
    available = torch.cuda.is_available()
    if available:
        torch.ones(1, device='cuda').sum().cpu()
    report = {'ok': True, 'torch': torch.__version__, 'torchaudio': torchaudio.__version__,
              'cudaVersion': torch.version.cuda, 'cudaAvailable': available,
              'site': str(Path(torch.__file__).resolve().parent.parent),
              'gpuName': torch.cuda.get_device_name(0) if available else None}
except Exception as error:
    report = {'ok': False, 'error': str(error)}
print(json.dumps(report, ensure_ascii=False), flush=True)
