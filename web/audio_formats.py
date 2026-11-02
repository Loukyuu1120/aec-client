"""Negotiate endpoint PCM formats and keep the processing chain mono."""
import numpy as np


def mono_pcm(data, frames, channels):
    samples = np.frombuffer(data, dtype=np.int16)
    if samples.size != frames * channels:
        raise ValueError('microphone_format_mismatch')
    if channels == 1:
        return data
    return np.rint(samples.reshape(frames, channels).astype(np.float32).mean(axis=1)).astype(np.int16).tobytes()


def open_pcm_stream(audio, module, device, callback, *, capture, frames_per_buffer=None):
    info = audio.get_device_info_by_index(device)
    maximum = int(info.get('maxInputChannels' if capture else 'maxOutputChannels', 0))
    if maximum < 1:
        raise ValueError('select_microphone' if capture else 'select_output')
    channels = list(dict.fromkeys(value for value in (1, 2, maximum) if value <= maximum))
    rates = list(dict.fromkeys((int(info.get('defaultSampleRate', 48000) or 48000), 48000, 44100, 16000)))
    last_error = None
    for rate in rates:
        for channel_count in channels:
            def on_audio(data, frames, timing, status, count=channel_count):
                if capture:
                    return callback(mono_pcm(data, frames, count), frames, timing, status)
                result, flag = callback(data, frames, timing, status)
                if result is not None and count > 1:
                    samples = np.frombuffer(result, dtype=np.int16)
                    if samples.size != frames:
                        raise ValueError('output_format_mismatch')
                    result = np.repeat(samples[:, None], count, axis=1).tobytes()
                return result, flag
            parameters = {'format':module.paInt16, 'channels':channel_count, 'rate':rate,
                          'frames_per_buffer':frames_per_buffer or max(128, rate // 100),
                          'stream_callback':on_audio, 'start':False}
            parameters.update({'input':True, 'input_device_index':device} if capture
                              else {'output':True, 'output_device_index':device})
            try:
                stream = audio.open(**parameters)
                return stream, rate, channel_count
            except (OSError, ValueError) as error:
                code = getattr(error, 'errno', None)
                if code is None:
                    code = next((item for item in error.args if isinstance(item, int)), None)
                if code not in (-9998, -9997, -9994):
                    raise
                last_error = error
    raise last_error
