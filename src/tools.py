from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def list_courses() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT DISTINCT course FROM notes ORDER BY course").fetchall()
    return [row["course"] for row in rows]


def ensure_note_upload_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS note_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uploaded_at TEXT NOT NULL,
            file_name TEXT NOT NULL,
            source_type TEXT DEFAULT '文件上传',
            course_scope TEXT,
            note_count INTEGER DEFAULT 0,
            summary TEXT
        )
        """
    )


def list_todos(status: str | None = None, course: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM todos WHERE 1=1"
    params: list[Any] = []
    if status and status != "全部":
        query += " AND status = ?"
        params.append(status)
    if course and course != "全部课程":
        query += " AND course = ?"
        params.append(course)
    query += " ORDER BY deadline ASC, id ASC"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return rows_to_dicts(rows)


def add_todo(course: str, task: str, deadline: str = "", priority: str = "中") -> dict[str, Any]:
    created_at = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO todos (course, task, deadline, status, priority, created_at)
            VALUES (?, ?, ?, '未完成', ?, ?)
            """,
            (course or "未分类", task, deadline, priority, created_at),
        )
        conn.commit()
        todo_id = cursor.lastrowid
    return {"id": todo_id, "course": course, "task": task, "deadline": deadline, "status": "未完成", "priority": priority}


def complete_todo(todo_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("UPDATE todos SET status = '已完成' WHERE id = ?", (todo_id,))
        conn.commit()
    return cursor.rowcount > 0


def update_todo_status(todo_id: int, status: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("UPDATE todos SET status = ? WHERE id = ?", (status, todo_id))
        conn.commit()
    return cursor.rowcount > 0


def search_todo_by_keyword(keyword: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM todos WHERE task LIKE ? ORDER BY deadline ASC LIMIT 1",
            (f"%{keyword}%",),
        ).fetchone()
    return dict(row) if row else None


def list_notes(course: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM notes WHERE 1=1"
    params: list[Any] = []
    if course and course != "全部课程":
        query += " AND course = ?"
        params.append(course)
    query += " ORDER BY course, id"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return rows_to_dicts(rows)


def add_note(course: str, title: str, content: str, tags: str = "") -> int:
    updated_at = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO notes (course, title, content, tags, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (course or "未分类", title or "未命名笔记", content, tags, updated_at),
        )
        conn.commit()
        return int(cursor.lastrowid)


def add_note_upload(
    file_name: str,
    source_type: str,
    course_scope: str,
    note_count: int,
    summary: str = "",
) -> int:
    uploaded_at = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        ensure_note_upload_table(conn)
        cursor = conn.execute(
            """
            INSERT INTO note_uploads (uploaded_at, file_name, source_type, course_scope, note_count, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uploaded_at, file_name, source_type, course_scope, note_count, summary),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_note_uploads(limit: int = 8) -> list[dict[str, Any]]:
    with get_connection() as conn:
        ensure_note_upload_table(conn)
        rows = conn.execute(
            """
            SELECT * FROM note_uploads
            ORDER BY uploaded_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return rows_to_dicts(rows)


def save_quiz_record(course: str, question: str, answer: str, note_id: int | None = None, chunk_id: str = "") -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO quiz_records (course, question, answer, note_id, chunk_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (course, question, answer, note_id, chunk_id, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_quiz_records() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM quiz_records ORDER BY id DESC").fetchall()
    return rows_to_dicts(rows)


def add_work_log(module: str, action: str, result: str, problem: str = "", solution: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO work_logs (date, module, action, result, problem, solution)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (datetime.now().date().isoformat(), module, action, result, problem, solution),
        )
        conn.commit()


def table_counts() -> dict[str, int]:
    tables = [
        "todos",
        "notes",
        "note_uploads",
        "quiz_records",
        "mistakes",
        "mastery",
        "study_sessions",
        "work_logs",
        "ai_usage_logs",
    ]
    counts: dict[str, int] = {}
    with get_connection() as conn:
        for table in tables:
            try:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                counts[table] = -1
    return counts


def db_exists() -> bool:
    return Path(DB_PATH).exists()
