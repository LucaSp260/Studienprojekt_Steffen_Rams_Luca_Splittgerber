"""Markdown ist die Wissensrepräsentation; YAML trägt die Quellenmetadaten."""

from pathlib import Path

import yaml

from src.data_layer.document_manager import safe_name
from src.knowledge_layer.models import KnowledgeNote

KNOWLEDGE_BASE_PATH = Path(__file__).resolve().parents[2] / "knowledge_base"
METADATA_FIELDS = ("title", "course", "topic", "tags", "difficulty", "source_file",
                   "source_pages", "related_topics")


def render_markdown(note):
    note = KnowledgeNote.model_validate(note)
    metadata = {name: getattr(note, name) for name in METADATA_FIELDS}
    frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
    parts = [f"---\n{frontmatter}---\n", f"# {note.title}", "## Definition", note.definition]
    for heading, value in [("Zentrale Konzepte", note.key_concepts), ("Beispiel", note.example),
                           ("Typische Fehler", note.common_mistakes),
                           ("Prüfungsrelevante Aspekte", note.exam_relevance)]:
        if value:
            parts.extend([f"## {heading}", "\n".join(f"- {item}" for item in value)
                          if isinstance(value, list) else value])
    return "\n\n".join(parts) + "\n"


def save_note(note):
    content = render_markdown(note)
    folder = KNOWLEDGE_BASE_PATH / safe_name(note.course.lower(), "kurs")
    folder.mkdir(parents=True, exist_ok=True)
    stem = safe_name(note.title.lower(), "thema")
    number = 0
    while True:
        suffix = f"_{number}" if number else ""
        path = folder / f"{stem}{suffix}.md"
        try:
            file = path.open("x", encoding="utf-8")
        except FileExistsError:
            number += 1
            continue
        try:
            with file:
                file.write(content)
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return path


def load_note(relative_path):
    path = (KNOWLEDGE_BASE_PATH.parent / relative_path).resolve()
    if not path.is_relative_to(KNOWLEDGE_BASE_PATH.resolve()):
        raise ValueError("Die Note liegt nicht im Wissensbasis-Ordner.")
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        raise ValueError("Die Note enthält keine gültigen Metadaten.")
    parts = content.split("\n---\n", 1)
    if len(parts) != 2:
        raise ValueError("Die Metadaten der Note sind unvollständig.")
    metadata = yaml.safe_load(parts[0][4:])
    if not isinstance(metadata, dict) or not all(name in metadata for name in METADATA_FIELDS):
        raise ValueError("Die Metadaten der Note sind unvollständig.")
    return metadata, parts[1].strip()
