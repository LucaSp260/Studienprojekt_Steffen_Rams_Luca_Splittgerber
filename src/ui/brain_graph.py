"""Interaktiver, rein lesender Wissensatlas für die Streamlit-Oberfläche."""

import html
import json
import textwrap

from pyvis.network import Network

from src.agent_layer.connection_agent import RELATION_TYPES


def _label(value, width=22):
    return textwrap.fill(str(value), width=width, max_lines=2, placeholder="…")


def _note_ids(value):
    return {int(part) for part in (value or "").split(",") if part.isdigit()}


def _safe(value):
    return html.escape(str(value or ""), quote=True)


def _edge_label(edge):
    if edge["relation_type"] == "related_to":
        description = " ".join((edge["relation_description"] or "").split())
        return textwrap.shorten(description, width=34, placeholder="…") if description else "related_to"
    return RELATION_TYPES.get(edge["relation_type"], edge["relation_type"])


def focus_subgraph(concepts, edges, focus_id, hops=1):
    """Zeigt nur Kanten, die in höchstens ein/zwei Schritten erreichbar sind."""
    if hops not in (1, 2):
        raise ValueError("Die Fokusansicht unterstützt ein oder zwei Hops.")
    visible_ids = {focus_id}
    visible_edges = []
    for _ in range(hops):
        frontier = visible_ids.copy()
        for edge in edges:
            source, target = int(edge["source_concept_id"]), int(edge["target_concept_id"])
            if source in frontier or target in frontier:
                if edge not in visible_edges:
                    visible_edges.append(edge)
                visible_ids.update((source, target))
    return ([concept for concept in concepts if int(concept["id"]) in visible_ids], visible_edges)


def build_graph_html(concepts, edges, notes, *, include_notes=False, show_edge_labels=True,
                     focus_concept_id=None):
    """Render persisted concepts, relationships and optional note evidence with PyVis."""
    network = Network(height="710px", width="100%", directed=True, cdn_resources="in_line")
    network.set_options(json.dumps({
        "layout": {"improvedLayout": True, "randomSeed": 42},
        "interaction": {"hover": True, "tooltipDelay": 120, "dragNodes": True,
                        "dragView": True, "zoomView": True, "navigationButtons": True,
                        "hideEdgesOnDrag": True},
        "physics": {"enabled": True, "solver": "barnesHut",
                    "barnesHut": {"gravitationalConstant": -7800, "centralGravity": 0.08,
                                  "springLength": 240, "springConstant": 0.02,
                                  "damping": 0.55, "avoidOverlap": 0.65},
                    "stabilization": {"enabled": True, "iterations": 400, "fit": True}},
        "nodes": {"margin": 12, "font": {"face": "Arial", "size": 17}},
        "edges": {"smooth": {"enabled": True, "type": "continuous", "roundness": 0.15},
                  "font": {"size": 13,
                  "color": "#172033", "background": "#f8fafc", "strokeWidth": 2,
                  "strokeColor": "#f8fafc", "align": "middle"}},
    }))
    notes_by_id = {int(note["id"]): note for note in notes}
    concept_names = {int(concept["id"]): concept["name"] for concept in concepts}
    connected = {concept_id: [] for concept_id in concept_names}
    note_concepts = {note_id: [] for note_id in notes_by_id}
    for concept in concepts:
        for note_id in _note_ids(concept["note_ids"]):
            if note_id in note_concepts:
                note_concepts[note_id].append(concept["name"])
    for edge in edges:
        source, target = int(edge["source_concept_id"]), int(edge["target_concept_id"])
        category = RELATION_TYPES.get(edge["relation_type"], edge["relation_type"])
        connected[source].append(f"{category} → {concept_names.get(target, target)}")
        connected[target].append(f"{concept_names.get(source, source)} → {category}")

    node_details, edge_details = {}, {}
    for concept in concepts:
        concept_id = int(concept["id"])
        source_notes = [notes_by_id[note_id]["title"] for note_id in sorted(_note_ids(concept["note_ids"]))
                        if note_id in notes_by_id]
        details = (
            f"<b>{_safe(concept['name'])}</b><br>Typ: Konzept"
            f"<br>{_safe(concept['description'])}"
            f"<br><b>Beleg-Notes:</b> {_safe('; '.join(source_notes) or 'Keine')}"
            f"<br><b>Verbundene Konzepte:</b> {_safe('; '.join(connected[concept_id]) or 'Keine')}"
        )
        node_details[f"concept-{concept_id}"] = details
        focused = concept_id == focus_concept_id
        network.add_node(f"concept-{concept_id}", label=_label(concept["name"]),
                         title=details, shape="box", color="#93c5fd" if focused else "#bfdbfe",
                         borderWidth=4 if focused else 2, size=27)

    if include_notes:
        for note_id, note in notes_by_id.items():
            source = note["source_file"] if "source_file" in note.keys() else ""
            pages = ", ".join(str(page) for page in json.loads(note["source_pages"] or "[]"))
            details = (f"<b>{_safe(note['title'])}</b><br>Typ: Knowledge Note"
                       f"<br>Quelle: {_safe(source)}, Seiten {_safe(pages)}"
                       f"<br>Verbundene Konzepte: {_safe('; '.join(note_concepts[note_id]) or 'Keine')}")
            node_details[f"note-{note_id}"] = details
            network.add_node(f"note-{note_id}", label=_label(note["title"], width=18),
                             title=details, shape="dot", color="#f9bd7e", size=14)
        for concept in concepts:
            for note_id in sorted(_note_ids(concept["note_ids"])):
                if note_id in notes_by_id:
                    evidence_id = f"evidence-{note_id}-{concept['id']}"
                    edge_details[evidence_id] = (
                        f"<b>{_safe(notes_by_id[note_id]['title'])} → {_safe(concept['name'])}</b>"
                        "<br>Typ: Beleg einer Knowledge Note für ein Konzept"
                    )
                    network.add_edge(f"note-{note_id}", f"concept-{concept['id']}",
                                     id=evidence_id,
                                     title="Knowledge Note belegt Konzept", arrows="",
                                     label="belegt" if show_edge_labels else "",
                                     color="#c3cad4", dashes=True, width=1)

    for edge in edges:
        category = RELATION_TYPES.get(edge["relation_type"], edge["relation_type"])
        description = edge["relation_description"] or edge["rationale"]
        evidence = [notes_by_id[note_id]["title"] for note_id in sorted(_note_ids(edge["note_ids"]))
                    if note_id in notes_by_id]
        details = (f"<b>{_safe(edge['source_name'])} → {_safe(edge['target_name'])}</b>"
                   f"<br>Kategorie: {_safe(category)}"
                   f"<br>Beschreibung: {_safe(description)}"
                   f"<br>Begründung: {_safe(edge['rationale'])}"
                   f"<br>Belege: {_safe('; '.join(evidence) or 'Keine')}"
                   f"<br>Status: {_safe(_status(edge))}"
                   f"<br>Herkunft: {_safe(_origin(edge))}")
        edge_id = f"relationship-{edge['id']}"
        edge_details[edge_id] = details
        network.add_edge(f"concept-{edge['source_concept_id']}",
                         f"concept-{edge['target_concept_id']}", id=edge_id,
                         title=details, arrows="to", color="#475569", width=2,
                         label=_edge_label(edge) if show_edge_labels else "")

    page = network.generate_html()
    details_data = json.dumps({"nodes": node_details, "edges": edge_details}, ensure_ascii=False).replace("<", "\\u003c")
    panel = (
        '<div id="brain-selection" style="font:14px Arial,sans-serif; padding:12px 16px; '
        'border:1px solid #cbd5e1; border-radius:8px; min-height:95px; overflow:auto;">'
        'Klicke auf einen Knoten oder eine Verbindung, um Details zu sehen.</div>'
        '<script>const brainDetails = ' + details_data + ';'
        'network.on("click", function(params) {'
        'const key = params.nodes.length ? params.nodes[0] : params.edges[0];'
        'const details = params.nodes.length ? brainDetails.nodes[key] : brainDetails.edges[key];'
        'document.getElementById("brain-selection").innerHTML = details || '
        '"Klicke auf einen Knoten oder eine Verbindung, um Details zu sehen.";'
        '});</script>'
    )
    focus = json.dumps(f"concept-{focus_concept_id}" if focus_concept_id is not None else None)
    settle = (
        '<script>network.once("stabilized", function() {'
        'network.setOptions({physics: false});'
        'network.fit({animation: false});'
        f'const focusNode = {focus};'
        'if (focusNode) {network.focus(focusNode, {scale: Math.min(network.getScale(), 1), '
        'animation: false});}'
        '});</script>'
    )
    return page.replace("</body>", panel + settle + "</body>")


def _status(edge):
    status = edge["review_status"] if "review_status" in edge.keys() else None
    return "KI-generiert" if status == "ai_generated" else "Nutzerbestätigt"


def _origin(edge):
    if "generation_source" in edge.keys() and edge["generation_source"]:
        return edge["generation_source"]
    return "Manuell" if edge["origin"] == "manual" else "Agentenvorschlag (übernommen)"
