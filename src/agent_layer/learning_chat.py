"""Quellenbasierter Lernchat über RetrievalService und LLMService."""

import json
import re

from pydantic import Field, model_validator

from src.agent_layer.common import MATH_FORMAT_RULES, build_sources, parse_model, sources_for_prompt
from src.agent_layer.models import AgentError, NonEmpty, StrictModel
from src.knowledge_layer.retrieval_service import retrieve
from src.llm.llm_service import LLMService

MAX_HISTORY_MESSAGES = 6


class ChatDraft(StrictModel):
    answer: NonEmpty
    source_ids: list[NonEmpty] = Field(default_factory=list)
    insufficient_information: bool

    @model_validator(mode="after")
    def grounded_answer_has_source(self):
        if not self.insufficient_information and not self.source_ids:
            raise ValueError("Eine fachliche Antwort benötigt mindestens eine Quelle.")
        return self


def answer_question(question, course=None, history=None, *, retriever=retrieve, llm_service=None):
    if not isinstance(question, str) or not question.strip():
        raise AgentError("Bitte eine Frage eingeben.")
    history = limited_history(history or [])
    query = retrieval_query(question.strip(), history)
    notes = retriever(query, top_k=5, course=course)
    sources = build_sources(notes)
    if not sources:
        return {
            "answer": "Diese Frage lässt sich mit den aktuell hinterlegten Unterlagen nicht ausreichend beantworten.",
            "sources": [],
            "insufficient_information": True,
        }

    prompt = (
        "Du bist der Lernchat einer persönlichen Wissensbasis. Beantworte fachliche Fragen ausschließlich "
        "auf Basis der bereitgestellten Knowledge Notes. Ergänze kein allgemeines Modellwissen. Wenn die Notes "
        "nicht ausreichen, setze insufficient_information auf true und sage transparent, dass die vorhandenen "
        "Unterlagen nicht genügend Informationen enthalten. Antworte zuerst direkt, auf verständlichem Deutsch, "
        "mit kurzen Absätzen und bei Bedarf Listen. " + MATH_FORMAT_RULES + "Vermeide lange Essays und Wiederholungen. Referenziere nur "
        "angebotene SOURCE_n-IDs; erfinde keine Dateinamen oder Seiten.\n"
        f"Ausgewählter Kurs: {course or 'Alle Kurse'}\n"
        f"Begrenzter Gesprächskontext: {json.dumps(history, ensure_ascii=False)}\n"
        f"Aktuelle Frage: {question.strip()}\n"
        f"Knowledge Notes: {sources_for_prompt(sources)}"
    )
    service = llm_service or LLMService()
    draft = parse_model(service.generate(prompt, ChatDraft), ChatDraft, "die Chat-Antwort")
    allowed = {source.citation.source_id: source.citation for source in sources}
    unknown = set(draft.source_ids) - set(allowed)
    if unknown:
        raise AgentError("Die Chat-Antwort enthält eine ungültige Source-ID: " + ", ".join(sorted(unknown)))
    citations = []
    seen = set()
    for source_id in draft.source_ids:
        if source_id not in seen:
            citations.append(allowed[source_id].model_dump())
            seen.add(source_id)
    answer = clean_answer(draft.answer)
    if not answer:
        raise AgentError("Die Chat-Antwort enthält keinen lesbaren Inhalt.")
    return {
        "answer": answer,
        "sources": citations,
        "insufficient_information": draft.insufficient_information,
    }


def limited_history(history):
    result = []
    for message in history[-MAX_HISTORY_MESSAGES:]:
        role = message["role"] if isinstance(message, dict) else message["role"]
        content = message["content"] if isinstance(message, dict) else message["content"]
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            result.append({"role": role, "content": content.strip()[:1500]})
    return result


def retrieval_query(question, history):
    """Kurze oder verweisende Folgefragen erhalten nur den jüngsten Kontext."""
    words = question.split()
    follow_up = len(words) <= 10 or bool(re.search(
        r"\b(sie|ihnen|diese|dieser|davon|dafür|damit|dabei)\b", question.casefold()
    ))
    if not follow_up or not history:
        return question
    recent = history[-2:]
    context = " ".join(message["content"] for message in recent)
    return f"{question}\nVorheriger Kontext: {context[:1200]}"


def clean_answer(answer):
    """Interne Source-IDs werden validiert, aber nicht als UI-Text angezeigt."""
    cleaned = re.sub(r"\s*\[SOURCE_\d+\]", "", answer)
    return re.sub(r"[ \t]+\n", "\n", cleaned).strip()
