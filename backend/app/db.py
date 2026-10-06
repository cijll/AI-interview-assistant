from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                target_role TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                question_limit INTEGER NOT NULL,
                time_limit_seconds INTEGER,
                focus_skills_json TEXT NOT NULL DEFAULT '[]',
                resume_text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                current_stage TEXT NOT NULL DEFAULT 'opening',
                turn_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                ended_at TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                turn_no INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_summaries (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                covered_turn INTEGER NOT NULL,
                summary_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                report_json TEXT NOT NULL,
                generation_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS resumes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                parsed_json TEXT NOT NULL DEFAULT '{}',
                parse_status TEXT NOT NULL DEFAULT 'READY',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS question_bank (
                id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                role TEXT NOT NULL,
                skill TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                question_type TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                followups_json TEXT NOT NULL DEFAULT '[]',
                rubric_json TEXT NOT NULL DEFAULT '[]',
                reference_answer TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'seed',
                created_at TEXT NOT NULL
            );
            """
        )


def create_user(username: str, password_hash: str) -> dict[str, Any] | None:
    user = {
        "id": new_id("u"),
        "username": username,
        "password_hash": password_hash,
        "created_at": now_iso(),
    }
    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user["id"], user["username"], user["password_hash"], user["created_at"]),
            )
    except sqlite3.IntegrityError:
        return None
    return user


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def create_session(payload: dict[str, Any], opening_question: str) -> dict[str, Any]:
    session_id = new_id("s")
    created_at = now_iso()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO sessions
            (id, user_id, target_role, difficulty, question_limit, time_limit_seconds,
             focus_skills_json, resume_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                payload["user_id"],
                payload["target_role"],
                payload["difficulty"],
                payload["question_limit"],
                payload.get("time_limit_seconds"),
                dumps(payload.get("focus_skills", [])),
                payload.get("resume_text", ""),
                created_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO messages
            (id, session_id, turn_no, role, content, metadata_json, created_at)
            VALUES (?, ?, 0, 'assistant', ?, ?, ?)
            """,
            (
                new_id("m"),
                session_id,
                opening_question,
                dumps({"kind": "opening_question"}),
                created_at,
            ),
        )
    return get_session(session_id) or {}


def get_session(session_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None


def get_session_for_user(session_id: str, user_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def list_sessions(user_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def list_messages(session_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY turn_no, created_at",
            (session_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = loads(item.pop("metadata_json"), {})
            result.append(item)
        return result


def add_message(
    session_id: str, turn_no: int, role: str, content: str, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    message = {
        "id": new_id("m"),
        "session_id": session_id,
        "turn_no": turn_no,
        "role": role,
        "content": content,
        "metadata": metadata or {},
        "created_at": now_iso(),
    }
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO messages
            (id, session_id, turn_no, role, content, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message["id"],
                session_id,
                turn_no,
                role,
                content,
                dumps(message["metadata"]),
                message["created_at"],
            ),
        )
    return message


def update_session(session_id: str, **fields: Any) -> None:
    allowed = {"status", "current_stage", "turn_count", "ended_at"}
    updates = [(key, value) for key, value in fields.items() if key in allowed]
    if not updates:
        return
    sql = "UPDATE sessions SET " + ", ".join(f"{key} = ?" for key, _ in updates)
    sql += " WHERE id = ?"
    with connection() as conn:
        conn.execute(sql, [value for _, value in updates] + [session_id])


def save_summary(session_id: str, covered_turn: int, summary: dict[str, Any]) -> dict[str, Any]:
    with connection() as conn:
        current = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM memory_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()["version"]
        version = int(current) + 1
        summary_id = new_id("sum")
        conn.execute(
            """
            INSERT INTO memory_summaries
            (id, session_id, version, covered_turn, summary_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (summary_id, session_id, version, covered_turn, dumps(summary), now_iso()),
        )
    return {"id": summary_id, "version": version, "covered_turn": covered_turn, **summary}


def latest_summary(session_id: str) -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM memory_summaries
            WHERE session_id = ?
            ORDER BY version DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return {"version": 0, "covered_turn": 0, "confirmed_facts": [], "keywords": []}
        summary = loads(row["summary_json"], {})
        summary.update({"version": row["version"], "covered_turn": row["covered_turn"]})
        return summary


def save_report(session_id: str, report: dict[str, Any], status: str = "READY") -> dict[str, Any]:
    with connection() as conn:
        current = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM reports WHERE session_id = ?",
            (session_id,),
        ).fetchone()["version"]
        version = int(current) + 1
        report_id = new_id("report")
        conn.execute(
            """
            INSERT INTO reports
            (id, session_id, version, report_json, generation_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (report_id, session_id, version, dumps(report), status, now_iso()),
        )
    return {"id": report_id, "session_id": session_id, "version": version, **report}


def latest_report(session_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE session_id = ? ORDER BY version DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        report = loads(row["report_json"], {})
        report.update(
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "version": row["version"],
                "generation_status": row["generation_status"],
            }
        )
        return report


def delete_session_for_user(session_id: str, user_id: str) -> bool:
    with connection() as conn:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        )
        return cursor.rowcount > 0


def delete_all_sessions(user_id: str) -> int:
    with connection() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return cursor.rowcount


def save_resume(
    user_id: str,
    original_filename: str,
    storage_path: str,
    parsed: dict[str, Any],
) -> dict[str, Any]:
    resume = {
        "id": new_id("resume"),
        "user_id": user_id,
        "original_filename": original_filename,
        "storage_path": storage_path,
        "parsed": parsed,
        "parse_status": "READY",
        "created_at": now_iso(),
    }
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO resumes
            (id, user_id, original_filename, storage_path, parsed_json, parse_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resume["id"],
                user_id,
                original_filename,
                storage_path,
                dumps(parsed),
                resume["parse_status"],
                resume["created_at"],
            ),
        )
    return resume
