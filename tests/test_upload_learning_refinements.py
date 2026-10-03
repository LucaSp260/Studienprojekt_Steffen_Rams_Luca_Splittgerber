"""Regression coverage for optional topics, upload processing and source selection."""
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from streamlit.testing.v1 import AppTest

from src.agent_layer.exercise_agent import create_exercises
from src.agent_layer.models import ExerciseRequest
from src.data_layer import document_manager
from src.persistence import database as db
from src.persistence.document_repository import load_documents, update_document_status
from src.persistence.artifact_repository import save_artifact
from src.ui.brain import _concept_sources
from test_agents import FakeLLM, exercise_draft, APPROVED_EXERCISE, SOURCES
from test_documents import make_pdf, upload

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("topic", [None, "", "   ", "Architekturrollen"])
def test_optional_exercise_topic_uses_bounded_course_retrieval(topic):
    values = dict(course="SWA", count=2, difficulty="mittel", exercise_type="Verständnisfrage")
    if topic is not None:
        values["topic"] = topic
    request = ExerciseRequest(**values)
    retriever = Mock(return_value=SOURCES)
    llm = FakeLLM(exercise_draft(), APPROVED_EXERCISE)
    result = create_exercises(request, retriever=retriever, llm_service=llm, persist=False)
    assert len(result["exercises"]) == 2
    assert retriever.call_count == 1
    assert retriever.call_args.kwargs == {"course": "SWA", "top_k": 5}
    if not request.topic:
        assert "Zentrale Themen und Grundlagen des Kurses SWA" in retriever.call_args.args[0]
    assert len(llm.calls) == 2  # Existing generator and critic; no topic-selection call.


@pytest.fixture
def upload_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(document_manager, "USER_DATA_PATH", tmp_path / "user_data")
    old = document_manager.add_document(upload(make_pdf("Old SWA"), "old.pdf"), "SWA")
    return old["document_id"]


def test_new_course_upload_processes_only_new_documents_and_skips_duplicates(upload_environment):
    def process(document_id, progress):
        update_document_status(document_id, True)
        progress("Indexiert")
        return {"message": "Verarbeitet"}

    files = [upload(make_pdf("Algorithm A"), "algo.pdf")]
    with patch("src.ui.documents.st.file_uploader", return_value=files), \
         patch("src.ui.documents.process_document", side_effect=process) as processing:
        app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
        app.radio(key="navigation").set_value("Unterlagen").run()
        app.selectbox(key="upload_course").set_value("+ Neuen Kurs anlegen").run()
        app.text_input(key="new_course_name").set_value(" Algo ")
        app.button(key="FormSubmitter:document_upload-Unterlagen hinzufügen").click().run()
        assert not app.exception
        new = load_documents(course="Algo")
        assert len(new) == 1 and new[0]["processed"] == 1
        assert processing.call_args.args == (new[0]["id"],)
        assert load_documents(course="SWA")[0]["id"] == upload_environment
        assert load_documents(course="SWA")[0]["processed"] == 0
        assert {"Algo", "SWA"} <= {item.value for item in app.subheader}
        assert f"process_{new[0]['id']}" not in [item.key for item in app.button]
        app.run()
        app.button(key="FormSubmitter:document_upload-Unterlagen hinzufügen").click().run()
        processing.assert_called_once()
        assert len(load_documents()) == 2


def test_upload_failure_keeps_pdf_allows_resume_and_continues_batch(upload_environment):
    files = [upload(make_pdf("Algorithm A"), "first.pdf"),
             upload(make_pdf("Algorithm B"), "second.pdf")]
    calls = []
    def process(document_id, progress):
        calls.append(document_id)
        if len(calls) == 1:
            raise RuntimeError("Simulierter Providerfehler")
        update_document_status(document_id, True)
        return {"message": "Verarbeitet"}

    with patch("src.ui.documents.st.file_uploader", return_value=files), \
         patch("src.ui.documents.process_document", side_effect=process):
        app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
        app.radio(key="navigation").set_value("Unterlagen").run()
        app.selectbox(key="upload_course").set_value("+ Neuen Kurs anlegen").run()
        app.text_input(key="new_course_name").set_value("Algo")
        app.button(key="FormSubmitter:document_upload-Unterlagen hinzufügen").click().run()
        assert not app.exception
        documents = {item["filename"]: item for item in load_documents("Algo")}
        assert documents["first.pdf"]["processed"] == 0
        assert documents["second.pdf"]["processed"] == 1
        assert len(calls) == 2
        app.run()
        assert len(calls) == 2
        first = documents["first.pdf"]["id"]
        app.button(key=f"process_{first}").click().run()
        assert len(calls) == 3 and calls[-1] == first
        assert all(item["processed"] for item in load_documents("Algo"))


def test_history_headings_and_optional_topic_form(upload_environment):
    exam = save_artifact("exam", "Testklausur", "SWA", {}, {"title": "Testklausur", "course": "SWA", "tasks": []})
    exercise = save_artifact("exercise", "Testaufgaben", "SWA", {}, {"title": "Testaufgaben", "course": "SWA", "exercises": []})
    with patch("src.ui.learning.create_exercises", return_value={"artifact_id": None}) as generate:
        app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
        app.radio(key="navigation").set_value("Meine Inhalte").run()
        assert [item.value for item in app.main.get("subheader")] == ["Übungsklausuren", "Übungsaufgaben"]
        assert app.button(key=f"open_exam_{exam}").label.startswith("Testklausur")
        assert app.button(key=f"open_exercise_{exercise}").label.startswith("Testaufgaben")
        app.radio(key="navigation").set_value("Übung erstellen").run()
        app.selectbox(key="learning_course").set_value("SWA").run()
        assert app.text_input[0].label == "Thema oder Beschreibung (optional)"
        app.button(key="FormSubmitter:exercise_agent-Übungen erstellen").click().run()
        assert not app.exception and not app.error
        assert generate.call_args.args[0].topic == ""


def test_atlas_preview_merges_pages_only_within_selected_concept_and_document():
    concept = {"note_ids": "1,2,3"}
    notes = [
        {"id": 1, "document_id": 10, "source_file": "same.pdf", "source_pages": "[2,3]", "title": "A"},
        {"id": 2, "document_id": 10, "source_file": "same.pdf", "source_pages": "[3,4]", "title": "B"},
        {"id": 3, "document_id": 11, "source_file": "same.pdf", "source_pages": "[5]", "title": "C"},
        {"id": 4, "document_id": 10, "source_file": "same.pdf", "source_pages": "[99]", "title": "Unrelated"},
    ]
    sources = _concept_sources(concept, notes)
    assert len(sources) == 2
    assert sources[0]["source_pages"] == [2, 3, 4]
    assert sources[0]["note_titles"] == ["A", "B"]
    assert sources[1]["source_pages"] == [5]

