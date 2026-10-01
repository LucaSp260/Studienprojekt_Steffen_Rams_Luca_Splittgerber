"""Inkrementelle Erweiterung des Second Brain nach der Indexierung neuer Notes."""

from contextlib import closing

from src.agent_layer.connection_agent import propose_incremental_connections
from src.knowledge_layer.indexing_service import prepare_record
from src.knowledge_layer.retrieval_service import retrieve
from src.persistence.brain_repository import load_graph, pending_brain_notes, save_incremental_graph
from src.persistence.database import get_connection
from src.persistence.knowledge_repository import load_notes

DEFAULT_TOP_K = 5
MAX_OLD_CONTEXT = 12
MAX_GRAPH_CONTEXT = 20


def extend_brain(document_id, *, llm_service=None, retrieval_service=None,
                 top_k=DEFAULT_TOP_K, progress=None):
    """Analysiert nur unmarkierte Notes; alte Notes dienen begrenzt als Kontext."""
    new_notes = pending_brain_notes(document_id)
    if not new_notes:
        return {"concepts": 0, "edges": 0, "processed_notes": 0, "context_notes": 0}
    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k muss eine positive ganze Zahl sein.")
    with closing(get_connection()) as connection:
        document = connection.execute("SELECT course FROM documents WHERE id = ?", (document_id,)).fetchone()
    if document is None:
        raise ValueError("Das Dokument wurde nicht gefunden.")
    course = document["course"] or ""
    new_ids = {int(row["id"]) for row in new_notes}
    all_course_notes = load_notes(course=course) if course else [
        row for row in load_notes() if not row["course"]]
    old_by_id = {int(row["id"]): row for row in all_course_notes if int(row["id"]) not in new_ids}
    search = retrieval_service or retrieve
    candidates = {}
    if old_by_id and progress:
        progress("Relevante bestehende Knowledge Notes werden gesucht …")
    for row in (new_notes if old_by_id else []):
        record = prepare_record(row)
        query = record["text"][:700]
        # Neue Notes sind bereits indexiert und können die ersten Treffer belegen.
        found_old = 0
        for match in search(query, top_k=top_k + len(new_ids), course=course or "Ohne Kurs"):
            note_id = int(match["knowledge_note_id"])
            if note_id not in old_by_id:
                continue
            prior = candidates.get(note_id)
            if prior is None or match["distance"] < prior:
                candidates[note_id] = match["distance"]
            found_old += 1
            if found_old >= top_k:
                break
        # Je neuer Note höchstens top_k alte Treffer übernehmen.
        if len(candidates) > MAX_OLD_CONTEXT * 4:
            candidates = dict(sorted(candidates.items(), key=lambda item: item[1])[:MAX_OLD_CONTEXT * 4])
    selected_ids = [note_id for note_id, _ in sorted(candidates.items(), key=lambda item: item[1])[:MAX_OLD_CONTEXT]]
    context_notes = [old_by_id[note_id] for note_id in selected_ids if note_id in old_by_id]
    concepts, edges = load_graph(course)
    relevant_ids = set(selected_ids) | new_ids
    relevant_concepts = [item for item in concepts if
                         relevant_ids.intersection(_ids(item["note_ids"]))][:MAX_GRAPH_CONTEXT]
    relevant_concept_ids = {int(item["id"]) for item in relevant_concepts}
    relevant_edges = [item for item in edges if
                      int(item["source_concept_id"]) in relevant_concept_ids or
                      int(item["target_concept_id"]) in relevant_concept_ids][:MAX_GRAPH_CONTEXT]
    if progress:
        progress("Connection Agent ergänzt das Second Brain aus neuen und relevanten Notes …")
    draft = propose_incremental_connections(
        course or "Ohne Kurs", new_notes, context_notes, relevant_concepts, relevant_edges,
        llm_service=llm_service,
    )
    result = save_incremental_graph(course, new_ids, draft)
    return {**result, "context_notes": len(context_notes)}


def _ids(value):
    return {int(part) for part in (value or "").split(",") if part.isdigit()}
