import math
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from app.signals import extract_signals


class AudioSignalsTests(unittest.TestCase):
    def make_wave(self, directory, segments, rate=16000, channels=1):
        path = Path(directory) / "audio.wav"
        samples = np.concatenate([
            (np.sin(np.arange(round(seconds * rate)) * (440 * 2 * math.pi / rate)) * level * 32767).astype("<i2")
            for seconds, level in segments
        ])
        if channels == 2:
            samples = np.repeat(samples[:, None], 2, axis=1).ravel()
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(channels)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(samples.tobytes())
        return path

    def test_silence_is_pause_without_bursts(self):
        with tempfile.TemporaryDirectory() as directory:
            result = extract_signals(self.make_wave(directory, [(4, 0)]))
        self.assertEqual(result["duration"], 4)
        self.assertEqual(result["pauses"], [{"start": 0, "end": 4}])
        self.assertEqual(result["bursts"], [])
        self.assertEqual(len(result["rms"]), 2)
        self.assertTrue(all(point["db"] == -96 for point in result["rms"]))

    def test_pause_and_relative_peak_use_source_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            result = extract_signals(self.make_wave(directory, [(2, .05), (2, 0), (2, .9), (2, .05)]))
        self.assertEqual(result["pauses"], [{"start": 2, "end": 4}])
        self.assertEqual(len(result["bursts"]), 1)
        self.assertEqual(result["bursts"][0]["time"], 5)
        self.assertGreater(result["bursts"][0]["strength"], 0)

    def test_stereo_downmix_and_partial_window(self):
        with tempfile.TemporaryDirectory() as directory:
            result = extract_signals(self.make_wave(directory, [(1.25, .2)], channels=2))
        self.assertEqual(result["duration"], 1.25)
        self.assertEqual(len(result["rms"]), 1)
        self.assertAlmostEqual(result["rms"][0]["time"], .625)
        self.assertEqual(result["pauses"], [])

    def test_reject_non_pcm16(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(1)
                audio.setframerate(8000)
                audio.writeframes(b"\x80" * 8000)
            with self.assertRaisesRegex(ValueError, "PCM16"):
                extract_signals(path)
