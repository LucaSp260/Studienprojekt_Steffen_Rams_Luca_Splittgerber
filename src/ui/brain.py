"""Second Brain: belegte KI-Vorschläge kuratieren und als Wissensatlas ansehen."""

import json
import sqlite3

import streamlit as st

from src.agent_layer.connection_agent import (
    AGENT_RELATION_TYPES, RELATION_TYPES, ConceptProposal, ConnectionProposal, propose_connections,
)
from src.knowledge_layer.obsidian_export import export_course_to_obsidian, validate_obsidian_export
from src.llm.base_provider import LLMError
from src.persistence.brain_repository import (
    accept_proposal, accept_proposals, add_manual_concept, add_manual_edge, delete_concept, delete_edge,
    load_graph, load_proposals, merge_concepts, reject_proposal, save_concept, save_edge,
    save_proposals, review_stats,
)
from src.persistence.database import get_connection
from src.persistence.knowledge_repository import load_notes
from src.ui.brain_graph import build_graph_html, focus_subgraph


def _courses(notes):
    values = {row["course"] for row in notes}
    connection = get_connection()
    try:
        values.update(row[0] or None for row in connection.execute("SELECT DISTINCT course FROM brain_concepts"))
        values.update(row[0] or None for row in connection.execute("SELECT DISTINCT course FROM brain_proposals"))
    finally:
        connection.close()
    return sorted(values, key=lambda value: (value is not None, (value or "").casefold()))


def _note_labels(notes):
    labels = {}
    for row in notes:
        pages = json.loads(row["source_pages"])
        page_text = ", ".join(str(value) for value in pages)
        labels[int(row["id"])] = f"#{row['id']} · {row['title']} · {row['course'] or 'Ohne Kurs'} · S. {page_text}"
    return labels


def show_brain_page():
    st.subheader("Second Brain")
    st.caption("Ein Wissensatlas aus belegten Konzepten und Beziehungen. KI-generierte Kanten sind als solche gekennzeichnet.")
    notes = load_notes()
    courses = _courses(notes)
    if not courses:
        st.info("Lade zuerst Unterlagen hoch und verarbeite sie zu Knowledge Notes.")
        return
    course = st.selectbox("Kurs", courses, format_func=lambda value: value or "Ohne Kurs", key="brain_course")
    course_key = course or ""
    course_notes = [row for row in notes if row["course"] == course]
    note_labels = _note_labels(course_notes)

    with st.expander("Wie entstehen Verbindungen?", expanded=False):
        st.write("Nach der Verarbeitung eines neuen Dokuments ergänzt der Connection Agent den Atlas automatisch aus den neuen Notes und wenigen semantisch relevanten alten Notes. Diese Beziehungen erscheinen sofort mit Status KI-generiert. Das kann API-Kosten verursachen.")
        st.write("Der optionale Button darunter startet die bisherige Analyse aller Kurs-Notes als prüfbare Vorschläge. Solche Vorschläge werden erst nach eurer Übernahme Teil des Atlas; Original und Entscheidung bleiben für die Auswertung gespeichert.")
        st.write("Der bestätigte Atlas wird als Obsidian-kompatible Markdown-Ansicht geschrieben. Die App bleibt die Quelle für Prüfung und Bearbeitung; Obsidian zeigt den jeweils exportierten Stand.")

    if st.button("Verbindungen mit dem Agenten vorschlagen", type="primary", key="brain_propose"):
        try:
            with st.spinner("Knowledge Notes werden nach belegten Konzepten und Beziehungen durchsucht …"):
                proposals = propose_connections(course or "Ohne Kurs", notes=course_notes)
                created = save_proposals(course_key, proposals)
            st.success(f"{created} neue Vorschläge gespeichert. Bestehende und manuelle Einträge bleiben erhalten.")
            st.rerun()
        except (LLMError, ValueError) as error:
            st.error(str(error))
        except (OSError, sqlite3.Error):
            st.error("Die Vorschläge konnten nicht gespeichert werden. Bitte Datenbank und Schreibrechte prüfen.")

    pending = load_proposals(course_key, "pending")
    stats = {row["review_outcome"]: row["amount"] for row in review_stats(course_key)}
    if stats:
        st.caption(f"Bisher geprüft: {stats.get('accepted', 0)} unverändert übernommen · "
                   f"{stats.get('edited', 0)} angepasst · {stats.get('rejected', 0)} abgelehnt. "
                   "Das sind Prüfergebnisse, keine automatische Wahrheitsquote.")
    if pending:
        st.markdown(f"#### Vorschläge prüfen · {len(pending)} offen")
        st.caption("Ändere Felder direkt vor der Übernahme. Die editierte Fassung wird gespeichert und im Atlas verwendet.")
        if st.button("Alle übernehmen", key="accept_all_brain_proposals"):
            try:
                batch = []
                for proposal in pending:
                    model = ConceptProposal if proposal["proposal_type"] == "concept" else ConnectionProposal
                    validated = model.model_validate(proposal["payload"])
                    if not set(validated.note_ids).issubset(note_labels):
                        raise ValueError("Mindestens ein Beleg gehört nicht mehr zum gewählten Kurs.")
                    batch.append((proposal["id"], validated.model_dump()))
                accept_proposals(batch)
                export_course_to_obsidian(course_key)
                st.rerun()
            except (ValueError, sqlite3.Error, OSError) as error:
                st.error(str(error) or "Die Vorschläge konnten nicht übernommen werden.")
        for proposal in pending:
            payload = proposal["payload"]
            title = (payload.get("name") if proposal["proposal_type"] == "concept"
                     else f"{payload.get('source_concept')} → {payload.get('target_concept')}")
            with st.expander(f"{proposal['proposal_type']}: {title} · Vorschlag #{proposal['id']}"):
                with st.form(f"review_brain_{proposal['id']}"):
                    if proposal["proposal_type"] == "concept":
                        name = st.text_input("Konzept", value=payload.get("name", ""), key=f"pn_{proposal['id']}")
                        description = st.text_area("Kurzbeschreibung", value=payload.get("description", ""), key=f"pd_{proposal['id']}")
                        chosen_notes = st.multiselect("Belege (Knowledge Notes)", list(note_labels),
                            default=[value for value in payload.get("note_ids", []) if value in note_labels],
                            format_func=lambda value: note_labels[value], key=f"ps_{proposal['id']}")
                        accepted = st.form_submit_button("Übernehmen")
                        rejected = st.form_submit_button("Ablehnen")
                        revised = {"name": name.strip(), "description": description.strip(), "note_ids": chosen_notes}
                    else:
                        source_name = st.text_input("Ausgehendes Konzept", value=payload.get("source_concept", ""), key=f"psrc_{proposal['id']}")
                        target_name = st.text_input("Zielkonzept", value=payload.get("target_concept", ""), key=f"ptgt_{proposal['id']}")
                        rel_options = list(AGENT_RELATION_TYPES)
                        if payload.get("relation_type") not in rel_options:
                            rel_options.append(payload.get("relation_type"))
                        current_relation = payload.get("relation_type") if payload.get("relation_type") in rel_options else rel_options[0]
                        relation = st.selectbox("Beziehung", rel_options, index=rel_options.index(current_relation),
                            format_func=RELATION_TYPES.get, key=f"pr_{proposal['id']}")
                        relation_description = st.text_area("Präzise Beziehungsbeschreibung",
                            value=payload.get("relation_description") or payload.get("rationale", ""),
                            key=f"pdesc_{proposal['id']}")
                        rationale = st.text_area("Begründung", value=payload.get("rationale", ""), key=f"pra_{proposal['id']}")
                        chosen_notes = st.multiselect("Belege (Knowledge Notes)", list(note_labels),
                            default=[value for value in payload.get("note_ids", []) if value in note_labels],
                            format_func=lambda value: note_labels[value], key=f"pe_{proposal['id']}")
                        accepted = st.form_submit_button("Übernehmen")
                        rejected = st.form_submit_button("Ablehnen")
                        revised = {"source_concept": source_name.strip(), "target_concept": target_name.strip(),
                                   "relation_type": relation, "relation_description": relation_description.strip(),
                                   "rationale": rationale.strip(), "note_ids": chosen_notes}
                    if accepted:
                        try:
                            model = ConceptProposal if proposal["proposal_type"] == "concept" else ConnectionProposal
                            validated = model.model_validate(revised)
                            if not set(validated.note_ids).issubset(note_labels):
                                raise ValueError("Mindestens ein ausgewählter Beleg gehört nicht mehr zum Kurs.")
                            accept_proposal(proposal["id"], validated.model_dump())
                            export_course_to_obsidian(course_key)
                            st.rerun()
                        except (ValueError, sqlite3.Error, OSError) as error:
                            st.error(str(error) or "Der Vorschlag konnte nicht übernommen werden.")
                    if rejected:
                        reject_proposal(proposal["id"])
                        st.rerun()

    reviewed_items = load_proposals(course_key, "accepted") + load_proposals(course_key, "rejected")
    if reviewed_items:
        with st.expander("Prüfverlauf der KI-Vorschläge"):
            reviewed_items = sorted(reviewed_items, key=lambda value: value["id"], reverse=True)[:30]
            selected_review = st.selectbox("Entscheidung anzeigen", range(len(reviewed_items)),
                format_func=lambda index: (f"#{reviewed_items[index]['id']} · {reviewed_items[index]['proposal_type']} · "
                                           f"{reviewed_items[index].get('review_outcome') or reviewed_items[index]['status']}"))
            item = reviewed_items[selected_review]
            st.markdown("**Originalvorschlag**")
            st.json(item["original_payload"])
            if item["reviewed_payload"] is not None:
                st.markdown("**Menschliche Entscheidung**")
                st.json(item["reviewed_payload"])

    concepts, edges = load_graph(course_key)
    st.markdown(f"#### Wissensatlas · {len(concepts)} Konzepte · {len(edges)} Verbindungen")
    if concepts:
        st.caption("Die Gesamtansicht zeigt die Struktur des Second Brains. Wähle ein Konzept aus, um seine direkten Beziehungen detailliert zu betrachten.")
        concept_names = {int(item["id"]): item["name"] for item in concepts}
        degrees = {concept_id: 0 for concept_id in concept_names}
        for edge in edges:
            degrees[int(edge["source_concept_id"])] += 1
            degrees[int(edge["target_concept_id"])] += 1
        concept_ids = sorted(concept_names, key=lambda concept_id: concept_names[concept_id].casefold())
        default_focus = max(concept_ids, key=lambda concept_id: degrees[concept_id])
        view = st.radio("Ansicht", ["Gesamtansicht", "Fokusansicht"], horizontal=True,
                        key="brain_graph_view")
        if view == "Gesamtansicht":
            focus_id = None
            visible_concepts, visible_edges = concepts, edges
            show_edge_labels = st.toggle("Kantenbeschriftungen anzeigen", value=False,
                                          key="brain_show_edge_labels")
        else:
            st.caption("Konzept suchen oder auswählen:")
            focus_id = st.selectbox("Graph-Fokus", concept_ids,
                index=concept_ids.index(default_focus),
                format_func=lambda concept_id: concept_names[concept_id], key="brain_graph_focus")
            hops = st.radio("Nachbarschaft", [1, 2], horizontal=True,
                            format_func=lambda value: f"{value} Hop" if value == 1 else "2 Hops",
                            key="brain_graph_hops")
            visible_concepts, visible_edges = focus_subgraph(concepts, edges, focus_id, hops)
            show_edge_labels = True
        include_notes = st.toggle("Beleg-Notes im Graphen anzeigen", value=False, key="brain_show_notes")
        visible_note_ids = {int(value) for concept in visible_concepts
                            for value in (concept["note_ids"] or "").split(",") if value.isdigit()}
        visible_notes = [note for note in course_notes if int(note["id"]) in visible_note_ids]
        st.caption("Blau: Konzepte · Orange: Knowledge Notes. Zoome mit dem Mausrad, verschiebe den Graphen oder einzelne Knoten und klicke für Details.")
        st.iframe(build_graph_html(visible_concepts, visible_edges, visible_notes,
                                   include_notes=include_notes,
                                   show_edge_labels=show_edge_labels,
                                   focus_concept_id=focus_id), height=970)
        with st.expander("Belege und Details im Atlas"):
            for concept in concepts:
                ids = [int(value) for value in (concept["note_ids"] or "").split(",") if value.isdigit()]
                sources = "; ".join(note_labels[value] for value in ids if value in note_labels) or "Keine Quelle verknüpft"
                st.markdown(f"**{concept['name']}** · {concept['origin']}  \n{concept['description'] or 'Keine Kurzbeschreibung'}  \nQuellen: {sources}")
            for edge in edges:
                ids = [int(value) for value in (edge["note_ids"] or "").split(",") if value.isdigit()]
                sources = "; ".join(note_labels[value] for value in ids if value in note_labels) or "Keine Quelle verknüpft"
                status = "KI-generiert" if edge["review_status"] == "ai_generated" else "Nutzerbestätigt"
                st.markdown(f"{edge['source_name']} — *{RELATION_TYPES.get(edge['relation_type'], edge['relation_type'])}* → {edge['target_name']}  \n{edge['relation_description']}  \nBegründung: {edge['rationale']}  \nBelege: {sources}  \nStatus: {status}")
    else:
        st.info("Noch keine Konzepte im Atlas. Verarbeite ein neues Dokument oder ergänze Konzepte manuell.")

    st.markdown("#### Wissen selbst ergänzen")
    with st.expander("Konzept manuell anlegen"):
        with st.form("manual_brain_concept"):
            manual_name = st.text_input("Konzeptname")
            manual_description = st.text_area("Kurzbeschreibung")
            manual_sources = st.multiselect("Belege (optional)", list(note_labels),
                format_func=lambda value: note_labels[value], key="manual_concept_sources")
            add_concept = st.form_submit_button("Konzept anlegen")
        if add_concept:
            try:
                if not manual_name.strip():
                    raise ValueError("Bitte einen Konzeptnamen eingeben.")
                add_manual_concept(course_key, manual_name, manual_description, manual_sources)
                export_course_to_obsidian(course_key)
                st.rerun()
            except (ValueError, sqlite3.Error, OSError) as error:
                st.error(str(error) or "Das Konzept konnte nicht angelegt werden.")

    concepts, edges = load_graph(course_key)
    concept_options = {item["id"]: item["name"] for item in concepts}
    if concepts:
        with st.expander("Verbindung manuell anlegen"):
            with st.form("manual_brain_edge"):
                source_id = st.selectbox("Von", list(concept_options), format_func=lambda value: concept_options[value])
                target_id = st.selectbox("Zu", list(concept_options), format_func=lambda value: concept_options[value], key="manual_edge_target")
                relation = st.selectbox("Beziehungskategorie", list(AGENT_RELATION_TYPES), format_func=RELATION_TYPES.get, key="manual_edge_relation")
                relation_description = st.text_area("Präzise Beziehungsbeschreibung")
                rationale = st.text_input("Begründung")
                evidence = st.multiselect("Belege (optional)", list(note_labels),
                    format_func=lambda value: note_labels[value], key="manual_edge_sources")
                add_edge = st.form_submit_button("Verbindung anlegen")
            if add_edge:
                try:
                    if not relation_description.strip() or not rationale.strip():
                        raise ValueError("Bitte Beschreibung und Begründung für die Beziehung angeben.")
                    add_manual_edge(course_key, source_id, target_id, relation, rationale, evidence,
                                    relation_description=relation_description)
                    export_course_to_obsidian(course_key)
                    st.rerun()
                except (ValueError, sqlite3.Error, OSError) as error:
                    st.error(str(error) or "Die Verbindung konnte nicht angelegt werden.")

        with st.expander("Bestätigte Konzepte bearbeiten oder zusammenführen"):
            for concept in concepts:
                with st.form(f"edit_concept_{concept['id']}"):
                    st.markdown(f"**{concept['name']}** · {concept['origin']}")
                    edited_name = st.text_input("Name", value=concept["name"], key=f"ecn_{concept['id']}")
                    edited_description = st.text_area("Kurzbeschreibung", value=concept["description"], key=f"ecd_{concept['id']}")
                    existing_ids = [int(value) for value in (concept["note_ids"] or "").split(",") if value.isdigit()]
                    evidence = st.multiselect("Quellen", list(note_labels), default=[i for i in existing_ids if i in note_labels],
                        format_func=lambda value: note_labels[value], key=f"ecs_{concept['id']}")
                    save = st.form_submit_button("Änderungen speichern")
                    remove = st.form_submit_button("Konzept löschen")
                if save:
                    try:
                        save_concept(concept["id"], edited_name, edited_description, evidence)
                        export_course_to_obsidian(course_key)
                        st.rerun()
                    except (ValueError, sqlite3.Error, OSError) as error:
                        st.error(str(error) or "Das Konzept konnte nicht gespeichert werden.")
                if remove:
                    delete_concept(concept["id"])
                    export_course_to_obsidian(course_key)
                    st.rerun()
            if len(concepts) > 1:
                with st.form("merge_brain_concepts"):
                    merge_source = st.selectbox("Dieses Konzept zusammenführen", list(concept_options),
                        format_func=lambda value: concept_options[value], key="merge_source")
                    merge_target = st.selectbox("Behalten als", list(concept_options),
                        format_func=lambda value: concept_options[value], key="merge_target")
                    do_merge = st.form_submit_button("Zusammenführen")
                if do_merge:
                    try:
                        merge_concepts(merge_source, merge_target)
                        export_course_to_obsidian(course_key)
                        st.rerun()
                    except (ValueError, sqlite3.Error, OSError) as error:
                        st.error(str(error))

    if edges:
        with st.expander("Verbindungen bearbeiten"):
            for edge in edges:
                with st.form(f"edit_edge_{edge['id']}"):
                    st.markdown(f"**{edge['source_name']} → {edge['target_name']}**")
                    source_id = st.selectbox("Von", list(concept_options), index=list(concept_options).index(edge["source_concept_id"]),
                        format_func=lambda value: concept_options[value], key=f"es_{edge['id']}")
                    target_id = st.selectbox("Zu", list(concept_options), index=list(concept_options).index(edge["target_concept_id"]),
                        format_func=lambda value: concept_options[value], key=f"et_{edge['id']}")
                    rel_options = list(AGENT_RELATION_TYPES)
                    if edge["relation_type"] not in rel_options:
                        rel_options.append(edge["relation_type"])
                    current = edge["relation_type"] if edge["relation_type"] in rel_options else rel_options[0]
                    relation = st.selectbox("Typ", rel_options, index=rel_options.index(current),
                        format_func=RELATION_TYPES.get, key=f"er_{edge['id']}")
                    relation_description = st.text_area("Präzise Beziehungsbeschreibung",
                        value=edge["relation_description"], key=f"edesc_{edge['id']}")
                    rationale = st.text_input("Begründung", value=edge["rationale"], key=f"era_{edge['id']}")
                    source_ids = [int(value) for value in (edge["note_ids"] or "").split(",") if value.isdigit()]
                    evidence = st.multiselect("Belege", list(note_labels), default=[i for i in source_ids if i in note_labels],
                        format_func=lambda value: note_labels[value], key=f"ev_{edge['id']}")
                    save = st.form_submit_button("Verbindung speichern")
                    remove = st.form_submit_button("Verbindung löschen")
                if save:
                    try:
                        if not relation_description.strip():
                            raise ValueError("Bitte die fachliche Beziehungsbeschreibung angeben.")
                        save_edge(edge["id"], source_id, target_id, relation, rationale, evidence,
                                  relation_description=relation_description)
                        export_course_to_obsidian(course_key)
                        st.rerun()
                    except (ValueError, sqlite3.Error, OSError) as error:
                        st.error(str(error) or "Die Verbindung konnte nicht gespeichert werden.")
                if remove:
                    delete_edge(edge["id"])
                    export_course_to_obsidian(course_key)
                    st.rerun()

    with st.expander("In Obsidian öffnen"):
        st.write("Exportiert die Knowledge Notes und die gekennzeichneten Beziehungen dieses Kurses. Öffne den Exportordner in Obsidian über **Vault öffnen → Ordner als Vault öffnen**.")
        if st.button("Obsidian-Export aktualisieren", key="export_brain_obsidian"):
            try:
                export_path = export_course_to_obsidian(course_key)
                report = validate_obsidian_export(export_path, expected_relationships=len(edges))
                st.success("Export aktualisiert. Der Ordner kann jetzt als Obsidian-Vault geöffnet werden.")
                st.code(str(export_path), language=None)
                st.write(
                    f"Validator: {report['exported_notes']} Notes · {report['exported_concepts']} Konzepte · "
                    f"{report['confirmed_relationships']} Beziehungen · "
                    f"{report['confirmed_relationship_wikilinks']} Wikilinks im Beziehungsabschnitt · "
                    f"{report['wikilinks']} Wikilinks insgesamt "
                    f"({report['resolvable_wikilinks']} auflösbar, {report['unresolvable_wikilinks']} fehlerhaft) · "
                    f"{report['connected_notes_and_concepts']} vernetzte · {report['isolated_notes_and_concepts']} isolierte Knoten."
                )
            except (ValueError, sqlite3.Error, OSError) as error:
                st.error(str(error) or "Der Obsidian-Export konnte nicht erstellt werden.")
        st.caption(f"{len(course_notes)} Notes und {len(edges)} Beziehungen werden exportiert. Offene und abgelehnte Vorschläge bleiben außen vor.")
        if not edges:
            st.info("Für einen verbundenen Graphen zuerst im Second Brain Beziehungsvorschläge prüfen und übernehmen oder eine Verbindung manuell anlegen.")
