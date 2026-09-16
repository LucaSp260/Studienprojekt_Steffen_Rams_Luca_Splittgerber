"""Phase 5 vollständig mit Mock-Retrieval und Mock-LLM, ohne API-Kosten."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from src.agent_layer.common import difficulty_distribution, estimate_task_minutes, time_target_range
from src.agent_layer.exam_agent import create_exam
from src.agent_layer.exercise_agent import create_exercises
from src.agent_layer.models import AgentError, ExamRequest, ExamTask, ExerciseRequest
from src.llm.base_provider import LLMError
from src.persistence import database as db
from src.persistence.artifact_repository import load_artifact, load_artifacts, save_artifact

ROOT = Path(__file__).resolve().parents[1]


def source(note_id, title, filename, pages):
    return {
        "knowledge_note_id": note_id,
        "title": title,
        "topic": title,
        "source_file": filename,
        "source_pages": pages,
        "content": f"# {title}\nFachlich belegter Inhalt zu {title}.",
    }


SOURCES = [
    source(1, "Architekturrollen", "intro.pdf", [12, 13, 14, 15]),
    source(2, "Business Capabilities", "eam.pdf", [19, 20, 21]),
    source(3, "Architekturebenen", "eam.pdf", [6, 26]),
]


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, prompt, response_model):
        self.calls.append((prompt, response_model))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def structured_parts(texts, answers=None, explanations=None, ids=None):
    ids = ids or [chr(ord("a") + index) for index in range(len(texts))]
    answers = answers or [f"Antwort {identifier}" for identifier in ids]
    explanations = explanations or [f"Begründung für die richtige Antwort {identifier}." for identifier in ids]
    return (
        [{"id": identifier, "text": text} for identifier, text in zip(ids, texts)],
        [{"id": identifier, "answer": answer} for identifier, answer in zip(ids, answers)],
        [{"id": identifier, "explanation": explanation}
         for identifier, explanation in zip(ids, explanations)],
    )


def exercise_item(number, difficulty="mittel", source_id="SOURCE_1", solution="Lösung",
                  exercise_type="Verständnisfrage", subtasks=None, task=None,
                  answers=None, explanations=None, ids=None):
    structured_subtasks, answer_items, explanation_items = structured_parts(
        subtasks or [], answers, explanations, ids
    )
    return {
        "number": number,
        "title": f"Aufgabe {number}",
        "task": task or "Erklären Sie die fachlichen Zusammenhänge.",
        "subtasks": structured_subtasks,
        "difficulty": difficulty,
        "exercise_type": exercise_type,
        "choices": ["A", "B", "C"] if exercise_type == "Multiple Choice" else [],
        "solution": "" if subtasks else solution,
        "explanation": "" if subtasks else "Die Lösung folgt aus der angegebenen Knowledge Note.",
        "short_answer_items": answer_items,
        "explanation_items": explanation_items,
        "estimated_minutes": 5,
        "source_ids": [source_id],
    }


def exercise_draft(count=2, source_id="SOURCE_1", solution="Lösung"):
    return {"title": "Übungen zu Architekturrollen",
            "exercises": [exercise_item(i, source_id=source_id, solution=solution)
                          for i in range(1, count + 1)]}


def exam_draft(difficulties=None, one_source=False):
    difficulties = difficulties or ["leicht", "mittel", "mittel", "mittel", "schwer", "schwer"]
    tasks = []
    for number, difficulty in enumerate(difficulties, start=1):
        item = exercise_item(number, difficulty,
                             "SOURCE_1" if one_source else f"SOURCE_{(number - 1) % 3 + 1}",
                             solution=" ".join(["Kompakte Antwort"] * 28))
        item.update({"points": number + 2, "estimated_minutes": 5})
        tasks.append(item)
    return {
        "title": "SWA Probeklausur",
        "course": "SWA",
        "duration_minutes": 30,
        "tasks": tasks,
        "total_points": sum(task["points"] for task in tasks),
    }


def balanced_exam_draft():
    tasks = [
        exercise_item(1, "leicht", "SOURCE_1", solution=" ".join(["Antwort"] * 55),
                      exercise_type="Multiple Choice"),
        exercise_item(2, "mittel", "SOURCE_2", subtasks=[
            "Ordnen Sie zwei Aussagen fachlich zu.",
            "Begründen Sie beide Zuordnungen kurz.",
            "Nennen Sie eine wichtige Abgrenzung.",
        ], answers=[" ".join(["Antwort"] * 20)] * 3,
                      task="Ordnen Sie zu und begründen Sie Ihre Entscheidung."),
        exercise_item(3, "schwer", "SOURCE_3", exercise_type="Anwendungsaufgabe", subtasks=[
            "Analysieren Sie die Ausgangssituation.",
            "Wenden Sie die Konzepte auf den Fall an.",
            "Begründen Sie die gewählte Lösung.",
            "Vergleichen Sie eine Alternative.",
            "Bewerten Sie die Auswirkungen.",
            "Formulieren Sie ein abschließendes Ergebnis.",
        ], answers=[" ".join(["Antwort"] * 19)] * 6,
                      task="Bearbeiten Sie den mehrstufigen Anwendungsfall."),
    ]
    for item, points in zip(tasks, (3, 7, 10)):
        item.update({"points": points, "estimated_minutes": 1})
    return {"title": "SWA Probeklausur", "course": "SWA", "duration_minutes": 30,
            "tasks": tasks, "total_points": 20}


APPROVED_EXERCISE = {"status": "approved", "issues": [], "revised_content": None}
APPROVED_EXAM = {"status": "approved", "issues": [], "revised_content": None}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "data" / "application.db"
        self.override = patch.object(db, "DATABASE_PATH", self.db_path)
        self.override.start()
        db.initialize_database()
        self.retriever = Mock(return_value=SOURCES)

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def test_exercise_count_difficulty_solutions_sources_and_persistence(self):
        llm = FakeLLM(exercise_draft(), APPROVED_EXERCISE)
        request = ExerciseRequest(course="SWA", topic="Architekturrollen", count=2,
                                  difficulty="mittel", exercise_type="Verständnisfrage")
        result = create_exercises(request, retriever=self.retriever, llm_service=llm)
        self.assertEqual(len(result["exercises"]), 2)
        self.assertTrue(all(item["difficulty"] == "mittel" for item in result["exercises"]))
        self.assertTrue(all(item["solution"] for item in result["exercises"]))
        self.assertTrue(all(item["solution"] != item["explanation"] for item in result["exercises"]))
        self.assertTrue(all(item["subtasks"] == [] for item in result["exercises"]))
        self.assertEqual(result["exercises"][0]["source_ids"], ["SOURCE_1"])
        self.assertEqual(result["critic"]["status"], "approved")
        self.assertEqual(load_artifact(result["artifact_id"])["content"]["title"], result["title"])
        self.retriever.assert_called_once_with(
            unittest.mock.ANY, top_k=5, course="SWA"
        )
        self.assertEqual(len(llm.calls), 2)

    def test_exercise_invalid_source_and_empty_retrieval(self):
        request = ExerciseRequest(course="SWA", topic="Rollen", count=2,
                                  difficulty="mittel", exercise_type="Verständnisfrage")
        with self.assertRaisesRegex(AgentError, "ungültige Source-ID"):
            create_exercises(request, retriever=self.retriever,
                             llm_service=FakeLLM(exercise_draft(source_id="SOURCE_99")), persist=False)
        with self.assertRaisesRegex(AgentError, "keine Knowledge Notes"):
            create_exercises(request, retriever=Mock(return_value=[]), llm_service=FakeLLM(), persist=False)

    def test_short_solution_and_explanation_must_be_separate(self):
        request = ExerciseRequest(course="SWA", topic="Rollen", count=2,
                                  difficulty="mittel", exercise_type="Verständnisfrage")
        draft = exercise_draft()
        for item in draft["exercises"]:
            item["explanation"] = item["solution"]
        with self.assertRaisesRegex(AgentError, "getrennte Inhalte"):
            create_exercises(request, retriever=self.retriever,
                             llm_service=FakeLLM(draft, APPROVED_EXERCISE), persist=False)

    def test_structured_answers_keep_letter_and_number_identifiers(self):
        letters = exercise_item(
            1, subtasks=["Breite Regel", "Technische Regel", "High-Level-Beschreibung"],
            answers=["Principle", "Standard", "Conceptual Architecture"],
            explanations=["Breit wiederverwendbar.", "Technisch und spezifisch.",
                          "Projektbezogene Übersicht."],
        ) | {"points": 6}
        letter_task = ExamTask(**letters)
        self.assertEqual([item.id for item in letter_task.subtasks], ["a", "b", "c"])
        self.assertEqual([item.id for item in letter_task.short_answer_items], ["a", "b", "c"])
        self.assertEqual([item.id for item in letter_task.explanation_items], ["a", "b", "c"])
        self.assertEqual([item.answer for item in letter_task.short_answer_items],
                         ["Principle", "Standard", "Conceptual Architecture"])
        self.assertTrue(all("weil" not in item.answer.casefold()
                            for item in letter_task.short_answer_items))

        numbers = exercise_item(2, subtasks=["Erster Schritt", "Zweiter Schritt"], ids=["1", "2"])
        number_task = ExamTask(**(numbers | {"points": 4}))
        self.assertEqual([item.id for item in number_task.subtasks], ["1", "2"])
        self.assertEqual([item.id for item in number_task.short_answer_items], ["1", "2"])
        self.assertEqual([item.id for item in number_task.explanation_items], ["1", "2"])

        mismatch = dict(letters)
        mismatch["short_answer_items"] = [dict(item) for item in letters["short_answer_items"]]
        mismatch["short_answer_items"][0]["id"] = "1"
        with self.assertRaises(ValidationError):
            ExamTask(**mismatch)

    def test_critic_revision_happens_once_and_failure_is_visible(self):
        revised = exercise_draft(solution="Verbesserte Lösung")
        critique = {
            "status": "needs_revision",
            "issues": [{"category": "solution", "message": "Lösung präzisieren", "task_number": 1}],
            "revised_content": revised,
        }
        request = ExerciseRequest(course="SWA", topic="Rollen", count=2,
                                  difficulty="mittel", exercise_type="Verständnisfrage")
        llm = FakeLLM(exercise_draft(), critique)
        result = create_exercises(request, retriever=self.retriever, llm_service=llm, persist=False)
        self.assertEqual(result["critic"]["status"], "needs_revision")
        self.assertEqual(result["exercises"][0]["solution"], "Verbesserte Lösung")
        self.assertEqual(len(llm.calls), 2)

        failing = FakeLLM(exercise_draft(), LLMError("Timeout"))
        result = create_exercises(request, retriever=self.retriever, llm_service=failing, persist=False)
        self.assertEqual(result["critic"]["status"], "failed")
        self.assertIn("ungeprüft", result["critic"]["issues"][0]["message"])
        self.assertEqual(len(failing.calls), 2)

    def test_exam_distribution_points_time_diversity_sources(self):
        self.assertEqual(difficulty_distribution(6, "gemischt"),
                         {"leicht": 1, "mittel": 3, "schwer": 2})
        request = ExamRequest(course="SWA", duration_minutes=30, task_count=6,
                              difficulty="gemischt")
        result = create_exam(request, retriever=self.retriever,
                             llm_service=FakeLLM(exam_draft(), APPROVED_EXAM))
        self.assertEqual(len(result["tasks"]), 6)
        self.assertEqual(result["total_points"], sum(task["points"] for task in result["tasks"]))
        estimated = sum(task["estimated_minutes"] for task in result["tasks"])
        self.assertTrue(time_target_range(30)[0] <= estimated <= time_target_range(30)[1])
        self.assertEqual(result["estimated_total_minutes"], estimated)
        self.assertNotEqual(estimated, result["total_points"])
        self.assertGreaterEqual(len({source_id for task in result["tasks"]
                                    for source_id in task["source_ids"]}), 2)
        self.assertEqual(len(result["sources"]), 3)

    def test_time_heuristic_uses_type_difficulty_scope_and_not_only_points(self):
        short = ExamTask(**(exercise_item(1, "leicht", exercise_type="Multiple Choice") |
                           {"points": 12, "estimated_minutes": 20}))
        long = ExamTask(**(exercise_item(
            2, "schwer", exercise_type="Anwendungsaufgabe",
            task="Analysieren und bewerten Sie den Fall und entwickeln Sie eine begründete Lösung.",
            subtasks=["Analysieren Sie die Situation.", "Wenden Sie das Konzept an.",
                      "Vergleichen Sie zwei Optionen.", "Begründen Sie Ihre Entscheidung."],
        ) | {"points": 8, "estimated_minutes": 2}))
        self.assertLessEqual(estimate_task_minutes(short), 5)
        self.assertGreaterEqual(estimate_task_minutes(long), 12)
        self.assertGreater(estimate_task_minutes(long), estimate_task_minutes(short))

    def test_too_short_exam_is_revised_by_content_instead_of_scaling_minutes(self):
        request = ExamRequest(course="SWA", duration_minutes=30, task_count=3,
                              difficulty="gemischt")
        short = exam_draft(["leicht", "mittel", "schwer"])
        revised = balanced_exam_draft()
        critique = {
            "status": "needs_revision",
            "issues": [{"category": "duration", "message": "Umfang erhöhen", "task_number": 3}],
            "revised_content": revised,
        }
        llm = FakeLLM(short, critique)
        result = create_exam(request, retriever=self.retriever, llm_service=llm, persist=False)
        total = sum(task["estimated_minutes"] for task in result["tasks"])
        self.assertTrue(28 <= total <= 32)
        self.assertGreaterEqual(len(result["tasks"][2]["subtasks"]), 3)
        self.assertLessEqual(result["tasks"][0]["estimated_minutes"], 5)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("aktuelle lokale Schätzung", llm.calls[1][0])

    def test_too_long_exam_is_shortened_once(self):
        request = ExamRequest(course="SWA", duration_minutes=30, task_count=3,
                              difficulty="gemischt")
        too_long = balanced_exam_draft()
        for item in too_long["tasks"]:
            item["exercise_type"] = "Anwendungsaufgabe"
            parts = structured_parts([
                f"Teilfrage {number}: analysieren und begründen." for number in range(1, 7)
            ])
            item["subtasks"], item["short_answer_items"], item["explanation_items"] = parts
            item["solution"] = item["explanation"] = ""
        critique = {
            "status": "needs_revision",
            "issues": [{"category": "duration", "message": "Umfang kürzen", "task_number": None}],
            "revised_content": balanced_exam_draft(),
        }
        llm = FakeLLM(too_long, critique)
        result = create_exam(request, retriever=self.retriever, llm_service=llm, persist=False)
        self.assertTrue(28 <= result["estimated_total_minutes"] <= 32)
        self.assertTrue(all(len(task["subtasks"]) <= 6 for task in result["tasks"]))
        self.assertEqual(len(llm.calls), 2)

    def test_exam_rejects_missing_diversity_and_bad_total(self):
        request = ExamRequest(course="SWA", duration_minutes=30, task_count=6,
                              difficulty="gemischt")
        with self.assertRaisesRegex(AgentError, "nicht mehrere Themen"):
            create_exam(request, retriever=self.retriever,
                        llm_service=FakeLLM(exam_draft(one_source=True)), persist=False)
        invalid = exam_draft()
        invalid["total_points"] += 1
        with self.assertRaisesRegex(AgentError, "ungültige strukturierte Daten"):
            create_exam(request, retriever=self.retriever, llm_service=FakeLLM(invalid), persist=False)

    def test_artifact_repository_both_types_and_process_restart(self):
        exercise_id = save_artifact("exercise", "Übung", "SWA", {"count": 1}, {"value": "äöü"})
        exam_id = save_artifact("exam", "Klausur", "SWA", {"minutes": 30}, {"points": 20})
        self.assertEqual(len(load_artifacts()), 2)
        self.assertEqual(load_artifact(exercise_id)["content"]["value"], "äöü")
        code = (
            "from pathlib import Path; from src.persistence import database as d; "
            f"d.DATABASE_PATH=Path({str(self.db_path)!r}); d.initialize_database(); "
            "from src.persistence.artifact_repository import load_artifact; "
            f"assert load_artifact({exam_id})['content']['points']==20"
        )
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)

    def test_requests_reject_impossible_or_empty_values(self):
        with self.assertRaises(ValidationError):
            ExamRequest(course="SWA", duration_minutes=10, task_count=11, difficulty="mittel")
        with self.assertRaises(ValidationError):
            ExerciseRequest(course="SWA", topic="", count=1, difficulty="leicht",
                            exercise_type="Offene Frage")


class AgentUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.override = patch.object(db, "DATABASE_PATH", Path(self.temp.name) / "data" / "application.db")
        self.override.start()

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def test_app_start_and_history_do_not_call_agents(self):
        with patch("src.ui.learning.create_exercises") as exercise, \
                patch("src.ui.learning.create_exam") as exam:
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            exercise.assert_not_called()
            exam.assert_not_called()

    def test_old_artifact_and_new_subtasks_render_without_api_calls(self):
        db.initialize_database()
        citation = {"source_id": "SOURCE_1", "knowledge_note_id": 1, "title": "Rollen",
                    "source_file": "intro.pdf", "source_pages": [12, 13]}
        base_item = exercise_item(1)
        base_item.pop("short_answer_items")
        base_item.pop("explanation_items")
        base = {"title": "Alte Übung", "course": "SWA", "configuration": {},
                "exercises": [base_item], "sources": [citation],
                "critic": {"status": "approved", "issues": []}}
        old_id = save_artifact("exercise", "Alte Übung", "SWA", {}, base)
        new_item = exercise_item(
            1, subtasks=["Erste Zuordnung", "Zweite Zuordnung"],
            answers=["Principle", "Standard"],
            explanations=["Breite Regel für mehrere Projekte.", "Technische, spezifische Regel."],
        )
        modern = {**base, "title": "Neue gegliederte Übung", "exercises": [new_item]}
        new_id = save_artifact("exercise", modern["title"], "SWA", {}, modern)

        with patch("src.ui.learning.create_exercises") as exercise, \
                patch("src.ui.learning.create_exam") as exam:
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            app.selectbox[0].set_value("Meine Inhalte").run()
            app.button(key=f"open_exercise_{old_id}").click().run()
            self.assertEqual(len(app.exception), 0)
            labels = [item.label for item in app.expander]
            self.assertIn("Kurzlösung zu Aufgabe 1 anzeigen", labels)
            self.assertIn("Erklärung zu Aufgabe 1 anzeigen", labels)
            app.button(key=f"open_exercise_{new_id}").click().run()
            self.assertEqual(len(app.exception), 0)
            markdown = [item.value for item in app.markdown]
            self.assertTrue(any("Erste Zuordnung" in value for value in markdown))
            self.assertTrue(any("**a)** Principle" in value for value in markdown))
            self.assertTrue(any("**b)** Standard" in value for value in markdown))
            self.assertTrue(any(value == "**a)**" for value in markdown))
            self.assertTrue(any("Breite Regel für mehrere Projekte" in item.value for item in app.markdown))
            exercise.assert_not_called()
            exam.assert_not_called()
            app.selectbox[0].set_value("Meine Inhalte").run()
            self.assertEqual(len(app.exception), 0)
            exercise.assert_not_called()
            exam.assert_not_called()


if __name__ == "__main__":
    unittest.main()
