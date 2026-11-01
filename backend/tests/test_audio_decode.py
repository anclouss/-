"""Exercise the real PyAV decoder used by Whisper, without downloading a model.

faster-whisper 1.2.1 calls av.open(metadata_errors="ignore"). PyAV 19 removed
that parameter, so these tests fail before inference with the incompatible
version instead of allowing API stubs to hide the integration regression.
"""
import wave

import numpy as np
from faster_whisper.audio import decode_audio


def write_pcm(path, samples, rate, channels=1):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(np.round(samples * 32767).astype('<i2').tobytes())


def test_real_whisper_decoder_accepts_mono_worker_wav(tmp_path):
    rate = 16000
    samples = .25 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate)
    path = tmp_path / 'speech-input.wav'
    write_pcm(path, samples, rate)

    decoded = decode_audio(str(path), sampling_rate=16000)

    assert decoded.dtype == np.float32
    assert decoded.ndim == 1
    assert len(decoded) == rate
    expected = np.round(samples * 32767).astype('<i2').astype(np.float32) / 32768
    np.testing.assert_allclose(decoded, expected, atol=1 / 32768)


def test_real_whisper_decoder_downmixes_and_resamples_stereo(tmp_path):
    source_rate = 48000
    duration = .4
    timeline = np.arange(round(source_rate * duration)) / source_rate
    left = .3 * np.sin(2 * np.pi * 440 * timeline)
    right = .3 * np.sin(2 * np.pi * 660 * timeline)
    path = tmp_path / 'stereo-input.wav'
    write_pcm(path, np.column_stack([left, right]), source_rate, channels=2)

    decoded = decode_audio(str(path), sampling_rate=16000)

    assert decoded.ndim == 1
    assert decoded.dtype == np.float32
    assert abs(len(decoded) - round(16000 * duration)) <= 1
    assert np.isfinite(decoded).all()
    assert .05 < np.sqrt(np.mean(decoded.astype(np.float64) ** 2)) < .5
    spectrum = np.abs(np.fft.rfft(decoded))
    frequencies = np.fft.rfftfreq(len(decoded), d=1 / 16000)
    # Both input channels must contribute, rather than selecting only one.
    for frequency in (440, 660):
        peak = spectrum[np.abs(frequencies - frequency) <= 3].max()
        assert peak > spectrum.max() * .7
