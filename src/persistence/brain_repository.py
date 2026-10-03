"""Persistenz für vorgeschlagene und menschlich kuratierte Wissensgraphen."""

import hashlib
import json
import re
from contextlib import closing, nullcontext

from src.persistence.database import get_connection


def _key(value):
    return re.sub(r"\s+", " ", value.casefold()).strip()


def save_proposals(course, proposals):
    connection = get_connection()
    created = 0
    try:
        with connection:
            for proposal_type, items in (("concept", proposals.concepts),
                                         ("connection", proposals.connections)):
                for item in items:
                    payload = item.model_dump()
                    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    fingerprint = hashlib.sha256(
                        f"{course}\0{proposal_type}\0{encoded}".encode("utf-8")
                    ).hexdigest()
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO brain_proposals
                           (course, proposal_type, fingerprint, payload_json, original_payload_json)
                           VALUES (?, ?, ?, ?, ?)""",
                        (course, proposal_type, fingerprint, encoded, encoded),
                    )
                    created += cursor.rowcount
    finally:
        connection.close()
    return created


def load_proposals(course=None, status="pending"):
    query = "SELECT * FROM brain_proposals WHERE status = ?"
    parameters = [status]
    if course is not None:
        query += " AND course = ?"
        parameters.append(course)
    query += " ORDER BY created_at DESC, id DESC"
    with closing(get_connection()) as connection:
        rows = connection.execute(query, parameters).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        item["original_payload"] = json.loads(item.get("original_payload_json") or "{}")
        reviewed = item.get("reviewed_payload_json")
        item["reviewed_payload"] = json.loads(reviewed) if reviewed else None
        output.append(item)
    return output


def update_proposal(proposal_id, payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with closing(get_connection()) as connection, connection:
        cursor = connection.execute(
            """UPDATE brain_proposals SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status = 'pending'""", (encoded, proposal_id)
        )
        if cursor.rowcount != 1:
            raise ValueError("Der Vorschlag ist nicht mehr offen.")


def reject_proposal(proposal_id):
    with closing(get_connection()) as connection, connection:
        cursor = connection.execute(
            """UPDATE brain_proposals SET status = 'rejected', review_outcome = 'rejected',
               reviewed_payload_json = payload_json, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status = 'pending'""", (proposal_id,)
        )
        if cursor.rowcount != 1:
            raise ValueError("Der Vorschlag ist nicht mehr offen.")


def accept_proposal(proposal_id, payload=None):
    accept_proposals([(proposal_id, payload)])


def accept_proposals(proposals):
    """Accept several pending proposals atomically.

    ``proposals`` is an iterable of ``(proposal_id, reviewed_payload)`` pairs.
    If any proposal is no longer pending, the whole batch is rolled back.
    """
    connection = get_connection()
    try:
        with connection:
            for proposal_id, payload in proposals:
                proposal = connection.execute(
                    "SELECT * FROM brain_proposals WHERE id = ? AND status = 'pending'", (proposal_id,)
                ).fetchone()
                if proposal is None:
                    raise ValueError("Der Vorschlag ist nicht mehr offen.")
                data = payload or json.loads(proposal["payload_json"])
                original = proposal["original_payload_json"] or proposal["payload_json"]
                reviewed_json = json.dumps(data, ensure_ascii=False, sort_keys=True)
                outcome = "accepted" if reviewed_json == original else "edited"
                if proposal["proposal_type"] == "concept":
                    _upsert_concept(connection, proposal["course"], data["name"], data.get("description", ""), "agent")
                    _link_sources(connection, "brain_concept_sources", "concept_id",
                                  _concept_id(connection, proposal["course"], data["name"]), data["note_ids"])
                else:
                    source_id = _upsert_concept(connection, proposal["course"], data["source_concept"], "", "agent")
                    target_id = _upsert_concept(connection, proposal["course"], data["target_concept"], "", "agent")
                    _link_sources(connection, "brain_concept_sources", "concept_id", source_id, data["note_ids"])
                    _link_sources(connection, "brain_concept_sources", "concept_id", target_id, data["note_ids"])
                    connection.execute(
                        """INSERT INTO brain_edges
                           (source_concept_id, target_concept_id, relation_type, relation_description, rationale, origin)
                           VALUES (?, ?, ?, ?, ?, 'agent') ON CONFLICT(source_concept_id, target_concept_id, relation_type)
                           DO UPDATE SET rationale = CASE WHEN brain_edges.origin = 'manual'
                               THEN brain_edges.rationale ELSE excluded.rationale END,
                               relation_description = CASE WHEN brain_edges.origin = 'manual'
                               THEN brain_edges.relation_description ELSE excluded.relation_description END,
                               review_status = 'user_confirmed',
                               updated_at = CURRENT_TIMESTAMP""",
                        (source_id, target_id, data["relation_type"],
                         data.get("relation_description") or data.get("rationale", ""), data.get("rationale", "")),
                    )
                    edge = connection.execute(
                        """SELECT id FROM brain_edges WHERE source_concept_id = ? AND target_concept_id = ?
                           AND relation_type = ?""",
                        (source_id, target_id, data["relation_type"]),
                    ).fetchone()
                    _link_sources(connection, "brain_edge_sources", "edge_id", edge["id"], data["note_ids"])
                connection.execute(
                    """UPDATE brain_proposals SET status = 'accepted', reviewed_payload_json = ?,
                       review_outcome = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (reviewed_json, outcome, proposal_id),
                )
    finally:
        connection.close()


def _concept_id(connection, course, name):
    return connection.execute(
        "SELECT id FROM brain_concepts WHERE course = ? AND name_key = ?", (course, _key(name))
    ).fetchone()["id"]


def _upsert_concept(connection, course, name, description, origin):
    connection.execute(
        """INSERT INTO brain_concepts (course, name, name_key, description, origin)
           VALUES (?, ?, ?, ?, ?) ON CONFLICT(course, name_key) DO UPDATE SET
           description = CASE WHEN brain_concepts.origin = 'manual' THEN brain_concepts.description
                              WHEN excluded.description != '' THEN excluded.description
                              ELSE brain_concepts.description END,
           updated_at = CURRENT_TIMESTAMP""",
        (course, name.strip(), _key(name), description.strip(), origin),
    )
    return _concept_id(connection, course, name)


def _link_sources(connection, table, owner_column, owner_id, note_ids):
    for note_id in sorted(set(note_ids)):
        connection.execute(
            f"INSERT OR IGNORE INTO {table} ({owner_column}, note_id) VALUES (?, ?)",
            (owner_id, note_id),
        )


def add_manual_concept(course, name, description, note_ids):
    connection = get_connection()
    try:
        with connection:
            concept_id = _upsert_concept(connection, course, name, description, "manual")
            _link_sources(connection, "brain_concept_sources", "concept_id", concept_id, note_ids)
            return concept_id
    finally:
        connection.close()


def save_concept(concept_id, name, description, note_ids):
    if not name.strip():
        raise ValueError("Der Konzeptname darf nicht leer sein.")
    connection = get_connection()
    try:
        with connection:
            row = connection.execute("SELECT course FROM brain_concepts WHERE id = ?", (concept_id,)).fetchone()
            if row is None:
                raise ValueError("Konzept wurde nicht gefunden.")
            duplicate = connection.execute(
                "SELECT id FROM brain_concepts WHERE course = ? AND name_key = ? AND id != ?",
                (row["course"], _key(name), concept_id),
            ).fetchone()
            if duplicate:
                raise ValueError("Dieses Konzept existiert im Kurs bereits. Nutze Zusammenführen über die Konzeptauswahl.")
            connection.execute(
                """UPDATE brain_concepts SET name = ?, name_key = ?, description = ?, origin = 'manual',
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (name.strip(), _key(name), description.strip(), concept_id),
            )
            connection.execute("DELETE FROM brain_concept_sources WHERE concept_id = ?", (concept_id,))
            _link_sources(connection, "brain_concept_sources", "concept_id", concept_id, note_ids)
    finally:
        connection.close()


def merge_concepts(source_id, target_id):
    if source_id == target_id:
        raise ValueError("Wähle zwei verschiedene Konzepte.")
    connection = get_connection()
    try:
        with connection:
            source = connection.execute("SELECT course FROM brain_concepts WHERE id = ?", (source_id,)).fetchone()
            target = connection.execute("SELECT course FROM brain_concepts WHERE id = ?", (target_id,)).fetchone()
            if source is None or target is None or source["course"] != target["course"]:
                raise ValueError("Konzepte können nur innerhalb desselben Kurses zusammengeführt werden.")
            connection.execute(
                "INSERT OR IGNORE INTO brain_concept_sources SELECT ?, note_id FROM brain_concept_sources WHERE concept_id = ?",
                (target_id, source_id),
            )
            for edge in connection.execute(
                "SELECT * FROM brain_edges WHERE source_concept_id = ? OR target_concept_id = ?", (source_id, source_id)
            ).fetchall():
                new_source = target_id if edge["source_concept_id"] == source_id else edge["source_concept_id"]
                new_target = target_id if edge["target_concept_id"] == source_id else edge["target_concept_id"]
                if new_source == new_target:
                    connection.execute("DELETE FROM brain_edges WHERE id = ?", (edge["id"],))
                    continue
                connection.execute(
                    """INSERT OR IGNORE INTO brain_edges
                       (source_concept_id,target_concept_id,relation_type,relation_description,rationale,origin)
                       VALUES (?,?,?,?,?,?)""",
                    (new_source, new_target, edge["relation_type"], edge["relation_description"],
                     edge["rationale"], "manual"),
                )
                new_edge = connection.execute(
                    "SELECT id FROM brain_edges WHERE source_concept_id=? AND target_concept_id=? AND relation_type=?",
                    (new_source, new_target, edge["relation_type"]),
                ).fetchone()["id"]
                connection.execute(
                    "INSERT OR IGNORE INTO brain_edge_sources SELECT ?, note_id FROM brain_edge_sources WHERE edge_id = ?",
                    (new_edge, edge["id"]),
                )
                if new_edge != edge["id"]:
                    connection.execute("DELETE FROM brain_edges WHERE id = ?", (edge["id"],))
            connection.execute("DELETE FROM brain_concepts WHERE id = ?", (source_id,))
    finally:
        connection.close()


def delete_concept(concept_id):
    with closing(get_connection()) as connection, connection:
        connection.execute("DELETE FROM brain_concepts WHERE id = ?", (concept_id,))


def add_manual_edge(course, source_id, target_id, relation_type, rationale, note_ids,
                    relation_description=None):
    if source_id == target_id:
        raise ValueError("Ein Konzept kann nicht mit sich selbst verbunden werden.")
    connection = get_connection()
    try:
        with connection:
            concepts = connection.execute(
                "SELECT id FROM brain_concepts WHERE course = ? AND id IN (?, ?)",
                (course, source_id, target_id),
            ).fetchall()
            if len(concepts) != 2:
                raise ValueError("Beide Konzepte müssen zum gewählten Kurs gehören.")
            connection.execute(
                """INSERT INTO brain_edges
                   (source_concept_id,target_concept_id,relation_type,relation_description,rationale,origin)
                   VALUES (?,?,?,?,?,'manual') ON CONFLICT(source_concept_id,target_concept_id,relation_type)
                   DO UPDATE SET relation_description=excluded.relation_description,
                   rationale=excluded.rationale, origin='manual', review_status='user_confirmed',
                   updated_at=CURRENT_TIMESTAMP""",
                (source_id, target_id, relation_type, (relation_description or rationale).strip(), rationale.strip()),
            )
            edge_id = connection.execute(
                "SELECT id FROM brain_edges WHERE source_concept_id=? AND target_concept_id=? AND relation_type=?",
                (source_id, target_id, relation_type),
            ).fetchone()["id"]
            connection.execute("DELETE FROM brain_edge_sources WHERE edge_id = ?", (edge_id,))
            _link_sources(connection, "brain_edge_sources", "edge_id", edge_id, note_ids)
            return edge_id
    finally:
        connection.close()


def save_edge(edge_id, source_id, target_id, relation_type, rationale, note_ids,
              relation_description=None):
    if source_id == target_id:
        raise ValueError("Ein Konzept kann nicht mit sich selbst verbunden werden.")
    if not rationale.strip():
        raise ValueError("Bitte eine Begründung für die Beziehung angeben.")
    connection = get_connection()
    try:
        with connection:
            edge = connection.execute("SELECT id FROM brain_edges WHERE id = ?", (edge_id,)).fetchone()
            if edge is None:
                raise ValueError("Beziehung wurde nicht gefunden.")
            same_course = connection.execute(
                """SELECT COUNT(*) FROM brain_concepts a JOIN brain_concepts b ON b.course = a.course
                   WHERE a.id = ? AND b.id = ?""", (source_id, target_id)
            ).fetchone()[0]
            if same_course != 1:
                raise ValueError("Eine Beziehung muss zwei Konzepte desselben Kurses verbinden.")
            connection.execute(
                """UPDATE brain_edges SET source_concept_id=?, target_concept_id=?, relation_type=?,
                   relation_description=?, rationale=?, origin='manual', review_status='user_confirmed',
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (source_id, target_id, relation_type,
                 (relation_description or rationale).strip(), rationale.strip(), edge_id),
            )
            connection.execute("DELETE FROM brain_edge_sources WHERE edge_id = ?", (edge_id,))
            _link_sources(connection, "brain_edge_sources", "edge_id", edge_id, note_ids)
    finally:
        connection.close()


def delete_edge(edge_id):
    with closing(get_connection()) as connection, connection:
        connection.execute("DELETE FROM brain_edges WHERE id = ?", (edge_id,))


def load_graph(course, connection=None):
    context = nullcontext(connection) if connection is not None else closing(get_connection())
    with context as connection:
        concepts = connection.execute(
            """SELECT c.*, GROUP_CONCAT(n.id) AS note_ids, GROUP_CONCAT(DISTINCT d.filename) AS sources
               FROM brain_concepts c
               LEFT JOIN brain_concept_sources cs ON cs.concept_id = c.id
               LEFT JOIN knowledge_notes n ON n.id = cs.note_id
               LEFT JOIN documents d ON d.id = n.document_id
               WHERE c.course = ? GROUP BY c.id ORDER BY c.name COLLATE NOCASE""", (course,)
        ).fetchall()
        edges = connection.execute(
            """SELECT e.*, s.name AS source_name, t.name AS target_name,
               GROUP_CONCAT(DISTINCT n.id) AS note_ids, GROUP_CONCAT(DISTINCT d.filename) AS sources
               FROM brain_edges e JOIN brain_concepts s ON s.id = e.source_concept_id
               JOIN brain_concepts t ON t.id = e.target_concept_id
               LEFT JOIN brain_edge_sources es ON es.edge_id = e.id
               LEFT JOIN knowledge_notes n ON n.id = es.note_id
               LEFT JOIN documents d ON d.id = n.document_id
               WHERE s.course = ? GROUP BY e.id ORDER BY s.name COLLATE NOCASE, t.name COLLATE NOCASE""",
            (course,),
        ).fetchall()
    return concepts, edges


def review_stats(course):
    with closing(get_connection()) as connection:
        return connection.execute(
            """SELECT review_outcome, COUNT(*) AS amount FROM brain_proposals
               WHERE course = ? AND review_outcome IS NOT NULL GROUP BY review_outcome""", (course,)
        ).fetchall()


def pending_brain_notes(document_id):
    """Nur noch nicht analysierte Notes eines Dokuments; Bestand ist migriert/markiert."""
    with closing(get_connection()) as connection:
        return connection.execute(
            """SELECT n.* FROM knowledge_notes n
               LEFT JOIN brain_note_processing p ON p.note_id = n.id
               WHERE n.document_id = ? AND p.note_id IS NULL ORDER BY n.id""",
            (document_id,),
        ).fetchall()


def save_incremental_graph(course, new_note_ids, draft, processing_version=1):
    """Fügt Agent-Ergebnisse und Verarbeitungsmarken atomar hinzu, ohne Alt-Kanten zu ändern."""
    ids = sorted(set(int(value) for value in new_note_ids))
    if not ids:
        return {"concepts": 0, "edges": 0, "processed_notes": 0}
    from src.agent_layer.connection_agent import AGENT_RELATION_TYPES

    connection = get_connection()
    created_concepts = created_edges = 0
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"""SELECT n.id, d.course FROM knowledge_notes n
                    JOIN documents d ON d.id = n.document_id WHERE n.id IN ({placeholders})""", ids
            ).fetchall()
            if len(rows) != len(ids) or any((row["course"] or "") != course for row in rows):
                raise ValueError("Neue Knowledge Notes gehören nicht vollständig zum gewählten Kurs.")
            already = connection.execute(
                f"SELECT COUNT(*) FROM brain_note_processing WHERE note_id IN ({placeholders})", ids
            ).fetchone()[0]
            if already:
                raise ValueError("Mindestens eine Knowledge Note wurde bereits für den Atlas verarbeitet.")
            new_ids = set(ids)
            for item in draft.concepts:
                if not new_ids.intersection(item.note_ids):
                    raise ValueError("Ein neues Konzept benötigt einen Beleg aus dem neuen Dokument.")
                before = connection.total_changes
                connection.execute(
                    """INSERT OR IGNORE INTO brain_concepts
                       (course,name,name_key,description,origin) VALUES (?,?,?,?,'agent')""",
                    (course, item.name.strip(), _key(item.name), item.description.strip()),
                )
                created_concepts += connection.total_changes - before
                concept_id = _concept_id(connection, course, item.name)
                _link_sources(connection, "brain_concept_sources", "concept_id", concept_id, item.note_ids)
            inverse = {"part_of": "has_part", "has_part": "part_of"}
            for item in draft.connections:
                if item.relation_type not in AGENT_RELATION_TYPES or not item.relation_description.strip():
                    raise ValueError("Neue Beziehungen brauchen eine kanonische Kategorie und Beschreibung.")
                if not new_ids.intersection(item.note_ids):
                    raise ValueError("Eine neue Beziehung benötigt einen Beleg aus dem neuen Dokument.")
                endpoints = []
                for name in (item.source_concept, item.target_concept):
                    before = connection.total_changes
                    connection.execute(
                        """INSERT OR IGNORE INTO brain_concepts
                           (course,name,name_key,description,origin) VALUES (?,?,?,'','agent')""",
                        (course, name.strip(), _key(name)),
                    )
                    created_concepts += connection.total_changes - before
                    endpoints.append(_concept_id(connection, course, name))
                source_id, target_id = endpoints
                if source_id == target_id:
                    continue
                for concept_id in endpoints:
                    _link_sources(connection, "brain_concept_sources", "concept_id", concept_id, item.note_ids)
                duplicate = connection.execute(
                    """SELECT id FROM brain_edges WHERE
                       (source_concept_id=? AND target_concept_id=? AND relation_type=?) OR
                       (source_concept_id=? AND target_concept_id=? AND relation_type=?)""",
                    (source_id, target_id, item.relation_type,
                     target_id, source_id, inverse.get(item.relation_type, "")),
                ).fetchone()
                if duplicate:
                    continue
                cursor = connection.execute(
                    """INSERT INTO brain_edges
                       (source_concept_id,target_concept_id,relation_type,relation_description,rationale,
                        origin,review_status,generation_source)
                       VALUES (?,?,?,?,?,'agent','ai_generated','connection_agent')""",
                    (source_id, target_id, item.relation_type, item.relation_description.strip(),
                     item.rationale.strip()),
                )
                _link_sources(connection, "brain_edge_sources", "edge_id", cursor.lastrowid, item.note_ids)
                created_edges += 1
            connection.executemany(
                """INSERT INTO brain_note_processing (note_id,processing_version,status)
                   VALUES (?,?,'completed')""",
                [(note_id, processing_version) for note_id in ids],
            )
    finally:
        connection.close()
    return {"concepts": created_concepts, "edges": created_edges, "processed_notes": len(ids)}
