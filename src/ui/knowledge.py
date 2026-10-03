"""Passive note browsing with course filters and original source metadata."""
import streamlit as st
import yaml
from src.knowledge_layer.markdown_store import load_note
from src.persistence.knowledge_repository import load_notes

def show_knowledge_page():
    notes = load_notes()
    courses = sorted({note["course"] for note in notes if note["course"]}, key=str.casefold)
    course = st.selectbox("Kursfilter", [None] + courses, key="notes_course",
                          format_func=lambda value: "Alle Kurse" if value is None else value)
    filtered = [note for note in notes if course is None or note["course"] == course]
    st.caption(f"{len(filtered)} Knowledge Notes")
    if not filtered:
        st.info("Noch keine Notes in dieser Auswahl. Verarbeite eigene Dokumente unter Unterlagen.")
    for note in filtered:
        with st.expander(f"{note['title']} · {note['course'] or 'Ohne Kurs'} · #{note['id']}"):
            try:
                metadata, content = load_note(note["markdown_path"])
                st.caption(f"Thema: {metadata['topic']} · Schwierigkeit: " +
                    {"easy": "Einfach", "medium": "Mittel", "hard": "Anspruchsvoll"}.get(metadata["difficulty"], metadata["difficulty"]))
                st.caption("Tags: " + ", ".join(metadata["tags"]))
                st.text(f"Quelle: {metadata['source_file']} · Seiten: " + ", ".join(map(str, metadata["source_pages"])))
                st.markdown(content)
            except (OSError, ValueError, TypeError, yaml.YAMLError):
                st.error("Diese Note konnte nicht gelesen werden. Bitte die Markdown-Datei prüfen.")
