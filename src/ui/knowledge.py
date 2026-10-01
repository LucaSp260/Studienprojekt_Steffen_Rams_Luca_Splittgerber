"""Dokumente explizit verarbeiten und Markdown-Notes lesbar anzeigen."""
import sqlite3

import streamlit as st
import yaml

from src.knowledge_layer.knowledge_manager import process_document
from src.knowledge_layer.markdown_store import load_note
from src.llm.base_provider import LLMError
from src.persistence.document_repository import load_documents
from src.persistence.knowledge_repository import load_notes
from src.persistence.brain_repository import pending_brain_notes
from src.ui.search import show_search
from src.knowledge_layer.embedding_service import EmbeddingError
from src.knowledge_layer.vector_store import SearchError


def show_knowledge_page():
    show_search()
    st.subheader('Noch zu verarbeiten')
    st.caption('Die Verarbeitung sendet PDF-Text an den unter Einstellungen gespeicherten Anbieter.')
    documents = load_documents()
    pending = [document for document in documents if not document['processed']]
    if not pending:
        st.info('Keine unverarbeiteten Dokumente vorhanden.')
    for document in pending:
        pages = f"{document['page_count']} Seiten" if document['page_count'] else 'Seitenzahl unbekannt'
        st.text(f"{document['filename']} – {document['course']} – {pages}")
        if st.button('In Wissensbasis verarbeiten', key=f"process_{document['id']}"):
            with st.status('PDF wird analysiert …', expanded=True) as status:
                try:
                    result = process_document(document['id'], progress=lambda text: status.update(label=text))
                    status.update(label=result['message'], state='complete')
                    st.session_state.knowledge_result = result['message']
                except (ValueError, LLMError) as error:
                    status.update(label=str(error), state='error')
                    return
                except (EmbeddingError, SearchError) as error:
                    status.update(label=str(error), state='error')
                    st.info('Bereits erzeugte Notes bleiben erhalten. Mit „Vorhandene Themen indexieren“ '
                            'kann die Indexierung ohne erneute Knowledge Extraction fortgesetzt werden.')
                    return
                except (OSError, sqlite3.Error):
                    status.update(label='Knowledge Notes konnten nicht gespeichert werden. '
                                  'Bitte Schreibrechte, Speicherplatz und Datenbank prüfen.', state='error')
                    return
            st.rerun()
    if message := st.session_state.pop('knowledge_result', None):
        st.success(message)
    processed = [document for document in documents if document['processed']]
    if processed:
        with st.expander('Bereits verarbeitete Dokumente'):
            for document in processed:
                st.text(f"{document['filename']} – {document['course']}")
                st.caption('Dieses Dokument wurde bereits in die Wissensbasis verarbeitet.')
                if pending_brain_notes(document['id']):
                    st.warning('Die Knowledge Notes sind indexiert; die automatische Ergänzung des Second Brains steht noch aus.')
                    if st.button('Second Brain erneut ergänzen', key=f"retry_brain_{document['id']}"):
                        try:
                            result = process_document(document['id'])
                            st.success(result['message'])
                            st.rerun()
                        except (ValueError, LLMError, EmbeddingError, SearchError, sqlite3.Error, OSError) as error:
                            st.error(str(error) or 'Das Second Brain konnte nicht ergänzt werden.')
    st.subheader('Meine Wissensbasis')
    notes = load_notes()
    if not notes:
        st.info('Noch keine Knowledge Notes vorhanden.')
        return
    courses = list(dict.fromkeys(note['course'] or 'Ohne Kurs' for note in notes))
    st.caption(f"{len(notes)} Themen in {len(courses)} Kursen")
    for course in courses:
        course_notes = [note for note in notes if (note['course'] or 'Ohne Kurs') == course]
        st.subheader(f'{course} · {len(course_notes)} Themen')
        for note in course_notes:
            with st.expander(f"{note['title']} · #{note['id']}"):
                try:
                    metadata, content = load_note(note['markdown_path'])
                    st.text(f"Thema: {metadata['topic']}")
                    st.text(f"Tags: {', '.join(metadata['tags'])}")
                    difficulty = {'easy': 'Einfach', 'medium': 'Mittel', 'hard': 'Anspruchsvoll'}
                    st.text(f"Schwierigkeit: {difficulty.get(metadata['difficulty'], metadata['difficulty'])}")
                    st.text(f"Quelle: {metadata['source_file']} · Seiten: "
                            + ', '.join(str(page) for page in metadata['source_pages']))
                    st.markdown(content)
                except (OSError, ValueError, TypeError, yaml.YAMLError):
                    st.error('Diese Knowledge Note konnte nicht gelesen werden. Bitte die Markdown-Datei prüfen.')
