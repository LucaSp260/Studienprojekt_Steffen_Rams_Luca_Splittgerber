"""Inkrementelles Second Brain mit temporären Daten und ausschließlich Mock-KI."""

import hashlib
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.agent_layer.connection_agent import ConceptProposal, ConnectionDraft, ConnectionProposal
from src.data_layer import document_manager
from src.knowledge_layer import brain_growth, knowledge_manager, markdown_store
from src.knowledge_layer import obsidian_export
from src.knowledge_layer.models import KnowledgeNote
from src.llm.base_provider import LLMError
from src.llm.config import LLMConfig
from src.llm.llm_service import LLMService, ConnectionResult
from src.llm.usage import generate_recorded, record_usage
from src.persistence import database as db
from src.persistence.brain_repository import (add_manual_concept, add_manual_edge, load_graph,
                                               pending_brain_notes, save_incremental_graph)
from src.persistence.knowledge_repository import load_notes, register_note
from src.ui.brain_graph import build_graph_html


def note(title, source="old.pdf"):
    return KnowledgeNote(
        title=title, topic="Architektur", tags=["architektur"], difficulty="medium",
        source_pages=[1], related_topics=[], definition=f"{title} wird fachlich beschrieben.",
        key_concepts=[title], example="", common_mistakes=[],
        exam_relevance=["Zusammenhang erklären."],
        course="SWA", source_file=source,
    )


class FakeService:
    def __init__(self, new_document_id):
        self.new_document_id = new_document_id
        self.extractions = 0
        self.connections = 0
        self.connection_prompt = ""
        self.fail_connection = False

    def generate(self, prompt, model):
        if model.__name__ == "ExtractionResult":
            self.extractions += 1
            return {"notes": [note("Neu A").model_dump(exclude={"course", "source_file"}),
                              note("Neu B").model_dump(exclude={"course", "source_file"})]}
        self.connections += 1
        self.connection_prompt = prompt
        if self.fail_connection:
            raise LLMError("Testfehler")
        ids = [int(row["id"]) for row in load_notes(document_id=self.new_document_id)]
        return ConnectionDraft(
            concepts=[ConceptProposal(name="Neu A", note_ids=[ids[0]]),
                      ConceptProposal(name="Neu B", note_ids=[ids[1]]),
                      ConceptProposal(name="Bestehend", note_ids=[ids[0]])],
            connections=[
                ConnectionProposal(source_concept="Neu A", target_concept="Bestehend",
                                   relation_type="supports", relation_description="Neu A unterstützt Bestehend.",
                                   rationale="Neue Note belegt dies.", note_ids=[ids[0]]),
                ConnectionProposal(source_concept="Neu A", target_concept="Neu B",
                                   relation_type="represented_by", relation_description="Neu A wird durch Neu B dargestellt.",
                                   rationale="Beide neuen Notes belegen dies.", note_ids=ids),
                ConnectionProposal(source_concept="Bestehend", target_concept="Anderes",
                                   relation_type="supports", relation_description="Bestehend unterstützt Anderes.",
                                   rationale="Ein alter Zusammenhang.", note_ids=[ids[0]]),
            ],
        )


class BrainGrowthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(db, "DATABASE_PATH", self.root / "data" / "app.db"),
            patch.object(markdown_store, "KNOWLEDGE_BASE_PATH", self.root / "knowledge_base"),
            patch.object(obsidian_export, "KNOWLEDGE_BASE_PATH", self.root / "knowledge_base"),
            patch.object(knowledge_manager, "PROJECT_PATH", self.root),
            patch.object(document_manager, "USER_DATA_PATH", self.root / "user_data"),
            patch.object(knowledge_manager, "read_pdf", return_value={"pages": ["Architekturtext"],
                                                                     "text": "Architekturtext"}),
            patch.object(knowledge_manager, "index_existing_notes", side_effect=self.mark_indexed),
        ]
        for item in self.patches:
            item.start()
        db.initialize_database()
        self.old_id = self.add_document("old.pdf", b"old")
        self.old_note_ids = [self.add_old_note(self.old_id, f"Alt {index}") for index in range(10)]
        first = add_manual_concept("SWA", "Bestehend", "Bestehende Beschreibung", [self.old_note_ids[0]])
        second = add_manual_concept("SWA", "Anderes", "", [self.old_note_ids[1]])
        self.old_edge_id = add_manual_edge("SWA", first, second, "supports", "Alte Begründung",
                                           self.old_note_ids[:2], relation_description="Alte Beschreibung")
        self.new_id = self.add_document("new.pdf", b"new")
        self.service = FakeService(self.new_id)
        self.search_calls = []
        self.search_patch = patch.object(brain_growth, "retrieve", side_effect=self.search)
        self.search_patch.start()
        self.old_hashes = {row["id"]: hashlib.sha256((self.root / row["markdown_path"]).read_bytes()).hexdigest()
                           for row in load_notes(document_id=self.old_id)}
        self.pdf_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in (self.root / "user_data" / "swa").glob("*.pdf")}

    def tearDown(self):
        self.search_patch.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def add_document(self, name, content):
        path = self.root / "user_data" / "swa" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        with closing(db.get_connection()) as connection, connection:
            return connection.execute(
                "INSERT INTO documents (filename,file_hash,course,file_path) VALUES (?,?,?,?)",
                (name, hashlib.sha256(content).hexdigest(), "SWA", path.relative_to(self.root).as_posix()),
            ).lastrowid

    def add_old_note(self, document_id, title):
        item = note(title)
        path = markdown_store.save_note(item)
        with closing(db.get_connection()) as connection, connection:
            return register_note(connection, document_id, item, path.relative_to(self.root).as_posix())

    def mark_indexed(self, document_id, **kwargs):
        with closing(db.get_connection()) as connection, connection:
            connection.execute("UPDATE documents SET processed=1 WHERE id=?", (document_id,))
        return {"indexed": 2, "skipped": 0, "total": 2}

    def search(self, query, top_k, course):
        self.search_calls.append((query, top_k, course))
        new = load_notes(document_id=self.new_id)
        rows = [{"knowledge_note_id": row["id"], "distance": 0.01} for row in new]
        rows += [{"knowledge_note_id": note_id, "distance": 0.1 + index / 100}
                 for index, note_id in enumerate(self.old_note_ids)]
        return rows[:top_k]

    def test_new_document_grows_graph_once_with_bounded_retrieval(self):
        before = load_graph("SWA")
        result = knowledge_manager.process_document(self.new_id, self.service)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.service.extractions, 1)
        self.assertEqual(self.service.connections, 1)
        self.assertEqual(len(self.search_calls), 2)
        self.assertTrue(all(top_k == 7 and course == "SWA" for _, top_k, course in self.search_calls))
        old_context = json.loads(self.service.connection_prompt.split("Relevante alte Notes: ", 1)[1]
                                 .split("\nVorhandener Graphausschnitt:", 1)[0])
        self.assertEqual(len(old_context), 5)
        concepts, edges = load_graph("SWA")
        self.assertEqual(len(concepts), len(before[0]) + 2)
        self.assertEqual(len(edges), len(before[1]) + 2)
        original = next(item for item in edges if item["id"] == self.old_edge_id)
        self.assertEqual(original["relation_description"], "Alte Beschreibung")
        self.assertEqual(original["review_status"], "user_confirmed")
        self.assertTrue(all(item["review_status"] == "ai_generated" and
                            item["generation_source"] == "connection_agent"
                            for item in edges if item["id"] != self.old_edge_id))
        self.assertTrue(any(item["source_name"] == "Neu A" and item["target_name"] == "Neu B"
                            for item in edges))
        self.assertEqual(len(pending_brain_notes(self.new_id)), 0)
        graph_html = build_graph_html(concepts, edges, load_notes(course="SWA"))
        self.assertIn("Status: KI-generiert", graph_html)
        self.assertIn("Herkunft: connection_agent", graph_html)
        self.assertIn("Status: Nutzerbestätigt", graph_html)
        db.initialize_database()  # App-Neustart: Marken bleiben persistent.
        self.assertEqual(knowledge_manager.process_document(self.new_id, self.service)["status"],
                         "already_processed")
        self.assertEqual(self.service.connections, 1)
        duplicate = document_manager.add_document(
            SimpleNamespace(name="new.pdf", getvalue=lambda: b"new"), "SWA")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(self.service.connections, 1)
        self.assertEqual({row["id"]: hashlib.sha256((self.root / row["markdown_path"]).read_bytes()).hexdigest()
                          for row in load_notes(document_id=self.old_id)}, self.old_hashes)
        self.assertEqual({path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (self.root / "user_data" / "swa").glob("*.pdf")}, self.pdf_hashes)
        export_root = obsidian_export.export_course_to_obsidian("SWA")
        self.assertEqual(obsidian_export.validate_obsidian_export(export_root,
                         expected_relationships=3)["unresolvable_wikilinks"], 0)
        exported = (export_root / "concept-3.md").read_text(encoding="utf-8")
        self.assertIn("Status: KI-generiert", exported)
        self.assertIn("Status: Nutzerbestätigt", (export_root / "concept-1.md").read_text(encoding="utf-8"))

    def test_failed_connection_step_retries_without_extraction_or_duplicates(self):
        self.service.fail_connection = True
        with self.assertRaises(LLMError):
            knowledge_manager.process_document(self.new_id, self.service)
        self.assertEqual(self.service.extractions, 1)
        self.assertEqual(len(load_notes(document_id=self.new_id)), 2)
        self.assertEqual(len(pending_brain_notes(self.new_id)), 2)
        self.service.fail_connection = False
        self.assertEqual(knowledge_manager.process_document(self.new_id, self.service)["status"], "success")
        self.assertEqual(self.service.extractions, 1)
        self.assertEqual(len(load_graph("SWA")[1]), 3)

    def test_first_document_of_course_skips_old_note_retrieval(self):
        with closing(db.get_connection()) as connection, connection:
            connection.execute("UPDATE documents SET course='Fresh' WHERE id=?", (self.new_id,))
        result = knowledge_manager.process_document(self.new_id, self.service)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.search_calls, [])
        self.assertEqual(self.service.connections, 1)

    def test_existing_notes_are_baselined_once_when_processing_table_is_migrated(self):
        with closing(db.get_connection()) as connection, connection:
            connection.execute("DROP TABLE brain_note_processing")
        db.initialize_database()
        with closing(db.get_connection()) as connection:
            statuses = connection.execute("SELECT status FROM brain_note_processing").fetchall()
        self.assertEqual(len(statuses), len(self.old_note_ids))
        self.assertTrue(all(item["status"] == "baseline" for item in statuses))
        self.assertEqual(len(pending_brain_notes(self.old_id)), 0)

    def test_inverse_duplicate_is_not_added_or_reclassified(self):
        existing = load_graph("SWA")[0]
        first = next(item["id"] for item in existing if item["name"] == "Bestehend")
        second = next(item["id"] for item in existing if item["name"] == "Anderes")
        add_manual_edge("SWA", first, second, "part_of", "Bestehende Aussage", self.old_note_ids[:1])
        new_note = self.add_old_note(self.new_id, "Neu")
        draft = ConnectionDraft(connections=[ConnectionProposal(
            source_concept="Anderes", target_concept="Bestehend", relation_type="has_part",
            relation_description="Anderes besteht aus Bestehend.", rationale="Neue Note.",
            note_ids=[new_note],
        )])
        result = save_incremental_graph("SWA", [new_note], draft)
        self.assertEqual(result["edges"], 0)
        self.assertEqual(len(load_graph("SWA")[1]), 2)
        self.assertEqual(len(pending_brain_notes(self.new_id)), 0)

    def test_usage_records_real_values_and_leaves_missing_values_empty(self):
        config = LLMConfig("openai", "test-model", "test-key")
        with patch("src.llm.llm_service.OpenAIProvider") as provider_class:
            provider = provider_class.return_value
            provider.generate.return_value = ConnectionResult(ok=True)
            provider.last_usage = SimpleNamespace(input_tokens=12, output_tokens=7, total_tokens=19)
            service = LLMService(config)
            generate_recorded(service, "prompt", ConnectionResult, "connection_agent",
                              document_id=self.new_id, note_ids=[1, 2])
            provider.last_usage = None
            generate_recorded(service, "prompt", ConnectionResult, "knowledge_extraction")
        record_usage(config, SimpleNamespace(prompt_tokens=8, total_tokens=8),
                     "embedding_documents", document_id=self.new_id)
        with closing(db.get_connection()) as connection:
            rows = connection.execute("SELECT * FROM llm_usage ORDER BY id").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[0]["operation"], rows[0]["provider"], rows[0]["model"]),
                         ("connection_agent", "openai", "test-model"))
        self.assertEqual((rows[0]["input_tokens"], rows[0]["output_tokens"],
                          rows[0]["total_tokens"]), (12, 7, 19))
        self.assertEqual(json.loads(rows[0]["note_ids_json"]), [1, 2])
        self.assertIsNone(rows[1]["total_tokens"])
        self.assertEqual((rows[2]["operation"], rows[2]["input_tokens"], rows[2]["output_tokens"]),
                         ("embedding_documents", 8, None))


if __name__ == "__main__":
    unittest.main()
