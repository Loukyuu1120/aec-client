"""Read-only installed-runtime smoke test; no microphone streams are opened."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deep', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    report = {'python':sys.version,'applicationRoot':str(root),'executable':sys.executable,'prefix':sys.prefix}
    result = 0
    try:
        import torch
        import torchaudio
        import scipy
        import soundfile
        import webview
        from web.audio import list_devices
        from web.paths import data_directory
        report.update({'torch':torch.__version__,'torchFile':torch.__file__,'torchaudio':torchaudio.__version__,
                       'cudaRuntime':torch.version.cuda,'cudaAvailable':torch.cuda.is_available(),
                       'dataDirectory':str(data_directory()),'devices':list_devices()})
        if torch.cuda.is_available():
            report['gpu'] = torch.cuda.get_device_name(0)
        report['ps4Available'] = (root/'models/ps4/checkpoint_epoch037.pt').is_file()
        report['speakerbeamAvailable'] = (root/'models/speakerbeam/model.ckpt').is_file()
        if args.deep:
            import numpy as np
            from web.voiceprint.model import TargetModel
            from web.voiceprint.speakerbeam import SpeakerBeamModel
            verifier = TargetModel()
            verifier.load()
            verifier.extract(np.zeros(24000,np.float32),np.ones(192,np.float32))
            report['ps4Inference'] = True
            if report['speakerbeamAvailable']:
                beam = SpeakerBeamModel()
                beam.extract(np.zeros(24000,np.float32),np.ones(512,np.float32))
                report['speakerbeamInference'] = True
        report['ok'] = True
    except Exception as error:
        report.update({'ok':False,'error':str(error)})
        result = 1
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text)
    return result


if __name__ == '__main__':
    raise SystemExit(main())
