"""Critic Agent: eine strukturierte Prüfung mit höchstens einer eingebetteten Revision."""

from src.agent_layer.common import parse_model, sources_for_prompt, time_target_range
from src.agent_layer.models import ExerciseCritique, ExamCritique


CRITIC_RULES = (
    "Prüfe Grounding, Lösung, Eindeutigkeit, Schwierigkeit, Redundanz und Quellen. "
    "Verwende für fachliche Aussagen ausschließlich die bereitgestellten Knowledge Notes. "
    "Quellen dürfen nur über die angebotenen SOURCE_n-IDs referenziert werden. "
    "Prüfe außerdem, ob Aufgaben kurz und klar formuliert, Teilfragen gegliedert, Kurzlösungen kompakt "
    "und vollständig sowie Erklärungen didaktisch vertiefend und ohne unnötige Wiederholung sind. "
    "Bei Teilaufgaben müssen IDs und Reihenfolge in subtasks, short_answer_items und explanation_items exakt "
    "übereinstimmen; a/b/c darf nicht zu 1/2/3 werden. Jede Teilaufgabe benötigt eine eigene Kurzantwort und "
    "eine getrennte Erklärung. Zuordnungs-Kurzantworten enthalten nur die Zuordnung, Begründungen stehen in "
    "explanation_items. Lösungselemente dürfen nicht zu einem Fließtext zusammengezogen werden. "
    "Die Erklärung muss mindestens einen zusätzlichen Zusammenhang, eine Abgrenzung, einen typischen "
    "Denkfehler oder einen Lernhinweis enthalten, der nicht nur die Kurzlösung umformuliert. "
    "Bei needs_revision liefere genau eine vollständig überarbeitete Fassung in revised_content. "
    "Bei approved muss revised_content null sein. Erfinde keine Lehrinhalte oder Quellen."
)


def review_exercises(request, sources, draft, llm_service):
    prompt = (
        "Du bist der Critic Agent für Lernübungen. " + CRITIC_RULES + "\n"
        "Nutzeranforderung: " + request.model_dump_json() + "\n"
        "Knowledge Notes: " + sources_for_prompt(sources) + "\n"
        "Entwurf: " + draft.model_dump_json(exclude_none=True) + "\n"
        "Bewerte den Entwurf strukturiert."
    )
    return parse_model(llm_service.generate(prompt, ExerciseCritique), ExerciseCritique, "die Übungskritik")


def review_exam(request, sources, draft, llm_service):
    timing = ""
    if request.duration_minutes is not None:
        lower, upper = time_target_range(request.duration_minutes)
        estimated = sum(task.estimated_minutes for task in draft.tasks)
        timing = (f"Historischer Zielbereich: {lower} bis {upper} Minuten; aktuelle lokale Schätzung: {estimated} Minuten. "
                  "Prüfe die Eignung des Aufgabenumfangs für diese historische Zeitvorgabe.")
    prompt = (
        "Du bist der Critic Agent für Probeklausuren. " + CRITIC_RULES + " "
        "Prüfe zusätzlich Themenvielfalt, Punkteberechnung und den fachlich sinnvollen Aufgabenumfang. "
        + timing + "\n"
        "Nutzeranforderung: " + request.model_dump_json(exclude_none=True) + "\n"
        "Knowledge Notes: " + sources_for_prompt(sources) + "\n"
        "Entwurf: " + draft.model_dump_json(exclude_none=True) + "\n"
        "Bewerte den Entwurf strukturiert."
    )
    return parse_model(llm_service.generate(prompt, ExamCritique), ExamCritique, "die Klausurkritik")
