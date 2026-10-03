"""PDF-Upload prüfen, lokal speichern und in SQLite registrieren."""

import re
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path

from src.data_layer.file_hash import calculate_file_hash
from src.data_layer.pdf_loader import read_pdf
from src.persistence.database import get_connection
from src.persistence.course_repository import resolve_course, ensure_course
from src.persistence.document_repository import find_document_by_hash, save_document

USER_DATA_PATH = Path(__file__).resolve().parents[2] / "user_data"


def safe_name(value, fallback):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_-")[:80]
    if not value:
        value = fallback
    if value.upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)],
                         *[f"LPT{i}" for i in range(10)]}:
        value = "_" + value
    return value


def store_new_file(folder, filename, content):
    """Exklusives Anlegen verhindert das Überschreiben bestehender Dateien."""
    stem = safe_name(filename[:-4], "dokument")
    number = 0
    while True:
        suffix = f"_{number}" if number else ""
        path = folder / f"{stem}{suffix}.pdf"
        try:
            file = path.open("xb")
        except FileExistsError:
            number += 1
            continue
        try:
            with file:
                file.write(content)
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return path


def add_document(uploaded_file, course):
    """Akzeptiert Streamlit UploadedFile (name/getvalue) und liefert eine UI-Meldung."""
    course = course.strip()
    if not course:
        return {"status": "error", "message": "Bitte einen Kurs angeben."}
    filename = uploaded_file.name.replace("\\", "/").split("/")[-1]
    if not filename.lower().endswith(".pdf"):
        return {"status": "error", "message": "Ungültige Datei: Bitte ausschließlich PDFs hochladen."}
    saved_path = None
    try:
        course = resolve_course(course)
        content = uploaded_file.getvalue()
        file_hash = calculate_file_hash(content)
        existing = find_document_by_hash(file_hash)
        if existing:
            return duplicate_result(existing)
        pdf = read_pdf(content)
        folder = USER_DATA_PATH / safe_name(course.lower(), "kurs")
        with closing(get_connection()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = find_document_by_hash(file_hash, connection)
            if existing:
                return duplicate_result(existing)
            course = ensure_course(course, connection)
            folder.mkdir(parents=True, exist_ok=True)
            saved_path = store_new_file(folder, filename, content)
            document_id = save_document(
                filename, file_hash, course,
                saved_path.relative_to(USER_DATA_PATH.parent).as_posix(),
                pdf["page_count"], connection,
            )
        return {"status": "success", "message": f"{filename} wurde erfolgreich zu {course} hinzugefügt.",
                "document_id": document_id, "warning": pdf["warning"]}
    except ValueError as error:
        return {"status": "error", "message": str(error)}
    except (OSError, sqlite3.Error):
        message = "Die Datei konnte nicht gespeichert werden. Bitte Schreibrechte, freien "
        message += "Speicherplatz und die Verfügbarkeit der Datenbank prüfen."
        if saved_path is not None:
            try:
                saved_path.unlink(missing_ok=True)
            except OSError:
                message += " Eine unregistrierte Datei ist zurückgeblieben: " + str(saved_path)
        return {"status": "error", "message": message}


def duplicate_result(document):
    return {"status": "duplicate", "message": "Diese Datei wurde bereits hochgeladen und wird "
            f"nicht erneut gespeichert. Vorhanden als {document['filename']} im Kurs {document['course']}.",
            "document_id": document["id"]}
