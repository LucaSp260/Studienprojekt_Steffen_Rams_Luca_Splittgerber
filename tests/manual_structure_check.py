"""Genau eine echte Zuordnungsaufgabe für den strukturierten Lösungstest."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent_layer.exercise_agent import create_exercises
from src.agent_layer.models import ExerciseRequest
from src.llm.config import load_config
from src.llm.llm_service import LLMService
from src.persistence.database import initialize_database


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
    result = create_exercises(ExerciseRequest(
        course="SWA",
        topic=("Ordnen Sie drei Aussagen den Begriffen Principle, Standard und "
               "Conceptual Architecture zu."),
        count=1,
        difficulty="mittel",
        exercise_type="Verständnisfrage",
    ), llm_service=llm)
    exercise = result["exercises"][0]
    subtask_ids = [item["id"] for item in exercise["subtasks"]]
    answer_ids = [item["id"] for item in exercise["short_answer_items"]]
    explanation_ids = [item["id"] for item in exercise["explanation_items"]]
    answers = [item["answer"].strip() for item in exercise["short_answer_items"]]
    assert subtask_ids == ["a", "b", "c"]
    assert answer_ids == subtask_ids == explanation_ids
    assert [answer.casefold() for answer in answers] == [
        "principle", "standard", "conceptual architecture"
    ]
    assert all(item["explanation"].strip() for item in exercise["explanation_items"])
    assert result["critic"]["status"] in {"approved", "needs_revision"}
    report = {
        "artifact_id": result["artifact_id"],
        "critic": result["critic"]["status"],
        "subtasks": exercise["subtasks"],
        "short_answer_items": exercise["short_answer_items"],
        "explanation_items": exercise["explanation_items"],
        "sources": exercise["source_ids"],
        "llm_calls": llm.calls,
        "embedding_calls": 1,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
