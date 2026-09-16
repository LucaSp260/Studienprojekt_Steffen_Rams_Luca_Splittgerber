"""Vorhandene Markdown-Notes indexieren, ohne Knowledge Extraction."""

import hashlib
import json
import re
from contextlib import closing
from threading import Lock

import yaml

from src.knowledge_layer.embedding_service import EmbeddingService
from src.knowledge_layer.markdown_store import load_note
from src.knowledge_layer.vector_store import SearchError, VectorStore, collection_name
from src.persistence.database import get_connection
from src.persistence.knowledge_repository import load_notes

_index_lock = Lock()


def prepare_record(row):
    try:
        metadata, content = load_note(row["markdown_path"])
        if not re.sub(r"(?m)^#+.*$", "", content).strip():
            raise ValueError("Leere Note")
        for key in ("title", "topic", "course", "source_file", "difficulty"):
            if not isinstance(metadata[key], str) or not metadata[key].strip():
                raise ValueError("Leere Metadaten")
        if not metadata["tags"] or any(not isinstance(tag, str) for tag in metadata["tags"]):
            raise ValueError("Ungültige Tags")
        if not metadata["source_pages"] or any(type(page) is not int or page < 1 for page in metadata["source_pages"]):
            raise ValueError("Ungültige Quellseiten")
        # Kein YAML, keine IDs oder Dateipfade in den Text aufnehmen.
        text = f"Titel: {metadata['title']}\nThema: {metadata['topic']}\nTags: {', '.join(metadata['tags'])}\n\n{content}"
        fingerprint = hashlib.sha256(json.dumps([metadata, content], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        chroma_metadata = {
            "knowledge_note_id": row["id"], "document_id": row["document_id"],
            "markdown_path": row["markdown_path"], "content_hash": fingerprint,
            **{key: metadata[key] for key in ("title", "course", "topic", "difficulty", "source_file")},
            **{key: json.dumps(metadata[key], ensure_ascii=False)
               for key in ("tags", "source_pages", "related_topics")},
        }
        return {"id": f"knowledge_note_{row['id']}", "text": text, "metadata": chroma_metadata,
                "note_metadata": metadata, "content": content}
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        raise SearchError(f"Knowledge Note #{row['id']} fehlt, ist leer oder ungültig. Bitte die Markdown-Datei prüfen.") from None


def index_existing_notes(document_id=None, embedding_service=None, store=None, progress=None):
    if not _index_lock.acquire(blocking=False):
        raise SearchError("Eine Indexierung läuft bereits. Bitte deren Abschluss abwarten.")
    own_store = store is None
    try:
        service = embedding_service if embedding_service is not None else EmbeddingService()
        store = store if store is not None else VectorStore(service.config)
        if store.name != collection_name(service.config):
            raise SearchError("Suchindex und Embedding-Modell passen nicht zusammen.")
        rows = load_notes(document_id=document_id)
        records = [prepare_record(row) for row in rows]
        existing = store.get(ids=[record["id"] for record in records])
        fingerprints = dict(zip(existing["ids"], existing["metadatas"]))
        pending = [record for record in records if
                   fingerprints.get(record["id"], {}).get("content_hash") != record["metadata"]["content_hash"]]
        if pending:
            if progress:
                progress(f"Embeddings für {len(pending)} Knowledge Notes werden erzeugt …")
            embeddings = service.embed_documents([record["text"] for record in pending])
            store.upsert(pending, embeddings)
        # Bei einem abgebrochenen Chroma-/SQLite-Schritt kann derselbe Aufruf
        # fortsetzen: stabile IDs und Hashes vermeiden neue API-Kosten.
        indexed = set(store.get(ids=[record["id"] for record in records])["ids"])
        if len(indexed) != len(records):
            raise SearchError("Nicht alle Themen wurden im lokalen Suchindex gespeichert.")
        with closing(get_connection()) as connection, connection:
            for source_id in {row["document_id"] for row in rows}:
                connection.execute("UPDATE documents SET processed = 1 WHERE id = ?", (source_id,))
        return {"indexed": len(pending), "skipped": len(records) - len(pending), "total": len(records)}
    finally:
        try:
            if own_store and store is not None:
                store.close()
        finally:
            _index_lock.release()
