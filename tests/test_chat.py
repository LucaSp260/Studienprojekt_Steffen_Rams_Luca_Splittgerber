"""Phase 6: Lernchat vollständig mit Mock-Retrieval und Mock-LLM."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from src.agent_layer.learning_chat import answer_question, limited_history, retrieval_query
from src.agent_layer.models import AgentError
from src.llm.base_provider import LLMError
from src.persistence import database as db

ROOT = Path(__file__).resolve().parents[1]


def note(note_id=1, title="Business Capabilities"):
    return {
        "knowledge_note_id": note_id,
        "title": title,
        "topic": title,
        "source_file": "eam.pdf",
        "source_pages": [19, 20, 21],
        "content": f"# {title}\nBusiness Capabilities beschreiben Fähigkeiten eines Unternehmens.",
    }


class FakeLLM:
    def __init__(self, response=None, error=None):
        self.response = response or {
            "answer": "**Business Capabilities** beschreiben, was ein Unternehmen leisten kann. [SOURCE_1]",
            "source_ids": ["SOURCE_1"],
            "insufficient_information": False,
        }
        self.error = error
        self.calls = []

    def generate(self, prompt, response_model):
        self.calls.append((prompt, response_model))
        if self.error:
            raise self.error
        return self.response


class LearningChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "data" / "application.db"
        self.override = patch.object(db, "DATABASE_PATH", self.db_path)
        self.override.start()
        db.initialize_database()

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def test_grounded_answer_sources_course_and_persistence(self):
        retriever = Mock(return_value=[note()])
        llm = FakeLLM()
        result = answer_question("Was sind Business Capabilities?", "SWA", [],
                                 retriever=retriever, llm_service=llm)
        retriever.assert_called_once_with("Was sind Business Capabilities?", top_k=5, course="SWA")
        self.assertEqual(result["sources"][0]["source_file"], "eam.pdf")
        self.assertEqual(result["sources"][0]["source_pages"], [19, 20, 21])
        self.assertNotIn("SOURCE_", result["answer"])
        self.assertIn("ausschließlich", llm.calls[0][0])
        self.assertIn("Mathematische Formeln müssen als korrektes LaTeX", llm.calls[0][0])

        chat_id = db.create_chat()
        db.save_chat_exchange(chat_id, "Was sind Business Capabilities?", result["answer"], result)
        messages = db.load_messages(chat_id)
        self.assertEqual([row["role"] for row in messages], ["user", "assistant"])
        self.assertEqual(db.message_metadata(messages[1])["sources"][0]["source_file"], "eam.pdf")
        self.assertNotEqual(db.list_chats()[0]["title"], "Neuer Chat")

    def test_follow_up_context_is_limited_and_used_for_retrieval(self):
        history = [{"role": "user" if index % 2 == 0 else "assistant",
                    "content": f"Nachricht {index}"} for index in range(10)]
        limited = limited_history(history)
        self.assertEqual(len(limited), 6)
        self.assertEqual(limited[0]["content"], "Nachricht 4")
        retriever = Mock(return_value=[note()])
        llm = FakeLLM()
        answer_question("Und wofür braucht man sie im EAM?", "SWA", history,
                        retriever=retriever, llm_service=llm)
        query = retriever.call_args.args[0]
        self.assertIn("Und wofür", query)
        self.assertIn("Nachricht 9", query)
        self.assertNotIn("Nachricht 3", query)
        self.assertIn("Nachricht 4", llm.calls[0][0])
        self.assertNotIn("Nachricht 3", llm.calls[0][0])
        standalone = "Ausführliche eigenständige fachliche Frage mit ausreichend vielen klaren Wörtern ohne Verweis"
        self.assertEqual(retrieval_query(standalone, limited), standalone)

    def test_no_results_invalid_source_and_provider_error(self):
        llm = FakeLLM()
        result = answer_question("Unbekannt?", "SWA", [], retriever=Mock(return_value=[]), llm_service=llm)
        self.assertTrue(result["insufficient_information"])
        self.assertEqual(result["sources"], [])
        self.assertEqual(llm.calls, [])

        invalid = FakeLLM({"answer": "Antwort", "source_ids": ["SOURCE_99"],
                           "insufficient_information": False})
        with self.assertRaisesRegex(AgentError, "ungültige Source-ID"):
            answer_question("Frage", None, [], retriever=Mock(return_value=[note()]), llm_service=invalid)
        with self.assertRaises(LLMError):
            answer_question("Frage", None, [], retriever=Mock(return_value=[note()]),
                            llm_service=FakeLLM(error=LLMError("Timeout")))

    def test_restart_loads_exchange_without_api(self):
        chat_id = db.create_chat()
        db.save_chat_exchange(chat_id, "Frage", "Antwort", {"sources": [], "course": "SWA"})
        code = (
            "from pathlib import Path; from src.persistence import database as d; "
            f"d.DATABASE_PATH=Path({str(self.db_path)!r}); d.initialize_database(); "
            f"m=d.load_messages({chat_id}); assert len(m)==2; assert m[1]['content']=='Antwort'"
        )
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)

    def test_navigation_and_old_chat_do_not_call_api(self):
        chat_id = db.create_chat()
        db.save_chat_exchange(chat_id, "Alte Frage", "Alte Antwort", {"sources": []})
        with patch("src.ui.learning.answer_question") as chat_agent:
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("Alte Antwort" in item.value for item in app.markdown))
            for page in ["Meine Inhalte", "Übung erstellen", "Probeklausur erstellen",
                         "Unterlagen", "Generierte Notes", "Einstellungen", "Chat"]:
                app.radio[0].set_value(page).run()
            chat_agent.assert_not_called()

    def test_formula_delimiters_render_as_streamlit_math(self):
        from src.ui.learning import _render_math_markdown
        source = r"Inline \(x^2\), display \[w_\ell=\frac{2\pi}{n}\] and [y_1=\cos(x)]."
        rendered = _render_math_markdown(source)
        self.assertIn("$x^2$", rendered)
        self.assertIn("$$\nw_\\ell=\\frac{2\\pi}{n}\n$$", rendered)
        self.assertIn("$y_1=\\cos(x)$", rendered)


if __name__ == "__main__":
    unittest.main()
