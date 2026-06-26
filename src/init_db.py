from __future__ import annotations

import sqlite3
from datetime import datetime

import pandas as pd

from config import DB_PATH, NOTES_XLSX, TODO_XLSX, DATA_DIR


def recreate_core_tables(conn: sqlite3.Connection, todos: pd.DataFrame, notes: pd.DataFrame) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS todos;
        DROP TABLE IF EXISTS notes;

        CREATE TABLE todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course TEXT NOT NULL,
            task TEXT NOT NULL,
            deadline TEXT,
            status TEXT DEFAULT '未完成',
            priority TEXT DEFAULT '中',
            created_at TEXT
        );

        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            updated_at TEXT
        );
        """
    )

    for _, row in todos.iterrows():
        conn.execute(
            """
            INSERT INTO todos (id, course, task, deadline, status, priority, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["id"]) if pd.notna(row.get("id")) else None,
                row.get("course", "未分类"),
                row.get("task", ""),
                row.get("deadline", ""),
                row.get("status", "未完成"),
                row.get("priority", "中"),
                row.get("created_at", datetime.now().isoformat(timespec="seconds")),
            ),
        )

    for _, row in notes.iterrows():
        conn.execute(
            """
            INSERT INTO notes (id, course, title, content, tags, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["id"]) if pd.notna(row.get("id")) else None,
                row.get("course", "未分类"),
                row.get("title", ""),
                row.get("content", ""),
                row.get("tags", ""),
                row.get("updated_at", datetime.now().isoformat(timespec="seconds")),
            ),
        )


def ensure_extra_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS quiz_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course TEXT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            user_answer TEXT,
            is_correct INTEGER DEFAULT 0,
            note_id INTEGER,
            chunk_id TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS mistakes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER,
            knowledge_point TEXT,
            mistake_reason TEXT,
            review_count INTEGER DEFAULT 0,
            next_review_at TEXT
        );

        CREATE TABLE IF NOT EXISTS mastery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course TEXT,
            knowledge_point TEXT,
            mastery_level TEXT DEFAULT '未学习',
            last_review_at TEXT
        );

        CREATE TABLE IF NOT EXISTS study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            course TEXT,
            goal TEXT,
            start_time TEXT,
            end_time TEXT,
            reflection TEXT,
            status TEXT DEFAULT '进行中'
        );

        CREATE TABLE IF NOT EXISTS work_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            module TEXT,
            action TEXT,
            result TEXT,
            problem TEXT,
            solution TEXT
        );

        CREATE TABLE IF NOT EXISTS ai_usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intent TEXT,
            risk_level TEXT,
            used_sources INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS note_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uploaded_at TEXT NOT NULL,
            file_name TEXT NOT NULL,
            source_type TEXT DEFAULT '文件上传',
            course_scope TEXT,
            note_count INTEGER DEFAULT 0,
            summary TEXT
        );
        """
    )


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TODO_XLSX.exists() or not NOTES_XLSX.exists():
        raise FileNotFoundError("Missing data/todo.xlsx or data/notes.xlsx. Run scripts/create_sample_data.py first.")

    todos = pd.read_excel(TODO_XLSX)
    notes = pd.read_excel(NOTES_XLSX)

    now = datetime.now().isoformat(timespec="seconds")
    if "created_at" not in todos.columns:
        todos["created_at"] = now
    if "updated_at" not in notes.columns:
        notes["updated_at"] = now

    with sqlite3.connect(DB_PATH) as conn:
        recreate_core_tables(conn, todos, notes)
        ensure_extra_tables(conn)
        conn.commit()

    print(f"Database initialized: {DB_PATH}")
    print(f"todos: {len(todos)}, notes: {len(notes)}")


if __name__ == "__main__":
    init_db()
