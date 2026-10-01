"""Obsidian-Export nutzt nur Notes und Beziehungen aus der lokalen Kuratierung."""

import hashlib
import json
import re
import textwrap
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import yaml
from streamlit.testing.v1 import AppTest

from src.agent_layer.connection_agent import ConnectionDraft, ConnectionProposal, propose_connections
from src.agent_layer.connection_agent import ConceptProposal
from src.knowledge_layer import markdown_store
from src.knowledge_layer.models import KnowledgeNote
from src.knowledge_layer.obsidian_export import export_course_to_obsidian, validate_obsidian_export
from src.persistence import database as db
from src.persistence.brain_repository import (
    accept_proposal,
    accept_proposals,
    add_manual_concept,
    add_manual_edge,
    load_graph,
    load_proposals,
    reject_proposal,
    save_proposals,
)
from src.persistence.knowledge_repository import load_notes, register_note
from src.ui.brain_graph import build_graph_html, focus_subgraph


def graph_edges(page):
    match = re.search(r"edges = new vis\.DataSet\((\[.*?\])\);", page, re.DOTALL)
    if not match:
        raise AssertionError("PyVis-Kanten fehlen im Graph-HTML")
    return json.loads(match.group(1))


def note_data(title, definition, pages):
    return KnowledgeNote(
        title=title,
        topic="Unternehmensarchitektur",
        tags=["EAM", "Architektur"],
        difficulty="medium",
        source_pages=pages,
        related_topics=["Architekturrollen"],
        definition=definition,
        key_concepts=["Strategie", "Architektur"],
        example="Ein kursbezogenes Beispiel.",
        common_mistakes=["Begriffe nicht gleichsetzen."],
        exam_relevance=["Begriffe erklären und abgrenzen."],
        course="SWA",
        source_file="SWA.pdf",
    )


class ObsidianExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_override = patch.object(db, "DATABASE_PATH", self.root / "data" / "application.db")
        self.kb_override = patch.object(markdown_store, "KNOWLEDGE_BASE_PATH", self.root / "knowledge_base")
        self.db_override.start()
        self.kb_override.start()
        db.initialize_database()
        self.pdf_path = self.root / "user_data" / "SWA" / "SWA.pdf"
        self.pdf_path.parent.mkdir(parents=True)
        self.pdf_path.write_bytes(b"existing course PDF; exporter must not modify it")
        with closing(db.get_connection()) as connection, connection:
            self.document_id = connection.execute(
                """INSERT INTO documents (filename,file_hash,course,file_path,processed)
                   VALUES (?,?,?,?,1)""",
                ("SWA.pdf", "test-hash", "SWA", str(self.pdf_path)),
            ).lastrowid
        self.note_ids = [self.add_note(1), self.add_note(2)]

    def tearDown(self):
        self.kb_override.stop()
        self.db_override.stop()
        self.temp.cleanup()

    def add_note(self, number):
        note = note_data(
            f"Architekturthema {number}",
            f"Dies ist der fachliche Inhalt der Knowledge Note {number}.",
            [number + 10, number + 11],
        )
        path = markdown_store.save_note(note)
        relative = path.relative_to(self.root).as_posix()
        with closing(db.get_connection()) as connection, connection:
            return register_note(connection, self.document_id, note, relative)

    def test_export_links_confirmed_edges_and_excludes_unreviewed_proposals(self):
        source_id = add_manual_concept("SWA", "Business Capabilities", "Was eine Organisation leisten kann.", [self.note_ids[0]])
        target_id = add_manual_concept("SWA", "Geschäftsprozesse", "Wie Aktivitäten ausgeführt werden.", [self.note_ids[1]])

        accepted = ConnectionProposal(
            source_concept="Business Capabilities",
            target_concept="Geschäftsprozesse",
            relation_type="explains",
            rationale="Die Notes ordnen Fähigkeiten und ihre Umsetzung ein.",
            note_ids=self.note_ids,
        )
        rejected = ConnectionProposal(
            source_concept="Rejected Konzept",
            target_concept="Offenes Ziel",
            relation_type="related_to",
            rationale="Abgelehnter Vorschlag.",
            note_ids=self.note_ids,
        )
        pending = ConnectionProposal(
            source_concept="Pending Konzept",
            target_concept="Pending Ziel",
            relation_type="related_to",
            rationale="Noch nicht geprüfter Vorschlag.",
            note_ids=self.note_ids,
        )
        self.assertEqual(save_proposals("SWA", ConnectionDraft(connections=[accepted, rejected, pending])), 3)
        proposals = load_proposals("SWA", "pending")
        by_rationale = {item["payload"]["rationale"]: item for item in proposals}
        accept_proposal(by_rationale[accepted.rationale]["id"], accepted.model_dump())
        reject_proposal(by_rationale[rejected.rationale]["id"])

        # The confirmed edge is persisted in SQLite and survives a fresh repository read.
        concepts, edges = load_graph("SWA")
        self.assertEqual(len(edges), 1)
        self.assertEqual({item["id"] for item in concepts}, {source_id, target_id})

        original_paths = [self.root / row["markdown_path"] for row in load_notes(course="SWA")]
        before_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in original_paths}
        pdf_hash = hashlib.sha256(self.pdf_path.read_bytes()).hexdigest()

        export_root = export_course_to_obsidian("SWA")
        report = validate_obsidian_export(export_root, expected_relationships=1)
        self.assertEqual(report["exported_notes"], 2)
        self.assertEqual(report["exported_concepts"], 2)
        self.assertEqual(report["confirmed_relationships"], 1)
        self.assertGreater(report["wikilinks"], 0)
        self.assertEqual(report["resolvable_wikilinks"], report["wikilinks"])
        self.assertGreater(report["connected_notes_and_concepts"], 0)
        self.assertEqual(report["isolated_notes_and_concepts"], 0)
        exported_notes = sorted((export_root / "notes").glob("note-*.md"))
        self.assertEqual(len(exported_notes), 2)
        note_text = {path.stem: path.read_text(encoding="utf-8") for path in exported_notes}
        self.assertIn("[[notes/note-2|Architekturthema 2]]", note_text["note-1"])
        self.assertIn("[[notes/note-1|Architekturthema 1]]", note_text["note-2"])
        self.assertIn("erklärt", note_text["note-1"])
        self.assertIn("SWA.pdf", note_text["note-1"])
        self.assertIn("source_pages:\n- 11\n- 12", note_text["note-1"])
        self.assertIn("tags:\n- eam\n- architektur", note_text["note-1"])
        self.assertNotIn("Rejected Konzept", "\n".join(note_text.values()))
        self.assertNotIn("Pending Konzept", "\n".join(note_text.values()))

        exported_files = list(export_root.rglob("*.md"))
        all_export_text = "\n".join(path.read_text(encoding="utf-8") for path in exported_files)
        for target in re.findall(r"\[\[([^]|#]+)(?:\|[^]]*)?\]\]", all_export_text):
            linked_path = export_root / f"{target}.md"
            self.assertTrue(linked_path.is_file(), f"Obsidian-Link hat kein Ziel: {target}")

        # Re-export is stable and does not duplicate generated nodes or touch sources.
        first_export = {path.relative_to(export_root): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in exported_files}
        export_course_to_obsidian("SWA")
        second_files = list(export_root.rglob("*.md"))
        second_export = {path.relative_to(export_root): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in second_files}
        self.assertEqual(first_export, second_export)
        self.assertEqual(len(second_files), 5)  # two Knowledge Notes, two concepts and Index
        self.assertEqual(before_hashes, {path: hashlib.sha256(path.read_bytes()).hexdigest()
                                         for path in original_paths})
        self.assertEqual(pdf_hash, hashlib.sha256(self.pdf_path.read_bytes()).hexdigest())

    def test_empty_graph_exports_notes_without_fabricating_relationships(self):
        export_root = export_course_to_obsidian("SWA")
        note_files = sorted((export_root / "notes").glob("note-*.md"))
        self.assertEqual(len(note_files), 2)
        self.assertEqual(load_graph("SWA")[1], [])
        self.assertIn("0 Beziehungen", (export_root / "Index.md").read_text(encoding="utf-8"))
        self.assertTrue(all("Keine bestätigten Beziehungen" in path.read_text(encoding="utf-8")
                            for path in note_files))

    def test_validator_rejects_confirmed_relationships_without_wikilinks(self):
        with self.assertRaisesRegex(ValueError, "keine Wikilinks"):
            validate_obsidian_export(self.root, expected_relationships=1)

    def test_accept_proposals_is_atomic_for_a_mixed_batch(self):
        concept = ConceptProposal(name="Business Capabilities", description="Fähigkeiten",
                                  note_ids=[self.note_ids[0]])
        connection = ConnectionProposal(
            source_concept="Business Capabilities", target_concept="Geschäftsprozesse",
            relation_type="explains", rationale="Die Notes belegen den Zusammenhang.",
            note_ids=self.note_ids,
        )
        save_proposals("SWA", ConnectionDraft(concepts=[concept], connections=[connection]))
        pending = load_proposals("SWA", "pending")
        by_type = {item["proposal_type"]: item for item in pending}
        batch = [(item["id"], item["payload"]) for item in pending]

        with self.assertRaises(ValueError):
            accept_proposals(batch + [(999999, {})])
        self.assertEqual(load_graph("SWA"), ([], []))
        self.assertEqual(len(load_proposals("SWA", "pending")), 2)

        accept_proposals([(by_type[item["proposal_type"]]["id"], item["payload"])
                          for item in pending])
        concepts, edges = load_graph("SWA")
        self.assertEqual(len(concepts), 2)
        self.assertEqual(len(edges), 1)
        self.assertEqual(load_proposals("SWA", "pending"), [])

    def test_precise_relationship_persists_in_graph_and_obsidian(self):
        source_id = add_manual_concept("SWA", "Business Capabilities", "Fähigkeiten",
                                       [self.note_ids[0]])
        target_id = add_manual_concept("SWA", "Strategie", "Ausrichtung",
                                       [self.note_ids[1]])
        description = "Business Capabilities unterstützen die strategische Ausrichtung durch stabile Fähigkeiten."
        edge_id = add_manual_edge("SWA", source_id, target_id, "supports",
                                  "Beide Notes erläutern Fähigkeiten und Strategie.", self.note_ids,
                                  relation_description=description)
        concepts, edges = load_graph("SWA")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["id"], edge_id)
        self.assertEqual(edges[0]["relation_type"], "supports")
        self.assertEqual(edges[0]["relation_description"], description)
        self.assertEqual(set(int(value) for value in edges[0]["note_ids"].split(",")), set(self.note_ids))

        graph_html = build_graph_html(concepts, edges, load_notes(course="SWA"), include_notes=True)
        rendered_edges = graph_edges(graph_html)
        relationship = next(item for item in rendered_edges if item["id"] == f"relationship-{edge_id}")
        self.assertEqual(relationship["label"], "unterstützt")
        self.assertEqual({item["label"] for item in rendered_edges if item["id"].startswith("evidence-")},
                         {"belegt"})
        self.assertIn("zoomView", graph_html)
        self.assertIn("dragNodes", graph_html)
        self.assertIn("Knowledge Note", graph_html)
        self.assertIn("Business Capabilities", graph_html)
        self.assertIn(description, graph_html)
        self.assertIn("Begründung", graph_html)
        self.assertIn("Belege:", relationship["title"])
        self.assertIn("Status: Nutzerbestätigt", relationship["title"])
        self.assertIn("Herkunft: Manuell", relationship["title"])

        hidden_edges = graph_edges(build_graph_html(concepts, edges, load_notes(course="SWA"),
                                                    show_edge_labels=False, include_notes=True))
        self.assertTrue(all(item["label"] == "" for item in hidden_edges))
        self.assertIn("Begründung", hidden_edges[-1]["title"])

        export_root = export_course_to_obsidian("SWA")
        report = validate_obsidian_export(export_root, expected_relationships=1)
        self.assertEqual(report["unresolvable_wikilinks"], 0)
        concept_text = (export_root / f"concept-{source_id}.md").read_text(encoding="utf-8")
        self.assertIn("unterstützt", concept_text)
        self.assertIn(description, concept_text)
        self.assertIn("Begründung:", concept_text)
        before = {path.relative_to(export_root): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in export_root.rglob("*.md")}
        export_course_to_obsidian("SWA")
        after = {path.relative_to(export_root): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in export_root.rglob("*.md")}
        self.assertEqual(before, after)

    def test_legacy_connection_without_description_remains_usable(self):
        old = ConnectionProposal(source_concept="A", target_concept="B", relation_type="related_to",
                                 rationale="Der bisherige Beleg nennt A und B gemeinsam.", note_ids=self.note_ids)
        save_proposals("SWA", ConnectionDraft(connections=[old]))
        proposal = load_proposals("SWA", "pending")[0]
        accept_proposal(proposal["id"], proposal["payload"])
        concepts, edges = load_graph("SWA")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["relation_type"], "related_to")
        self.assertEqual(edges[0]["relation_description"], old.rationale)
        legacy = graph_edges(build_graph_html(concepts, edges, load_notes(course="SWA")))[0]
        self.assertEqual(legacy["label"], textwrap.shorten(old.rationale, width=34, placeholder="…"))
        self.assertIn("Kategorie:", legacy["title"])
        self.assertIn("Begründung:", legacy["title"])
        self.assertIn("Belege:", legacy["title"])

        without_description = dict(edges[0])
        without_description["relation_description"] = ""
        fallback = graph_edges(build_graph_html(concepts, [without_description],
                                               load_notes(course="SWA")))[0]
        self.assertEqual(fallback["label"], "related_to")
        self.assertEqual(load_graph("SWA")[1][0]["relation_description"], old.rationale)

    def test_long_legacy_description_is_shortened_only_in_visible_label(self):
        first = add_manual_concept("SWA", "A", "", [self.note_ids[0]])
        second = add_manual_concept("SWA", "B", "", [self.note_ids[1]])
        description = "A und B stehen in einer sehr ausführlich beschriebenen Beziehung mit mehreren fachlichen Details."
        add_manual_edge("SWA", first, second, "related_to", "Begründung", self.note_ids,
                        relation_description=description)
        concepts, edges = load_graph("SWA")
        rendered = graph_edges(build_graph_html(concepts, edges, load_notes(course="SWA")))[0]
        self.assertLessEqual(len(rendered["label"]), 34)
        self.assertTrue(rendered["label"].endswith("…"))
        self.assertIn(description, rendered["title"])
        self.assertEqual(edges[0]["relation_type"], "related_to")
        self.assertEqual(edges[0]["relation_description"], description)

    def test_focus_graph_contains_one_or_two_hops_and_centers_selected_concept(self):
        first = add_manual_concept("SWA", "A", "", [self.note_ids[0]])
        second = add_manual_concept("SWA", "B", "", [self.note_ids[0]])
        third = add_manual_concept("SWA", "C", "", [self.note_ids[1]])
        add_manual_edge("SWA", first, second, "supports", "A unterstützt B.", self.note_ids)
        add_manual_edge("SWA", second, third, "part_of", "B ist Teil von C.", self.note_ids)
        concepts, edges = load_graph("SWA")
        one_concepts, one_edges = focus_subgraph(concepts, edges, first, 1)
        two_concepts, two_edges = focus_subgraph(concepts, edges, first, 2)
        self.assertEqual({item["name"] for item in one_concepts}, {"A", "B"})
        self.assertEqual(len(one_edges), 1)
        self.assertEqual({item["name"] for item in two_concepts}, {"A", "B", "C"})
        self.assertEqual(len(two_edges), 2)
        page = build_graph_html(one_concepts, one_edges, load_notes(course="SWA"),
                                focus_concept_id=first)
        self.assertEqual(graph_edges(page)[0]["label"], "unterstützt")
        self.assertIn(f'network.focus(focusNode', page)
        self.assertIn(f'const focusNode = "concept-{first}"', page)

    def test_atlas_labels_default_to_focus_and_full_graph_toggle(self):
        first = add_manual_concept("SWA", "A", "", [self.note_ids[0]])
        second = add_manual_concept("SWA", "B", "", [self.note_ids[1]])
        third = add_manual_concept("SWA", "C", "", [self.note_ids[1]])
        add_manual_edge("SWA", first, second, "supports", "Begründung", self.note_ids,
                        relation_description="A unterstützt B.")
        add_manual_edge("SWA", second, third, "part_of", "Begründung", self.note_ids,
                        relation_description="B ist Teil von C.")
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with patch("src.ui.brain.build_graph_html", wraps=build_graph_html) as render:
            app = AppTest.from_file(str(app_path)).run(timeout=30)
            app.radio[0].set_value("Second Brain").run(timeout=30)
            self.assertEqual(app.exception, [])
            self.assertFalse(render.call_args.kwargs["show_edge_labels"])
            labels_toggle = next(item for item in app.toggle if item.label == "Kantenbeschriftungen anzeigen")
            labels_toggle.set_value(True).run(timeout=30)
            self.assertEqual(app.exception, [])
            self.assertTrue(render.call_args.kwargs["show_edge_labels"])
            view = next(item for item in app.radio if item.label == "Ansicht")
            view.set_value("Fokusansicht").run(timeout=30)
            self.assertEqual(app.exception, [])
            self.assertTrue(render.call_args.kwargs["show_edge_labels"])
            self.assertIsNotNone(render.call_args.kwargs["focus_concept_id"])
            self.assertFalse(any(item.label == "Kantenbeschriftungen anzeigen" for item in app.toggle))
            focus = next(item for item in app.selectbox if item.label == "Graph-Fokus")
            focus.set_value(first).run(timeout=30)
            self.assertEqual(len(render.call_args.args[1]), 1)
            hops = next(item for item in app.radio if item.label == "Nachbarschaft")
            hops.set_value(2).run(timeout=30)
            self.assertEqual(len(render.call_args.args[1]), 2)

    def test_connection_agent_requires_precise_description_without_api(self):
        class FakeService:
            def generate(self, prompt, model):
                self.prompt = prompt
                return ConnectionDraft(connections=[ConnectionProposal(
                    source_concept="A", target_concept="B", relation_type="supports",
                    relation_description="A unterstützt B durch die in der Note beschriebenen Fähigkeiten.",
                    rationale="Die Note beschreibt die Fähigkeiten.", note_ids=[self_note_id],
                )])

        self_note_id = self.note_ids[0]
        service = FakeService()
        result = propose_connections("SWA", llm_service=service, notes=load_notes(course="SWA")[:1])
        self.assertEqual(result.connections[0].relation_type, "supports")
        self.assertIn("relation_description", service.prompt)
        self.assertIn("unterstützt", service.prompt)


class LegacySchemaMigrationTests(unittest.TestCase):
    def test_existing_edge_description_is_copied_without_changing_category_or_rationale(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.db"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    """CREATE TABLE brain_edges (id INTEGER PRIMARY KEY, source_concept_id INTEGER,
                       target_concept_id INTEGER, relation_type TEXT, rationale TEXT, origin TEXT)"""
                )
                connection.execute(
                    "INSERT INTO brain_edges VALUES (1, 1, 2, 'related_to', 'Alter Belegtext', 'agent')"
                )
            with patch.object(db, "DATABASE_PATH", path):
                db.initialize_database()
                with closing(db.get_connection()) as connection:
                    row = connection.execute(
                        "SELECT relation_type, relation_description, rationale FROM brain_edges WHERE id=1"
                    ).fetchone()
                self.assertEqual(tuple(row), ("related_to", "Alter Belegtext", "Alter Belegtext"))
                db.initialize_database()
                with closing(db.get_connection()) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM brain_edges").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
