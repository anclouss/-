"""Validated, non-destructive editing instructions in source/timeline coordinates.

This module has no filesystem, subprocess or model execution. All tools are an
explicit Pydantic union. LLM output can only request one of these operations.
"""

from __future__ import annotations
from copy import deepcopy
from decimal import Decimal
import math
import re
from typing import Annotated, Literal, Union
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .config import CLIP_DEFAULTS

EPSILON = 1e-6
MAX_SEGMENTS = 256
MAX_DURATION = 24 * 3600


class Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, strict=True, validate_default=True
    )


class Effects(Strict):
    zoom: float | None = Field(default=None, ge=1, le=3)
    crop_x: float | None = Field(default=None, ge=0, le=1)
    crop_y: float | None = Field(default=None, ge=0, le=1)
    volume: float | None = Field(default=None, ge=0, le=3)
    text: str | None = Field(default=None, max_length=200)
    text_size: int | None = Field(default=None, ge=24, le=120)
    text_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class Segment(Strict):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    speed: float = Field(default=1, ge=0.25, le=4)
    effects: Effects = Field(default_factory=Effects)


class Settings(Strict):
    layout: Literal["crop", "fit", "split"] = "crop"
    crop_x: float = Field(default=0.5, ge=0, le=1)
    crop_y: float = Field(default=0.5, ge=0, le=1)
    face_box: list[float] = Field(
        default_factory=lambda: [0.65, 0, 0.35, 0.4], min_length=4, max_length=4
    )
    face_tracking: bool = False
    subtitle_font: str = Field(default="Arial", pattern=r"^[\w .-]{1,64}$")
    subtitle_size: int = Field(default=58, ge=24, le=120)
    subtitle_position: float = Field(default=0.76, ge=0.1, le=0.9)
    subtitle_color: str = Field(default="#B8FF60", pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitles: bool = True
    subtitle_highlight: bool = True
    auto_zoom: bool = False
    remove_silence: bool = False
    volume: float = Field(default=1, ge=0, le=3)
    normalize_audio: bool = True
    aspect_ratio: Literal["9:16", "16:9", "1:1", "4:5"] = "9:16"

    @model_validator(mode="after")
    def valid_box(self):
        x, y, w, h = self.face_box
        if (
            any(not math.isfinite(v) or not 0 <= v <= 1 for v in self.face_box)
            or w < 0.05
            or h < 0.05
            or x + w > 1 + EPSILON
            or y + h > 1 + EPSILON
        ):
            raise ValueError("Область камеры должна быть внутри исходного кадра")
        return self


class Transition(Strict):
    type: Literal["none", "fade"] = "none"
    duration: float = Field(default=0.2, ge=0.05, le=2)


class Document(Strict):
    version: Literal[1] = 1
    segments: list[Segment] = Field(min_length=1, max_length=MAX_SEGMENTS)
    settings: Settings = Field(default_factory=Settings)
    transition: Transition = Field(default_factory=Transition)
    caption_overrides: dict[str, str] = Field(default_factory=dict, max_length=20000)


def _new_id():
    return uuid.uuid4().hex


def document_duration(document: dict) -> float:
    segments = document.get("segments", [])
    duration = sum(
        (float(s["end"]) - float(s["start"])) / float(s.get("speed", 1)) for s in segments
    )
    transition = document.get("transition", {})
    if transition.get("type") == "fade":
        duration -= float(transition.get("duration", 0.2)) * max(0, len(segments) - 1)
    return round(duration, 6)


def validate_document(document: dict, source_duration: float) -> dict:
    if (
        isinstance(source_duration, bool)
        or not isinstance(source_duration, (int, float))
        or not math.isfinite(source_duration)
        or source_duration <= 0
    ):
        raise ValueError("Неизвестна длительность исходного видео")
    parsed = Document.model_validate(document)
    lengths = []
    ids = set()
    for segment in parsed.segments:
        if segment.id in ids:
            raise ValueError("Идентификаторы фрагментов должны быть уникальны")
        ids.add(segment.id)
        if segment.end > source_duration + EPSILON or segment.end - segment.start < 0.02:
            raise ValueError(
                "Границы фрагмента вне исходного видео или фрагмент короче 0.02 секунды"
            )
        lengths.append((segment.end - segment.start) / segment.speed)
    if (
        parsed.transition.type == "fade"
        and len(lengths) > 1
        and parsed.transition.duration > min(lengths) / 2 + EPSILON
    ):
        raise ValueError(
            "Длительность перехода не должна превышать половину самого короткого фрагмента"
        )
    normalized_captions = {}
    for key, value in parsed.caption_overrides.items():
        if (
            not re.fullmatch(r"\d+(?:\.\d+)?", key)
            or not 0 <= float(key) <= source_duration
            or len(value) > 200
        ):
            raise ValueError("Некорректные таймкоды или текст субтитров")
        canonical = format(Decimal(key).normalize(), "f")
        if canonical not in normalized_captions or key == canonical:
            normalized_captions[canonical] = value
    result = parsed.model_dump(exclude_none=True)
    result["caption_overrides"] = normalized_captions
    duration = document_duration(result)
    if not 0.02 <= duration <= MAX_DURATION:
        raise ValueError("Длительность монтажа должна быть от 0.02 секунды до 24 часов")
    return result


def default_document(clip: dict, source_duration: float) -> dict:
    # Older rendered clips may have silence removed, but they did not preserve
    # those cuts. Keep their MP4 intact; new editable state uses the source range.
    settings = deepcopy(CLIP_DEFAULTS)
    allowed = Settings.model_fields
    settings.update({k: deepcopy(v) for k, v in clip.get("settings", {}).items() if k in allowed})
    settings["remove_silence"] = False
    overrides = {
        str(w["start"]): str(w["text"])[:200]
        for w in clip.get("words", [])
        if isinstance(w, dict) and "start" in w and "text" in w
    }
    return validate_document(
        {
            "version": 1,
            "segments": [
                {
                    "id": _new_id(),
                    "start": clip.get("start", 0),
                    "end": clip.get("end", source_duration),
                    "speed": 1,
                    "effects": {},
                }
            ],
            "settings": settings,
            "transition": {"type": "none", "duration": 0.2},
            "caption_overrides": overrides,
        },
        source_duration,
    )


class Span(Strict):
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)


class Trim(Span):
    type: Literal["trim"]


class Cut(Span):
    type: Literal["cut", "remove"]


class Split(Strict):
    type: Literal["split"]
    at: float | None = Field(default=None, ge=0)


class Move(Strict):
    type: Literal["move"]
    index: int = Field(ge=0, le=255)
    to: int = Field(ge=0, le=255)


class Speed(Span):
    type: Literal["speed"]
    value: float = Field(ge=0.25, le=4)


class Zoom(Span):
    type: Literal["zoom"]
    scale: float = Field(ge=1, le=3)


class Crop(Strict):
    type: Literal["crop"]
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Resize(Strict):
    type: Literal["resize"]
    aspect_ratio: Literal["9:16", "16:9", "1:1", "4:5"]


class Reframe(Strict):
    type: Literal["reframe"]
    layout: Literal["crop", "fit", "split"] = "crop"
    crop_x: float | None = Field(default=None, ge=0, le=1)
    crop_y: float | None = Field(default=None, ge=0, le=1)


class Layout(Strict):
    type: Literal["layout"]
    value: Literal["crop", "fit", "split"]


class FaceTracking(Strict):
    type: Literal["face_tracking"]
    enabled: bool


class Subtitles(Strict):
    type: Literal["subtitles"]
    enabled: bool
    highlight: bool | None = None


class SubtitleStyle(Strict):
    type: Literal["subtitle_style"]
    size: int | None = Field(default=None, ge=24, le=120)
    font: str | None = Field(default=None, pattern=r"^[\w .-]{1,64}$")
    position: float | None = Field(default=None, ge=0.1, le=0.9)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    preset: Literal["small", "large", "tiktok", "clean"] | None = None


class TextOverlay(Span):
    type: Literal["text_overlay"]
    text: str = Field(max_length=200)
    size: int = Field(default=58, ge=24, le=120)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")


class Normalize(Strict):
    type: Literal["normalize_audio"]
    enabled: bool


class Volume(Span):
    type: Literal["volume"]
    value: float = Field(ge=0, le=3)


class TransitionAction(Strict):
    type: Literal["transition"]
    value: Literal["none", "fade"]
    duration: float = Field(default=0.2, ge=0.05, le=2)


class RemoveSilences(Strict):
    type: Literal["remove_silences"]
    min_duration: float = Field(default=1.5, ge=0.3, le=30)


class Hook(Span):
    type: Literal["create_hook"]


class SourceSpan(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class Replace(Strict):
    type: Literal["replace_segments"]
    segments: list[SourceSpan] = Field(min_length=1, max_length=MAX_SEGMENTS)


class Find(Strict):
    type: Literal["find_moments"]
    query: str = Field(min_length=1, max_length=300)


class Highlights(Strict):
    type: Literal["auto_highlights"]


class TikTok(Strict):
    type: Literal["tiktok"]


Action = Annotated[
    Union[
        Trim,
        Cut,
        Split,
        Move,
        Speed,
        Zoom,
        Crop,
        Resize,
        Reframe,
        Layout,
        FaceTracking,
        Subtitles,
        SubtitleStyle,
        TextOverlay,
        Normalize,
        Volume,
        TransitionAction,
        RemoveSilences,
        Hook,
        Replace,
        Find,
        Highlights,
        TikTok,
    ],
    Field(discriminator="type"),
]


class ActionPlan(Strict):
    summary: str = Field(default="", max_length=1000)
    actions: list[Action] = Field(min_length=1, max_length=32)


def action_schema() -> dict:
    return ActionPlan.model_json_schema()


def _timings(document):
    cursor = 0.0
    fade = document["transition"]["duration"] if document["transition"]["type"] == "fade" else 0
    result = []
    for index, segment in enumerate(document["segments"]):
        length = (segment["end"] - segment["start"]) / segment["speed"]
        result.append((segment, cursor, cursor + length))
        cursor += length - (fade if index < len(document["segments"]) - 1 else 0)
    return result


def _span(action, document, context, *, whole=False, trim=False):
    duration = document_duration(document)
    start = action.get("start")
    end = action.get("end")
    selection = (context or {}).get("selection") or {}
    if start is None and end is None:
        if selection:
            start, end = selection.get("start"), selection.get("end")
        elif trim and (context or {}).get("playhead") is not None:
            start, end = context["playhead"], duration
        elif whole:
            start, end = 0, duration
        else:
            raise ValueError("Выберите диапазон на timeline или укажите начало и конец")
    start = 0 if start is None else start
    end = duration if end is None else end
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end))
        or start < 0
        or end > duration + EPSILON
        or end - start < 0.02
    ):
        raise ValueError(
            "Диапазон должен находиться внутри timeline и длиться не меньше 0.02 секунды"
        )
    return float(start), min(float(end), duration)


def _piece(segment, left, right, original_start):
    result = deepcopy(segment)
    result["id"] = _new_id()
    result["start"] = round(segment["start"] + (left - original_start) * segment["speed"], 6)
    result["end"] = round(segment["start"] + (right - original_start) * segment["speed"], 6)
    return result


def _partition(document, start, end):
    before = []
    middle = []
    after = []
    for segment, left, right in _timings(document):
        if right <= start + EPSILON:
            before.append(deepcopy(segment))
            continue
        if left >= end - EPSILON:
            after.append(deepcopy(segment))
            continue
        if left < start - EPSILON:
            before.append(_piece(segment, left, start, left))
        a, b = max(left, start), min(right, end)
        if b - a >= 0.02 - EPSILON:
            if abs(a - left) < EPSILON and abs(b - right) < EPSILON:
                middle.append(deepcopy(segment))
            else:
                middle.append(_piece(segment, a, b, left))
        if right > end + EPSILON:
            after.append(_piece(segment, end, right, left))
    return before, middle, after


def _effect(document, action, context, changes, *, speed=False):
    start, end = _span(action, document, context, whole=True)
    before, middle, after = _partition(document, start, end)
    for segment in middle:
        if speed:
            segment["speed"] = changes["speed"]
        else:
            segment["effects"].update(changes)
    document["segments"] = before + middle + after


def _source_segments(ranges, source_duration, *, strict=False):
    result = []
    for value in ranges[:MAX_SEGMENTS]:
        start, end = value.get("start"), value.get("end")
        if (
            not all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                for x in (start, end)
            )
            or start < 0
            or end > source_duration + EPSILON
            or end - start < 0.02
        ):
            if strict:
                raise ValueError("Границы заменяющего фрагмента вне исходного видео")
            continue
        if any(
            abs(start - old["start"]) < EPSILON and abs(end - old["end"]) < EPSILON
            for old in result
        ):
            continue
        result.append(
            {"id": _new_id(), "start": float(start), "end": float(end), "speed": 1, "effects": {}}
        )
    if not result:
        raise ValueError("Не найдено подходящих моментов по транскрипции")
    return result


def _find_context_matches(query, context, source_duration):
    matches = (context or {}).get("matches")
    if matches:
        return matches
    words = (context or {}).get("words", [])
    tokens = [s for s in re.findall(r"\w+", query.lower()) if len(s) > 2]
    if not words or not tokens:
        return []
    candidates = []
    for index, word in enumerate(words):
        if any(token in str(word.get("text", "")).lower() for token in tokens):
            start = max(0, float(word["start"]) - 8)
            end = min(source_duration, float(word.get("end", word["start"])) + 20)
            if not any(start < old["end"] and end > old["start"] for old in candidates):
                candidates.append({"start": start, "end": end})
        if len(candidates) >= 8:
            break
    return candidates


def _remove_silences(document, context, minimum):
    pauses = (context or {}).get("pauses") or []
    if not pauses:
        raise ValueError(
            "Паузы ещё не проанализированы. Дождитесь анализа аудио или задайте удаляемый диапазон вручную"
        )
    words = (context or {}).get("words") or []
    kept = []
    for segment in document["segments"]:
        cuts = []
        for pause in pauses:
            try:
                a, b = float(pause["start"]) + 0.12, float(pause["end"]) - 0.12
            except (KeyError, ValueError, TypeError):
                continue
            if not all(math.isfinite(v) for v in (a, b)) or b - a + 0.24 < minimum:
                continue
            a, b = max(a, segment["start"]), min(b, segment["end"])
            if b - a < 0.02 or any(float(w["start"]) < b and float(w["end"]) > a for w in words):
                continue
            cuts.append((a, b))
        if not cuts:
            kept.append(deepcopy(segment))
            continue
        cursor = segment["start"]
        for a, b in sorted(cuts):
            if a > cursor + 0.02:
                piece = deepcopy(segment)
                piece.update(id=_new_id(), start=cursor, end=a)
                kept.append(piece)
            cursor = max(cursor, b)
        if segment["end"] > cursor + 0.02:
            piece = deepcopy(segment)
            piece.update(id=_new_id(), start=cursor, end=segment["end"])
            kept.append(piece)
    document["segments"] = kept
    document["settings"]["remove_silence"] = False


def apply_actions(
    document: dict, actions: list[dict], source_duration: float, context: dict | None = None
) -> dict:
    result = validate_document(deepcopy(document), source_duration)
    parsed = ActionPlan.model_validate({"actions": actions})
    for tool in parsed.actions:
        action = tool.model_dump(exclude_none=True)
        kind = action.pop("type")
        if kind == "trim":
            start, end = _span(action, result, context, trim=True)
            _, middle, _ = _partition(result, start, end)
            result["segments"] = middle
        elif kind in ("cut", "remove"):
            start, end = _span(action, result, context)
            before, _, after = _partition(result, start, end)
            result["segments"] = before + after
        elif kind == "split":
            at = action.get("at", (context or {}).get("playhead"))
            if at is None or not math.isfinite(at) or not 0 < at < document_duration(result):
                raise ValueError("Точка разделения должна находиться внутри timeline")
            before, middle, after = _partition(result, at, document_duration(result))
            result["segments"] = before + middle + after
        elif kind == "move":
            if action["index"] >= len(result["segments"]) or action["to"] >= len(
                result["segments"]
            ):
                raise ValueError("Номер перемещаемого фрагмента вне timeline")
            item = result["segments"].pop(action["index"])
            result["segments"].insert(action["to"], item)
        elif kind == "speed":
            _effect(result, action, context, {"speed": action["value"]}, speed=True)
        elif kind == "zoom":
            _effect(result, action, context, {"zoom": action["scale"]})
        elif kind == "crop":
            result["settings"].update(crop_x=action["x"], crop_y=action["y"], layout="crop")
        elif kind == "resize":
            result["settings"]["aspect_ratio"] = action["aspect_ratio"]
        elif kind == "reframe":
            result["settings"].update(action)
        elif kind == "layout":
            result["settings"]["layout"] = action["value"]
        elif kind == "face_tracking":
            result["settings"]["face_tracking"] = action["enabled"]
        elif kind == "subtitles":
            result["settings"]["subtitles"] = action["enabled"]
            if "highlight" in action:
                result["settings"]["subtitle_highlight"] = action["highlight"]
        elif kind == "subtitle_style":
            preset = action.pop("preset", None)
            if preset:
                result["settings"].update(
                    {"subtitle_size": {"small": 40, "large": 84, "tiktok": 76, "clean": 58}[preset]}
                )
                if preset == "tiktok":
                    result["settings"].update(
                        subtitles=True, subtitle_highlight=True, subtitle_color="#B8FF60"
                    )
                if preset == "clean":
                    result["settings"].update(subtitle_color="#FFFFFF", subtitle_highlight=False)
            for key, target in [
                ("size", "subtitle_size"),
                ("font", "subtitle_font"),
                ("position", "subtitle_position"),
                ("color", "subtitle_color"),
            ]:
                if key in action:
                    result["settings"][target] = action[key]
        elif kind == "text_overlay":
            _effect(
                result,
                action,
                context,
                {
                    "text": action["text"],
                    "text_size": action["size"],
                    "text_color": action["color"],
                },
            )
        elif kind == "normalize_audio":
            result["settings"]["normalize_audio"] = action["enabled"]
        elif kind == "volume":
            if "start" in action or "end" in action or (context or {}).get("selection"):
                _effect(result, action, context, {"volume": action["value"]})
            else:
                result["settings"]["volume"] = action["value"]
        elif kind == "transition":
            result["transition"] = {"type": action["value"], "duration": action["duration"]}
        elif kind == "remove_silences":
            _remove_silences(result, context, action["min_duration"])
        elif kind == "create_hook":
            start, end = _span(action, result, context)
            before, middle, after = _partition(result, start, end)
            result["segments"] = middle + before + after
        elif kind == "replace_segments":
            result["segments"] = _source_segments(action["segments"], source_duration, strict=True)
        elif kind == "find_moments":
            result["segments"] = _source_segments(
                _find_context_matches(action["query"], context, source_duration), source_duration
            )
        elif kind == "auto_highlights":
            highlights = (context or {}).get("highlights") or (context or {}).get("matches") or []
            result["segments"] = _source_segments(highlights, source_duration)
        elif kind == "tiktok":
            result["settings"].update(
                layout="crop",
                aspect_ratio="9:16",
                subtitles=True,
                subtitle_highlight=True,
                subtitle_size=76,
                normalize_audio=True,
            )
        result = validate_document(result, source_duration)
    return result
