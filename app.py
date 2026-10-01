"""Oberfläche mit persistenten Chats, PDF-Unterlagen und Knowledge Notes."""

import sqlite3

import streamlit as st

from src.persistence.database import (
    create_chat, initialize_database, list_chats,
)
from src.ui.documents import show_documents_page
from src.ui.settings import show_settings_page
from src.ui.knowledge import show_knowledge_page
from src.ui.brain import show_brain_page
from src.ui.learning import show_learning_page

st.set_page_config(page_title="AI Learning Companion", page_icon="📚")


def main():
    initialize_database()
    chats = list_chats()
    if not chats:
        create_chat()
        chats = list_chats()
    if st.session_state.get("chat_id") not in [chat["id"] for chat in chats]:
        st.session_state.chat_id = chats[0]["id"]

    with st.sidebar:
        if st.button("+ Neuer Chat", use_container_width=True):
            st.session_state.chat_id = create_chat()
            st.rerun()
        st.subheader("Letzte Chats")
        for chat in chats:
            if st.button(chat["title"], key=f"chat_{chat['id']}",
                         type="primary" if chat["id"] == st.session_state.chat_id else "secondary",
                         use_container_width=True):
                st.session_state.chat_id = chat["id"]
                st.rerun()
        page = st.radio("Navigation", ["Lernen", "Unterlagen", "Wissensbasis", "Second Brain", "Einstellungen"])

    st.title("AI Learning Companion")
    st.write("Persönliche KI-Lernumgebung auf Basis deiner Studienunterlagen.")
    if page == "Unterlagen":
        show_documents_page()
        return
    if page == "Einstellungen":
        show_settings_page()
        return
    if page == "Wissensbasis":
        show_knowledge_page()
        return
    if page == "Second Brain":
        show_brain_page()
        return

    show_learning_page(st.session_state.chat_id)


try:
    main()
except (sqlite3.Error, OSError):
    st.error("Die Datenbank konnte nicht geöffnet oder gespeichert werden. "
             "Bitte prüfe den freien Speicherplatz und die Schreibrechte im Ordner data.")
