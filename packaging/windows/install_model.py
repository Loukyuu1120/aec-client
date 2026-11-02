"""Fetch a pinned publisher-hosted model without redistributing its weights."""
import hashlib
import os
from pathlib import Path
import sys
import urllib.request


URL = 'https://huggingface.co/wyz/librimix_tse_clean_2mix_training_td_speakerbeam_16k/resolve/05b6c13640c95a9995b51ee5bc01ce86ad239834/exp/training_td_speakerbeam_16k/epoch92_step12347611_valid.loss.ckpt?download=true'
EXPECTED = '453780d8cca4e2f22e6db85a7314874cdbc0d0ddf4cea61155023eb1feaa7ab5'


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    target = Path(__file__).resolve().parent/'models/speakerbeam/model.ckpt'
    if target.exists() and digest(target) == EXPECTED:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.download')
    try:
        request = urllib.request.Request(URL, headers={'User-Agent':'AECStudio-Installer/0.11.7'})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as output:
            while True:
                block = response.read(1024*1024)
                if not block:
                    break
                output.write(block)
        if digest(temporary) != EXPECTED:
            raise ValueError('Downloaded model checksum does not match the pinned release')
        os.replace(temporary, target)
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        temporary.unlink(missing_ok=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
