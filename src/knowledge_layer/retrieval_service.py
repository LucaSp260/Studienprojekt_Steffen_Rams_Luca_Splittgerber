"""Semantisches Retrieval für UI und spätere Agents, ohne generative Antwort."""

from src.knowledge_layer.embedding_service import EmbeddingService
from src.knowledge_layer.indexing_service import prepare_record
from src.knowledge_layer.vector_store import SearchError, VectorStore, collection_name
from src.persistence.knowledge_repository import load_notes


def retrieve(query, top_k=5, course=None, *, embedding_service=None, store=None):
    if not isinstance(query, str) or not query.strip():
        raise SearchError("Bitte eine Suchanfrage eingeben.")
    if type(top_k) is not int or top_k < 1:
        raise SearchError("Die Anzahl der Ergebnisse muss eine positive ganze Zahl sein.")
    service = embedding_service if embedding_service is not None else EmbeddingService()
    own_store = store is None
    store = store if store is not None else VectorStore(service.config)
    try:
        if store.name != collection_name(service.config):
            raise SearchError("Suchindex und Embedding-Modell passen nicht zusammen.")
        available = store.get(course=course)
        if not available["ids"]:
            return []
        embedding = service.embed_query(query.strip())
        result = store.query(embedding, min(top_k, len(available["ids"])), course)
        rows = {row["id"]: row for row in load_notes()}
        output = []
        for metadata, distance in zip(result["metadatas"][0], result["distances"][0]):
            row = rows.get(metadata["knowledge_note_id"])
            if row is None:
                raise SearchError("Ein Suchindex-Eintrag gehört zu keiner vorhandenen Knowledge Note.")
            record = prepare_record(row)
            if metadata.get("content_hash") != record["metadata"]["content_hash"]:
                raise SearchError("Eine Knowledge Note wurde geändert. Bitte vorhandene Themen erneut indexieren.")
            output.append({"knowledge_note_id": row["id"], "document_id": row["document_id"],
                           "markdown_path": row["markdown_path"], **record["note_metadata"],
                           "content": record["content"], "distance": float(distance)})
        return sorted(output, key=lambda item: item["distance"])
    finally:
        if own_store:
            store.close()
