"""Local, bounded FFmpeg video processing; no shell or remote media protocols.

All transcript timestamps passed to render_clip are in the source time base.
Exports use a temporary directory and become visible only after successful probe.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
from typing import Any


class MediaError(RuntimeError):
    pass


_VIDEO_FORMATS = "mov,matroska,webm,avi,flv,mpegts,mpeg,mpegvideo"


def _threads() -> str:
    try:
        return str(max(1, min(8, int(os.getenv("FFMPEG_THREADS", "2")))))
    except ValueError:
        return "2"


def _run(args: list[str], *, cwd: Path | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                                timeout=timeout, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise MediaError(f"{args[0]} не найден. Установите FFmpeg и добавьте ffmpeg/ffprobe в PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"Обработка {args[0]} превысила лимит {timeout} секунд.") from exc
    if result.returncode:
        raise MediaError(f"{args[0]} завершился с ошибкой: {result.stderr[-6000:]}")
    return result


def probe(path: Path) -> dict[str, Any]:
    source = Path(path).resolve(strict=True)
    result = _run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", _VIDEO_FORMATS,
                   "-show_format", "-show_streams", "-of", "json", str(source)], timeout=60)
    data = json.loads(result.stdout)
    videos = [s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise MediaError("Файл не содержит видеодорожку.")
    video = videos[0]
    try:
        duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
        width, height = int(video["width"]), int(video["height"])
    except (TypeError, ValueError, KeyError) as exc:
        raise MediaError("Не удалось определить параметры видео.") from exc
    if not math.isfinite(duration) or duration <= 0 or width < 2 or height < 2:
        raise MediaError("Некорректная длительность или разрешение видео.")
    return {"duration": duration, "width": width, "height": height,
            "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
            "video_codec": video.get("codec_name")}


def extract_audio(source: Path, target: Path) -> None:
    source, target = Path(source).resolve(strict=True), Path(target).resolve()
    if not probe(source)["has_audio"]:
        raise MediaError("В видео нет аудиодорожки: транскрипция речи невозможна.")
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
          "-threads", _threads(), "-protocol_whitelist", "file,pipe", "-format_whitelist", _VIDEO_FORMATS, "-i", str(source),
          "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
         timeout=7200)


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except (ValueError, TypeError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(low, min(high, value))


def _timestamp(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02}:{centis // 100 % 60:02}.{centis % 100:02}"


def _ass_text(text: Any) -> str:
    # Braces and slashes must never become user-controlled ASS override tags.
    return str(text).replace("\\", "／").replace("{", "（").replace("}", "）").replace("\r", " ").replace("\n", " ")


def _ass_color(color: Any) -> str:
    color = str(color or "#B8FF60")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#B8FF60"
    return "&H00" + color[5:7] + color[3:5] + color[1:3]


def _write_ass(path: Path, words: list[dict], settings: dict, duration: float) -> None:
    font = re.sub(r"[^\w .-]", "", str(settings.get("subtitle_font", "Arial")))[:60] or "Arial"
    size = round(_number(settings.get("subtitle_size"), 58, 24, 120))
    y = round(1920 * _number(settings.get("subtitle_position"), .76, .1, .9))
    highlight = _ass_color(settings.get("subtitle_color"))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,5,70,70,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    clean = []
    for word in words:
        try:
            start, end = max(0., float(word["start"])), min(duration, float(word["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(start) and math.isfinite(end) and end > start and str(word.get("text", "")).strip():
            clean.append({"start": start, "end": end, "text": _ass_text(word["text"]).strip()})
    clean.sort(key=lambda word: word["start"])
    # Readable 2–6 word phrases. Each event highlights only the current word.
    groups, group = [], []
    for word in clean:
        if group and (len(group) >= 6 or sum(len(w["text"]) + 1 for w in group) + len(word["text"]) > 38
                      or word["start"] - group[-1]["end"] > .7
                      or group[-1]["text"].endswith((".", "!", "?"))):
            groups.append(group)
            group = []
        group.append(word)
    if group:
        groups.append(group)
    lines = [header]
    for phrase in groups:
        phrase_start, phrase_end = phrase[0]["start"], min(duration, phrase[-1]["end"] + .15)
        boundaries = sorted({phrase_start, phrase_end, *[w["start"] for w in phrase], *[w["end"] for w in phrase]})
        for left, right in zip(boundaries, boundaries[1:]):
            if right - left < .01:
                continue
            parts = []
            for word in phrase:
                active = word["start"] <= (left + right) / 2 < word["end"]
                parts.append(("{\\c" + highlight + "&}" + word["text"] + "{\\c&HFFFFFF&}") if active else word["text"])
            text = f"{{\\an5\\pos(540,{y})}}" + " ".join(parts)
            lines.append(f"Dialogue: 0,{_timestamp(left)},{_timestamp(right)},Default,,0,0,0,,{text}\n")
    path.write_text("".join(lines), encoding="utf-8-sig")


def _silence_segments(source: Path, start: float, duration: float, words: list[dict]) -> list[tuple[float, float]]:
    result = _run(["ffmpeg", "-hide_banner", "-nostdin", "-threads", _threads(), "-ss", str(start),
                   "-t", str(duration), "-protocol_whitelist", "file,pipe", "-format_whitelist", _VIDEO_FORMATS, "-i", str(source),
                   "-vn", "-af", "silencedetect=noise=-38dB:d=1.5", "-f", "null", "-"], timeout=600)
    silence_start = None
    removed = []
    for match in re.finditer(r"silence_(start|end):\s*([0-9.]+)", result.stderr):
        kind, value = match[1], float(match[2])
        if kind == "start":
            silence_start = value
        elif silence_start is not None:
            left, right = max(.2, silence_start + .2), min(duration - .2, value - .2)
            # Cut only verified quiet regions which have no recognized speech.
            speech_overlap = any(float(w["start"]) - start < right and float(w["end"]) - start > left for w in words)
            if right - left > 1 and not speech_overlap:
                removed.append((left, right))
            silence_start = None
    # Bound filter graph size, preserving the largest gaps first.
    removed = sorted(sorted(removed, key=lambda p: p[1] - p[0], reverse=True)[:20])
    segments, cursor = [], 0.
    for left, right in removed:
        if left > cursor:
            segments.append((cursor, left))
        cursor = right
    if cursor < duration:
        segments.append((cursor, duration))
    return segments or [(0., duration)]


def _remap_words(words: list[dict], start: float, segments: list[tuple[float, float]]) -> list[dict]:
    mapped, cursor = [], 0.
    for left, right in segments:
        for word in words:
            try:
                a, b = float(word["start"]) - start, float(word["end"]) - start
            except (KeyError, TypeError, ValueError):
                continue
            if b > left and a < right:
                mapped.append({"start": cursor + max(a, left) - left,
                               "end": cursor + min(b, right) - left, "text": word.get("text", "")})
        cursor += right - left
    return mapped


def _face_center(source: Path, start: float, duration: float) -> tuple[float, float] | None:
    try:
        import cv2
    except ImportError as exc:
        raise MediaError("Для face_tracking установите opencv-python-headless или отключите слежение за лицом.") from exc
    classifier = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
    if classifier.empty():
        raise MediaError("Не найден локальный каскад OpenCV для определения лица.")
    cap = cv2.VideoCapture(str(source))
    centers = []
    try:
        for fraction in (.05, .2, .4, .6, .8, .95):
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + duration * fraction) * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            scale = min(1., 800 / w)
            frame = cv2.resize(frame, (round(w * scale), round(h * scale)))
            faces = classifier.detectMultiScale(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
            if len(faces):
                x, y, fw, fh = max(faces, key=lambda p: int(p[2]) * int(p[3]))
                centers.append(((x + fw / 2) / frame.shape[1], (y + fh / 2) / frame.shape[0]))
    finally:
        cap.release()
    # Stabilized center across sparse samples, not frame-by-frame person tracking.
    return (statistics.median(x for x, _ in centers), statistics.median(y for _, y in centers)) if centers else None


def render_clip(source: Path, output: Path, start: float, end: float, words: list[dict], settings: dict) -> None:
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    info = probe(source)
    if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end - start < .2 or end > info["duration"] + .2:
        raise MediaError("Границы клипа выходят за пределы исходного видео.")
    if not info["has_audio"]:
        raise MediaError("Для экспорта требуется видео с аудиодорожкой.")
    end = min(end, info["duration"])
    duration = end - start
    segments = _silence_segments(source, start, duration, words) if settings.get("remove_silence") else [(0., duration)]
    rendered_duration = sum(b - a for a, b in segments)
    crop_x = _number(settings.get("crop_x"), .5, 0, 1)
    crop_y = _number(settings.get("crop_y"), .5, 0, 1)
    layout = settings.get("layout", "crop")
    if layout not in ("crop", "fit", "split"):
        raise MediaError("Неизвестный вариант кадрирования.")
    if settings.get("face_tracking") and layout == "crop":
        center = _face_center(source, start, duration)
        if center:
            crop_x, crop_y = center
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="render-", dir=output.parent) as temporary:
        work = Path(temporary)
        filters = []
        if len(segments) == 1:
            video, audio = "0:v:0", "0:a:0"
        else:
            n = len(segments)
            filters += [f"[0:v:0]split={n}" + "".join(f"[vs{i}]" for i in range(n)),
                        f"[0:a:0]asplit={n}" + "".join(f"[as{i}]" for i in range(n))]
            for i, (left, right) in enumerate(segments):
                filters += [f"[vs{i}]trim=start={left:.6f}:end={right:.6f},setpts=PTS-STARTPTS[v{i}]",
                            f"[as{i}]atrim=start={left:.6f}:end={right:.6f},asetpts=PTS-STARTPTS[a{i}]"]
            filters.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[basev][basea]")
            video, audio = "basev", "basea"
        if layout == "crop":
            filters.append(f"[{video}]scale=1080:1920:force_original_aspect_ratio=increase,"
                           f"crop=1080:1920:x='max(0,min(iw-ow,iw*{crop_x:.6f}-ow/2))':"
                           f"y='max(0,min(ih-oh,ih*{crop_y:.6f}-oh/2))',setsar=1[layout]")
        elif layout == "fit":
            filters.append(f"[{video}]scale=1080:1920:force_original_aspect_ratio=decrease,"
                           "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x111318,setsar=1[layout]")
        else:
            box = settings.get("face_box", [.7, 0, .3, .4])
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                raise MediaError("face_box должен содержать x, y, ширину и высоту от 0 до 1.")
            x, y, w, h = [_number(v, d, 0 if i < 2 else .05, 1) for i, (v, d) in enumerate(zip(box, [.7, 0, .3, .4]))]
            x, y = min(x, 1 - w), min(y, 1 - h)
            filters += [f"[{video}]split=2[face][game]",
                        f"[face]crop=w='trunc(iw*{w:.6f}/2)*2':h='trunc(ih*{h:.6f}/2)*2':x='iw*{x:.6f}':y='ih*{y:.6f}',"
                        "scale=1080:640:force_original_aspect_ratio=increase,crop=1080:640,setsar=1[top]",
                        f"[game]scale=1080:1280:force_original_aspect_ratio=increase,"
                        f"crop=1080:1280:x='max(0,min(iw-ow,iw*{crop_x:.6f}-ow/2))':"
                        f"y='max(0,min(ih-oh,ih*{crop_y:.6f}-oh/2))',setsar=1[bottom]",
                        "[top][bottom]vstack=inputs=2[layout]"]
        effect = "layout"
        if settings.get("auto_zoom"):
            # Gentle deterministic 1–4% movement. It does not pretend to detect emotions.
            filters.append("[layout]scale=1124:2000,crop=1080:1920:"
                           "x='22+12*sin(t*0.8)':y='40+20*sin(t*0.6)',setsar=1[zoomed]")
            effect = "zoomed"
        if settings.get("subtitles", True):
            _write_ass(work / "subtitles.ass", _remap_words(words, start, segments), settings, rendered_duration)
            filters.append(f"[{effect}]ass=filename=subtitles.ass[vout]")
        else:
            filters.append(f"[{effect}]null[vout]")
        filters.append(f"[{audio}]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[aout]")
        target = work / "output.mp4"
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
              "-threads", _threads(), "-filter_complex_threads", _threads(),
              "-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-protocol_whitelist", "file,pipe", "-format_whitelist", _VIDEO_FORMATS, "-i", str(source),
              "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
              "-r", "30", "-threads", _threads(), "-c:a", "aac", "-b:a", "160k",
              "-movflags", "+faststart", "-map_metadata", "-1", "-t", f"{rendered_duration:.6f}", str(target)],
             cwd=work, timeout=max(900, round(rendered_duration * 40)))
        result = probe(target)
        if result["width"] != 1080 or result["height"] != 1920 or not result["has_audio"]:
            raise MediaError("Экспорт не прошёл проверку разрешения или аудиодорожки.")
        if abs(result["duration"] - rendered_duration) > 1:
            raise MediaError("Экспорт не прошёл проверку длительности.")
        os.replace(target, output)
