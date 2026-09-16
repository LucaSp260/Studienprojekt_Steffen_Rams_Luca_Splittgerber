"""Kleine, streng validierte Datenmodelle für Phase 5."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmpty = Annotated[str, Field(min_length=1)]
Difficulty = Literal["leicht", "mittel", "schwer"]
RequestedDifficulty = Literal["leicht", "mittel", "schwer", "gemischt"]
ExerciseType = Literal[
    "Offene Frage", "Verständnisfrage", "Anwendungsaufgabe", "Multiple Choice", "Gemischt"
]


class AgentError(Exception):
    """Verständlicher Fehler der Agent-Schicht."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExerciseRequest(StrictModel):
    course: NonEmpty
    topic: NonEmpty
    count: int = Field(ge=1, le=20)
    difficulty: RequestedDifficulty
    exercise_type: ExerciseType


class ExamRequest(StrictModel):
    course: NonEmpty
    duration_minutes: int = Field(ge=10, le=300)
    task_count: int = Field(ge=1, le=20)
    difficulty: RequestedDifficulty
    focus: str = ""

    @model_validator(mode="after")
    def enough_time(self):
        if self.duration_minutes < self.task_count:
            raise ValueError("Die Dauer muss mindestens eine Minute pro Aufgabe erlauben.")
        return self


class SourceCitation(StrictModel):
    source_id: NonEmpty
    knowledge_note_id: int = Field(gt=0)
    title: NonEmpty
    source_file: NonEmpty
    source_pages: list[int] = Field(min_length=1)


class Subtask(StrictModel):
    id: NonEmpty = Field(pattern=r"^(?:[A-Za-z]+|[0-9]+)$")
    text: NonEmpty


class ShortAnswerItem(StrictModel):
    id: NonEmpty = Field(pattern=r"^(?:[A-Za-z]+|[0-9]+)$")
    answer: NonEmpty


class ExplanationItem(StrictModel):
    id: NonEmpty = Field(pattern=r"^(?:[A-Za-z]+|[0-9]+)$")
    explanation: NonEmpty


class StructuredAnswersMixin:
    @model_validator(mode="after")
    def matching_answer_ids(self):
        subtask_ids = [item.id for item in self.subtasks]
        answer_ids = [item.id for item in self.short_answer_items]
        explanation_ids = [item.id for item in self.explanation_items]
        if len(subtask_ids) != len(set(subtask_ids)):
            raise ValueError("Teilaufgaben-Bezeichner müssen eindeutig sein.")
        if subtask_ids:
            if answer_ids != subtask_ids or explanation_ids != subtask_ids:
                raise ValueError("Teilaufgaben, Kurzlösungen und Erklärungen benötigen identische Bezeichner.")
        elif answer_ids or explanation_ids:
            raise ValueError("Strukturierte Lösungen sind nur für vorhandene Teilaufgaben zulässig.")
        elif not self.solution.strip() or not self.explanation.strip():
            raise ValueError("Aufgaben ohne Teilaufgaben benötigen Kurzlösung und Erklärung.")
        return self


class ExerciseItem(StructuredAnswersMixin, StrictModel):
    number: int = Field(gt=0)
    title: NonEmpty
    task: NonEmpty
    subtasks: list[Subtask] = Field(default_factory=list)
    difficulty: Difficulty
    exercise_type: ExerciseType
    choices: list[NonEmpty] = Field(default_factory=list)
    solution: str
    explanation: str
    short_answer_items: list[ShortAnswerItem] = Field(default_factory=list)
    explanation_items: list[ExplanationItem] = Field(default_factory=list)
    estimated_minutes: int | None = Field(default=None, gt=0)
    source_ids: list[NonEmpty] = Field(min_length=1)

    @model_validator(mode="after")
    def multiple_choice_has_options(self):
        if self.exercise_type == "Multiple Choice" and len(self.choices) < 3:
            raise ValueError("Multiple-Choice-Aufgaben benötigen mindestens drei Antwortmöglichkeiten.")
        return self


class ExerciseDraft(StrictModel):
    title: NonEmpty
    exercises: list[ExerciseItem] = Field(min_length=1)


class ExamTask(StructuredAnswersMixin, StrictModel):
    number: int = Field(gt=0)
    title: NonEmpty
    task: NonEmpty
    subtasks: list[Subtask] = Field(default_factory=list)
    difficulty: Difficulty
    exercise_type: ExerciseType
    choices: list[NonEmpty] = Field(default_factory=list)
    points: int = Field(gt=0)
    estimated_minutes: int = Field(gt=0)
    solution: str
    explanation: str
    short_answer_items: list[ShortAnswerItem] = Field(default_factory=list)
    explanation_items: list[ExplanationItem] = Field(default_factory=list)
    source_ids: list[NonEmpty] = Field(min_length=1)

    @model_validator(mode="after")
    def multiple_choice_has_options(self):
        if self.exercise_type == "Multiple Choice" and len(self.choices) < 3:
            raise ValueError("Multiple-Choice-Aufgaben benötigen mindestens drei Antwortmöglichkeiten.")
        return self


class ExamDraft(StrictModel):
    title: NonEmpty
    course: NonEmpty
    duration_minutes: int = Field(gt=0)
    tasks: list[ExamTask] = Field(min_length=1)
    total_points: int = Field(gt=0)

    @model_validator(mode="after")
    def points_match(self):
        if self.total_points != sum(task.points for task in self.tasks):
            raise ValueError("Die Gesamtpunkte entsprechen nicht der Summe der Aufgabenpunkte.")
        return self


class CriticIssue(StrictModel):
    category: Literal[
        "grounding", "solution", "clarity", "difficulty", "redundancy",
        "sources", "diversity", "points", "duration", "other"
    ]
    message: NonEmpty
    task_number: int | None = Field(default=None, gt=0)


class ExerciseCritique(StrictModel):
    status: Literal["approved", "needs_revision"]
    issues: list[CriticIssue]
    revised_content: ExerciseDraft | None = None


class ExamCritique(StrictModel):
    status: Literal["approved", "needs_revision"]
    issues: list[CriticIssue]
    revised_content: ExamDraft | None = None
