"""SQLite verwaltet nur Übersicht und Zuordnung der Markdown-Notes."""

import json
from contextlib import closing

from src.persistence.database import get_connection


def register_note(connection, document_id, note, markdown_path):
    return connection.execute(
        """INSERT INTO knowledge_notes
           (document_id, title, topic, markdown_path, difficulty, source_pages)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (document_id, note.title, note.topic, markdown_path, note.difficulty,
         json.dumps(note.source_pages)),
    ).lastrowid


def load_notes(document_id=None, course=None):
    query = """SELECT knowledge_notes.*, documents.course, documents.filename AS source_file FROM knowledge_notes
               JOIN documents ON documents.id = knowledge_notes.document_id"""
    conditions, parameters = [], []
    if document_id is not None:
        conditions.append("document_id = ?")
        parameters.append(document_id)
    if course is not None:
        conditions.append("documents.course = ?")
        parameters.append(course)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY documents.course, title, knowledge_notes.id"
    with closing(get_connection()) as connection:
        return connection.execute(query, parameters).fetchall()
