"""Persistent proxy/preview/export jobs. Snapshots live outside API response rows."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from . import db
from .config import DATA


class JobCancelled(RuntimeError):
    pass


def source_fingerprint(path):
    path = Path(path)
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns]


def job(ident):
    with db.connection() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone()
        return dict(row) if row else None


def cancelled(ident):
    row = job(ident)
    return not row or bool(row["cancel_requested"])


def check_cancel(ident):
    if cancelled(ident):
        raise JobCancelled("Обработка отменена пользователем")


def progress(ident, stage, percent):
    check_cancel(ident)
    with db.connection() as con:
        con.execute(
            "UPDATE jobs SET stage=?,progress=?,updated_at=? WHERE id=?",
            (stage, max(0, min(100, percent)), db.now(), ident),
        )


def public(row):
    payload = json.loads(row.get("payload") or "{}")
    output = payload.get("output")
    ready = row["status"] == "done" and output and Path(output).is_file()
    result = {
        k: row.get(k)
        for k in ("id", "kind", "status", "stage", "progress", "error", "created_at", "updated_at")
    }
    result.update(
        revision=payload.get("revision"),
        estimated_bytes=payload.get("estimated_bytes"),
        options=payload.get("options", {}),
        url=f"/media/editor/{row['id']}" if ready else None,
        download_url=f"/api/editor-jobs/{row['id']}/download" if ready else None,
    )
    return result


def list_jobs(clip_id):
    with db.connection() as con:
        rows = con.execute(
            "SELECT * FROM jobs WHERE clip_id=? AND kind IN ('proxy','preview','export') ORDER BY id DESC LIMIT 30",
            (clip_id,),
        ).fetchall()
    return [public(dict(row)) for row in rows]


def relevant_words(document, words):
    intervals = []
    for segment in sorted(document["segments"], key=lambda s: s["start"]):
        if intervals and segment["start"] <= intervals[-1][1]:
            intervals[-1][1] = max(intervals[-1][1], segment["end"])
        else:
            intervals.append([segment["start"], segment["end"]])
    result = []
    index = 0
    for word in sorted(words, key=lambda w: w["start"]):
        while index < len(intervals) and intervals[index][1] <= word["start"]:
            index += 1
        if index == len(intervals):
            break
        if intervals[index][0] < word["end"]:
            result.append(word)
    return result


def queue(clip, project, kind, document, options, words=None, revision=0):
    path = project.get("source_path")
    if not path or not Path(path).is_file():
        raise ValueError("Исходник удалён. Загрузите видео снова для preview или экспорта.")
    from .editing import document_duration

    words = relevant_words(document, words or []) if kind != "proxy" else []
    fingerprint = source_fingerprint(path)
    identity = {"source": fingerprint, "kind": kind, "state": document, "options": options}
    identity["options"] = {k: v for k, v in options.items() if k != "platform"}
    if kind == "proxy":
        identity.pop("state")
    else:
        identity["words"] = words or []
    key = hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    filename = DATA / ("proxies" if kind == "proxy" else "editor/results") / (key + ".mp4")
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        if (
            not con.execute("SELECT id FROM projects WHERE id=?", (project["id"],)).fetchone()
            or not con.execute("SELECT id FROM clips WHERE id=?", (clip["id"],)).fetchone()
        ):
            raise KeyError("Проект или клип уже удалён")
        # Cache belongs to an actual file/job, never just a successful old PID.
        for row in con.execute(
            "SELECT * FROM jobs WHERE kind=? AND project_id=? AND status IN ('queued','running','done') ORDER BY id DESC",
            (kind, project["id"]),
        ):
            previous = json.loads(row["payload"] or "{}")
            if previous.get("key") == key and (row["status"] != "done" or filename.is_file()):
                return public(dict(row))
        manifest = DATA / "editor/manifests" / (key + ".json")
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"state": document, "words": words or []}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, manifest)
        duration = document_duration(document) if kind != "proxy" else project["duration"]
        rates = {"fast": 3, "balanced": 6, "high": 10, "maximum": 16}
        bitrate = options.get("max_bitrate_mbps") or rates.get(options.get("preset", "balanced"), 6)
        scale = (
            1
            if options.get("max_bitrate_mbps")
            else {"720p": 0.5, "1080p": 1, "1440p": 1.8, "4k": 4}.get(
                options.get("resolution", "1080p"), 1
            )
        )
        estimate = int(duration * (float(bitrate) * scale * 1_000_000 + 160_000) / 8)
        payload = {
            "key": key,
            "source": fingerprint,
            "manifest": str(manifest),
            "output": str(filename),
            "options": options,
            "revision": revision,
            "estimated_bytes": estimate,
        }
        cursor = con.execute(
            "INSERT INTO jobs(project_id,clip_id,kind,created_at,payload,updated_at) VALUES(?,?,?,?,?,?)",
            (project["id"], clip["id"], kind, db.now(), json.dumps(payload), db.now()),
        )
        ident = cursor.lastrowid
    return public(job(ident))


def request_cancel(ident):
    with db.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone()
        if not row:
            raise KeyError(ident)
        if row["status"] in ("done", "failed", "cancelled"):
            return public(dict(row))
        con.execute(
            "UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated_at=? WHERE id=?",
            (db.now(), ident),
        )
    return public(job(ident))


def retry(ident):
    row = job(ident)
    if not row:
        raise KeyError(ident)
    if row["status"] not in ("failed", "cancelled"):
        raise ValueError("Повтор доступен только для отменённого задания или ошибки")
    payload = json.loads(row.get("payload") or "{}")
    source = payload.get("source")
    if not source or not Path(source[0]).is_file():
        raise ValueError("Исходник уже удалён")
    with db.connection() as con:
        con.execute(
            "UPDATE jobs SET status='queued',cancel_requested=0,error=NULL,progress=0,stage='queued',updated_at=? WHERE id=? AND status IN ('failed','cancelled')",
            (db.now(), ident),
        )
    return public(job(ident))


def run(row):
    from .editor_media import render_document, create_proxy

    payload = json.loads(row["payload"])
    source = Path(payload["source"][0])
    if source_fingerprint(source) != payload["source"]:
        raise ValueError("Исходное видео изменилось; создайте новое задание")
    output = Path(payload["output"])
    report = lambda stage, percent: progress(row["id"], stage, percent)
    cancel = lambda: cancelled(row["id"])
    check_cancel(row["id"])
    if row["kind"] == "proxy":
        metadata = create_proxy(
            source, output, payload["options"].get("quality", "720p"), report, cancel
        )
    else:
        manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
        metadata = render_document(
            source, output, manifest["state"], manifest["words"], payload["options"], report, cancel
        )
    check_cancel(row["id"])
    with db.connection() as con:
        payload["metadata"] = metadata
        changed = con.execute(
            "UPDATE jobs SET status='done',stage='done',progress=100,error=NULL,payload=?,updated_at=? WHERE id=? AND cancel_requested=0",
            (json.dumps(payload), db.now(), row["id"]),
        )
        if not changed.rowcount:
            raise JobCancelled("Экспорт отменён до публикации файла")


def finish_cancelled(ident):
    with db.connection() as con:
        con.execute(
            "UPDATE jobs SET status='cancelled',stage='cancelled',error=NULL,updated_at=? WHERE id=?",
            (db.now(), ident),
        )
