"""Verarbeitung vollständig abschließen oder neue Dateien zurückrollen."""

from contextlib import closing
from pathlib import Path
from threading import Lock

from src.data_layer.pdf_loader import read_pdf
from src.knowledge_layer.knowledge_extractor import extract_knowledge
from src.knowledge_layer import markdown_store
from src.llm.llm_service import LLMService
from src.persistence.database import get_connection
from src.persistence.knowledge_repository import register_note
from src.persistence.knowledge_repository import load_notes
from src.knowledge_layer.indexing_service import index_existing_notes
from src.knowledge_layer.brain_growth import extend_brain

PROJECT_PATH = Path(__file__).resolve().parents[2]
_processing_lock = Lock()


def process_document(document_id, llm_service=None, progress=None, embedding_service=None):
    # Streamlit-Sitzungen teilen dieses Modul. Ein zweiter Klick während einer
    # laufenden Verarbeitung darf keine zusätzlichen API-Anfragen starten.
    if not _processing_lock.acquire(blocking=False):
        raise ValueError("Eine Verarbeitung läuft bereits. Bitte deren Abschluss abwarten.")
    saved_paths = []
    notes_committed = False
    try:
        with closing(get_connection()) as connection:
            document = connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if document is None:
            raise ValueError("Das Dokument wurde nicht gefunden.")
        if document["processed"]:
            result = extend_brain(document_id, llm_service=llm_service, progress=progress)
            if result["processed_notes"]:
                return {"status": "success", "message":
                        f"Second Brain wurde um {result['concepts']} Konzepte und {result['edges']} Beziehungen ergänzt."}
            return {"status": "already_processed", "message": "Dieses Dokument wurde bereits in die Wissensbasis verarbeitet."}
        if load_notes(document_id=document_id):
            index_existing_notes(document_id, embedding_service=embedding_service, progress=progress)
            growth = extend_brain(document_id, llm_service=llm_service, progress=progress)
            return {"status": "success", "message":
                    f"Vorhandene Knowledge Notes wurden indexiert; {growth['edges']} neue Beziehungen ergänzt."}
        source_path = (PROJECT_PATH / document["file_path"]).resolve()
        if not source_path.is_relative_to((PROJECT_PATH / "user_data").resolve()):
            raise ValueError("Die PDF liegt nicht im Unterlagen-Ordner.")
        if progress:
            progress("PDF wird analysiert …")
        pdf = read_pdf(source_path)
        if not pdf["text"].strip():
            raise ValueError("Die PDF enthält keinen extrahierbaren Text. Texterkennung ist noch nicht verfügbar.")
        service = llm_service if llm_service is not None else LLMService()
        notes = extract_knowledge(pdf["pages"], document, service, progress)
        if progress:
            progress("Knowledge Notes werden gespeichert …")
        with closing(get_connection()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT processed FROM documents WHERE id = ?", (document_id,)).fetchone()
            if current is None or current["processed"]:
                raise ValueError("Der Dokumentstatus hat sich geändert. Bitte die Übersicht neu laden.")
            for note in notes:
                path = markdown_store.save_note(note)
                saved_paths.append(path)
                relative_path = path.relative_to(markdown_store.KNOWLEDGE_BASE_PATH.parent).as_posix()
                register_note(connection, document_id, note, relative_path)
        notes_committed = True
        index_existing_notes(document_id, embedding_service=embedding_service, progress=progress)
        growth = extend_brain(document_id, llm_service=service, progress=progress)
        return {"status": "success", "message":
                f"{len(notes)} neue Themen wurden erkannt. Das Second Brain erhielt "
                f"{growth['concepts']} Konzepte und {growth['edges']} Beziehungen."}
    except Exception:
        if notes_committed:
            # Embedding-/Chroma-Fehler: Notes behalten und beim nächsten Klick
            # nur indexieren. Keine erneute kostenpflichtige Knowledge Extraction.
            raise
        # Nur Dateien dieses Versuchs entfernen; bestehende Notes bleiben erhalten.
        cleanup_failed = False
        for path in saved_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise ValueError("Speicherung fehlgeschlagen. Nicht registrierte Markdown-Dateien konnten "
                             "nicht entfernt werden. Bitte Schreibrechte im Wissensbasis-Ordner prüfen.") from None
        raise
    finally:
        _processing_lock.release()
