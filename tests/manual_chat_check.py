"""Kleiner echter Phase-6-Chat-Test mit vorhandenen Notes und genau zwei Fragen."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.agent_layer.learning_chat import answer_question
from src.knowledge_layer.retrieval_service import retrieve
from src.llm.config import load_config
from src.llm.llm_service import LLMService
from src.persistence.artifact_repository import load_artifacts
from src.persistence.database import (
    DATABASE_PATH,
    create_chat,
    get_connection,
    initialize_database,
    load_messages,
    message_metadata,
    save_chat_exchange,
)
from src.persistence.knowledge_repository import load_notes

REPORT = ROOT / "tmp" / "phase6_chat_report.json"


class CountingLLM:
    def __init__(self):
        self.service = LLMService()
        self.calls = 0

    def generate(self, prompt, response_model):
        self.calls += 1
        return self.service.generate(prompt, response_model)


class CountingRetriever:
    def __init__(self):
        self.calls = []

    def __call__(self, query, top_k=5, course=None):
        self.calls.append({"query": query, "top_k": top_k, "course": course})
        return retrieve(query, top_k=top_k, course=course)


def compact(result):
    return {
        "answer": result["answer"],
        "sources": [
            {"source_file": item["source_file"], "source_pages": item["source_pages"]}
            for item in result["sources"]
        ],
    }


def main():
    if not load_config().api_key:
        print("ÜBERSPRUNGEN: Kein API-Key konfiguriert.")
        return
    initialize_database()
    llm = CountingLLM()
    retriever = CountingRetriever()
    chat_id = create_chat()
    questions = [
        "Was sind Business Capabilities und warum sind sie für die Unternehmensarchitektur relevant?",
        "Wie unterscheiden sie sich von konkreten Geschäftsprozessen?",
    ]
    results = []
    for question in questions:
        history = load_messages(chat_id)
        result = answer_question(
            question, course="SWA", history=history, retriever=retriever, llm_service=llm
        )
        assert result["answer"] and result["sources"]
        save_chat_exchange(
            chat_id,
            question,
            result["answer"],
            {"sources": result["sources"], "course": "SWA",
             "insufficient_information": result["insufficient_information"]},
        )
        results.append(compact(result))

    assert llm.calls == 2
    assert len(retriever.calls) == 2
    assert retriever.calls[0]["query"] == questions[0]
    assert questions[1] in retriever.calls[1]["query"]
    assert "Vorheriger Kontext:" in retriever.calls[1]["query"]
    stored = load_messages(chat_id)
    assert len(stored) == 4
    assert compact({"answer": stored[1]["content"],
                    "sources": message_metadata(stored[1])["sources"]}) == results[0]
    assert compact({"answer": stored[3]["content"],
                    "sources": message_metadata(stored[3])["sources"]}) == results[1]

    connection = get_connection()
    counts = {
        "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "processed_documents": connection.execute(
            "SELECT COUNT(*) FROM documents WHERE processed = 1"
        ).fetchone()[0],
        "notes": len(load_notes()),
        "exercises": len(load_artifacts("exercise")),
        "exams": len(load_artifacts("exam")),
    }
    connection.close()
    assert counts["documents"] and counts["notes"] and counts["exercises"] and counts["exams"]

    code = (
        "import json; from pathlib import Path; from src.persistence import database as d; "
        f"d.DATABASE_PATH=Path({str(DATABASE_PATH)!r}); d.initialize_database(); "
        "from src.persistence.artifact_repository import load_artifacts; "
        "from src.persistence.knowledge_repository import load_notes; "
        f"m=d.load_messages({chat_id}); assert len(m)==4; "
        "assert d.message_metadata(m[1]).get('sources'); "
        "assert d.message_metadata(m[3]).get('sources'); "
        "assert load_notes(); assert load_artifacts('exercise'); assert load_artifacts('exam')"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
    report = {
        "chat_id": chat_id,
        "course": "SWA",
        "questions": questions,
        "results": results,
        "llm_calls": llm.calls,
        "embedding_query_calls": len(retriever.calls),
        "knowledge_extraction_calls": 0,
        "exercise_or_exam_generation_calls": 0,
        "persisted_counts": counts,
        "restart_check": "ok",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
