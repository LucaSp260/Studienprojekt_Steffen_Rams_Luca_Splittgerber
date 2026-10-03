"""SQLite-Persistenz für fertig generierte Übungen und Probeklausuren."""

import json
from contextlib import closing

from src.persistence.database import get_connection


def save_artifact(artifact_type, title, course, configuration, content):
    if artifact_type not in {"exercise", "exam"}:
        raise ValueError("Unbekannter Artefakttyp.")
    if not title.strip() or not course.strip():
        raise ValueError("Titel und Kurs dürfen nicht leer sein.")
    configuration_json = json.dumps(configuration, ensure_ascii=False)
    content_json = json.dumps(content, ensure_ascii=False)
    with closing(get_connection()) as connection, connection:
        return connection.execute(
            """INSERT INTO generated_artifacts
               (artifact_type, title, course, configuration_json, content_json)
               VALUES (?, ?, ?, ?, ?)""",
            (artifact_type, title.strip(), course.strip(), configuration_json, content_json),
        ).lastrowid


def load_artifacts(artifact_type=None, course=None):
    query = "SELECT * FROM generated_artifacts"
    parameters = ()
    if artifact_type is not None:
        if artifact_type not in {"exercise", "exam"}:
            raise ValueError("Unbekannter Artefakttyp.")
        query += " WHERE artifact_type = ?"
        parameters = (artifact_type,)
    if course is not None:
        query += (" AND" if artifact_type is not None else " WHERE") + " course = ?"
        parameters += (course,)
    query += " ORDER BY created_at DESC, id DESC"
    with closing(get_connection()) as connection:
        return [_decode(row) for row in connection.execute(query, parameters).fetchall()]


def delete_artifact(artifact_id):
    with closing(get_connection()) as connection, connection:
        connection.execute("DELETE FROM generated_artifacts WHERE id=?", (artifact_id,))


def load_artifact(artifact_id):
    with closing(get_connection()) as connection:
        row = connection.execute(
            "SELECT * FROM generated_artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
    return _decode(row) if row is not None else None


def _decode(row):
    result = dict(row)
    try:
        result["configuration"] = json.loads(result.pop("configuration_json"))
        result["content"] = json.loads(result.pop("content_json"))
    except (json.JSONDecodeError, TypeError):
        raise ValueError("Ein gespeichertes Lernartefakt enthält ungültige Daten.") from None
    return result
