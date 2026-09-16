"""Validierte fachliche Felder; Quelle und Kurs stammen aus SQLite."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NonEmpty = Annotated[str, Field(min_length=1)]
PageNumber = Annotated[int, Field(strict=True, gt=0)]


class ExtractedNote(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: NonEmpty
    topic: NonEmpty
    tags: list[NonEmpty] = Field(min_length=1)
    difficulty: Literal["easy", "medium", "hard"]
    source_pages: list[PageNumber] = Field(min_length=1)
    related_topics: list[NonEmpty]
    definition: NonEmpty
    key_concepts: list[NonEmpty] = Field(min_length=1)
    example: str
    common_mistakes: list[NonEmpty]
    exam_relevance: list[NonEmpty] = Field(min_length=1)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, tags):
        cleaned = []
        for tag in tags:
            tag = re.sub(r"[^\w-]+", "-", tag.lower()).strip("-_")
            if not tag or len(tag) > 50 or len(tag.split("-")) > 5:
                raise ValueError("Tags müssen kurze Begriffe sein.")
            if tag not in cleaned:
                cleaned.append(tag)
        return cleaned

    @field_validator("source_pages")
    @classmethod
    def unique_pages(cls, pages):
        return sorted(set(pages))


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: list[ExtractedNote]


class KnowledgeNote(ExtractedNote):
    course: NonEmpty
    source_file: NonEmpty
