"""Cheap streaming audio hints. Energy is evidence of activity, never of virality.

Input is the worker's PCM16 WAV. Memory is proportional to the compact output,
not the size of a many-hour recording. No model or network is used here.
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np


def extract_signals(audio_path: Path) -> dict:
    """Return a 2-second RMS envelope, pauses, and relative energy bursts.

    Pause timing uses 250 ms blocks and is intentionally approximate. All times
    refer to the original audio, not a silence-trimmed timeline. Bursts are only
    inexpensive candidate hints and must not substitute for transcript context.
    """
    envelope: list[dict] = []
    pauses: list[dict] = []
    silence_start: float | None = None
    silence_threshold_db = -38.0
    with wave.open(str(audio_path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise ValueError("Audio analysis requires uncompressed PCM16 WAV.")
        rate = audio.getframerate()
        channels = audio.getnchannels()
        frame_count = audio.getnframes()
        if rate <= 0 or channels <= 0:
            raise ValueError("Invalid WAV sample rate or channel count.")
        duration = frame_count / rate
        block_size = max(1, rate // 4)
        position = 0
        window_start = 0.0
        window_sum = 0.0
        window_samples = 0
        window_frames = 0
        while True:
            data = audio.readframes(block_size)
            if not data:
                break
            samples = np.frombuffer(data, dtype="<i2").astype(np.float64)
            if samples.size % channels:
                raise ValueError("Truncated WAV audio frame.")
            frames = samples.size // channels
            if channels > 1:
                samples = samples.reshape(-1, channels).mean(axis=1)
            samples /= 32768.0
            square_sum = float(np.dot(samples, samples))
            mean_square = square_sum / max(1, frames)
            db = max(-96.0, 10.0 * math.log10(max(mean_square, 1e-12)))
            block_start = position / rate
            block_end = min(duration, (position + frames) / rate)
            if db < silence_threshold_db:
                if silence_start is None:
                    silence_start = block_start
            elif silence_start is not None:
                if block_start - silence_start >= 0.75:
                    pauses.append({"start": round(silence_start, 3), "end": round(block_start, 3)})
                silence_start = None
            window_sum += square_sum
            window_samples += frames
            window_frames += frames
            position += frames
            if window_frames >= rate * 2:
                rms_db = max(-96.0, 10.0 * math.log10(max(window_sum / window_samples, 1e-12)))
                envelope.append({"time": round((window_start + block_end) / 2, 3), "db": round(rms_db, 2)})
                window_start = block_end
                window_sum = 0.0
                window_samples = 0
                window_frames = 0
        if window_samples:
            rms_db = max(-96.0, 10.0 * math.log10(max(window_sum / window_samples, 1e-12)))
            envelope.append({"time": round((window_start + duration) / 2, 3), "db": round(rms_db, 2)})
        if silence_start is not None and duration - silence_start >= 0.75:
            pauses.append({"start": round(silence_start, 3), "end": round(duration, 3)})

    bursts: list[dict] = []
    if envelope:
        levels = np.asarray([point["db"] for point in envelope])
        # Absolute floor prevents an almost silent recording from becoming a
        # collection of "exciting" peaks merely due to its relative envelope.
        median = float(np.median(levels))
        threshold = max(-32.0, float(np.percentile(levels, 75)), median + 4.0)
        for index, point in enumerate(envelope):
            level = point["db"]
            previous = levels[index - 1] if index else -96.0
            following = levels[index + 1] if index + 1 < len(levels) else -96.0
            if level >= threshold and level >= previous and level > following:
                bursts.append({
                    "time": point["time"],
                    "strength": round(min(1.0, max(0.0, (level - median) / 24.0)), 3),
                    "db": level,
                })
    return {
        "duration": round(duration, 3),
        "rms": envelope,
        "pauses": pauses,
        "bursts": bursts,
        "metadata": {
            "source": "audio-energy",
            "rms_window_seconds": 2,
            "pause_resolution_seconds": 0.25,
            "silence_threshold_db": silence_threshold_db,
            "note": "Energy and pauses are approximate hints, not detected emotions or viral scores.",
        },
    }
