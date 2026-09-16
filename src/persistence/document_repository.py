"""Einfache SQLite-Operationen für Dokumente."""

from contextlib import closing, nullcontext

from src.persistence.database import get_connection


def find_document_by_hash(file_hash, connection=None):
    context = nullcontext(connection) if connection is not None else closing(get_connection())
    with context as connection:
        return connection.execute(
            "SELECT * FROM documents WHERE file_hash = ? ORDER BY id LIMIT 1", (file_hash,)
        ).fetchone()


def save_document(filename, file_hash, course, file_path, page_count, connection):
    """Aufrufer hält BEGIN IMMEDIATE bis nach dem Speichern der Datei.

    So bleibt die Hash-Prüfung auch bei gleichzeitigen Uploads zuverlässig.
    Bereits vorhandene Altdaten müssen dafür nicht gelöscht werden.
    """
    existing = find_document_by_hash(file_hash, connection)
    if existing:
        return existing["id"]
    cursor = connection.execute(
        """INSERT INTO documents
           (filename, file_hash, course, file_path, page_count, processed)
           VALUES (?, ?, ?, ?, ?, 0)""",
        (filename, file_hash, course, file_path, page_count),
    )
    return cursor.lastrowid


def load_documents(course=None):
    with closing(get_connection()) as connection:
        if course is not None:
            return connection.execute(
                "SELECT * FROM documents WHERE course = ? ORDER BY created_at DESC, id DESC",
                (course,),
            ).fetchall()
        return connection.execute(
            "SELECT * FROM documents ORDER BY course, created_at DESC, id DESC"
        ).fetchall()


def update_document_status(document_id, processed):
    with closing(get_connection()) as connection, connection:
        connection.execute(
            "UPDATE documents SET processed = ? WHERE id = ?", (int(bool(processed)), document_id)
        )
