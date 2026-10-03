"""Exportiert Knowledge Notes und den bestätigten Wissensatlas für Obsidian."""

import hashlib
import json
import re
from pathlib import Path

import yaml

from src.agent_layer.connection_agent import RELATION_TYPES
from src.knowledge_layer.markdown_store import KNOWLEDGE_BASE_PATH, load_note
from src.persistence.brain_repository import load_graph
from src.persistence.database import get_connection
from src.persistence.knowledge_repository import load_notes


def _slug(value):
    value = re.sub(r"[^\w-]+", "-", value.casefold(), flags=re.UNICODE).strip("-_")
    return value[:70] or "kurs"


def _safe_text(value):
    return str(value).replace("[", "&#91;").replace("]", "&#93;")


def _linked_note(note_id, notes_by_id):
    note = notes_by_id.get(note_id)
    if note is None:
        return None
    return f"[[notes/note-{note_id}|{_safe_text(note['metadata']['title'])}]]"


def _relationship_details(description, rationale):
    details = []
    if description:
        details.append(_safe_text(description))
    if rationale and rationale != description:
        details.append(f"Begründung: {_safe_text(rationale)}")
    return " — " + " · ".join(details) if details else ""


def _relationship_status(edge):
    return "KI-generiert" if edge["review_status"] == "ai_generated" else "Nutzerbestätigt"


def validate_obsidian_export(root, expected_relationships=None):
    """Count and resolve exported Wikilinks, and report graph connectivity."""
    root = Path(root)
    markdown_files = sorted(root.rglob("*.md"))
    note_files = [path for path in markdown_files if re.fullmatch(r"note-\d+\.md", path.name)]
    concept_files = [path for path in markdown_files if re.fullmatch(r"concept-\d+\.md", path.name)]
    graph_files = note_files + concept_files
    degrees = {path.resolve(): set() for path in graph_files}
    wikilinks = resolvable = relationship_wikilinks = 0
    unresolved = []
    for source in graph_files:
        source_text = source.read_text(encoding="utf-8")
        relationship_section = re.search(
            r"^## (?:Bestätigte )?Beziehungen\s*$(.*?)(?=^## |\Z)",
            source_text, flags=re.MULTILINE | re.DOTALL,
        )
        if relationship_section:
            relationship_wikilinks += len(re.findall(r"\[\[[^\]]+\]\]", relationship_section.group(1)))
        for raw in re.findall(r"\[\[([^\]]+)\]\]", source_text):
            wikilinks += 1
            target = raw.split("|", 1)[0].split("#", 1)[0].strip()
            target_path = root / target
            if target_path.suffix.lower() != ".md":
                target_path = target_path.with_suffix(".md")
            if not target_path.is_file():
                unresolved.append({"source": source.relative_to(root).as_posix(), "target": target})
                continue
            resolvable += 1
            source_resolved, target_resolved = source.resolve(), target_path.resolve()
            if target_resolved in degrees:
                degrees[source_resolved].add(target_resolved)
                degrees[target_resolved].add(source_resolved)

    connected = sum(bool(neighbors) for neighbors in degrees.values())
    isolated = len(degrees) - connected
    isolated_nodes = []
    for path in graph_files:
        if degrees[path.resolve()]:
            continue
        if path in note_files:
            frontmatter = path.read_text(encoding="utf-8").split("---", 2)[1]
            title = (yaml.safe_load(frontmatter) or {}).get("title", path.stem)
            node_type = "Knowledge Note"
        else:
            title = path.read_text(encoding="utf-8").splitlines()[0].removeprefix("# ")
            node_type = "Konzept"
        isolated_nodes.append({"file": path.relative_to(root).as_posix(), "type": node_type, "title": title})
    report = {
        "exported_notes": len(note_files),
        "knowledge_notes": [
            {"file": path.relative_to(root).as_posix(),
             "title": yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1]).get("title", path.stem)}
            for path in note_files
        ],
        "exported_concepts": len(concept_files),
        "confirmed_relationships": expected_relationships,
        "wikilinks": wikilinks,
        "confirmed_relationship_wikilinks": relationship_wikilinks,
        "resolvable_wikilinks": resolvable,
        "unresolvable_wikilinks": len(unresolved),
        "unresolvable_targets": unresolved,
        "connected_notes_and_concepts": connected,
        "isolated_notes_and_concepts": isolated,
        "isolated_nodes": isolated_nodes,
        "graph_nodes": len(graph_files),
    }
    if expected_relationships and not wikilinks:
        raise ValueError("Export fehlerhaft: bestätigte Beziehungen vorhanden, aber keine Wikilinks erzeugt.")
    if unresolved:
        raise ValueError(f"Export enthält {len(unresolved)} nicht auflösbare Wikilinks.")
    return report


def export_course_to_obsidian(course, *, graph=None, notes=None):
    """Schreibt Kurs-Notes sowie KI-generierte und bestätigte Graphkanten.

    Die Exportdateien liegen in einem eigenen, direkt als Vault nutzbaren
    Unterordner. Die ursprünglichen Knowledge Notes werden nur gelesen.
    """
    course_key = course or ""
    course_label = course_key or "Ohne Kurs"
    concepts, edges = graph if graph is not None else load_graph(course_key)
    notes = notes if notes is not None else load_notes(course=course_key)
    course_folder = f"{_slug(course_label)}-{hashlib.sha256(course_key.encode('utf-8')).hexdigest()[:8]}"
    root = KNOWLEDGE_BASE_PATH / "Second Brain" / course_folder
    notes_folder = root / "notes"
    notes_folder.mkdir(parents=True, exist_ok=True)

    notes_by_id = {}
    for row in notes:
        metadata, body = load_note(row["markdown_path"])
        notes_by_id[int(row["id"])] = {"metadata": metadata, "body": body}

    concepts_by_id = {int(item["id"]): item for item in concepts}
    concept_note_ids = {
        int(concept["id"]): {
            int(value) for value in (concept["note_ids"] or "").split(",")
            if value.isdigit() and int(value) in notes_by_id
        }
        for concept in concepts
    }
    note_concepts = {note_id: [] for note_id in notes_by_id}
    for concept_id, note_ids in concept_note_ids.items():
        for note_id in note_ids:
            note_concepts[note_id].append(concept_id)

    note_edges = {note_id: {} for note_id in notes_by_id}
    for edge in edges:
        source_ids = concept_note_ids.get(int(edge["source_concept_id"]), set())
        target_ids = concept_note_ids.get(int(edge["target_concept_id"]), set())
        relation = RELATION_TYPES.get(edge["relation_type"], edge["relation_type"])
        for source_note_id in source_ids:
            for target_note_id in target_ids - {source_note_id}:
                note_edges[source_note_id][(target_note_id, int(edge["id"]))] = {
                    "target_id": target_note_id,
                    "relation": relation,
                    "description": edge["relation_description"],
                    "rationale": edge["rationale"],
                    "status": _relationship_status(edge),
                }

    written_notes = set()
    for note_id, item in notes_by_id.items():
        metadata = item["metadata"]
        exported_metadata = {
            key: metadata[key]
            for key in ("title", "course", "topic", "tags", "difficulty", "source_file",
                        "source_pages", "related_topics")
            if key in metadata
        }
        frontmatter = yaml.safe_dump(exported_metadata, allow_unicode=True, sort_keys=False)
        lines = ["---", frontmatter.rstrip(), "---", "", item["body"], "", "## Bestätigte Konzepte", ""]
        linked_concepts = []
        for concept_id in sorted(note_concepts[note_id]):
            concept = concepts_by_id[concept_id]
            linked_concepts.append(f"- [[concept-{concept_id}|{_safe_text(concept['name'])}]]")
        lines.extend(linked_concepts or ["Noch keine bestätigten Konzepte."])
        lines.extend(["", "## Beziehungen", ""])
        relation_lines = []
        for relation in note_edges[note_id].values():
            target_link = _linked_note(relation["target_id"], notes_by_id)
            if target_link:
                details = _relationship_details(relation["description"], relation["rationale"])
                relation_lines.append(f"- {relation['relation']}: {target_link}{details}"
                                      f" · Status: {relation['status']}")
        lines.extend(relation_lines or ["Keine bestätigten Beziehungen zu anderen Notes."])
        (notes_folder / f"note-{note_id}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        written_notes.add(f"note-{note_id}.md")

    for path in notes_folder.glob("note-[0-9]*.md"):
        if re.fullmatch(r"note-\d+\.md", path.name) and path.name not in written_notes:
            path.unlink()

    written_concepts = set()
    for concept in concepts:
        concept_id = int(concept["id"])
        path = root / f"concept-{concept_id}.md"
        lines = [f"# {_safe_text(concept['name'])}", "", _safe_text(concept["description"]),
                 "", "## Beziehungen", ""]
        relationship_lines = []
        for edge in edges:
            if int(edge["source_concept_id"]) == concept_id:
                target = concepts_by_id[int(edge["target_concept_id"])]
                relation = RELATION_TYPES.get(edge["relation_type"], edge["relation_type"])
                relationship_lines.append(
                    f"- {relation}: [[concept-{target['id']}|{_safe_text(target['name'])}]]"
                    f"{_relationship_details(edge['relation_description'], edge['rationale'])}"
                    f" · Status: {_relationship_status(edge)}"
                )
            elif int(edge["target_concept_id"]) == concept_id:
                source = concepts_by_id[int(edge["source_concept_id"])]
                relation = RELATION_TYPES.get(edge["relation_type"], edge["relation_type"])
                relationship_lines.append(
                    f"- Eingehend ({relation}): [[concept-{source['id']}|{_safe_text(source['name'])}]]"
                    f"{_relationship_details(edge['relation_description'], edge['rationale'])}"
                    f" · Status: {_relationship_status(edge)}"
                )
        lines.extend(relationship_lines or ["Noch keine bestätigten Beziehungen."])
        lines.extend(["", "## Belegte Knowledge Notes", ""])
        source_links = []
        for note_id in sorted(concept_note_ids.get(concept_id, set())):
            link = _linked_note(note_id, notes_by_id)
            if link:
                metadata = notes_by_id[note_id]["metadata"]
                pages = ", ".join(str(page) for page in metadata["source_pages"])
                source_links.append(
                    f"- {link} · {_safe_text(metadata['source_file'])}, S. {pages}"
                )
        lines.extend(source_links or ["Keine Knowledge Note zugeordnet."])
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        written_concepts.add(path.name)

    for path in root.glob("concept-[0-9]*.md"):
        if re.fullmatch(r"concept-\d+\.md", path.name) and path.name not in written_concepts:
            path.unlink()

    index_lines = [
        f"# Second Brain: {_safe_text(course_label)}", "",
        f"{len(notes_by_id)} Knowledge Notes · {len(concepts)} Konzepte · "
        f"{len(edges)} Beziehungen.", "",
        "Öffne den Graph View in Obsidian, um Notes, Konzepte und gekennzeichnete Verbindungen zu erkunden.",
        "", "## Knowledge Notes", "",
    ]
    index_lines.extend(
        f"- `notes/note-{note_id}.md` — {_safe_text(item['metadata']['title'])}"
        for note_id, item in notes_by_id.items()
    )
    index_lines.extend(["", "## Bestätigte Konzepte", ""])
    index_lines.extend(
        f"- `concept-{item['id']}.md` — {_safe_text(item['name'])}" for item in concepts
    )
    (root / "Index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    report = validate_obsidian_export(root, expected_relationships=len(edges))
    (root / "Export validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return root
