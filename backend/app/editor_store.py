"""Persistent editor snapshots with optimistic revisions and bounded history.

Legacy clips are migrated lazily; their original video and exports are untouched.
The base snapshot is retained independently of the 100 undo/redo snapshots.
"""

from __future__ import annotations
from contextlib import nullcontext
import json
import threading
from . import db
from .editing import default_document, validate_document

MAX_HISTORY = 100
_schema_lock = threading.Lock()
_schema_paths: set[str] = set()


class ConflictError(ValueError):
    pass


def init():
    key = str(db.DATA / "clipforge.sqlite3")
    with _schema_lock:
        if key in _schema_paths and (db.DATA / "clipforge.sqlite3").exists():
            return
        db.init()
        with db.connection() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS editor_documents(
              clip_id TEXT PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
              source_duration REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
              state TEXT NOT NULL, initial_state TEXT NOT NULL,
              cursor INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS editor_history(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              clip_id TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
              position INTEGER NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL,
              state TEXT NOT NULL, UNIQUE(clip_id,position)
            );
            CREATE INDEX IF NOT EXISTS editor_history_clip_idx ON editor_history(clip_id,position);
            """)
        _schema_paths.add(key)


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _get(c, clip_id):
    row = c.execute("SELECT * FROM editor_documents WHERE clip_id=?", (clip_id,)).fetchone()
    if not row:
        return None
    entries = c.execute(
        "SELECT position,label,created_at FROM editor_history WHERE clip_id=? ORDER BY position",
        (clip_id,),
    ).fetchall()
    cursor = next((i for i, item in enumerate(entries) if item["position"] == row["cursor"]), 0)
    return {
        "clip_id": clip_id,
        "revision": row["revision"],
        "state": json.loads(row["state"]),
        "source_duration": row["source_duration"],
        "updated_at": row["updated_at"],
        "history": {
            "can_undo": cursor > 0,
            "can_redo": cursor < len(entries) - 1,
            "cursor": cursor,
            "entries": [
                {"label": item["label"], "created_at": item["created_at"]} for item in entries
            ],
        },
    }


def get(clip_id, *, connection=None):
    if connection is not None:
        return _get(connection, clip_id)
    init()
    with db.connection() as c:
        return _get(c, clip_id)


def ensure(clip, project):
    init()
    clip_id = clip["id"]
    with db.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = _get(c, clip_id)
        if existing:
            return existing
        duration = project.get("duration") or clip.get("end")
        state = default_document(clip, duration)
        now = db.now()
        encoded = _json(state)
        c.execute(
            "INSERT INTO editor_documents(clip_id,source_duration,revision,state,initial_state,cursor,updated_at) VALUES(?,?,0,?,?,0,?)",
            (clip_id, float(duration), encoded, encoded, now),
        )
        c.execute(
            "INSERT INTO editor_history(clip_id,position,label,created_at,state) VALUES(?,0,?,?,?)",
            (clip_id, "Исходное состояние", now, encoded),
        )
        return _get(c, clip_id)


def _require(c, clip_id, expected_revision):
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise ValueError("Некорректная версия монтажа")
    row = c.execute("SELECT * FROM editor_documents WHERE clip_id=?", (clip_id,)).fetchone()
    if not row:
        raise ValueError("Сначала откройте клип в редакторе")
    if row["revision"] != expected_revision:
        raise ConflictError(
            "Монтаж уже изменён в другой вкладке. Обновите состояние и повторите действие"
        )
    return row


def _touch_project(c, clip_id, now):
    row = c.execute(
        "SELECT p.id,p.payload FROM projects p JOIN clips c ON c.project_id=p.id WHERE c.id=?",
        (clip_id,),
    ).fetchone()
    if row:
        project = json.loads(row["payload"])
        project["updated_at"] = now
        c.execute("UPDATE projects SET payload=? WHERE id=?", (_json(project), row["id"]))


def save(clip_id, expected_revision, state, label="Изменение", *, connection=None):
    """CAS-save; external connection must already own an active transaction."""
    if connection is None:
        init()
    elif not connection.in_transaction:
        raise ValueError("Внешнее сохранение требует активной транзакции")
    with db.connection() if connection is None else nullcontext(connection) as c:
        if connection is None:
            c.execute("BEGIN IMMEDIATE")
        row = _require(c, clip_id, expected_revision)
        validated = validate_document(state, row["source_duration"])
        encoded = _json(validated)
        if encoded == row["state"]:
            return _get(c, clip_id)
        if not isinstance(label, str) or len(label) > 200:
            raise ValueError("Название изменения должно быть строкой до 200 символов")
        position = row["cursor"] + 1
        now = db.now()
        c.execute(
            "DELETE FROM editor_history WHERE clip_id=? AND position>?", (clip_id, row["cursor"])
        )
        c.execute(
            "INSERT INTO editor_history(clip_id,position,label,created_at,state) VALUES(?,?,?,?,?)",
            (clip_id, position, label or "Изменение", now, encoded),
        )
        c.execute(
            "DELETE FROM editor_history WHERE clip_id=? AND position NOT IN (SELECT position FROM editor_history WHERE clip_id=? ORDER BY position DESC LIMIT ?)",
            (clip_id, clip_id, MAX_HISTORY),
        )
        c.execute(
            "UPDATE editor_documents SET state=?,revision=revision+1,cursor=?,updated_at=? WHERE clip_id=?",
            (encoded, position, now, clip_id),
        )
        _touch_project(c, clip_id, now)
        return _get(c, clip_id)


def history(clip_id, expected_revision, action):
    init()
    if action not in ("undo", "redo", "reset"):
        raise ValueError("Неизвестное действие истории")
    with db.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        row = _require(c, clip_id, expected_revision)
        if action == "reset":
            return save(
                clip_id,
                expected_revision,
                json.loads(row["initial_state"]),
                "Сброс к исходному состоянию",
                connection=c,
            )
        operator = "<" if action == "undo" else ">"
        ordering = "DESC" if action == "undo" else "ASC"
        target = c.execute(
            f"SELECT position,state FROM editor_history WHERE clip_id=? AND position{operator}? ORDER BY position {ordering} LIMIT 1",
            (clip_id, row["cursor"]),
        ).fetchone()
        if not target:
            return _get(c, clip_id)
        now = db.now()
        c.execute(
            "UPDATE editor_documents SET state=?,cursor=?,revision=revision+1,updated_at=? WHERE clip_id=?",
            (target["state"], target["position"], now, clip_id),
        )
        _touch_project(c, clip_id, now)
        return _get(c, clip_id)
