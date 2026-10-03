"""Exercise Agent: Retrieval, strukturierte Erzeugung, Critic und Persistenz."""

import sqlite3

from pydantic import ValidationError

from src.agent_layer.common import (
    MATH_FORMAT_RULES, build_sources, difficulty_plan, notify, parse_model, sources_for_prompt,
    validate_number_and_difficulty, validate_solution_layers, validate_source_ids,
)
from src.agent_layer.critic_agent import review_exercises
from src.agent_layer.models import AgentError, ExerciseDraft, ExerciseRequest
from src.knowledge_layer.retrieval_service import retrieve
from src.llm.base_provider import LLMError
from src.llm.llm_service import LLMService
from src.persistence.artifact_repository import save_artifact


def create_exercises(request, *, retriever=retrieve, llm_service=None, critic=review_exercises,
                     run_critic=True, persist=True, progress=None):
    try:
        request = request if isinstance(request, ExerciseRequest) else ExerciseRequest.model_validate(request)
    except ValidationError:
        raise AgentError("Die Angaben für die Übungen sind ungültig.") from None

    notify(progress, "Wissensbasis wird durchsucht...")
    focus = request.topic or f"Zentrale Themen und Grundlagen des Kurses {request.course}"
    query = (f"{focus}, Definition, Zweck, Zusammenhänge und Anwendung; "
             f"geeignet für {request.exercise_type}")
    notes = retriever(query, top_k=5, course=request.course)
    sources = build_sources(notes)
    if not sources:
        raise AgentError("Für diesen Kurs und dieses Thema wurden keine Knowledge Notes gefunden.")

    plan = difficulty_plan(request.count, request.difficulty)
    prompt = (
        "Du bist der Exercise Agent. Erzeuge genau die verlangten Lernaufgaben auf Deutsch. "
        "Verwende für fachliche Aussagen ausschließlich die bereitgestellten Knowledge Notes. "
        "Jede Aufgabe muss mindestens eine passende SOURCE_n-ID verwenden. Erfinde keine Quellen. "
        "Bei Multiple Choice: mindestens drei plausible Optionen; die Lösung nennt eindeutig die richtige Option. "
        + MATH_FORMAT_RULES
        + "Formuliere Aufgaben kurz und klar. Nutze subtasks für echte Teilfragen und Listen statt Fließtext. "
        "Jede Teilaufgabe erhält eine ID ohne Satzzeichen, zum Beispiel a, b, c oder 1, 2, 3. "
        "Übernimm exakt dieselben IDs und dieselbe Reihenfolge in short_answer_items und explanation_items. "
        "Ändere a/b/c niemals in 1/2/3 oder umgekehrt. Bei Teilaufgaben bleiben solution und explanation leer. "
        "Kurzantworten enthalten die kürzestmögliche vollständige Antwort; bei Zuordnungen nur die direkte "
        "Zuordnung ohne Begründung. Die jeweilige Begründung gehört in explanation_items und erklärt WARUM. "
        "Bei Aufgaben ohne Teilaufgaben bleiben die strukturierten Listen leer; solution enthält die kompakte "
        "Kurzlösung und explanation die didaktische Vertiefung. "
        "Bei Typ Gemischt darfst du die anderen vier Typen sinnvoll kombinieren. "
        "Ist kein Thema angegeben, wähle passende Themen aus den bereitgestellten Notes des Kurses.\n"
        f"Anforderung: {request.model_dump_json()}\n"
        f"Verbindlicher Schwierigkeitsplan in Aufgabenreihenfolge: {plan}\n"
        f"Knowledge Notes: {sources_for_prompt(sources)}"
    )
    notify(progress, "Aufgaben werden erstellt...")
    service = llm_service or LLMService()
    draft = parse_model(service.generate(prompt, ExerciseDraft), ExerciseDraft, "die Übungen")
    _validate_draft(draft, request, sources)

    if run_critic:
        notify(progress, "Aufgaben werden geprüft...")
        critic_status, issues, final = _apply_critic(request, sources, draft, service, critic)
    else:
        critic_status, issues, final = "skipped", [], draft
    validate_solution_layers(final.exercises)
    artifact = {
        "title": final.title,
        "course": request.course,
        "configuration": request.model_dump(),
        "exercises": [item.model_dump() for item in final.exercises],
        "sources": [source.citation.model_dump() for source in sources],
        "critic": {"status": critic_status, "issues": issues},
    }
    artifact_id = None
    if persist:
        try:
            artifact_id = save_artifact("exercise", final.title, request.course,
                                        request.model_dump(), artifact)
        except (sqlite3.Error, OSError, ValueError):
            raise AgentError("Die Übungen wurden erzeugt, konnten aber nicht gespeichert werden.") from None
    return {"artifact_id": artifact_id, **artifact}


def _validate_draft(draft, request, sources):
    validate_number_and_difficulty(draft.exercises, request.count, request.difficulty)
    validate_source_ids(draft.exercises, sources)
    if request.exercise_type != "Gemischt" and any(
            item.exercise_type != request.exercise_type for item in draft.exercises):
        raise AgentError("Mindestens eine Aufgabe hat nicht den gewünschten Aufgabentyp.")


def _apply_critic(request, sources, draft, service, critic):
    try:
        critique = critic(request, sources, draft, service)
        issues = [issue.model_dump() for issue in critique.issues]
        if critique.status == "needs_revision" and critique.revised_content is not None:
            _validate_draft(critique.revised_content, request, sources)
            return critique.status, issues, critique.revised_content
        return critique.status, issues, draft
    except (LLMError, AgentError, ValidationError, ValueError, TypeError):
        return "failed", [{"category": "other", "message":
                           "Die Qualitätsprüfung ist fehlgeschlagen; der Entwurf ist ungeprüft."}], draft
