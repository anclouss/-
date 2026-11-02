"""Versioned editor sessions, bounded transcript access and explicit export APIs."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import threading
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from . import db, editor_store, editing, editor_ai, editor_jobs
from .config import DATA, CLIP_DEFAULTS
from .media import probe

router = APIRouter()
_chat_requests: dict[tuple[str, str], threading.Event] = {}


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Save(Body):
    revision: int = Field(ge=0)
    state: dict
    label: str = Field(default="Монтаж сохранён", max_length=120)


class Actions(Body):
    revision: int = Field(ge=0)
    actions: list[dict] = Field(min_length=1, max_length=32)
    label: str = Field(default="Изменение монтажа", max_length=120)
    context: dict = Field(default_factory=dict)


class History(Body):
    revision: int = Field(ge=0)
    action: str = Field(pattern="^(undo|redo|reset)$")


class Chat(Body):
    revision: int = Field(ge=0)
    prompt: str = Field(min_length=1, max_length=3000)
    context: dict = Field(default_factory=dict)
    request_id: str = Field(default_factory=lambda: uuid4().hex, pattern="^[a-zA-Z0-9_-]{1,80}$")


class ChatCancel(Body):
    request_id: str = Field(pattern="^[a-zA-Z0-9_-]{1,80}$")


class Decision(Body):
    revision: int = Field(ge=0)
    action: str = Field(pattern="^(apply|cancel)$")


class Proxy(Body):
    quality: str = Field(default="auto", pattern="^(auto|360p|480p|720p|1080p)$")


class Preview(Proxy):
    revision: int = Field(ge=0)
    proposal_id: str | None = None


class Export(Body):
    revision: int = Field(ge=0)
    platform: str = Field(default="tiktok", pattern="^(tiktok|shorts|reels|custom)$")
    resolution: str = Field(default="1080p", pattern="^(720p|1080p|1440p|4k)$")
    fps: str = Field(default="30", pattern="^(original|30|60)$")
    preset: str = Field(default="balanced", pattern="^(fast|balanced|high|maximum)$")
    codec: str = Field(default="h264", pattern="^(h264|h265)$")
    subtitles: bool = True
    watermark: str = Field(default="", max_length=100)
    max_bitrate_mbps: float | None = Field(default=None, ge=0.5, le=100)
    crf: int | None = Field(default=None, ge=14, le=36)


def require(kind, ident):
    value = db.get(kind, ident)
    if not value:
        raise HTTPException(404, "Не найдено")
    return value


def records(ident):
    clip = require("clips", ident)
    project = require("projects", clip["project_id"])
    touch = project.get("editor_opened_at")
    if (
        not touch
        or (datetime.now(timezone.utc) - datetime.fromisoformat(touch)).total_seconds() > 60
    ):
        project = db.update("projects", project["id"], editor_opened_at=db.now())
    editor_store.ensure(clip, project)
    return clip, project


@lru_cache(maxsize=4)
def _read_words(filename, size, modified):
    return json.loads(Path(filename).read_text(encoding="utf-8"))


def words_for(clip, project):
    path = DATA / "transcripts" / f"{project['id']}.json"
    if path.is_file():
        stat = path.stat()
        return _read_words(str(path), stat.st_size, stat.st_mtime_ns)
    return clip.get("words", [])


def signals_for(project):
    path = DATA / "signals" / f"{project['id']}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def context_for(clip, project, context):
    words = words_for(clip, project)
    signals = signals_for(project)
    return {
        **context,
        "_source_path": project.get("source_path"),
        "words": words,
        "pauses": signals.get("pauses", []),
        "highlights": [
            {"start": c["start"], "end": c["end"], "score": c.get("score", 0)}
            for c in db.clips(project["id"])
            if not c.get("manual_editor")
        ],
    }


def meta_for(project):
    path = project.get("source_path")
    available = bool(path and Path(path).is_file())
    info = project.get("media") or {}
    if available and not info:
        info = probe(Path(path))
        db.update("projects", project["id"], media=info)
    return {
        "duration": project.get("duration") or info.get("duration", 0),
        "width": info.get("width", 0),
        "height": info.get("height", 0),
        "fps": info.get("fps", 30),
        "available": available,
    }


def response(ident):
    from .main import public_clip, public_project

    clip, project = records(ident)
    session = editor_store.get(ident)
    waveform_file = DATA / "waveforms" / f"{project['id']}.json"
    waveform = (
        json.loads(waveform_file.read_text(encoding="utf-8")) if waveform_file.is_file() else []
    )
    with db.connection() as con:
        rows = con.execute(
            "SELECT * FROM jobs WHERE project_id=? AND kind='proxy' ORDER BY id DESC LIMIT 10",
            (project["id"],),
        ).fetchall()
    proxy = {
        "status": "source",
        "quality": "auto",
        "url": f"/media/source/{project['id']}",
        "error": None,
    }
    if rows:
        public = editor_jobs.public(dict(rows[0]))
        proxy = {**public, "quality": public["options"].get("quality", "auto")}
    result = {
        **session,
        "clip": {**public_clip(clip), "words": []},
        "project": public_project(project),
        "messages": editor_ai.messages(ident),
        "proposals": editor_ai.proposals(ident),
        "source": meta_for(project),
        "proxy": proxy,
        "waveform": {"peaks": waveform, "duration": project.get("duration") or 0},
        "exports": editor_jobs.list_jobs(ident),
    }
    return result


def translate(exc):
    if type(exc).__name__ in ("ConflictError", "RevisionConflict"):
        return HTTPException(409, str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(404, "Не найдено")
    from .ai import AIError

    if isinstance(exc, AIError):
        return HTTPException(422, str(exc))
    if isinstance(exc, (ValueError, TypeError)):
        return HTTPException(422, str(exc)[:500])
    return exc


@router.get("/api/editor/{ident}")
def get_editor(ident: str):
    return response(ident)


@router.patch("/api/editor/{ident}")
def save(ident: str, body: Save):
    records(ident)
    try:
        editor_store.save(ident, body.revision, body.state, body.label)
    except Exception as exc:
        raise translate(exc)
    return response(ident)


@router.post("/api/editor/{ident}/actions")
def actions(ident: str, body: Actions):
    clip, project = records(ident)
    current = editor_store.get(ident)
    if current["revision"] != body.revision:
        raise HTTPException(409, "Монтаж изменился в другой вкладке. Обновите состояние.")
    try:
        state = editing.apply_actions(
            current["state"],
            body.actions,
            float(project["duration"]),
            context_for(clip, project, body.context),
        )
        editor_store.save(ident, body.revision, state, body.label)
    except Exception as exc:
        raise translate(exc)
    return response(ident)


@router.post("/api/editor/{ident}/history")
def history(ident: str, body: History):
    records(ident)
    try:
        editor_store.history(ident, body.revision, body.action)
    except Exception as exc:
        raise translate(exc)
    return response(ident)


@router.get("/api/editor/{ident}/transcript")
def transcript(
    ident: str,
    q: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    start: float | None = Query(default=None, ge=0, allow_inf_nan=False),
    end: float | None = Query(default=None, ge=0, allow_inf_nan=False),
):
    clip, project = records(ident)
    words = words_for(clip, project)
    overrides = editor_store.get(ident)["state"].get("caption_overrides", {})
    if q:
        matched = set()
        needle = q.casefold()
        for index, word in enumerate(words):
            phrase = " ".join(w["text"] for w in words[index : index + 12]).casefold()
            if needle in phrase:
                matched.update(range(index, min(len(words), index + 12)))
        selected = [word for index, word in enumerate(words) if index in matched]
    else:
        selected = words
    if start is not None:
        selected = [w for w in selected if w["end"] > start]
    if end is not None:
        selected = [w for w in selected if w["start"] < end]
    page = [
        {
            **w,
            "text": overrides.get(
                str(w["start"]),
                overrides.get(format(float(w["start"]), ".3f").rstrip("0").rstrip("."), w["text"]),
            ),
        }
        for w in selected[offset : offset + limit]
    ]
    return {"words": page, "total": len(selected), "offset": offset}


@router.post("/api/editor/{ident}/chat")
async def chat(ident: str, body: Chat, request: Request):
    clip, project = records(ident)
    token = (ident, body.request_id)
    if token in _chat_requests:
        raise HTTPException(409, "Эта команда уже обрабатывается")
    flag = threading.Event()
    _chat_requests[token] = flag

    async def disconnect_watch():
        while not flag.is_set():
            if await request.is_disconnected():
                flag.set()
                return
            await asyncio.sleep(0.25)

    watcher = asyncio.create_task(disconnect_watch())
    try:
        context = context_for(clip, project, body.context)
        context["_cancel"] = flag.is_set
        signals = {**signals_for(project), "highlights": context["highlights"]}
        await run_in_threadpool(
            editor_ai.propose,
            ident,
            body.revision,
            body.prompt,
            context,
            words_for(clip, project),
            signals,
            db.settings(),
        )
    except Exception as exc:
        raise translate(exc)
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        _chat_requests.pop(token, None)
    return response(ident)


@router.post("/api/editor/{ident}/chat/cancel")
def cancel_chat(ident: str, body: ChatCancel):
    require("clips", ident)
    event = _chat_requests.get((ident, body.request_id))
    if event:
        event.set()
        if hasattr(editor_ai, "cancel_requests"):
            editor_ai.cancel_requests(ident)
    return {"cancelled": bool(event)}


@router.post("/api/editor/{ident}/proposals/{proposal}")
def proposal(ident: str, proposal: str, body: Decision):
    records(ident)
    try:
        editor_ai.decision(ident, proposal, body.revision, body.action)
    except Exception as exc:
        raise translate(exc)
    return response(ident)


@router.post("/api/editor/{ident}/proxy", status_code=202)
def proxy(ident: str, body: Proxy):
    clip, project = records(ident)
    current = editor_store.get(ident)
    try:
        return editor_jobs.queue(
            clip,
            project,
            "proxy",
            current["state"],
            body.model_dump(),
            revision=current["revision"],
        )
    except Exception as exc:
        raise translate(exc)


@router.post("/api/editor/{ident}/preview", status_code=202)
def preview(ident: str, body: Preview):
    clip, project = records(ident)
    current = editor_store.get(ident)
    if current["revision"] != body.revision:
        raise HTTPException(409, "Сначала сохраните монтаж")
    state = current["state"]
    if body.proposal_id:
        proposal = next(
            (p for p in editor_ai.proposals(ident) if p["id"] == body.proposal_id), None
        )
        if (
            not proposal
            or proposal["status"] != "pending"
            or proposal["base_revision"] != body.revision
        ):
            raise HTTPException(409, "Предложение устарело или отменено")
        state = proposal["state"]
    quality = body.quality if body.quality != "auto" else "480p"
    try:
        return editor_jobs.queue(
            clip,
            project,
            "preview",
            state,
            {
                "preview": True,
                "quality": quality,
                "resolution": quality,
                "preset": "fast",
                "fps": "30",
                "codec": "h264",
                "subtitles": state["settings"].get("subtitles", True),
                "watermark": "",
            },
            words_for(clip, project),
            body.revision,
        )
    except Exception as exc:
        raise translate(exc)


@router.post("/api/editor/{ident}/exports", status_code=202)
def export(ident: str, body: Export):
    clip, project = records(ident)
    current = editor_store.get(ident)
    if current["revision"] != body.revision:
        raise HTTPException(409, "Сначала сохраните монтаж")
    info = meta_for(project)
    if body.resolution == "4k" and not (
        max(info["width"], info["height"]) >= 3840 and min(info["width"], info["height"]) >= 2160
    ):
        raise HTTPException(422, "Для 4K требуется исходник с разрешением не ниже 3840×2160")
    try:
        from copy import deepcopy

        snapshot = deepcopy(current["state"])
        if body.platform != "custom":
            snapshot["settings"]["aspect_ratio"] = "9:16"
        return editor_jobs.queue(
            clip,
            project,
            "export",
            snapshot,
            body.model_dump(exclude={"revision"}, exclude_none=True),
            words_for(clip, project),
            body.revision,
        )
    except Exception as exc:
        raise translate(exc)


@router.get("/api/editor-jobs/{ident}")
def get_job(ident: int):
    row = editor_jobs.job(ident)
    if not row:
        raise HTTPException(404, "Задание не найдено")
    return editor_jobs.public(row)


@router.post("/api/editor-jobs/{ident}/cancel")
def cancel_job(ident: int):
    try:
        return editor_jobs.request_cancel(ident)
    except Exception as exc:
        raise translate(exc)


@router.post("/api/editor-jobs/{ident}/retry")
def retry_job(ident: int):
    try:
        return editor_jobs.retry(ident)
    except Exception as exc:
        raise translate(exc)


def job_path(ident):
    row = editor_jobs.job(ident)
    if not row or row["kind"] not in ("proxy", "preview", "export"):
        raise HTTPException(404, "Файл не найден")
    payload = json.loads(row["payload"])
    path = Path(payload["output"])
    if row["status"] != "done" or not path.is_file():
        raise HTTPException(409, "Файл ещё не готов")
    return path


@router.get("/media/editor/{ident}")
def media(ident: int):
    return FileResponse(job_path(ident), media_type="video/mp4")


@router.get("/api/editor-jobs/{ident}/download")
def download(ident: int):
    return FileResponse(
        job_path(ident), media_type="video/mp4", filename=f"clipforge-export-{ident}.mp4"
    )


@router.post("/api/projects/{ident}/editor", status_code=201)
def manual_editor(ident: str):
    from .main import public_clip

    project = require("projects", ident)
    if (
        not project.get("duration")
        or not project.get("source_path")
        or not Path(project["source_path"]).is_file()
    ):
        raise HTTPException(409, "Дождитесь загрузки исходного видео")
    clip = {
        "id": db.new_id(),
        "project_id": ident,
        "start": 0,
        "end": project["duration"],
        "title": project["title"],
        "score": 0,
        "reason": "Ручной AI Video Editor",
        "status": "draft",
        "revision": 0,
        "settings": dict(CLIP_DEFAULTS),
        "words": [],
        "manual_editor": True,
        "created_at": db.now(),
        "error": None,
    }
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        if not con.execute("SELECT id FROM projects WHERE id=?", (ident,)).fetchone():
            raise HTTPException(404, "Проект уже удалён")
        existing = next(
            (
                json.loads(row[0])
                for row in con.execute("SELECT payload FROM clips WHERE project_id=?", (ident,))
                if json.loads(row[0]).get("manual_editor")
            ),
            None,
        )
        if existing:
            clip = existing
        else:
            con.execute(
                "INSERT INTO clips(id,project_id,payload) VALUES(?,?,?)",
                (clip["id"], ident, json.dumps(clip, ensure_ascii=False)),
            )
    editor_store.ensure(clip, project)
    return public_clip(clip)


@router.post("/api/projects/{ident}/highlights", status_code=202)
def find_highlights(ident: str):
    from .main import public_project

    project = require("projects", ident)
    if not project.get("source_path") or not Path(project["source_path"]).is_file():
        raise HTTPException(410, "Для поиска моментов нужен исходник")
    if project.get("media", {}).get("has_audio") is False:
        raise HTTPException(422, "Для поиска моментов по речи требуется аудиодорожка")
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        current = con.execute("SELECT payload FROM projects WHERE id=?", (ident,)).fetchone()
        if not current:
            raise HTTPException(404, "Проект удалён")
        active = con.execute(
            "SELECT id FROM jobs WHERE project_id=? AND kind='analyze' AND status IN ('queued','running')",
            (ident,),
        ).fetchone()
        if not active:
            value = json.loads(current[0])
            value.update(
                status="queued",
                stage="analyzing",
                error=None,
                defer_render=True,
                updated_at=db.now(),
            )
            con.execute(
                "UPDATE projects SET payload=? WHERE id=?",
                (json.dumps(value, ensure_ascii=False), ident),
            )
            con.execute(
                "INSERT INTO jobs(project_id,kind,created_at) VALUES(?,?,?)",
                (ident, "analyze", db.now()),
            )
    return public_project(require("projects", ident), True, True)


@router.get("/api/editor/{ident}/events")
async def events(ident: str, request: Request):
    records(ident)

    async def stream():
        previous = None
        touched = asyncio.get_running_loop().time()
        while not await request.is_disconnected():
            session = editor_store.get(ident)
            if not session:
                return
            if asyncio.get_running_loop().time() - touched > 60:
                clip = db.get("clips", ident)
                if clip:
                    db.update("projects", clip["project_id"], editor_opened_at=db.now())
                touched = asyncio.get_running_loop().time()
            with db.connection() as con:
                chat = con.execute(
                    "SELECT COUNT(*),COALESCE(MAX(id),0),SUM(CASE WHEN status='processing' THEN 1 ELSE 0 END),SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) FROM editor_chat_messages WHERE clip_id=?",
                    (ident,),
                ).fetchone()
            clip = db.get("clips", ident)
            project = db.get("projects", clip["project_id"]) if clip else None
            fingerprint = (
                {k: project.get(k) for k in ("status", "stage", "progress", "updated_at")}
                if project
                else None
            )
            data = {
                "revision": session["revision"],
                "exports": editor_jobs.list_jobs(ident),
                "chat": list(chat),
                "project": fingerprint,
            }
            value = json.dumps(data, ensure_ascii=False)
            if value != previous:
                yield "data: " + value + "\n\n"
                previous = value
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )
