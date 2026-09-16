"""Technische SQLite-Persistenz, kein zusätzlicher fachlicher Layer."""

import json
import sqlite3
from pathlib import Path

DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "application.db"


def get_connection():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database():
    connection = get_connection()
    try:
        with connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT 'Neuer Chat',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL REFERENCES chats(id),
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    course TEXT,
                    file_path TEXT NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0 CHECK(processed IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS exams (
                    id INTEGER PRIMARY KEY,
                    chat_id INTEGER REFERENCES chats(id),
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS knowledge_notes (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id),
                    title TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    markdown_path TEXT NOT NULL UNIQUE,
                    difficulty TEXT NOT NULL CHECK(difficulty IN ('easy', 'medium', 'hard')),
                    source_pages TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS generated_artifacts (
                    id INTEGER PRIMARY KEY,
                    artifact_type TEXT NOT NULL CHECK(artifact_type IN ('exercise', 'exam')),
                    title TEXT NOT NULL,
                    course TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(documents)")}
            if "page_count" not in columns:
                connection.execute("ALTER TABLE documents ADD COLUMN page_count INTEGER")
            message_columns = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
            if "metadata_json" not in message_columns:
                connection.execute("ALTER TABLE messages ADD COLUMN metadata_json TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash)"
            )
    finally:
        connection.close()


def create_chat():
    connection = get_connection()
    try:
        with connection:
            cursor = connection.execute("INSERT INTO chats (title) VALUES (?)", ("Neuer Chat",))
            return cursor.lastrowid
    finally:
        connection.close()


def list_chats():
    connection = get_connection()
    try:
        return connection.execute("SELECT * FROM chats ORDER BY updated_at DESC, id DESC").fetchall()
    finally:
        connection.close()


def load_messages(chat_id):
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,)
        ).fetchall()
    finally:
        connection.close()


def save_message(chat_id, content):
    if not content.strip():
        raise ValueError("Bitte eine Nachricht eingeben.")
    connection = get_connection()
    try:
        with connection:
            first_message = connection.execute(
                "SELECT id FROM messages WHERE chat_id = ? LIMIT 1", (chat_id,)
            ).fetchone() is None
            connection.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
                (chat_id, content),
            )
            if first_message:
                connection.execute(
                    "UPDATE chats SET title = ? WHERE id = ?",
                    (" ".join(content.split())[:50], chat_id),
                )
            connection.execute(
                "UPDATE chats SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (chat_id,)
            )
    finally:
        connection.close()


def save_chat_exchange(chat_id, user_content, assistant_content, assistant_metadata):
    """Speichert Frage und Antwort atomar, damit kein halber Chat-Turn zurückbleibt."""
    if not user_content.strip() or not assistant_content.strip():
        raise ValueError("Frage und Antwort dürfen nicht leer sein.")
    metadata_json = json.dumps(assistant_metadata, ensure_ascii=False)
    connection = get_connection()
    try:
        with connection:
            first_message = connection.execute(
                "SELECT id FROM messages WHERE chat_id = ? LIMIT 1", (chat_id,)
            ).fetchone() is None
            connection.execute(
                "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
                (chat_id, user_content.strip()),
            )
            connection.execute(
                """INSERT INTO messages (chat_id, role, content, metadata_json)
                   VALUES (?, 'assistant', ?, ?)""",
                (chat_id, assistant_content.strip(), metadata_json),
            )
            if first_message:
                connection.execute(
                    "UPDATE chats SET title = ? WHERE id = ?",
                    (" ".join(user_content.split())[:50], chat_id),
                )
            connection.execute(
                "UPDATE chats SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (chat_id,)
            )
    finally:
        connection.close()


def message_metadata(message):
    value = message["metadata_json"] if "metadata_json" in message.keys() else None
    if not value:
        return {}
    try:
        metadata = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return metadata if isinstance(metadata, dict) else {}
