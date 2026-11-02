"""Single-user, local-first ClipForge API. Run worker as a separate process."""

from contextlib import asynccontextmanager
from pathlib import Path
import math
import os
import shutil
import tempfile
from urllib.parse import urlsplit
import zipfile

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import db
from .config import ROOT, DATA, MAX_BYTES, MAX_UPLOAD_GB, MAX_DURATION
from .media import probe
from .upload_limit import UploadLimitMiddleware


@asynccontextmanager
async def lifespan(app):
    db.init()
    from . import editor_store, editor_ai

    editor_store.init()
    editor_ai.init()
    yield


app = FastAPI(title="ClipForge", version="0.2.0", lifespan=lifespan)
app.add_middleware(UploadLimitMiddleware)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def local_origin(request: Request, call_next):
    # Browsers must not let an unrelated website drive the local processing server.
    origin = request.headers.get("origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
        o = urlsplit(origin)
        if o.netloc != request.headers.get("host") and origin not in (
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ):
            return JSONResponse({"detail": "Недопустимый Origin"}, status_code=403)
    return await call_next(request)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class UrlProject(Strict):
    url: str = Field(max_length=2048)
    title: str = Field(default="", max_length=120)
    mode: str = Field(default="auto", pattern="^(auto|editor)$")
    defer_render: bool = True


class Word(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=100)

    @model_validator(mode="after")
    def order(self):
        if self.end <= self.start:
            raise ValueError("Конец слова должен быть позже начала")
        return self


class ClipSettings(Strict):
    layout: str = Field(default="crop", pattern="^(crop|fit|split)$")
    crop_x: float = Field(default=0.5, ge=0, le=1)
    crop_y: float = Field(default=0.5, ge=0, le=1)
    face_box: list[float] = Field(
        default_factory=lambda: [0.65, 0, 0.35, 0.4], min_length=4, max_length=4
    )
    face_tracking: bool = False
    subtitle_font: str = Field(default="Arial", pattern=r"^[\w -]{1,64}$")
    subtitle_size: int = Field(default=58, ge=24, le=120)
    subtitle_position: float = Field(default=0.76, ge=0.1, le=0.9)
    subtitle_color: str = Field(default="#B8FF60", pattern="^#[0-9A-Fa-f]{6}$")
    subtitles: bool = True
    auto_zoom: bool = False
    remove_silence: bool = False

    @model_validator(mode="after")
    def box(self):
        x, y, w, h = self.face_box
        if (
            any(not math.isfinite(v) or v < 0 or v > 1 for v in self.face_box)
            or w < 0.05
            or h < 0.05
            or x + w > 1.00001
            or y + h > 1.00001
        ):
            raise ValueError(
                "Область камеры должна быть внутри кадра, ширина и высота не меньше 5%"
            )
        return self


class EditClip(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str | None = Field(default=None, max_length=120)
    settings: ClipSettings
    words: list[Word] | None = Field(default=None, max_length=1500)


class SettingsPatch(Strict):
    transcription_provider: str | None = Field(default=None, pattern="^(local|openai)$")
    analysis_provider: str | None = Field(default=None, pattern="^(ollama|openai|heuristic)$")
    whisper_model: str | None = Field(
        default=None, pattern="^(tiny|base|small|medium|large-v3|turbo)$"
    )
    openai_model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9._:-]{1,100}$")
    ollama_model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9._:/-]{1,100}$")
    language: str | None = Field(default=None, pattern="^(auto|[a-z]{2,3})$")
    max_clips: int | None = Field(default=None, ge=1, le=20)
    retention_days: int | None = Field(default=None, ge=1, le=365)


def require(kind, ident):
    item = db.get(kind, ident)
    if not item:
        raise HTTPException(404, "Не найдено")
    return item


def latest_export(clip_id):
    import json

    with db.connection() as con:
        for row in con.execute(
            "SELECT * FROM jobs WHERE clip_id=? AND kind='export' AND status='done' ORDER BY id DESC",
            (clip_id,),
        ):
            payload = json.loads(row["payload"])
            if payload.get("output") and Path(payload["output"]).is_file():
                return dict(row), payload
    return None


def exports_for_project(project_id):
    import json

    result = {}
    with db.connection() as con:
        for row in con.execute(
            "SELECT * FROM jobs WHERE project_id=? AND kind='export' AND status='done' ORDER BY id DESC",
            (project_id,),
        ):
            if row["clip_id"] in result:
                continue
            payload = json.loads(row["payload"])
            if payload.get("output") and Path(payload["output"]).is_file():
                result[row["clip_id"]] = (dict(row), payload)
    return result


def public_clip(c, exported=None):
    if exported is None:
        exported = latest_export(c["id"])
    if exported:
        row, payload = exported
        return {
            **{k: v for k, v in c.items() if k not in ("output_path",)},
            "status": "ready",
            "duration": payload.get("metadata", {}).get(
                "duration", round(c["end"] - c["start"], 3)
            ),
            "preview_url": f"/media/editor/{row['id']}",
            "download_url": f"/api/editor-jobs/{row['id']}/download",
        }
    return {
        **{k: v for k, v in c.items() if k not in ("output_path",)},
        "duration": round(c["end"] - c["start"], 3),
        "preview_url": f"/media/clips/{c['id']}?v={c.get('revision', 0)}"
        if c["status"] == "ready"
        else None,
        "download_url": f"/api/clips/{c['id']}/download" if c["status"] == "ready" else None,
    }


def public_project(p, detail=False, compact=False, count=None):
    clips = db.clips(p["id"]) if detail or count is None else []
    result = {k: v for k, v in p.items() if k not in ("source_path", "source_url")}
    result["clip_count"] = len(clips) if count is None else count
    result["source_available"] = bool(p.get("source_path") and Path(p["source_path"]).is_file())
    if detail:
        exports = exports_for_project(p["id"])
        result["clips"] = [
            {**public_clip(c, exports.get(c["id"], False)), "words": []}
            if compact
            else public_clip(c, exports.get(c["id"], False))
            for c in clips
        ]
    return result


def new_project(title, name, source_path=None, source_url=None, duration=None):
    ident = db.new_id()
    return {
        "id": ident,
        "title": title or name,
        "source_name": name,
        "source_path": str(source_path) if source_path else None,
        "source_url": source_url,
        "duration": duration,
        "status": "queued",
        "stage": "uploading",
        "progress": 0,
        "error": None,
        "created_at": db.now(),
        "updated_at": db.now(),
        "analysis_provider": db.settings()["analysis_provider"],
    }


def persist_job(project):
    with db.connection() as c:
        import json

        c.execute(
            "INSERT INTO projects VALUES(?,?)",
            (project["id"], json.dumps(project, ensure_ascii=False)),
        )
        c.execute(
            "INSERT INTO jobs(project_id,kind,created_at) VALUES(?,?,?)",
            (project["id"], "prepare" if project.get("mode") == "editor" else "analyze", db.now()),
        )


def check_video(path, require_audio=True):
    try:
        info = probe(path)
    except (ValueError, RuntimeError, OSError) as e:
        raise HTTPException(
            400, "FFmpeg не смог прочитать видео. Загрузите MP4, MKV, MOV или WebM с аудио."
        ) from e
    if require_audio and not info.get("has_audio"):
        raise HTTPException(400, "В видео нет аудиодорожки: распознавание речи невозможно")
    if not 1 <= info["duration"] <= MAX_DURATION:
        raise HTTPException(400, "Допустимая длительность видео: от 1 секунды до 12 часов")
    return info


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": "0.2.0",
        "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
    }


@app.get("/api/projects")
def projects():
    counts = db.project_counts()
    return [public_project(p, count=counts.get(p["id"], 0)) for p in db.all_projects()]


@app.get("/api/projects/{ident}")
def project(ident: str, compact: bool = False):
    return public_project(require("projects", ident), True, compact)


@app.post("/api/projects/upload", status_code=201)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(default="", max_length=120),
    mode: str = Form(default="auto", pattern="^(auto|editor)$"),
    defer_render: bool = Form(default=True),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts", ".flv"):
        raise HTTPException(400, "Неподдерживаемый формат видео")
    ident = db.new_id()
    path = DATA / "sources" / (ident + suffix)
    try:
        size = 0
        with path.open("xb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, f"Максимальный размер: {MAX_UPLOAD_GB:g} ГБ")
                if shutil.disk_usage(DATA).free < len(chunk) + 100 * 1024**2:
                    raise HTTPException(507, "Недостаточно места на диске")
                await run_in_threadpool(out.write, chunk)
        info = await run_in_threadpool(check_video, path, mode != "editor")
        if await request.is_disconnected():
            raise HTTPException(499, "Загрузка отменена")
        p = new_project(
            title, (Path(file.filename or "Видео").name)[:200], path, duration=info["duration"]
        )
        p.update(mode=mode, defer_render=defer_render, media=info)
        persist_job(p)
        return public_project(p, True)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.post("/api/projects/url", status_code=201)
def from_url(body: UrlProject):
    try:
        u = urlsplit(body.url)
        port = u.port
    except ValueError as exc:
        raise HTTPException(400, "Некорректная ссылка на видео") from exc
    allowed = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "twitch.tv",
        "www.twitch.tv",
        "m.twitch.tv",
    }
    if (
        u.scheme != "https"
        or u.hostname not in allowed
        or u.username
        or u.password
        or port not in (None, 443)
    ):
        raise HTTPException(
            400, "Поддерживаются только HTTPS-ссылки на публичные записи YouTube и Twitch"
        )
    if not u.path or u.path == "/":
        raise HTTPException(400, "Укажите ссылку на конкретную запись")
    p = new_project(body.title, u.hostname, source_url=body.url)
    p.update(mode=body.mode, defer_render=body.defer_render)
    persist_job(p)
    return public_project(p, True)


@app.post("/api/projects/{ident}/retry")
def retry(ident: str):
    p = require("projects", ident)
    if db.active(ident):
        raise HTTPException(409, "Проект уже в очереди или обрабатывается")
    if p["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, "Повтор доступен только для проекта с ошибкой")
    if not p.get("source_url") and not (p.get("source_path") and Path(p["source_path"]).is_file()):
        raise HTTPException(410, "Исходник удалён. Загрузите видео заново")
    import json

    with db.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute(
            "SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (ident,)
        ).fetchone():
            raise HTTPException(409, "Проект уже обрабатывается")
        row = c.execute("SELECT payload FROM projects WHERE id=?", (ident,)).fetchone()
        if not row:
            raise HTTPException(404, "Не найдено")
        current = json.loads(row[0])
        current.update(status="queued", error=None, progress=0, updated_at=db.now())
        c.execute(
            "UPDATE projects SET payload=? WHERE id=?",
            (json.dumps(current, ensure_ascii=False), ident),
        )
        c.execute(
            "INSERT INTO jobs(project_id,kind,created_at) VALUES(?,?,?)",
            (ident, "prepare" if current.get("mode") == "editor" else "analyze", db.now()),
        )
    return public_project(require("projects", ident), True)


@app.delete("/api/projects/{ident}")
def delete_project(ident: str):
    p = require("projects", ident)
    if db.active(ident):
        raise HTTPException(409, "Дождитесь завершения обработки")
    clips = db.clips(ident)
    artifacts = []
    with db.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute(
            "SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (ident,)
        ).fetchone():
            raise HTTPException(409, "Дождитесь завершения обработки")
        import json

        for row in c.execute("SELECT payload FROM jobs WHERE project_id=?", (ident,)):
            payload = json.loads(row["payload"] or "{}")
            artifacts.extend(payload.get(key) for key in ("output", "manifest") if payload.get(key))
        c.execute("DELETE FROM projects WHERE id=?", (ident,))
    for filename in artifacts:
        path = Path(filename)
        if path.resolve().is_relative_to(DATA.resolve()):
            path.unlink(missing_ok=True)
    for c in clips:
        (DATA / "clips" / f"{c['id']}.mp4").unlink(missing_ok=True)
    if p.get("source_path"):
        Path(p["source_path"]).unlink(missing_ok=True)
    (DATA / "audio" / f"{ident}.wav").unlink(missing_ok=True)
    (DATA / "transcripts" / f"{ident}.json").unlink(missing_ok=True)
    (DATA / "transcripts" / f"{ident}.meta.json").unlink(missing_ok=True)
    (DATA / "transcripts" / f"{ident}.highlights.json").unlink(missing_ok=True)
    (DATA / "signals" / f"{ident}.json").unlink(missing_ok=True)
    (DATA / "waveforms" / f"{ident}.json").unlink(missing_ok=True)
    return {"deleted": True}


@app.get("/api/clips/{ident}")
def get_clip(ident: str):
    return public_clip(require("clips", ident))


@app.post("/api/clips/{ident}/render")
def edit(ident: str, body: EditClip):
    clip = require("clips", ident)
    p = require("projects", clip["project_id"])
    if db.active(p["id"], ident):
        raise HTTPException(409, "Клип уже в очереди или обрабатывается")
    if not p.get("source_path") or not Path(p["source_path"]).is_file():
        raise HTTPException(410, "Исходник удалён; повторный экспорт недоступен")
    if (
        body.end <= body.start
        or body.end > float(p["duration"]) + 0.05
        or body.end - body.start > 180
        or body.end - body.start < 1
    ):
        raise HTTPException(
            422, "Границы должны быть внутри видео, длительность — от 1 до 180 секунд"
        )
    words = [w.model_dump() for w in body.words] if body.words is not None else clip["words"]
    if body.words is not None:
        if any(w["end"] > p["duration"] + 0.05 for w in words) or any(
            words[i]["start"] > words[i + 1]["start"] for i in range(len(words) - 1)
        ):
            raise HTTPException(
                422, "Таймкоды слов должны идти по порядку и находиться внутри видео"
            )
    # Include newly exposed speech when handles extend, retaining text edits.
    transcript = DATA / "transcripts" / f"{p['id']}.json"
    if transcript.is_file() and (body.start < clip["start"] or body.end > clip["end"]):
        import json

        full = json.loads(transcript.read_text(encoding="utf-8"))
        edited = {(w["start"], w["end"]): w for w in words}
        words = [
            edited.get((w["start"], w["end"]), w)
            for w in full
            if w["end"] > body.start and w["start"] < body.end
        ]
    value = {
        **clip,
        "start": body.start,
        "end": body.end,
        "settings": body.settings.model_dump(),
        "words": words,
        "status": "queued",
        "error": None,
    }
    if body.title is not None:
        value["title"] = body.title
    import json

    with db.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute(
            "SELECT 1 FROM jobs WHERE project_id=? AND (clip_id=? OR kind='analyze') AND status IN ('queued','running')",
            (p["id"], ident),
        ).fetchone():
            raise HTTPException(409, "Клип уже обрабатывается")
        current = c.execute("SELECT payload FROM projects WHERE id=?", (p["id"],)).fetchone()
        current_clip = c.execute("SELECT id FROM clips WHERE id=?", (ident,)).fetchone()
        if not current or not current_clip:
            raise HTTPException(404, "Не найдено")
        current = json.loads(current[0])
        if not current.get("source_path") or not Path(current["source_path"]).is_file():
            raise HTTPException(410, "Исходник удалён")
        c.execute(
            "UPDATE clips SET payload=? WHERE id=?", (json.dumps(value, ensure_ascii=False), ident)
        )
        c.execute(
            "INSERT INTO jobs(project_id,clip_id,kind,created_at) VALUES(?,?,?,?)",
            (p["id"], ident, "render", db.now()),
        )
    return public_clip(value)


@app.delete("/api/clips/{ident}")
def delete_clip(ident: str):
    c = require("clips", ident)
    if db.active(c["project_id"], ident):
        raise HTTPException(409, "Дождитесь завершения обработки")
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute(
            "SELECT 1 FROM jobs WHERE project_id=? AND (clip_id=? OR kind='analyze') AND status IN ('queued','running')",
            (c["project_id"], ident),
        ).fetchone():
            raise HTTPException(409, "Дождитесь завершения обработки")
        import json

        artifacts = []
        for row in con.execute(
            "SELECT payload FROM jobs WHERE clip_id=? AND kind IN ('preview','export')", (ident,)
        ):
            payload = json.loads(row["payload"] or "{}")
            artifacts.extend(payload.get(key) for key in ("output", "manifest") if payload.get(key))
        con.execute("DELETE FROM jobs WHERE clip_id=? AND kind!='proxy'", (ident,))
        con.execute("UPDATE jobs SET clip_id=NULL WHERE clip_id=? AND kind='proxy'", (ident,))
        con.execute("DELETE FROM clips WHERE id=?", (ident,))
    for filename in artifacts:
        path = Path(filename)
        if path.resolve().is_relative_to(DATA.resolve()):
            path.unlink(missing_ok=True)
    (DATA / "clips" / f"{ident}.mp4").unlink(missing_ok=True)
    return {"deleted": True}


def ready_path(ident):
    c = require("clips", ident)
    exported = latest_export(ident)
    if exported:
        return Path(exported[1]["output"])
    path = DATA / "clips" / f"{ident}.mp4"
    if c["status"] != "ready" or not path.is_file():
        raise HTTPException(409, "Экспорт ещё не готов")
    return path


@app.get("/media/clips/{ident}")
def preview(ident: str):
    return FileResponse(ready_path(ident), media_type="video/mp4")


@app.get("/api/clips/{ident}/download")
def download(ident: str):
    return FileResponse(
        ready_path(ident), media_type="video/mp4", filename=f"clipforge-{ident[:8]}.mp4"
    )


@app.get("/media/source/{ident}")
def source(ident: str):
    p = require("projects", ident)
    if not p.get("source_path") or not Path(p["source_path"]).is_file():
        raise HTTPException(410, "Исходник удалён")
    return FileResponse(p["source_path"])


@app.get("/api/projects/{ident}/download")
def archive(ident: str, ids: str | None = None):
    require("projects", ident)
    clips = db.clips(ident)
    selected = (
        set(ids.split(","))
        if ids
        else {c["id"] for c in clips if public_clip(c)["status"] == "ready"}
    )
    if not selected or not selected.issubset({c["id"] for c in clips}):
        raise HTTPException(400, "Выберите готовые клипы этого проекта")
    paths = [(i, ready_path(i)) for i in selected]
    fd, name = tempfile.mkstemp(suffix=".zip", dir=DATA / "tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(name, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
            for i, path in paths:
                z.write(path, f"clipforge-{i[:8]}.mp4")
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return FileResponse(
        name,
        media_type="application/zip",
        filename=f"clipforge-{ident[:8]}.zip",
        background=BackgroundTask(Path(name).unlink, missing_ok=True),
    )


@app.get("/api/settings")
async def get_settings():
    import httpx

    ollama = False
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(
                os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/") + "/api/tags"
            )
            ollama = response.is_success
    except httpx.HTTPError:
        pass
    return {
        **db.settings(),
        "max_upload_gb": MAX_UPLOAD_GB,
        "providers": {
            "openai": bool(os.getenv("OPENAI_API_KEY")),
            "ollama": ollama,
            "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        },
    }


@app.patch("/api/settings")
def save_settings(body: SettingsPatch):
    db.save_settings(body.model_dump(exclude_none=True, exclude_unset=True))
    return db.settings()


@app.delete("/api/storage/sources")
def cleanup():
    from .worker import cleanup_sources

    return {"deleted": cleanup_sources()}


@app.post("/api/projects/{ident}/cancel")
def cancel_project(ident: str):
    require("projects", ident)
    from . import editor_jobs

    with db.connection() as con:
        rows = con.execute(
            "SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')", (ident,)
        ).fetchall()
    for row in rows:
        editor_jobs.request_cancel(row["id"])
    if not db.active(ident):
        db.update("projects", ident, status="cancelled", stage="cancelled", error=None)
    return public_project(require("projects", ident), True, True)


from .editor_api import router as editor_router

app.include_router(editor_router)

# Register the SPA last so /api and /media keep precedence.
DIST = ROOT / "frontend" / "dist"
if DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith(("api/", "media/")):
            raise HTTPException(404, "Не найдено")
        return FileResponse(DIST / "index.html")
