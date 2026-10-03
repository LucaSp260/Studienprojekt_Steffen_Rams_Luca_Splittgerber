"""Rename course metadata with SQL transaction and compensating file/index rollback."""
import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
from threading import RLock
import chromadb
from chromadb.config import Settings
import yaml
from src.persistence.database import get_connection, backup_database
from src.persistence.course_repository import clean_course, list_courses, resolve_course
from src.knowledge_layer import markdown_store, vector_store
from src.knowledge_layer.obsidian_export import _slug, _safe_text

_rename_lock = RLock()

def _replace_course(data, old, new):
    if isinstance(data, dict):
        return {key: new if key == "course" and value == old else _replace_course(value, old, new)
                for key, value in data.items()}
    if isinstance(data, list):
        return [_replace_course(value, old, new) for value in data]
    return data

def _course_markdown(raw, old, new):
    text = raw.decode("utf-8")
    import re
    header = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if header:
        metadata = yaml.safe_load(header.group(1))
        if metadata.get("course") == old:
            metadata["course"] = new
        newline = "\r\n" if text.startswith("---\r\n") else "\n"
        prefix = ("---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n")
        return (prefix.replace("\n", newline) + text[header.end():]).encode("utf-8"), metadata
    return raw, None

def _atomic_write(path, content):
    temporary = path.with_name(path.name + ".course-tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def _vault_path(course):
    return markdown_store.KNOWLEDGE_BASE_PATH / "Second Brain" / (
        f"{_slug(course)}-{hashlib.sha256(course.encode()).hexdigest()[:8]}")

def rename_course(old, new):
    new = clean_course(new)
    from src.knowledge_layer.knowledge_manager import _processing_lock
    from src.knowledge_layer.indexing_service import _index_lock
    with _rename_lock, _processing_lock, _index_lock, closing(get_connection()) as conn:
        old = resolve_course(old, conn)
        names = list_courses(conn)
        if old not in names:
            raise ValueError("Der ursprüngliche Kurs wurde nicht gefunden.")
        if new == old:
            return
        if any(name != old and name.casefold() == new.casefold() for name in names):
            raise ValueError("Der Zielname gehört bereits zu einem anderen Kurs. Kurse werden nicht zusammengeführt.")
        backup_database("course-rename")
        files, replacements, hashes, changed_files, snapshots = {}, {}, {}, [], []
        client = None
        old_vault, new_vault = _vault_path(old), _vault_path(new)
        moved_vault = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            if old_vault.exists() and new_vault.exists() and old_vault != new_vault:
                raise ValueError("Der Obsidian-Zielordner existiert bereits. Bitte den Exportordner prüfen.")
            rows = conn.execute("""SELECT n.* FROM knowledge_notes n JOIN documents d ON d.id=n.document_id
                WHERE d.course=?""", (old,)).fetchall()
            for row in rows:
                path = (markdown_store.KNOWLEDGE_BASE_PATH.parent / row["markdown_path"]).resolve()
                if not path.is_relative_to(markdown_store.KNOWLEDGE_BASE_PATH.resolve()):
                    raise ValueError("Ungültiger Note-Pfad.")
                files[path] = path.read_bytes()
                replacements[path], metadata = _course_markdown(files[path], old, new)
                if not metadata or metadata.get("course") != new:
                    raise ValueError("SQLite und Markdown enthalten unterschiedliche Kursnamen.")
                _, body = markdown_store.load_note(row["markdown_path"])
                hashes[int(row["id"])] = hashlib.sha256(json.dumps([metadata, body], sort_keys=True,
                                                    ensure_ascii=False).encode()).hexdigest()
            if old_vault.exists():
                for path in old_vault.rglob("*.md"):
                    files[path] = path.read_bytes()
                    replacements[path], _ = _course_markdown(files[path], old, new)
                    if path.name == "Index.md":
                        replacements[path] = replacements[path].replace(
                            f"# Second Brain: {_safe_text(old)}".encode(),
                            f"# Second Brain: {_safe_text(new)}".encode(), 1)
            # All collections, including inactive provider/model indices. No embedding function.
            if vector_store.CHROMA_PATH.exists():
                client = chromadb.PersistentClient(path=str(vector_store.CHROMA_PATH),
                                                    settings=Settings(anonymized_telemetry=False))
                for info in client.list_collections():
                    collection = client.get_collection(info.name, embedding_function=None)
                    values = collection.get(where={"course": old}, include=["metadatas"])
                    if not values["ids"]:
                        continue
                    updated = []
                    for metadata in values["metadatas"]:
                        metadata = dict(metadata, course=new)
                        note_id = metadata.get("knowledge_note_id")
                        if note_id in hashes:
                            metadata["content_hash"] = hashes[note_id]
                        updated.append(metadata)
                    snapshots.append((collection, values["ids"], values["metadatas"]))
                    collection.update(ids=values["ids"], metadatas=updated)
            for path, content in replacements.items():
                changed_files.append(path)
                _atomic_write(path, content)
            if old_vault.exists() and old_vault != new_vault:
                old_vault.rename(new_vault)
                moved_vault = True
            for table in ("documents", "brain_concepts", "brain_proposals", "generated_artifacts"):
                conn.execute(f"UPDATE {table} SET course=? WHERE course=?", (new, old))
            # Nested course filters/configuration in chat history, artifacts and proposals.
            for table, columns in (("messages", ("metadata_json",)),
                    ("generated_artifacts", ("configuration_json", "content_json")),
                    ("brain_proposals", ("payload_json", "original_payload_json", "reviewed_payload_json"))):
                for row in conn.execute(f"SELECT * FROM {table}").fetchall():
                    for column in columns:
                        if row[column]:
                            data = json.loads(row[column])
                            revised = _replace_course(data, old, new)
                            if revised != data:
                                conn.execute(f"UPDATE {table} SET {column}=? WHERE id=?",
                                    (json.dumps(revised, ensure_ascii=False), row["id"]))
            # Legacy exams can contain JSON; preserve non-JSON legacy text verbatim.
            for row in conn.execute("SELECT id,content FROM exams").fetchall():
                try:
                    data = json.loads(row["content"])
                except (ValueError, TypeError):
                    continue
                revised = _replace_course(data, old, new)
                if revised != data:
                    conn.execute("UPDATE exams SET content=? WHERE id=?",
                                 (json.dumps(revised, ensure_ascii=False), row["id"]))
            # Fingerprints include course identity. Preserve the original payload/review history.
            for row in conn.execute("SELECT * FROM brain_proposals WHERE course=?", (new,)).fetchall():
                encoded = json.dumps(json.loads(row['payload_json']), ensure_ascii=False, sort_keys=True)
                fingerprint = hashlib.sha256(f"{new}\0{row['proposal_type']}\0{encoded}".encode()).hexdigest()
                conn.execute("UPDATE brain_proposals SET fingerprint=? WHERE id=?", (fingerprint, row["id"]))
            conn.execute("DELETE FROM courses WHERE name=?", (old,))
            conn.execute("INSERT INTO courses(name,name_key) VALUES (?,?)", (new, new.casefold()))
            conn.commit()
        except Exception as error:
            conn.rollback()
            recovery_errors = []
            if moved_vault:
                try:
                    new_vault.rename(old_vault)
                except OSError:
                    recovery_errors.append("Obsidian")
            for path in reversed(changed_files):
                try:
                    _atomic_write(path, files[path])
                except OSError:
                    recovery_errors.append("Markdown")
            for collection, ids, metadata in reversed(snapshots):
                try:
                    collection.update(ids=ids, metadatas=metadata)
                except Exception:
                    recovery_errors.append("Chroma")
            if recovery_errors:
                raise RuntimeError("Rücknahme unvollständig: " + ", ".join(recovery_errors)
                                   + ". Datenbanksicherung ist vorhanden.") from error
            raise
        finally:
            if client is not None:
                client.close()
