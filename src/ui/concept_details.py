"""Short local summaries for graph exploration; no LLM access."""
import re
import textwrap
from src.knowledge_layer.markdown_store import load_note

def concept_summary(concept, notes):
    text = concept["description"].strip()
    if not text:
        ids = {int(value) for value in (concept["note_ids"] or "").split(",") if value.isdigit()}
        for note in notes:
            if int(note["id"]) in ids:
                try:
                    _, body = load_note(note["markdown_path"])
                    section = re.search(r"## Definition\s*\n(.*?)(?=\n## |\Z)", body, re.S)
                    text = section.group(1) if section else " ".join(line for line in body.splitlines() if not line.startswith("#"))
                    break
                except (OSError, ValueError):
                    continue
    text = " ".join(text.split())
    if not text:
        return f"{concept['name']} ist ein Konzept im Kurs {concept['course']}. Seine gespeicherten Beziehungen und Belege stehen unten."
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return textwrap.shorten(" ".join(sentences[:3]), width=420, placeholder=" …")
