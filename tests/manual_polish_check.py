"""Genau eine echte 30-Minuten-Klausur für den Phase-5-Polishing-Test."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent_layer.exam_agent import create_exam
from src.agent_layer.models import ExamRequest
from src.llm.config import load_config
from src.llm.llm_service import LLMService
from src.persistence.database import DATABASE_PATH, initialize_database


class CountingLLM:
    def __init__(self):
        self.service = LLMService()
        self.calls = []

    def generate(self, prompt, response_model):
        self.calls.append(response_model.__name__)
        return self.service.generate(prompt, response_model)


def main():
    if not load_config().api_key:
        print("ÜBERSPRUNGEN: Kein API-Key konfiguriert.")
        return
    initialize_database()
    llm = CountingLLM()
    exam = create_exam(ExamRequest(
        course="SWA", duration_minutes=30, task_count=3, difficulty="gemischt"
    ), llm_service=llm)
    tasks = exam["tasks"]
    times = [task["estimated_minutes"] for task in tasks]
    assert 28 <= sum(times) <= 32
    assert min(times) <= 6 and max(times) >= 10 and max(times) > min(times)
    assert any(task["subtasks"] for task in tasks)
    assert all((task["solution"] and task["explanation"]) or
               (task["short_answer_items"] and task["explanation_items"]) for task in tasks)
    assert all(task["source_ids"] for task in tasks)
    assert exam["critic"]["status"] in {"approved", "needs_revision"}

    artifact_id = exam["artifact_id"]
    code = (
        "from pathlib import Path; from streamlit.testing.v1 import AppTest; "
        "from src.persistence import database as d; "
        f"d.DATABASE_PATH=Path({str(DATABASE_PATH)!r}); d.initialize_database(); "
        "from src.persistence.artifact_repository import load_artifact; "
        f"a=load_artifact({artifact_id}); assert a['artifact_type']=='exam'; "
        f"app=AppTest.from_file({str(ROOT / 'app.py')!r}).run(timeout=30); "
        "app.selectbox[0].set_value('Meine Inhalte').run(timeout=30); "
        f"assert any(b.key=='open_exam_{artifact_id}' for b in app.button); assert not app.exception"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
    report = {
        "artifact_id": artifact_id,
        "critic": exam["critic"]["status"],
        "estimated_total_minutes": sum(times),
        "llm_calls": llm.calls,
        "embedding_calls": 1,
        "tasks": [{
            "number": task["number"], "title": task["title"],
            "type": task["exercise_type"], "difficulty": task["difficulty"],
            "minutes": task["estimated_minutes"], "points": task["points"],
            "subtasks": len(task["subtasks"]), "source_ids": task["source_ids"],
            "short_solution_words": len((task["solution"] or " ".join(
                item["answer"] for item in task["short_answer_items"])).split()),
            "explanation_words": len((task["explanation"] or " ".join(
                item["explanation"] for item in task["explanation_items"])).split()),
        } for task in tasks],
        "restart_check": "ok",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
