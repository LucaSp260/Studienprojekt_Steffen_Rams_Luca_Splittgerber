"""Exam Agent: vielfältige, zeitlich passende Probeklausuren aus Retrieval-Quellen."""

import sqlite3

from pydantic import ValidationError

from src.agent_layer.common import (
    apply_time_estimates, build_sources, difficulty_plan, notify, parse_model,
    sources_for_prompt, time_target_range, validate_number_and_difficulty,
    validate_solution_layers, validate_source_ids,
)
from src.agent_layer.critic_agent import review_exam
from src.agent_layer.models import AgentError, ExamDraft, ExamRequest
from src.knowledge_layer.retrieval_service import retrieve
from src.llm.base_provider import LLMError
from src.llm.llm_service import LLMService
from src.persistence.artifact_repository import save_artifact


def create_exam(request, *, retriever=retrieve, llm_service=None, critic=review_exam,
                persist=True, progress=None):
    try:
        request = request if isinstance(request, ExamRequest) else ExamRequest.model_validate(request)
    except ValidationError:
        raise AgentError("Die Angaben für die Probeklausur sind ungültig.") from None

    notify(progress, "Wissensbasis wird durchsucht...")
    focus = request.focus.strip() or "verschiedene prüfungsrelevante Themen"
    query = f"{focus}; Definitionen, Zusammenhänge, Anwendungen und typische Abgrenzungen"
    notes = retriever(query, top_k=min(10, max(5, request.task_count * 2)), course=request.course)
    sources = build_sources(notes)
    if not sources:
        raise AgentError("Für diesen Kurs wurden keine Knowledge Notes gefunden.")

    plan = difficulty_plan(request.task_count, request.difficulty)
    lower, upper = time_target_range(request.duration_minutes)
    prompt = (
        "Du bist der Exam Agent. Erzeuge eine vollständige Probeklausur auf Deutsch. "
        "Verwende für fachliche Aussagen ausschließlich die bereitgestellten Knowledge Notes. "
        "Jede Aufgabe muss mindestens eine passende SOURCE_n-ID verwenden. Erfinde keine Quellen. "
        "Decke mehrere der tatsächlich vorhandenen Notes ab und vermeide redundante Aufgaben. "
        "Vergib positive ganzzahlige Punkte und rechne total_points exakt aus. "
        "Formuliere kurze, klare Aufgabenstellungen. Nutze subtasks für Teilfragen und Listen statt Fließtext. "
        "Jede Teilaufgabe erhält eine ID ohne Satzzeichen, zum Beispiel a, b, c oder 1, 2, 3. "
        "Übernimm exakt dieselben IDs und dieselbe Reihenfolge in short_answer_items und explanation_items; "
        "ändere a/b/c niemals in 1/2/3 oder umgekehrt. Bei Teilaufgaben bleiben solution und explanation leer. "
        "Kurzantworten sind die kürzestmöglichen vollständigen Antworten; Zuordnungen enthalten keine Begründung. "
        "Jedes explanation_item erklärt separat WARUM die zugehörige Antwort richtig ist. Bei Aufgaben ohne "
        "Teilaufgaben bleiben die strukturierten Listen leer und solution/explanation enthalten die zwei Ebenen. "
        "Wähle Aufgabentyp, Umfang, Teilfragen, Begründungs- und Transferanteil so, dass die Klausur realistisch "
        f"{lower} bis {upper} Minuten benötigt. Kurze Aufgaben bleiben kurz; umfangreichere Aufgaben tragen den "
        "größeren Zeitanteil. estimated_minutes muss den tatsächlichen Inhalt abbilden und darf nicht zum bloßen "
        "Auffüllen der Gesamtdauer erhöht werden.\n"
        f"Anforderung: {request.model_dump_json()}\n"
        f"Verbindlicher Schwierigkeitsplan in Aufgabenreihenfolge: {plan}\n"
        f"Knowledge Notes: {sources_for_prompt(sources)}"
    )
    notify(progress, "Probeklausur wird erstellt...")
    service = llm_service or LLMService()
    draft = parse_model(service.generate(prompt, ExamDraft), ExamDraft, "die Probeklausur")
    draft = apply_time_estimates(draft, request.duration_minutes)
    _validate_draft(draft, request, sources)

    notify(progress, "Probeklausur wird geprüft...")
    critic_status, issues, final = _apply_critic(request, sources, draft, service, critic)
    final = apply_time_estimates(final, request.duration_minutes)
    _validate_time(final, request.duration_minutes)
    validate_solution_layers(final.tasks)
    artifact = {
        "title": final.title,
        "course": request.course,
        "configuration": request.model_dump(),
        "duration_minutes": final.duration_minutes,
        "estimated_total_minutes": sum(task.estimated_minutes for task in final.tasks),
        "tasks": [item.model_dump() for item in final.tasks],
        "total_points": final.total_points,
        "sources": [source.citation.model_dump() for source in sources],
        "critic": {"status": critic_status, "issues": issues},
    }
    artifact_id = None
    if persist:
        try:
            artifact_id = save_artifact("exam", final.title, request.course,
                                        request.model_dump(), artifact)
        except (sqlite3.Error, OSError, ValueError):
            raise AgentError("Die Probeklausur wurde erzeugt, konnte aber nicht gespeichert werden.") from None
    return {"artifact_id": artifact_id, **artifact}


def _validate_draft(draft, request, sources):
    validate_number_and_difficulty(draft.tasks, request.task_count, request.difficulty)
    used = validate_source_ids(draft.tasks, sources)
    if draft.course != request.course or draft.duration_minutes != request.duration_minutes:
        raise AgentError("Kurs oder Dauer der Probeklausur entspricht nicht der Anforderung.")
    if draft.total_points != sum(task.points for task in draft.tasks):
        raise AgentError("Die Gesamtpunkte sind rechnerisch inkonsistent.")
    if request.task_count > 1 and len(sources) > 1 and len(used) < 2:
        raise AgentError("Die Probeklausur deckt trotz vorhandener Quellen nicht mehrere Themen ab.")


def _validate_time(draft, duration):
    lower, upper = time_target_range(duration)
    total = sum(task.estimated_minutes for task in draft.tasks)
    if not lower <= total <= upper:
        raise AgentError(
            f"Der Aufgabenumfang entspricht auch nach der Qualitätsprüfung nur etwa {total} Minuten; "
            f"erforderlich sind ungefähr {lower} bis {upper} Minuten."
        )


def _apply_critic(request, sources, draft, service, critic):
    try:
        critique = critic(request, sources, draft, service)
        issues = [issue.model_dump() for issue in critique.issues]
        if critique.status == "needs_revision" and critique.revised_content is not None:
            revised = apply_time_estimates(critique.revised_content, request.duration_minutes)
            _validate_draft(revised, request, sources)
            return critique.status, issues, revised
        return critique.status, issues, draft
    except (LLMError, AgentError, ValidationError, ValueError, TypeError):
        return "failed", [{"category": "other", "message":
                           "Die Qualitätsprüfung ist fehlgeschlagen; der Entwurf ist ungeprüft."}], draft
