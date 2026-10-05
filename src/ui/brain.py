"""Wissensatlas: explore and edit persisted graph entries without model calls."""
import json
import sqlite3
import streamlit as st
from src.agent_layer.connection_agent import RELATION_TYPES
from src.knowledge_layer.obsidian_export import export_course_to_obsidian
from src.persistence.brain_repository import load_graph, save_concept, delete_concept, save_edge, delete_edge
from src.persistence.course_repository import list_courses
from src.persistence.knowledge_repository import load_notes
from src.ui.atlas_component import atlas_graph
from src.ui.brain_graph import build_graph_html, focus_subgraph
from src.ui.concept_details import concept_summary
from src.ui.learning import _render_chat_sources

def _ids(row):
    return [int(value) for value in (row["note_ids"] or "").split(",") if value.isdigit()]

def _concept_sources(concept, notes):
    selected_ids = set(_ids(concept))
    documents = {}
    for note in notes:
        if note["id"] not in selected_ids:
            continue
        source = documents.setdefault(note["document_id"], {
            "knowledge_note_id": note["id"], "source_file": note["source_file"],
            "source_pages": [], "note_titles": [],
        })
        pages = json.loads(note["source_pages"])
        source["source_pages"] = sorted(set(source["source_pages"]) | set(pages))
        source["note_titles"].append(note["title"])
    return list(documents.values())


def show_brain_page():
    courses = list_courses()
    if not courses:
        st.info("Verarbeite eigene Unterlagen, um den Wissensatlas aufzubauen.")
        return
    course = st.selectbox("Kurs", courses, key="brain_course")
    concepts, edges = load_graph(course)
    notes = load_notes(course=course)
    if not concepts:
        st.info("Noch keine Konzepte. Nach der Verarbeitung neuer Dokumente wächst der Atlas automatisch.")
    else:
        st.caption("Die Gesamtansicht zeigt die Struktur des Second Brains. Wähle ein Konzept aus, um seine direkten Beziehungen detailliert zu betrachten.")
        names = {int(item["id"]): item["name"] for item in concepts}
        view = st.radio("Ansicht", ["Gesamtansicht", "Fokusansicht"], horizontal=True, key="brain_graph_view")
        focus_id = None
        if view == "Fokusansicht":
            focus_id = st.selectbox("Graph-Fokus", list(names), format_func=names.get, key="brain_graph_focus")
            hops = st.radio("Nachbarschaft", [1, 2], horizontal=True, key="brain_graph_hops")
            visible, visible_edges = focus_subgraph(concepts, edges, focus_id, hops)
            labels = True
        else:
            visible, visible_edges = concepts, edges
            labels = st.toggle("Kantenbeschriftungen anzeigen", value=False, key="brain_show_edge_labels")
        include_notes = st.toggle("Beleg-Notes im Graphen anzeigen", value=False, key="brain_show_notes")
        displayed = [dict(concept, description=concept_summary(concept, notes)) for concept in visible]
        selected = atlas_graph(build_graph_html(displayed, visible_edges, notes,
            include_notes=include_notes, show_edge_labels=labels, focus_concept_id=focus_id), key=f"atlas_{course}")
        if selected and selected != st.session_state.get("last_atlas_click"):
            st.session_state.last_atlas_click = selected
            if selected.get("kind") == "concept" and selected.get("id") in names:
                st.session_state.brain_selected_concept = selected["id"]
            elif selected.get("kind") == "edge" and selected.get("id") in {e["id"] for e in edges}:
                st.session_state.brain_selected_edge = selected["id"]
        if st.session_state.get("brain_selected_concept") not in names:
            st.session_state.brain_selected_concept = focus_id or next(iter(names))
        concept_id = st.selectbox("Ausgewähltes Konzept", list(names), format_func=names.get, key="brain_selected_concept")
        concept = next(item for item in concepts if item["id"] == concept_id)
        st.subheader(concept["name"])
        st.write(concept_summary(concept, notes))
        linked = [edge for edge in edges if concept_id in (edge["source_concept_id"], edge["target_concept_id"])]
        for edge in linked:
            category = RELATION_TYPES.get(edge["relation_type"], edge["relation_type"])
            st.caption(f"{edge['source_name']} — {category} → {edge['target_name']}")
        _render_chat_sources(_concept_sources(concept, notes), expand_first=True)
        with st.expander("Konzept bearbeiten"):
            with st.form(f"edit_concept_{concept_id}"):
                name = st.text_input("Konzeptname", value=concept["name"])
                description = st.text_area("Kurzbeschreibung", value=concept["description"])
                save = st.form_submit_button("Konzept speichern")
            if save:
                try:
                    save_concept(concept_id, name, description, _ids(concept))
                    st.rerun()
                except (ValueError, sqlite3.Error, OSError) as error:
                    st.error(str(error) or "Das Konzept konnte nicht gespeichert werden.")
        with st.expander("Konzept löschen"):
            confirm = st.checkbox("Konzept und seine Graph-Verbindungen löschen; Notes und PDFs bleiben erhalten.", key=f"confirm_concept_{concept_id}")
            if st.button("Konzept löschen", key=f"delete_concept_{concept_id}", disabled=not confirm):
                delete_concept(concept_id)
                st.session_state.pop("last_atlas_click", None)
                st.rerun()
        if edges:
            st.subheader("Beziehungen")
            edge_options = {e["id"]: f"{e['source_name']} → {e['target_name']}" for e in edges}
            if st.session_state.get("brain_selected_edge") not in edge_options:
                st.session_state.brain_selected_edge = next(iter(edge_options))
            edge_id = st.selectbox("Ausgewählte Beziehung", list(edge_options), format_func=edge_options.get, key="brain_selected_edge")
            edge = next(e for e in edges if e["id"] == edge_id)
            st.caption("Status: " + ("KI-generiert" if edge["review_status"] == "ai_generated" else "Nutzerbestätigt"))
            st.caption("Herkunft: " + (edge["generation_source"] or edge["origin"]))
            st.write(edge["relation_description"])
            st.caption("Begründung: " + edge["rationale"])
            for note in notes:
                if note["id"] in _ids(edge):
                    st.caption(f"Beleg: {note['title']} · {note['source_file']} · Seiten {note['source_pages']}")
            with st.expander("Beziehung bearbeiten"):
                with st.form(f"edit_edge_{edge_id}"):
                    category = st.selectbox("Kategorie", list(RELATION_TYPES), index=list(RELATION_TYPES).index(edge["relation_type"]), format_func=RELATION_TYPES.get)
                    description = st.text_area("Beziehungsbeschreibung", value=edge["relation_description"])
                    save = st.form_submit_button("Beziehung speichern")
                if save:
                    try:
                        if not description.strip():
                            raise ValueError("Bitte eine Beziehungsbeschreibung eingeben.")
                        save_edge(edge_id, edge["source_concept_id"], edge["target_concept_id"], category,
                                  edge["rationale"], _ids(edge), relation_description=description)
                        st.rerun()
                    except (ValueError, sqlite3.Error, OSError) as error:
                        st.error(str(error) or "Die Beziehung konnte nicht gespeichert werden.")
            with st.expander("Beziehung löschen"):
                confirm = st.checkbox("Nur diese Graph-Beziehung löschen.", key=f"confirm_edge_{edge_id}")
                if st.button("Beziehung löschen", disabled=not confirm, key=f"delete_edge_{edge_id}"):
                    delete_edge(edge_id)
                    st.session_state.pop("last_atlas_click", None)
                    st.rerun()
    with st.expander("Optionaler Obsidian-Export"):
        if st.button("Obsidian-Export aktualisieren", key="obsidian_export"):
            try:
                path = export_course_to_obsidian(course)
                st.success("Export wurde aktualisiert.")
                st.code(str(path))
            except (OSError, ValueError, sqlite3.Error):
                st.error("Der Export konnte nicht geschrieben werden.")
