"""Pinned, public PS4 weights; no personal audio leaves this device."""
from pathlib import Path
from huggingface_hub import hf_hub_download

root = Path(__file__).resolve().parents[1]
path = hf_hub_download('TaurenMountain/PS4', 'checkpoint_epoch037.pt',
                       revision='8feb7a1b3da1264659f155c42bfb40a5be88d730',
                       local_dir=root / 'models/ps4')
print(f'Model ready: {path}')
beam = hf_hub_download('wyz/librimix_tse_clean_2mix_training_td_speakerbeam_16k',
    'exp/training_td_speakerbeam_16k/epoch92_step12347611_valid.loss.ckpt',
    revision='05b6c13640c95a9995b51ee5bc01ce86ad239834')
import shutil
target = root / 'models/speakerbeam/model.ckpt'
target.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile(beam,target)
print(f'SpeakerBeam ready: {target}')
