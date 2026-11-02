"""Non-destructive EDL playback exports, proxies and compact PCM waveform data.

Only validated numeric values and fixed filter/codec names enter FFmpeg argv.
User text stays in a generated ASS file, never in a shell or filter expression.
"""

from __future__ import annotations

from collections.abc import Callable
import math
from functools import lru_cache
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import wave

import numpy as np

from .media import MediaError, _VIDEO_FORMATS, _face_center, _threads, _write_ass, probe


class MediaCancelled(MediaError):
    pass


Progress = Callable[[str, float], None]
Cancel = Callable[[], bool]
_RESOLUTIONS = {"360p": 360, "480p": 480, "720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160}
_PRESETS = {
    "fast": ("veryfast", 28, 3.0),
    "balanced": ("fast", 23, 6.0),
    "high": ("medium", 20, 10.0),
    "maximum": ("slow", 17, 18.0),
}


def _cancelled(cancel: Cancel | None):
    if cancel and cancel():
        raise MediaCancelled("Операция отменена пользователем.")


def _emit(progress: Progress | None, stage: str, value: float):
    if progress:
        progress(stage, round(max(0.0, min(100.0, value)), 2))


def _checked_float(value, name: str, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaError(f"Некорректный параметр: {name}.") from exc
    if not math.isfinite(number) or not low <= number <= high:
        raise MediaError(f"Параметр {name} вне допустимого диапазона.")
    return number


def _segments(document: dict) -> list[dict]:
    values = document.get("segments", [])
    if not isinstance(values, list) or not 1 <= len(values) <= 256:
        raise MediaError("Монтаж должен содержать от 1 до 256 фрагментов.")
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise MediaError("Некорректный фрагмент монтажа.")
        start = _checked_float(value.get("start"), "начало", 0, 24 * 3600)
        end = _checked_float(value.get("end"), "конец", 0, 24 * 3600)
        speed = _checked_float(value.get("speed", 1), "скорость", 0.25, 4)
        if end - start < 0.02:
            raise MediaError("Фрагмент слишком короткий или его границы перепутаны.")
        effects = value.get("effects", {})
        if not isinstance(effects, dict):
            raise MediaError("Некорректные эффекты фрагмента.")
        result.append(
            {
                **value,
                "start": start,
                "end": end,
                "speed": speed,
                "duration": (end - start) / speed,
                "effects": effects,
            }
        )
    return result


def _fade(document: dict, segments: list[dict]) -> float:
    transition = document.get("transition", {})
    kind = transition.get("type", "none")
    if kind not in ("none", "fade"):
        raise MediaError("Неизвестный переход между фрагментами.")
    if kind == "none" or len(segments) < 2:
        return 0.0
    duration = _checked_float(transition.get("duration", 0.2), "длительность перехода", 0.01, 2)
    if duration > min(segment["duration"] for segment in segments) / 2 + 0.00001:
        raise MediaError("Переход не может быть длиннее половины короткого фрагмента.")
    return duration


def mapped_words(document: dict, words: list[dict]) -> list[dict]:
    """Map source words to the edited output, including repeats, order and speed."""
    segments = _segments(document)
    fade = _fade(document, segments)
    overrides = document.get("caption_overrides", {})
    result, cursor = [], 0.0
    for index, segment in enumerate(segments):
        for word in words:
            try:
                start, end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                continue
            if end <= segment["start"] or start >= segment["end"]:
                continue
            text = word.get("text", "")
            # JS String(number) and Python str(float) differ for whole seconds.
            for key in (
                str(word["start"]),
                str(start),
                f"{start:g}",
                f"{start:.3f}".rstrip("0").rstrip("."),
            ):
                if key in overrides:
                    text = overrides[key]
                    break
            left = cursor + (max(start, segment["start"]) - segment["start"]) / segment["speed"]
            right = cursor + (min(end, segment["end"]) - segment["start"]) / segment["speed"]
            result.append(
                {
                    "start": round(left, 6),
                    "end": round(right, 6),
                    "text": str(text),
                    "source_start": start,
                    "source_end": end,
                    "segment_id": segment.get("id", str(index)),
                }
            )
        cursor += segment["duration"] - (fade if index < len(segments) - 1 else 0)
    return sorted(result, key=lambda word: (word["start"], word["end"]))


def _atempo(speed: float) -> str:
    pieces = []
    while speed > 2 + 1e-9:
        pieces.append("atempo=2")
        speed /= 2
    while speed < 0.5 - 1e-9:
        pieces.append("atempo=0.5")
        speed /= 0.5
    pieces.append(f"atempo={speed:.8f}")
    return ",".join(pieces)


def _gain_filters(segments: list[dict], fade: float) -> list[str]:
    """Apply requested gains after normalization, with ramps over crossfades."""
    result, cursor = [], 0.0
    gains = [
        _checked_float(segment["effects"].get("volume", 1), "громкость фрагмента", 0, 3)
        for segment in segments
    ]
    for index, segment in enumerate(segments):
        left = cursor + (fade if index else 0)
        right = cursor + segment["duration"] - (fade if index < len(segments) - 1 else 0)
        if gains[index] != 1 and right > left:
            result.append(f"volume={gains[index]:.8f}:enable='gte(t,{left:.8f})*lt(t,{right:.8f})'")
        if fade and index < len(segments) - 1 and (gains[index] != 1 or gains[index + 1] != 1):
            start = cursor + segment["duration"] - fade
            end = start + fade
            expression = f"{gains[index]:.8f}+({gains[index + 1] - gains[index]:.8f})*(t-{start:.8f})/{fade:.8f}"
            result.append(
                f"volume='if(isnan(t),1,{expression})':eval=frame:enable='gte(t,{start:.8f})*lt(t,{end:.8f})'"
            )
        cursor += segment["duration"] - (fade if index < len(segments) - 1 else 0)
    return result


@lru_cache(maxsize=256)
def _cached_face(path: str, start: float, duration: float, mtime_ns: int, size: int):
    # Fingerprint arguments invalidate samples if a source is replaced on disk.
    return _face_center(Path(path), start, duration)


def _dimensions(info: dict, settings: dict, options: dict) -> tuple[int, int, float, str, int, str]:
    preview = bool(options.get("preview"))
    resolution = str(
        options.get("resolution", options.get("quality", "480p" if preview else "1080p"))
    )
    if resolution == "auto":
        resolution = "480p"
    if resolution not in _RESOLUTIONS or (not preview and resolution in ("360p", "480p")):
        raise MediaError("Неизвестное качество видео.")
    if resolution == "4k" and (
        min(info["width"], info["height"]) < 2160 or max(info["width"], info["height"]) < 3840
    ):
        raise MediaError("Для экспорта 4K нужен исходник с разрешением не ниже 3840×2160.")
    ratio = settings.get("aspect_ratio", "9:16")
    if ratio == "original":
        fraction = info["width"] / info["height"]
    elif ratio in ("9:16", "16:9", "1:1", "4:5"):
        a, b = ratio.split(":")
        fraction = int(a) / int(b)
    else:
        raise MediaError("Неизвестное соотношение сторон.")
    side = _RESOLUTIONS[resolution]
    if fraction <= 1:
        width, height = side, round(side / fraction / 2) * 2
    else:
        width, height = round(side * fraction / 2) * 2, side
    fps_value = options.get("fps", "30")
    if str(fps_value) == "original":
        fps = _checked_float(info.get("fps", 30), "исходный FPS", 1, 240)
    else:
        fps = _checked_float(fps_value, "FPS", 1, 240)
        if fps not in (30, 60):
            raise MediaError("Выберите исходный FPS, 30 или 60.")
    if preview:
        fps = min(30, fps)
    preset_name = "fast" if preview else str(options.get("preset", "balanced"))
    if preset_name not in _PRESETS:
        raise MediaError("Неизвестный профиль качества.")
    preset, default_crf, _ = _PRESETS[preset_name]
    crf = int(
        _checked_float(
            options.get("crf") if options.get("crf") is not None else default_crf, "CRF", 0, 40
        )
    )
    codec = "h264" if preview else str(options.get("codec", "h264"))
    if codec not in ("h264", "h265"):
        raise MediaError("Поддерживаются кодеки H.264 и H.265.")
    return width, height, fps, preset, crf, codec


def _run_progress(
    args: list[str],
    cwd: Path,
    duration: float,
    progress: Progress | None,
    cancel: Cancel | None,
    *,
    start: float = 15,
    span: float = 75,
    timeout: float | None = None,
):
    """Drain FFmpeg progress in a thread while the owner checks cancellation."""
    _cancelled(cancel)
    messages: queue.Queue[str | None] = queue.Queue(maxsize=128)
    error_path = cwd / "ffmpeg-error.log"
    started = time.monotonic()
    process = None
    reader = None
    with error_path.open("wb") as error_file:
        try:
            process = subprocess.Popen(
                args,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=error_file,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            def drain():
                try:
                    if process and process.stdout:
                        for line in process.stdout:
                            # Keep the queue bounded even after cancellation.
                            try:
                                messages.put(line.strip(), timeout=0.2)
                            except queue.Full:
                                pass
                except (OSError, ValueError):
                    # The owner may close stdout after terminating FFmpeg.
                    pass
                finally:
                    try:
                        messages.put(None, timeout=0.2)
                    except queue.Full:
                        pass

            reader = threading.Thread(target=drain, name="clipforge-ffmpeg-progress", daemon=True)
            reader.start()
            while True:
                _cancelled(cancel)
                if timeout and time.monotonic() - started > timeout:
                    raise MediaError("FFmpeg превысил допустимое время обработки.")
                try:
                    line = messages.get(timeout=0.2)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                if line is None and process.poll() is not None:
                    break
                if line and line.startswith("out_time_us="):
                    try:
                        fraction = min(
                            1.0, max(0.0, float(line.split("=", 1)[1]) / 1e6 / max(duration, 0.01))
                        )
                        _emit(
                            progress,
                            "rendering" if fraction < 0.7 else "encoding",
                            start + fraction * span,
                        )
                    except ValueError:
                        pass
            process.wait(timeout=10)
            if process.returncode:
                error_file.flush()
                detail = error_path.read_bytes()[-6000:].decode("utf-8", errors="replace")
                if "No space left on device" in detail or "not enough space" in detail:
                    message = "Недостаточно места на диске для обработки видео."
                elif "Unknown encoder" in detail or "Encoder not found" in detail:
                    message = "В этой сборке FFmpeg отсутствует выбранный кодек. Выберите H.264 или установите полную сборку FFmpeg."
                elif "No such filter" in detail:
                    message = "В этой сборке FFmpeg отсутствует нужный фильтр (ASS/zoom/transition). Установите полную сборку FFmpeg."
                else:
                    message = f"FFmpeg завершился с ошибкой (код {process.returncode}). Проверьте параметры монтажа и доступность исходника."
                error = MediaError(message)
                error.diagnostic = detail
                raise error
        except FileNotFoundError as exc:
            raise MediaError("FFmpeg не найден. Установите FFmpeg и добавьте его в PATH.") from exc
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            if process and process.stdout:
                process.stdout.close()
            if reader:
                reader.join(timeout=1)


def _input_args(source: Path, work: Path) -> str:
    """A hard link keeps many Windows input arguments below its argv limit."""
    link = work / "input.media"
    try:
        os.link(source, link)
        return link.name
    except OSError:
        return str(source)


def render_document(
    source: Path,
    output: Path,
    document: dict,
    words: list[dict],
    options: dict,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
) -> dict:
    _cancelled(cancel)
    _emit(progress, "preparing", 0)
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    info = probe(source)
    segments = _segments(document)
    if any(segment["end"] > info["duration"] + 0.05 for segment in segments):
        raise MediaError("Фрагмент выходит за пределы исходного видео.")
    fade = _fade(document, segments)
    duration = sum(segment["duration"] for segment in segments) - fade * (len(segments) - 1)
    if not 0.02 <= duration <= 24 * 3600:
        raise MediaError("Некорректная итоговая длительность монтажа.")
    settings = document.get("settings", {})
    width, height, fps, preset, crf, codec = _dimensions(info, settings, options)
    volume = _checked_float(settings.get("volume", 1), "громкость", 0, 3)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="editor-render-", dir=output.parent) as directory:
        work = Path(directory)
        local_source = _input_args(source, work)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-filter_complex_threads",
            _threads(),
            "-progress",
            "pipe:1",
            "-stats_period",
            "0.25",
        ]
        graph, overlays = [], []
        cursor = 0.0
        for index, segment in enumerate(segments):
            _cancelled(cancel)
            command += [
                "-threads",
                "1",
                "-ss",
                f"{segment['start']:.6f}",
                "-t",
                f"{segment['end'] - segment['start']:.6f}",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                _VIDEO_FORMATS,
                "-i",
                local_source,
            ]
            effects = segment["effects"]
            cx = _checked_float(
                effects.get("crop_x", settings.get("crop_x", 0.5)), "центр кадра X", 0, 1
            )
            cy = _checked_float(
                effects.get("crop_y", settings.get("crop_y", 0.5)), "центр кадра Y", 0, 1
            )
            if settings.get("face_tracking") and settings.get("layout", "crop") == "crop":
                stat = source.stat()
                center = _cached_face(
                    str(source),
                    segment["start"],
                    segment["end"] - segment["start"],
                    stat.st_mtime_ns,
                    stat.st_size,
                )
                if center:
                    cx, cy = center
            name = f"v{index}"
            base = f"[{index}:v:0]setpts=(PTS-STARTPTS)/{segment['speed']:.8f}"
            layout = settings.get("layout", "crop")
            if layout == "crop":
                graph.append(
                    base + f",scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height}:x='max(0,min(iw-ow,iw*{cx:.8f}-ow/2))':y='max(0,min(ih-oh,ih*{cy:.8f}-oh/2))',setsar=1[{name}]"
                )
            elif layout == "fit":
                graph.append(
                    base + f",scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x111318,setsar=1[{name}]"
                )
            elif layout == "split":
                box = settings.get("face_box", [0.65, 0, 0.35, 0.4])
                if not isinstance(box, list) or len(box) != 4:
                    raise MediaError("Некорректная область камеры.")
                x, y, w, h = [_checked_float(value, "область камеры", 0, 1) for value in box]
                if w < 0.05 or h < 0.05 or x + w > 1.00001 or y + h > 1.00001:
                    raise MediaError("Область камеры выходит за границы кадра.")
                top = round(height / 3 / 2) * 2
                bottom = height - top
                graph += [
                    base + f",split=2[face{index}][game{index}]",
                    f"[face{index}]crop=w='max(2,trunc(iw*{w:.8f}/2)*2)':h='max(2,trunc(ih*{h:.8f}/2)*2)':x='iw*{x:.8f}':y='ih*{y:.8f}',"
                    f"scale={width}:{top}:force_original_aspect_ratio=increase,crop={width}:{top},setsar=1[top{index}]",
                    f"[game{index}]scale={width}:{bottom}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{bottom}:x='max(0,min(iw-ow,iw*{cx:.8f}-ow/2))':y='max(0,min(ih-oh,ih*{cy:.8f}-oh/2))',setsar=1[bottom{index}]",
                    f"[top{index}][bottom{index}]vstack=inputs=2[{name}]",
                ]
            else:
                raise MediaError("Неизвестный вариант кадрирования.")
            zoom = _checked_float(effects.get("zoom", 1), "приближение", 1, 3)
            auto_zoom = settings.get("auto_zoom", False) and "zoom" not in effects
            if zoom > 1 or auto_zoom:
                zoom = zoom if zoom > 1 else 1.03
                scaled_w, scaled_h = round(width * zoom / 2) * 2, round(height * zoom / 2) * 2
                pan_x = (scaled_w - width) * 0.15 if auto_zoom else 0
                pan_y = (scaled_h - height) * 0.15 if auto_zoom else 0
                # Spatial scale/crop preserves timestamps. zoompan manufactures
                # a new frame clock and would require a second FPS conversion,
                # which can stall concat on speed-adjusted short segments.
                graph.append(
                    f"[{name}]scale={scaled_w}:{scaled_h},crop={width}:{height}:"
                    f"x='max(0,min(iw-ow,iw*{cx:.8f}-ow/2+{pan_x:.8f}*sin(t*0.8)))':"
                    f"y='max(0,min(ih-oh,ih*{cy:.8f}-oh/2+{pan_y:.8f}*sin(t*0.6)))',setsar=1[z{index}]"
                )
                name = f"z{index}"
            # Exactly one FPS stage after all time/spatial operations. vstack
            # and trim erase CFR metadata required by FFmpeg xfade.
            graph.append(
                f"[{name}]trim=duration={segment['duration']:.8f},setpts=PTS-STARTPTS,fps={fps:.8f},settb=AVTB[vout{index}]"
            )
            if info["has_audio"]:
                graph.append(
                    f"[{index}:a:0]asetpts=PTS-STARTPTS,{_atempo(segment['speed'])},aresample=48000,"
                    "aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"apad=whole_dur={segment['duration']:.8f},atrim=duration={segment['duration']:.8f},asetpts=PTS-STARTPTS[aout{index}]"
                )
            else:
                graph.append(
                    f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={segment['duration']:.8f},asetpts=PTS-STARTPTS[aout{index}]"
                )
            if effects.get("text"):
                overlays.append(
                    {
                        "start": cursor,
                        "end": cursor + segment["duration"],
                        "text": str(effects["text"])[:200],
                        "size": _checked_float(
                            effects.get("text_size", 64), "размер текста", 24, 120
                        ),
                        "color": effects.get("text_color", "#FFFFFF"),
                    }
                )
            cursor += segment["duration"] - (fade if index < len(segments) - 1 else 0)
        if fade:
            video, audio = "vout0", "aout0"
            elapsed = segments[0]["duration"]
            for index, segment in enumerate(segments[1:], 1):
                graph += [
                    f"[{video}][vout{index}]xfade=transition=fade:duration={fade:.8f}:offset={elapsed - fade:.8f}[joinv{index}]",
                    f"[{audio}][aout{index}]acrossfade=d={fade:.8f}:c1=tri:c2=tri[joina{index}]",
                ]
                video, audio = f"joinv{index}", f"joina{index}"
                elapsed += segment["duration"] - fade
        elif len(segments) > 1:
            graph.append(
                "".join(f"[vout{i}][aout{i}]" for i in range(len(segments)))
                + f"concat=n={len(segments)}:v=1:a=1[mergedv][mergeda]"
            )
            video, audio = "mergedv", "mergeda"
        else:
            video, audio = "vout0", "aout0"
        captions = bool(options.get("subtitles", settings.get("subtitles", True)))
        watermark = str(options.get("watermark", ""))[:80]
        if captions or overlays or watermark:
            _emit(progress, "adding_subtitles", 10)
            _write_ass(
                work / "subtitles.ass",
                mapped_words(document, words) if captions else [],
                settings,
                duration,
                width=width,
                height=height,
                overlays=overlays,
                watermark=watermark,
            )
            graph.append(f"[{video}]ass=filename=subtitles.ass[finalv]")
        else:
            graph.append(f"[{video}]null[finalv]")
        # Normalize the joined stream once. Output-timeline gain envelopes
        # follow it so explicit volume edits are not normalized away.
        audio_filters = (
            ["loudnorm=I=-16:TP=-1.5:LRA=11"] if settings.get("normalize_audio", True) else []
        )
        audio_filters += _gain_filters(segments, fade)
        audio_filters += [
            f"volume={volume:.8f}",
            "alimiter=limit=0.95:level=false",
            "aresample=48000",
        ]
        graph.append(f"[{audio}]" + ",".join(audio_filters) + "[finala]")
        (work / "filters.txt").write_text(";\n".join(graph), encoding="utf-8")
        command += [
            "-filter_complex_script",
            "filters.txt",
            "-map",
            "[finalv]",
            "-map",
            "[finala]",
            "-c:v",
            "libx264" if codec == "h264" else "libx265",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-threads",
            _threads(),
            "-r",
            f"{fps:.8f}",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            "-t",
            f"{duration:.8f}",
        ]
        if codec == "h265":
            command += ["-tag:v", "hvc1", "-x265-params", "pools=1:frame-threads=1:log-level=error"]
        bitrate = options.get("max_bitrate_mbps")
        if bitrate is not None:
            bitrate = _checked_float(bitrate, "битрейт", 0.1, 200)
            command += [
                "-b:v",
                f"{bitrate:.4f}M",
                "-maxrate",
                f"{bitrate:.4f}M",
                "-bufsize",
                f"{bitrate * 2:.4f}M",
            ]
        target = work / "output.mp4"
        command += [target.name]
        if os.name == "nt" and sum(len(value) + 3 for value in command) > 30000:
            raise MediaError(
                "Слишком длинные пути для большого числа фрагментов. Перенесите каталог данных ближе к корню диска."
            )
        _emit(progress, "rendering", 15)
        _run_progress(command, work, duration, progress, cancel, timeout=max(900, duration * 80))
        _cancelled(cancel)
        _emit(progress, "finalizing", 94)
        metadata = probe(target)
        if (
            metadata["width"] != width
            or metadata["height"] != height
            or not metadata["has_audio"]
            or abs(metadata["duration"] - duration) > max(0.35, 2 / fps)
        ):
            raise MediaError("Экспорт не прошёл проверку разрешения, длительности или звука.")
        estimate_preset = (
            "fast" if options.get("preview") else str(options.get("preset", "balanced"))
        )
        metadata.update(
            size_bytes=target.stat().st_size,
            codec=codec,
            estimated_bytes=int(
                duration * ((bitrate or _PRESETS[estimate_preset][2]) * 1e6 + 160000) / 8
            ),
        )
        os.replace(target, output)
        _emit(progress, "done", 100)
        return metadata


def create_proxy(
    source: Path,
    output: Path,
    quality: str,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
) -> dict:
    _cancelled(cancel)
    info = probe(source)
    quality = "480p" if quality == "auto" else quality
    if quality not in ("360p", "480p", "720p", "1080p"):
        raise MediaError("Неизвестное качество proxy.")
    factor = min(1.0, _RESOLUTIONS[quality] / min(info["width"], info["height"]))
    width = max(2, round(info["width"] * factor / 2) * 2)
    height = max(2, round(info["height"] * factor / 2) * 2)
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="proxy-", dir=output.parent) as temporary:
        work = Path(temporary)
        source_name = _input_args(source, work)
        target = work / "proxy.mp4"
        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-threads",
            "1",
            "-progress",
            "pipe:1",
            "-stats_period",
            "0.25",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            _VIDEO_FORMATS,
            "-i",
            source_name,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            f"scale={width}:{height},setsar=1",
            "-r",
            f"{min(info['fps'], 30):.8f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-threads",
            _threads(),
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            target.name,
        ]
        _run_progress(
            args,
            work,
            info["duration"],
            progress,
            cancel,
            start=0,
            span=95,
            timeout=max(900, info["duration"] * 4),
        )
        _cancelled(cancel)
        metadata = probe(target)
        if (
            metadata["width"] != width
            or metadata["height"] != height
            or abs(metadata["duration"] - info["duration"]) > 0.5
        ):
            raise MediaError("Proxy не прошёл проверку параметров.")
        metadata["size_bytes"] = target.stat().st_size
        os.replace(target, output)
        _emit(progress, "done", 100)
        return metadata


def waveform(audio_path: Path, max_points: int = 2048) -> list[float]:
    """Streaming peak buckets: bounded memory even for multi-hour PCM WAV."""
    max_points = max(1, min(16384, int(max_points)))
    with wave.open(str(audio_path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise MediaError("Waveform требует несжатое PCM16-аудио.")
        frames = audio.getnframes()
        channels = audio.getnchannels()
        if not frames:
            return []
        bucket = max(1, math.ceil(frames / max_points))
        result = []
        remaining = bucket
        peak = 0
        while True:
            raw = audio.readframes(65536)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype="<i2").astype(np.int32).reshape(-1, channels)
            offset = 0
            while offset < len(samples):
                length = min(remaining, len(samples) - offset)
                peak = max(peak, int(np.abs(samples[offset : offset + length]).max()))
                remaining -= length
                offset += length
                if remaining == 0:
                    result.append(round(min(1.0, peak / 32768), 5))
                    remaining, peak = bucket, 0
        if remaining < bucket:
            result.append(round(min(1.0, peak / 32768), 5))
        return result
