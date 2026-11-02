"""Small durable SQLite job queue. One OS-locked worker owns all media jobs."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import uuid
from .config import DATA, DEFAULTS, prepare


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    prepare()
    con = sqlite3.connect(DATA / "clipforge.sqlite3", timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def init():
    with connection() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clips(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            clip_id TEXT, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL, error TEXT);
        CREATE INDEX IF NOT EXISTS queue_idx ON jobs(status,id);
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
        """)
        c.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in c.execute("PRAGMA table_info(jobs)")}
        for name, declaration in {
            "payload": "TEXT NOT NULL DEFAULT '{}'",
            "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
            "progress": "REAL NOT NULL DEFAULT 0",
            "stage": "TEXT NOT NULL DEFAULT 'queued'",
            "updated_at": "TEXT",
        }.items():
            if name not in columns:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        c.execute("CREATE INDEX IF NOT EXISTS jobs_clip_kind_idx ON jobs(clip_id,kind,status,id)")
        c.execute(
            "CREATE INDEX IF NOT EXISTS jobs_project_kind_idx ON jobs(project_id,kind,status,id)"
        )


def project_counts():
    with connection() as c:
        return {
            row[0]: row[1]
            for row in c.execute("SELECT project_id,COUNT(*) FROM clips GROUP BY project_id")
        }


def new_id():
    return uuid.uuid4().hex


def get(kind, ident):
    assert kind in ("projects", "clips")
    with connection() as c:
        row = c.execute(f"SELECT payload FROM {kind} WHERE id=?", (ident,)).fetchone()
        return json.loads(row[0]) if row else None


def all_projects():
    with connection() as c:
        return [
            json.loads(r[0]) for r in c.execute("SELECT payload FROM projects ORDER BY rowid DESC")
        ]


def clips(project_id):
    with connection() as c:
        return [
            json.loads(r[0])
            for r in c.execute(
                "SELECT payload FROM clips WHERE project_id=? ORDER BY rowid", (project_id,)
            )
        ]


def put(kind, value):
    assert kind in ("projects", "clips")
    with connection() as c:
        if kind == "projects":
            c.execute(
                "INSERT INTO projects VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (value["id"], json.dumps(value, ensure_ascii=False)),
            )
        else:
            c.execute(
                "INSERT INTO clips VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (value["id"], value["project_id"], json.dumps(value, ensure_ascii=False)),
            )
    return value


def update(kind, ident, **fields):
    assert kind in ("projects", "clips")
    # Read-modify-write inside a write transaction to avoid lost UI/worker changes.
    with connection() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(f"SELECT payload FROM {kind} WHERE id=?", (ident,)).fetchone()
        if not row:
            return None
        item = json.loads(row[0])
        item.update(fields)
        item["updated_at"] = now()
        c.execute(
            f"UPDATE {kind} SET payload=? WHERE id=?", (json.dumps(item, ensure_ascii=False), ident)
        )
        return item


def settings():
    with connection() as c:
        row = c.execute("SELECT payload FROM settings WHERE id=1").fetchone()
        return {**DEFAULTS, **(json.loads(row[0]) if row else {})}


def save_settings(values):
    value = {**settings(), **values}
    with connection() as c:
        c.execute(
            "INSERT INTO settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (json.dumps(value),),
        )
    return value


def enqueue(project_id, kind="analyze", clip_id=None):
    with connection() as c:
        c.execute(
            "INSERT INTO jobs(project_id,clip_id,kind,created_at) VALUES(?,?,?,?)",
            (project_id, clip_id, kind, now()),
        )


def claim():
    with connection() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if row:
            c.execute("UPDATE jobs SET status='running' WHERE id=?", (row["id"],))
            return dict(row)


def active(project_id, clip_id=None):
    with connection() as c:
        if clip_id:
            return bool(
                c.execute(
                    "SELECT 1 FROM jobs WHERE project_id=? AND (clip_id=? OR kind='analyze') AND status IN ('queued','running') LIMIT 1",
                    (project_id, clip_id),
                ).fetchone()
            )
        return bool(
            c.execute(
                "SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running') LIMIT 1",
                (project_id,),
            ).fetchone()
        )


def finish(job_id, error=None):
    with connection() as c:
        c.execute(
            "UPDATE jobs SET status=?,error=? WHERE id=?",
            ("failed" if error else "done", error, job_id),
        )


def recover():
    with connection() as c:
        c.execute(
            "UPDATE jobs SET status='cancelled' WHERE cancel_requested=1 AND status IN ('running','queued')"
        )
        c.execute("UPDATE jobs SET status='queued' WHERE status='running'")


def delete(kind, ident):
    assert kind in ("projects", "clips")
    with connection() as c:
        c.execute(f"DELETE FROM {kind} WHERE id=?", (ident,))
