"""Gemeinsame, lokale Hilfen für Grounding und nachvollziehbare Verteilungen."""

import json
import math
from dataclasses import dataclass

from pydantic import ValidationError

from src.agent_layer.models import AgentError, SourceCitation


MATH_FORMAT_RULES = (
    "Mathematische Formeln müssen als korrektes LaTeX ausgegeben werden, damit sie lesbar gerendert werden: "
    "inline ausschließlich mit $...$, abgesetzt mit $$ auf eigenen Zeilen. "
    "Verwende keine eckigen Klammern als Formeldelimiter, keine LaTeX-Formeln in Codeblöcken und keine "
    "ASCII-Umschreibungen wie x^2, wenn eine echte Formel möglich ist. "
    "Nutze verständliche Variablendefinitionen im umgebenden Text. "
)


@dataclass(frozen=True)
class SourceMaterial:
    citation: SourceCitation
    topic: str
    content: str


def build_sources(notes):
    """Vergibt stabile IDs nur innerhalb eines Agent-Laufs."""
    sources = []
    seen = set()
    for note in notes:
        note_id = note.get("knowledge_note_id")
        if note_id in seen:
            continue
        seen.add(note_id)
        try:
            citation = SourceCitation(
                source_id=f"SOURCE_{len(sources) + 1}",
                knowledge_note_id=note_id,
                title=note["title"],
                source_file=note["source_file"],
                source_pages=note["source_pages"],
            )
            sources.append(SourceMaterial(citation, note["topic"], note["content"]))
        except (KeyError, TypeError, ValidationError):
            raise AgentError("Das Retrieval lieferte unvollständige Quelleninformationen.") from None
    return sources


def sources_for_prompt(sources):
    return json.dumps([
        {
            "source_id": source.citation.source_id,
            "title": source.citation.title,
            "topic": source.topic,
            "source_file": source.citation.source_file,
            "source_pages": source.citation.source_pages,
            "content": source.content,
        }
        for source in sources
    ], ensure_ascii=False)


def validate_source_ids(items, sources):
    allowed = {source.citation.source_id for source in sources}
    used = set()
    for item in items:
        unknown = set(item.source_ids) - allowed
        if unknown:
            raise AgentError("Das Modell hat eine ungültige Source-ID verwendet: " + ", ".join(sorted(unknown)))
        used.update(item.source_ids)
    return used


def validate_solution_layers(items):
    for item in items:
        if item.subtasks:
            for short_item, explanation_item in zip(item.short_answer_items, item.explanation_items):
                short = " ".join(short_item.answer.casefold().split())
                explanation = " ".join(explanation_item.explanation.casefold().split())
                if short == explanation:
                    raise AgentError("Kurzlösung und Erklärung müssen getrennte Inhalte besitzen.")
        else:
            short = " ".join(item.solution.casefold().split())
            explanation = " ".join(item.explanation.casefold().split())
            if short == explanation:
                raise AgentError("Kurzlösung und Erklärung müssen getrennte Inhalte besitzen.")


def parse_model(value, model, label):
    try:
        return value if isinstance(value, model) else model.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise AgentError(f"Das Modell lieferte ungültige strukturierte Daten für {label}.") from None


def difficulty_distribution(count, requested):
    """Verteilt 20/50/30 per größtem Rest; Gleichstände folgen leicht, mittel, schwer."""
    if requested != "gemischt":
        return {name: count if name == requested else 0 for name in ("leicht", "mittel", "schwer")}
    weights = {"leicht": 0.2, "mittel": 0.5, "schwer": 0.3}
    raw = {name: count * weight for name, weight in weights.items()}
    result = {name: math.floor(value) for name, value in raw.items()}
    remaining = count - sum(result.values())
    order = sorted(weights, key=lambda name: (raw[name] - result[name], -list(weights).index(name)), reverse=True)
    for name in order[:remaining]:
        result[name] += 1
    return result


def difficulty_plan(count, requested):
    distribution = difficulty_distribution(count, requested)
    return [name for name in ("leicht", "mittel", "schwer") for _ in range(distribution[name])]


def validate_number_and_difficulty(items, count, requested):
    if len(items) != count:
        raise AgentError(f"Das Modell erzeugte {len(items)} statt {count} Aufgaben.")
    if [item.number for item in items] != list(range(1, count + 1)):
        raise AgentError("Die Aufgabennummern sind nicht vollständig oder nicht fortlaufend.")
    actual = sorted(item.difficulty for item in items)
    expected = sorted(difficulty_plan(count, requested))
    if actual != expected:
        raise AgentError("Die erzeugte Schwierigkeitsverteilung entspricht nicht der Anforderung.")


def time_target_range(duration):
    """Akzeptiert ungefähr ±7 Prozent, sinnvoll auf ganze Minuten gerundet."""
    return math.ceil(duration * 0.93), math.floor(duration * 1.07)


def estimate_task_minutes(task):
    """Schätzt Arbeitsaufwand aus Inhalt; Punkte haben nur einen kleinen Einfluss."""
    base = {
        "Multiple Choice": 2,
        "Offene Frage": 3,
        "Verständnisfrage": 3,
        "Anwendungsaufgabe": 5,
        "Gemischt": 5,
    }[task.exercise_type]
    difficulty = {"leicht": 0, "mittel": 1, "schwer": 2}[task.difficulty]
    subtask_effort = min(8, len(task.subtasks))
    subtask_text = " ".join(item.text for item in task.subtasks)
    words = len((task.task + " " + subtask_text).split())
    text_effort = min(2, max(0, math.ceil((words - 45) / 50)))
    answer_text = task.solution or " ".join(item.answer for item in task.short_answer_items)
    solution_words = len(answer_text.split())
    answer_effort = min(2, max(0, math.ceil((solution_words - 50) / 60)))
    text = (task.task + " " + subtask_text).casefold()
    demanding = ("begründen", "vergleichen", "analysieren", "bewerten", "transfer",
                 "anwenden", "entwickeln", "herleiten")
    reasoning_effort = min(2, sum(term in text for term in demanding))
    points = getattr(task, "points", 0)
    point_effort = min(1, max(0, round((points - 8) / 8))) if points else 0
    return max(2, min(30, base + difficulty + subtask_effort + text_effort
                      + answer_effort + reasoning_effort + point_effort))


def apply_time_estimates(draft, duration):
    """Ersetzt Modellschätzungen durch die Inhaltsheuristik, ohne Zeiten hochzuskalieren."""
    data = draft.model_dump()
    data["duration_minutes"] = duration
    for original, task in zip(draft.tasks, data["tasks"]):
        task["estimated_minutes"] = estimate_task_minutes(original)
    return type(draft).model_validate(data)


def notify(progress, message):
    if progress:
        progress(message)
