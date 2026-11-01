"""Trim build-only assets and emit checksummed, independently skippable payload groups."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def prepare(stage, manifest):
    stage = stage.resolve(strict=True)
    if stage.name != 'app' or not (stage/'python/python.exe').is_file():
        raise ValueError('expected_staged_application_directory')
    before = list(stage.rglob('*'))
    removed = []
    def prune(target):
        target = target.resolve()
        target.relative_to(stage)
        if target == stage or not target.exists():
            return
        if target.is_dir():
            removed.extend(str(path.relative_to(stage)) for path in target.rglob('*') if path.is_file())
            shutil.rmtree(target)
        else:
            removed.append(str(target.relative_to(stage)))
            target.unlink()
    for cache in list(stage.rglob('__pycache__')):
        prune(cache)
    for site in (stage/'python/Lib/site-packages', stage/'runtime/cpu'):
        prune(site/'torch/include')
        prune(site/'torch/share/cmake')
        for name in ('numpy','scipy','sympy','networkx','onnx','onnxruntime'):
            package = site/name
            if package.is_dir():
                for target in list(package.rglob('*')):
                    if target.is_dir() and target.name in ('tests','test'):
                        prune(target)
    private = {'reference.wav','embedding.npy','active.json','adaptation.json','studio-settings.json','last-voice-diagnostic.json'}
    files = []
    for path in stage.rglob('*'):
        if not path.is_file() or path.suffix == '.pyc' or path.name == 'model.ckpt':
            continue
        relative = path.relative_to(stage)
        if relative.parts[0] == 'data' or path.name in private:
            raise ValueError('personal_data_in_payload')
        if relative.parts[0] == 'prerequisites':
            continue
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        group = {'python':1,'runtime':2,'models':3}.get(relative.parts[0],0)
        files.append({'path':str(relative),'bytes':path.stat().st_size,'sha256':digest,'group':group})
    files.sort(key=lambda item:(item['group'],item['path'].lower()))
    fingerprint = hashlib.sha256()
    for item in files:
        if item['group']:
            fingerprint.update((item['path']+'\0'+item['sha256']+'\n').encode('utf-8'))
    runtime_id = fingerprint.hexdigest()
    lines = ['#define PayloadRuntimeId "'+runtime_id+'"']
    previous = None
    for item in files:
        relative = item['path']
        parent = str(Path(relative).parent)
        destination = '{app}' if parent == '.' else '{app}\\'+parent
        flags = 'ignoreversion'+(' solidbreak' if previous != item['group'] else '')
        lines.append('Source: "{#StageRoot}\\'+relative+'"; DestDir: "'+destination+'"; Flags: '+flags+'; Check: NeedsPayloadFile(\''+relative.replace("'","''")+'\', \''+item['sha256']+'\')')
        previous = item['group']
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('\n'.join(lines)+'\n', 'utf-8-sig')
    stats = {'beforeFiles':sum(path.is_file() for path in before)+len(removed),
             'removedFiles':len(removed),'payloadFiles':len(files),'payloadBytes':sum(item['bytes'] for item in files),
             'runtimeId':runtime_id,'files':files}
    manifest.with_suffix('.json').write_text(json.dumps(stats,indent=2), 'utf-8')
    print(json.dumps({key:value for key,value in stats.items() if key!='files'}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    arguments = parser.parse_args()
    prepare(arguments.stage, arguments.manifest)
