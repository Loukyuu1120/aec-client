"""Download build inputs without installing or executing downloaded programs."""
import argparse
import hashlib
from pathlib import Path
import urllib.request
import zipfile


INPUTS = (
    ('python-3.12.10-embed-amd64.zip',
     'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip',
     '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'),
    ('VBCABLE_Driver_Pack45.zip',
     'https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip',
     'b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb'),
    ('vc_redist.x64.exe', 'https://aka.ms/vs/17/release/vc_redist.x64.exe', None),
    ('MicrosoftEdgeWebView2RuntimeInstallerX64.exe',
     'https://go.microsoft.com/fwlink/?linkid=2124701', None),
)


def prepare(downloads):
    downloads.mkdir(parents=True, exist_ok=True)
    for filename, address, expected_hash in INPUTS:
        destination = downloads / filename
        if not destination.is_file():
            temporary = destination.with_suffix(destination.suffix + '.partial')
            with urllib.request.urlopen(address, timeout=120) as response, temporary.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(destination)
        if expected_hash:
            with destination.open('rb') as source:
                actual_hash = hashlib.file_digest(source, 'sha256').hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(f'Download hash mismatch: {filename}')
        print(f'Ready: {filename}', flush=True)
    with zipfile.ZipFile(downloads / 'VBCABLE_Driver_Pack45.zip') as archive:
        archive.extractall(downloads / 'vbcable')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--downloads', type=Path, required=True)
    prepare(parser.parse_args().downloads)
