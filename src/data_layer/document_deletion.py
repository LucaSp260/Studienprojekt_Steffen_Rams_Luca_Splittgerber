"""Delete one uploaded PDF and only knowledge that depends on it.

The SQL transaction, file journal, and Chroma snapshots are coordinated under the
same locks as course renames, extraction, and indexing. No model is called.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import chromadb
from chromadb.config import Settings

from src.data_layer import document_manager
from src.knowledge_layer import markdown_store, vector_store
from src.knowledge_layer.course_manager import _rename_lock, _vault_path
from src.knowledge_layer.indexing_service import _index_lock
from src.knowledge_layer.knowledge_manager import _processing_lock
from src.knowledge_layer.obsidian_export import export_course_to_obsidian
from src.persistence.brain_repository import load_graph
from src.persistence.database import get_connection
from src.persistence.knowledge_repository import load_notes


def _checked_path(base, relative):
    base = Path(base).resolve()
    path = (base.parent / relative).resolve()
    if not path.is_relative_to(base):
        raise ValueError("Ein gespeicherter Dateipfad liegt außerhalb des Projektordners.")
    return path


def _managed_vault_files(root):
    if not root.exists():
        return set()
    files = set(root.glob("concept-[0-9]*.md"))
    files.update((root / "notes").glob("note-[0-9]*.md"))
    files.update(path for path in (root / "Index.md", root / "Export validation.json") if path.exists())
    return {path for path in files if path.is_file()}


def _contains_note_reference(value, note_ids):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "note_ids" and isinstance(item, list) and any(
                str(note_id) in note_ids for note_id in item
            ):
                return True
            if key in {"note_id", "knowledge_note_id"} and str(item) in note_ids:
                return True
            if _contains_note_reference(item, note_ids):
                return True
    elif isinstance(value, list):
        return any(_contains_note_reference(item, note_ids) for item in value)
    return False


def _mark_deleted_sources(value, note_ids):
    """Keep old learning content readable without resolving reused note IDs."""
    changed = False
    if isinstance(value, dict):
        if str(value.get("knowledge_note_id")) in note_ids:
            value["knowledge_note_id"] = None
            value["deleted"] = True
            changed = True
        for item in value.values():
            changed = _mark_deleted_sources(item, note_ids) or changed
    elif isinstance(value, list):
        for item in value:
            changed = _mark_deleted_sources(item, note_ids) or changed
    return changed


def _update_historical_sources(connection, note_ids):
    for table, columns in (
        ("messages", ("metadata_json",)),
        ("generated_artifacts", ("configuration_json", "content_json")),
        ("exams", ("content",)),
    ):
        for row in connection.execute(f"SELECT * FROM {table}").fetchall():
            for column in columns:
                raw = row[column]
                if not raw:
                    continue
                try:
                    value = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if _mark_deleted_sources(value, note_ids):
                    connection.execute(
                        f"UPDATE {table} SET {column}=? WHERE id=?",
                        (json.dumps(value, ensure_ascii=False), row["id"]),
                    )


def _update_usage(connection, document_id, note_ids):
    connection.execute("UPDATE llm_usage SET document_id=NULL WHERE document_id=?", (document_id,))
    for row in connection.execute("SELECT id,note_ids_json FROM llm_usage WHERE note_ids_json IS NOT NULL").fetchall():
        try:
            values = json.loads(row["note_ids_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(values, list):
            continue
        kept = [value for value in values if str(value) not in note_ids]
        if kept != values:
            connection.execute("UPDATE llm_usage SET note_ids_json=? WHERE id=?",
                               (json.dumps(kept), row["id"]))


def _delete_sql(connection, document_id, note_ids):
    if note_ids:
        placeholders = ",".join("?" for _ in note_ids)
        parameters = tuple(int(value) for value in note_ids)
        concepts = [row[0] for row in connection.execute(
            f"SELECT DISTINCT concept_id FROM brain_concept_sources WHERE note_id IN ({placeholders})",
            parameters,
        )]
        edges = [row[0] for row in connection.execute(
            f"SELECT DISTINCT edge_id FROM brain_edge_sources WHERE note_id IN ({placeholders})",
            parameters,
        )]
        for row in connection.execute("SELECT id,payload_json,original_payload_json,reviewed_payload_json "
                                      "FROM brain_proposals").fetchall():
            if any(_contains_note_reference(json.loads(row[column]), note_ids)
                   for column in ("payload_json", "original_payload_json", "reviewed_payload_json")
                   if row[column]):
                connection.execute("DELETE FROM brain_proposals WHERE id=?", (row["id"],))
        _update_historical_sources(connection, note_ids)
        _update_usage(connection, document_id, note_ids)
        # Foreign keys remove the evidence and the persistent processing markers.
        connection.execute("DELETE FROM knowledge_notes WHERE document_id=?", (document_id,))
        for edge_id in edges:
            connection.execute(
                "DELETE FROM brain_edges WHERE id=? AND NOT EXISTS "
                "(SELECT 1 FROM brain_edge_sources WHERE edge_id=?)",
                (edge_id, edge_id),
            )
        for concept_id in concepts:
            connection.execute(
                "DELETE FROM brain_concepts WHERE id=? "
                "AND NOT EXISTS (SELECT 1 FROM brain_concept_sources WHERE concept_id=?) "
                "AND NOT EXISTS (SELECT 1 FROM brain_edges e JOIN brain_edge_sources es "
                "ON es.edge_id=e.id WHERE e.source_concept_id=? OR e.target_concept_id=?)",
                (concept_id, concept_id, concept_id, concept_id),
            )
    else:
        _update_usage(connection, document_id, set())
    connection.execute("DELETE FROM documents WHERE id=?", (document_id,))


def _restore_vectors(snapshots):
    errors = []
    for collection, values in reversed(snapshots):
        if not values["ids"]:
            continue
        options = {"ids": values["ids"], "embeddings": values["embeddings"],
                   "metadatas": values["metadatas"]}
        if values.get("documents") is not None and all(
            document is not None for document in values["documents"]
        ):
            options["documents"] = values["documents"]
        try:
            collection.upsert(**options)
        except Exception:
            errors.append(collection.name)
    return errors


def delete_document(document_id):
    """Remove a PDF, its notes, index records and unsupported graph evidence."""
    with _rename_lock:
        if not _processing_lock.acquire(blocking=False):
            raise ValueError("Eine Dokumentverarbeitung läuft. Bitte danach erneut versuchen.")
        try:
            if not _index_lock.acquire(blocking=False):
                raise ValueError("Eine Indexierung läuft. Bitte danach erneut versuchen.")
            try:
                with closing(get_connection()) as connection:
                    client = None
                    snapshots = []
                    original_files = {}
                    vault = None
                    vault_before = set()
                    changed_files = False
                    try:
                        connection.execute("BEGIN IMMEDIATE")
                        document = connection.execute("SELECT * FROM documents WHERE id=?",
                                                      (document_id,)).fetchone()
                        if document is None:
                            raise ValueError("Das Dokument wurde nicht gefunden.")
                        notes = connection.execute(
                            "SELECT id,markdown_path FROM knowledge_notes WHERE document_id=?",
                            (document_id,),
                        ).fetchall()
                        note_ids = {str(row["id"]) for row in notes}
                        files = [_checked_path(document_manager.USER_DATA_PATH, document["file_path"])]
                        files.extend(_checked_path(markdown_store.KNOWLEDGE_BASE_PATH, row["markdown_path"])
                                     for row in notes)
                        # A registered file shared by another record must remain in place.
                        shared = connection.execute("SELECT 1 FROM documents WHERE id!=? AND file_path=? LIMIT 1",
                                                    (document_id, document["file_path"])).fetchone()
                        if shared:
                            files.pop(0)
                        vault = _vault_path(document["course"])
                        vault_before = _managed_vault_files(vault)
                        for path in set(files) | vault_before:
                            if path.exists():
                                original_files[path] = path.read_bytes()
                        if note_ids and vector_store.CHROMA_PATH.exists():
                            client = chromadb.PersistentClient(
                                path=str(vector_store.CHROMA_PATH),
                                settings=Settings(anonymized_telemetry=False),
                            )
                            ids = [f"knowledge_note_{note_id}" for note_id in note_ids]
                            for info in client.list_collections():
                                if not info.name.startswith("knowledge_"):
                                    continue
                                collection = client.get_collection(info.name, embedding_function=None)
                                values = collection.get(ids=ids, include=["embeddings", "metadatas", "documents"])
                                if values["ids"]:
                                    snapshots.append((collection, values))
                                    collection.delete(ids=values["ids"])
                        _delete_sql(connection, document_id, note_ids)
                        changed_files = True
                        for path in files:
                            path.unlink(missing_ok=True)
                        if vault_before:
                            export_course_to_obsidian(
                                document["course"],
                                graph=load_graph(document["course"], connection=connection),
                                notes=load_notes(course=document["course"], connection=connection),
                            )
                        connection.commit()
                        return {"deleted_notes": len(notes), "course": document["course"]}
                    except Exception as error:
                        connection.rollback()
                        failures = _restore_vectors(snapshots)
                        if changed_files:
                            current_vault = _managed_vault_files(vault)
                            for path in current_vault - vault_before:
                                try:
                                    path.unlink()
                                except OSError:
                                    failures.append(str(path))
                            for path, content in original_files.items():
                                try:
                                    path.parent.mkdir(parents=True, exist_ok=True)
                                    path.write_bytes(content)
                                except OSError:
                                    failures.append(str(path))
                        if failures:
                            raise RuntimeError("Rücknahme unvollständig. Bitte lokale Daten prüfen: "
                                               + ", ".join(failures)) from error
                        raise
                    finally:
                        if client is not None:
                            client.close()
            finally:
                _index_lock.release()
        finally:
            _processing_lock.release()

