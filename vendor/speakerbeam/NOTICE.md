# TD-SpeakerBeam runtime sources

`tcn.py` and `adapt_layers.py` are from ESPnet:

- https://github.com/espnet/espnet/blob/master/espnet2/enh/layers/tcn.py
- https://github.com/espnet/espnet/blob/master/espnet2/enh/layers/adapt_layers.py
- ESPnet license: `LICENSE-ESPnet` (Apache-2.0).

Only the TCN import was changed to a package-relative import. Original attribution
and copyright notices are preserved. The TCN file attributes the Conv-TasNet
implementation to its original MIT-licensed source. Adaptation layers retain
the original BUT / NTT copyright notice.

The small model adapter in `web/voiceprint/speakerbeam.py` implements the published
convolutional encoder/decoder and extractor configuration, and loads every key
strictly. It does not execute Hugging Face user code or deserialize arbitrary objects.

Checkpoint source:
https://huggingface.co/wyz/librimix_tse_clean_2mix_training_td_speakerbeam_16k

Pinned revision: `05b6c13640c95a9995b51ee5bc01ce86ad239834`.
Artifact: `exp/training_td_speakerbeam_16k/epoch92_step12347611_valid.loss.ckpt`.
Weights are not committed. No additional commercial-use rights for model weights
are asserted by this fork.
