"""Локальное хранилище сессий и сообщений (SQLite)."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

DB_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DB_DIR / "chat.sqlite"
BILLING_PATH = DB_DIR / "billing.json"


def _now() -> float:
    return time.time()


def _connect() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT 'Новый чат',
                system TEXT,
                model TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'completed',
                model TEXT,
                attachment_name TEXT,
                request_json TEXT,
                result_json TEXT,
                error_json TEXT,
                created_at REAL NOT NULL,
                completed_at REAL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_messages_status
                ON messages(status);
            """
        )
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "archived_at" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN archived_at REAL")


def create_session(*, title: str = "Новый чат", system: str | None = None, model: str | None = None) -> dict:
    sid = str(uuid.uuid4())
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (id, title, system, model, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sid, title, system, model, ts, ts),
        )
    return get_session(sid)


def list_sessions(*, limit: int = 40, archived: bool = False) -> list[dict]:
    clause = "archived_at IS NOT NULL" if archived else "archived_at IS NULL"
    order = "archived_at DESC" if archived else "updated_at DESC"
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, title, system, model, created_at, updated_at, archived_at
            FROM sessions
            WHERE {clause}
            ORDER BY {order}
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_session_row(r) for r in rows]


def get_session(session_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, title, system, model, created_at, updated_at, archived_at
            FROM sessions WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
    return _session_row(row) if row else None


def is_session_archived(session_id: str) -> bool:
    session = get_session(session_id)
    return bool(session and session.get("archived_at"))


def archive_session(session_id: str) -> dict | None:
    session = get_session(session_id)
    if not session:
        return None
    if session.get("archived_at"):
        return session
    ts = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET archived_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, session_id),
        )
    return get_session(session_id)


def delete_session_permanently(session_id: str) -> bool:
    session = get_session(session_id)
    if not session or not session.get("archived_at"):
        return False
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cur = conn.execute("DELETE FROM sessions WHERE id = ? AND archived_at IS NOT NULL", (session_id,))
    return cur.rowcount > 0


def get_messages(session_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, role, content, status, model, attachment_name,
                   request_json, result_json, error_json, created_at, completed_at
            FROM messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()
    return [_message_row(r) for r in rows]


def get_message(message_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, session_id, role, content, status, model, attachment_name,
                   request_json, result_json, error_json, created_at, completed_at
            FROM messages
            WHERE id = ?
            """,
            (message_id,),
        ).fetchone()
    return _message_row(row) if row else None


def touch_session(session_id: str, *, title: str | None = None) -> None:
    ts = _now()
    with _connect() as conn:
        if title:
            conn.execute(
                "UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                (ts, title, session_id),
            )
        else:
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (ts, session_id))


def update_session_settings(session_id: str, *, system: str | None = None, model: str | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET system = ?, model = ?, updated_at = ? WHERE id = ?",
            (system, model, _now(), session_id),
        )


def enqueue_chat(
    session_id: str,
    *,
    user_content: str,
    request_payload: dict[str, Any],
    attachment_name: str | None = None,
) -> dict:
    user_id = str(uuid.uuid4())
    assistant_id = str(uuid.uuid4())
    ts = _now()
    model = request_payload.get("model")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO messages
                (id, session_id, role, content, status, model, attachment_name, created_at)
            VALUES (?, ?, 'user', ?, 'completed', ?, ?, ?)
            """,
            (user_id, session_id, user_content, model, attachment_name, ts),
        )
        conn.execute(
            """
            INSERT INTO messages
                (id, session_id, role, content, status, model, request_json, created_at)
            VALUES (?, ?, 'assistant', '', 'pending', ?, ?, ?)
            """,
            (assistant_id, session_id, model, json.dumps(request_payload, ensure_ascii=False), ts + 0.001),
        )
    title = (user_content or "").strip()
    if title:
        short = title[:60] + ("…" if len(title) > 60 else "")
        touch_session(session_id, title=short)
    else:
        touch_session(session_id)
    return {
        "user_message_id": user_id,
        "assistant_message_id": assistant_id,
        "status": "pending",
    }


def set_message_status(message_id: str, status: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE messages SET status = ? WHERE id = ?", (status, message_id))


def get_message_status(message_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT status FROM messages WHERE id = ?", (message_id,)).fetchone()
    return str(row["status"]) if row else None


def cancel_assistant_message(message_id: str) -> bool:
    ts = _now()
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE messages
            SET status = 'cancelled', content = ?, completed_at = ?, error_json = NULL, result_json = NULL
            WHERE id = ? AND role = 'assistant' AND status IN ('pending', 'running')
            """,
            ("Запрос остановлен.", ts, message_id),
        )
    return cur.rowcount > 0


def complete_assistant_message(message_id: str, *, reply: str, result: dict[str, Any]) -> None:
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE messages
            SET status = 'completed', content = ?, result_json = ?, completed_at = ?, error_json = NULL
            WHERE id = ? AND status IN ('pending', 'running')
            """,
            (reply, json.dumps(result, ensure_ascii=False), ts, message_id),
        )


def fail_assistant_message(message_id: str, error: dict[str, Any]) -> None:
    ts = _now()
    msg = error.get("message") or json.dumps(error, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            """
            UPDATE messages
            SET status = 'failed', content = ?, error_json = ?, completed_at = ?
            WHERE id = ? AND status IN ('pending', 'running')
            """,
            (msg, json.dumps(error, ensure_ascii=False), ts, message_id),
        )


def count_pending_jobs() -> int:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM messages
            WHERE role = 'assistant' AND status IN ('pending', 'running')
            """
        ).fetchone()
    return int(row["n"]) if row else 0


def get_resumable_jobs() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, request_json
            FROM messages
            WHERE role = 'assistant' AND status IN ('pending', 'running')
            ORDER BY created_at ASC
            """
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        payload = json.loads(row["request_json"]) if row["request_json"] else None
        if payload:
            out.append({"assistant_message_id": row["id"], "request_payload": payload})
    return out


def _session_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "system": row["system"],
        "model": row["model"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "archived_at": row["archived_at"],
    }


def _message_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "status": row["status"],
        "model": row["model"],
        "attachment_name": row["attachment_name"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "error": json.loads(row["error_json"]) if row["error_json"] else None,
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


def get_billing_config() -> dict[str, Any]:
    if not BILLING_PATH.exists():
        return {}
    try:
        data = json.loads(BILLING_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def set_billing_credit(
    credit_usd: float,
    *,
    baseline_spent_usd: float | None = None,
    anchor_day_unix: int | None = None,
) -> dict[str, Any]:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    cfg: dict[str, Any] = {
        "credit_usd": round(max(0.0, float(credit_usd)), 2),
        "set_at_unix": _now(),
    }
    if anchor_day_unix is not None:
        cfg["anchor_day_unix"] = int(anchor_day_unix)
    if baseline_spent_usd is not None:
        cfg["baseline_spent_usd"] = round(max(0.0, float(baseline_spent_usd)), 4)
    BILLING_PATH.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return cfg


def patch_billing_config(updates: dict[str, Any]) -> dict[str, Any]:
    cfg = get_billing_config()
    cfg.update(updates)
    BILLING_PATH.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return cfg
