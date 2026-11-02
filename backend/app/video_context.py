"""Sparse local frame evidence; bounded work, no remote vision or emotion guesses."""

from functools import lru_cache
from pathlib import Path
import math
import time


def inspect_video(path, ranges, max_seconds=90):
    if not path:
        return {"available": False, "scenes": [], "faces": []}
    source = Path(path)
    try:
        stat = source.stat()
        budget = max(1, min(90, float(max_seconds)))
        selected = []
        for item in ranges[:3]:
            start, end = float(item["start"]), float(item["end"])
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                continue
            end = min(end, start + budget)
            selected.append((round(start, 3), round(end, 3)))
            budget -= end - start
            if budget <= 0:
                break
        return _inspect(str(source.resolve()), stat.st_size, stat.st_mtime_ns, tuple(selected))
    except (OSError, ValueError, TypeError, KeyError):
        return {"available": False, "scenes": [], "faces": []}


@lru_cache(maxsize=64)
def _inspect(filename, size, mtime, ranges):
    # Fingerprint arguments invalidate cache; paths never enter the returned data.
    try:
        import cv2
        import numpy as np

        capture = cv2.VideoCapture(filename)
        if not capture.isOpened():
            return {"available": False, "scenes": [], "faces": []}
        cascade = cv2.CascadeClassifier(
            str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        )
        total = sum(end - start for start, end in ranges)
        scenes, faces = [], []
        samples = 0
        started = time.monotonic()
        try:
            for start, end in ranges:
                previous = None
                count = max(2, round(24 * (end - start) / max(total, 0.01)))
                for index in range(count):
                    if samples >= 24 or time.monotonic() - started > 4:
                        break
                    point = start + (end - start) * index / count
                    capture.set(cv2.CAP_PROP_POS_MSEC, point * 1000)
                    ok, frame = capture.read()
                    if not ok:
                        continue
                    samples += 1
                    height, width = frame.shape[:2]
                    factor = min(1.0, 640 / width)
                    image = cv2.resize(
                        frame, (max(2, round(width * factor)), max(2, round(height * factor)))
                    )
                    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
                    cv2.normalize(hist, hist)
                    if previous is not None:
                        distance = float(cv2.compareHist(previous, hist, cv2.HISTCMP_BHATTACHARYYA))
                        if np.isfinite(distance) and distance > 0.45:
                            scenes.append(
                                {
                                    "time": round(point, 3),
                                    "strength": round(distance, 3),
                                    "label": "изменение кадра между редкими выборками",
                                }
                            )
                    previous = hist
                    if not cascade.empty():
                        detected = cascade.detectMultiScale(
                            gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24)
                        )
                        if len(detected):
                            x, y, w, h = max(detected, key=lambda face: int(face[2]) * int(face[3]))
                            faces.append(
                                {
                                    "time": round(point, 3),
                                    "x": round(float(x / image.shape[1]), 4),
                                    "y": round(float(y / image.shape[0]), 4),
                                    "width": round(float(w / image.shape[1]), 4),
                                    "height": round(float(h / image.shape[0]), 4),
                                }
                            )
        finally:
            capture.release()
        return {
            "available": True,
            "samples": samples,
            "scenes": scenes[:12],
            "faces": faces[:12],
            "method": "sparse histogram changes and frontal face detections; no emotion, identity or event inference",
        }
    except Exception:
        # Optional analysis cannot block text-only edits or reveal decoder paths.
        return {"available": False, "scenes": [], "faces": []}
