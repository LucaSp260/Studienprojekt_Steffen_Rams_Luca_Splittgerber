"""Lokale ChromaDB, getrennte Collections pro Embedding-Konfiguration."""

import hashlib
import json
from pathlib import Path

import chromadb
from chromadb.config import Settings

CHROMA_PATH = Path(__file__).resolve().parents[2] / "chroma_db"
INDEX_VERSION = 1


class SearchError(Exception):
    """Verständlicher Fehler bei Indexierung oder Suche."""


def collection_name(config):
    identity = json.dumps([config.provider, config.model, config.dimensions, INDEX_VERSION])
    return "knowledge_" + hashlib.sha256(identity.encode()).hexdigest()[:32]


class VectorStore:
    def __init__(self, config):
        self.name = collection_name(config)
        self.dimensions = config.dimensions
        self.client = None
        try:
            self.client = chromadb.PersistentClient(
                path=str(CHROMA_PATH), settings=Settings(anonymized_telemetry=False)
            )
            self.collection = self.client.get_or_create_collection(
                name=self.name, embedding_function=None,
                configuration={"hnsw": {"space": "cosine"}},
                metadata={"provider": config.provider, "model": config.model,
                          "dimensions": config.dimensions, "index_version": INDEX_VERSION},
            )
            if self.collection.metadata != {"provider": config.provider, "model": config.model,
                                             "dimensions": config.dimensions, "index_version": INDEX_VERSION}:
                raise SearchError("Die Collection passt nicht zum gewählten Embedding-Modell.")
        except Exception as error:
            if self.client is not None:
                self.client.close()
            if isinstance(error, SearchError):
                raise
            raise SearchError("Der lokale Suchindex konnte nicht geöffnet werden. Schreibrechte und Speicher prüfen.") from None

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _run(self, function, **kwargs):
        try:
            return function(**kwargs)
        except Exception:
            raise SearchError("Die Suche im lokalen Index ist fehlgeschlagen. Einstellungen und Speicher prüfen.") from None

    def get(self, ids=None, course=None):
        if ids == []:
            return {"ids": [], "metadatas": []}
        options = {"include": ["metadatas"]}
        if ids is not None:
            options["ids"] = ids
        if course is not None:
            options["where"] = {"course": course}
        return self._run(self.collection.get, **options)

    def upsert(self, records, embeddings):
        if len(records) != len(embeddings) or any(len(vector) != self.dimensions for vector in embeddings):
            raise SearchError("Die Embeddings passen nicht zur Dimension der Collection.")
        if records:
            self._run(self.collection.upsert, ids=[record["id"] for record in records],
                      embeddings=embeddings, metadatas=[record["metadata"] for record in records])

    def query(self, embedding, top_k, course=None):
        options = {"query_embeddings": [embedding], "n_results": top_k,
                   "include": ["metadatas", "distances"]}
        if course is not None:
            options["where"] = {"course": course}
        return self._run(self.collection.query, **options)
