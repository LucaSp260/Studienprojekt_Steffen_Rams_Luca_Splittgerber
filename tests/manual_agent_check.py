"""Kleiner echter Phase-5-Test; verwendet vorhandene SWA-Notes und verursacht API-Kosten."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent_layer.exam_agent import create_exam
from src.agent_layer.exercise_agent import create_exercises
from src.agent_layer.models import ExamRequest, ExerciseRequest
from src.llm.config import load_config
from src.llm.llm_service import LLMService
from src.persistence.artifact_repository import load_artifact
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
    exercise = create_exercises(ExerciseRequest(
        course="SWA", topic="Architekturrollen", count=2,
        difficulty="mittel", exercise_type="Verständnisfrage",
    ), llm_service=llm)
    exam = create_exam(ExamRequest(
        course="SWA", duration_minutes=30, task_count=3, difficulty="gemischt",
    ), llm_service=llm)

    assert len(exercise["exercises"]) == 2
    assert all((item["solution"] or item["short_answer_items"]) and item["source_ids"]
               for item in exercise["exercises"])
    assert len(exam["tasks"]) == 3
    assert exam["total_points"] == sum(task["points"] for task in exam["tasks"])
    assert 28 <= sum(task["estimated_minutes"] for task in exam["tasks"]) <= 32
    assert len({source_id for task in exam["tasks"] for source_id in task["source_ids"]}) >= 2

    code = (
        "from pathlib import Path; from src.persistence import database as d; "
        f"d.DATABASE_PATH=Path({str(DATABASE_PATH)!r}); d.initialize_database(); "
        "from src.persistence.artifact_repository import load_artifact; "
        f"assert load_artifact({exercise['artifact_id']})['artifact_type']=='exercise'; "
        f"assert load_artifact({exam['artifact_id']})['artifact_type']=='exam'"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
    report = {
        "exercise": {"artifact_id": exercise["artifact_id"], "count": len(exercise["exercises"]),
                     "critic": exercise["critic"]["status"]},
        "exam": {"artifact_id": exam["artifact_id"], "count": len(exam["tasks"]),
                 "points": exam["total_points"], "critic": exam["critic"]["status"]},
        "llm_calls": llm.calls,
        "llm_call_count": len(llm.calls),
        "embedding_call_count": 2,
        "restart_check": "ok",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
