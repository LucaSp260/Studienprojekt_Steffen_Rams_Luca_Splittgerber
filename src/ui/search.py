"""Explizite Indexierung und semantische Suche, ohne generative Antworten."""

import sqlite3

import streamlit as st

from src.knowledge_layer.embedding_service import EmbeddingError
from src.knowledge_layer.indexing_service import index_existing_notes
from src.knowledge_layer.retrieval_service import retrieve
from src.knowledge_layer.vector_store import SearchError, collection_name
from src.llm.config import load_embedding_config, PROVIDER_LABELS
from src.persistence.knowledge_repository import load_notes


def show_search():
    st.subheader('Wissensbasis durchsuchen')
    try:
        config = load_embedding_config()
    except (ValueError, OSError):
        st.error('Embedding-Konfiguration konnte nicht gelesen werden. Bitte Einstellungen prüfen.')
        return
    identity = collection_name(config)
    if st.session_state.get('search_collection') != identity:
        st.session_state.pop('search_results', None)
        st.session_state.search_collection = identity
    st.caption(f"Suchmodell: {PROVIDER_LABELS[config.provider]} · {config.model}. "
               'Indexierung und Suche senden Texte an diesen Anbieter und können API-Kosten verursachen.')
    if st.button('Vorhandene Themen indexieren', key='index_existing'):
        try:
            with st.spinner('Wissensbasis für die Suche vorbereiten …'):
                result = index_existing_notes()
            st.success(f"{result['indexed']} Themen indexiert, {result['skipped']} bereits aktuell.")
            st.session_state.pop('search_results', None)
        except (EmbeddingError, SearchError, ValueError) as error:
            st.error(str(error))
        except (OSError, sqlite3.Error):
            st.error('Indexierung fehlgeschlagen. Markdown-Dateien und lokale Datenbank prüfen.')
    courses = sorted({row['course'] or 'Ohne Kurs' for row in load_notes()})
    with st.form('semantic_search'):
        query = st.text_input('Wonach möchtest du suchen?')
        course = st.selectbox('Kursfilter', [None] + courses,
                              format_func=lambda value: 'Alle Kurse' if value is None else value)
        top_k = st.number_input('Anzahl Ergebnisse', min_value=1, max_value=20, value=5, step=1)
        submitted = st.form_submit_button('Suchen')
    if submitted:
        st.session_state.pop('search_results', None)
        try:
            with st.spinner('Relevante Knowledge Notes werden gesucht …'):
                st.session_state.search_results = retrieve(query, int(top_k), course)
        except (EmbeddingError, SearchError, ValueError) as error:
            st.error(str(error))
        except (OSError, sqlite3.Error):
            st.error('Suche fehlgeschlagen. Lokale Wissensbasis und Datenbank prüfen.')
    if 'search_results' in st.session_state:
        results = st.session_state.search_results
        if not results:
            st.info('Keine indexierten Themen gefunden. Bitte vorhandene Themen indexieren oder den Kursfilter ändern.')
        for rank, result in enumerate(results, start=1):
            st.markdown(f"**{rank}. {result['title']}**")
            st.text(f"Kurs: {result['course']} · Thema: {result['topic']} · Schwierigkeit: {result['difficulty']}")
            st.text(f"Quelle: {result['source_file']} · Seiten: " + ', '.join(map(str, result['source_pages'])))
            excerpt = ' '.join(line for line in result['content'].splitlines() if not line.startswith('#')).strip()
            st.write(excerpt[:350] + (' …' if len(excerpt) > 350 else ''))
            with st.expander(f"Vollständige Note anzeigen · Treffer {rank}"):
                st.text('Tags: ' + ', '.join(result['tags']))
                st.text('Verwandte Themen: ' + ', '.join(result['related_topics']))
                st.markdown(result['content'])
