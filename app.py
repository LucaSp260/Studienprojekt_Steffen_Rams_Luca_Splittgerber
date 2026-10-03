"""Streamlit navigation with persistent chat management."""
import sqlite3
import streamlit as st
from src.persistence.database import (create_chat, initialize_database, list_chats,
                                      set_chat_pinned, delete_chat)
from src.ui.documents import show_documents_page
from src.ui.settings import show_settings_page
from src.ui.knowledge import show_knowledge_page
from src.ui.brain import show_brain_page
from src.ui.learning import show_learning_page
from src.ui.page_heading import page_heading, protect_browser_translation

NAVIGATION = ["Lernen", "Meine Inhalte", "Übung erstellen", "Probeklausur erstellen",
              "Unterlagen", "Generierte Notes", "Wissensatlas", "Einstellungen"]
LEARNING_MODES = {
    "Lernen": "Lernchat",
    "Meine Inhalte": "Meine Inhalte",
    "Übung erstellen": "Übungen erstellen",
    "Probeklausur erstellen": "Probeklausur erstellen",
}
st.set_page_config(page_title="AI Learning Companion", page_icon="📚")

def activate_chat(chat_id):
    st.session_state.chat_id = chat_id
    st.session_state.navigation = "Lernen"


def add_new_chat():
    activate_chat(create_chat())


def main():
    protect_browser_translation()
    initialize_database()
    chats = list_chats()
    if not chats:
        create_chat()
        chats = list_chats()
    if st.session_state.get("chat_id") not in [chat["id"] for chat in chats]:
        st.session_state.chat_id = chats[0]["id"]
    with st.sidebar:
        # Streamlit stacks columns on narrow screens; chat actions stay next to their chat.
        st.html("""<style>
        [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {flex-wrap: nowrap;}
        [data-testid="stSidebar"] [data-testid="stColumn"] {min-width: 0 !important; flex: 1 1 0 !important;}
        [data-testid="stSidebar"] [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:nth-child(3)) > [data-testid="stColumn"] {flex: 0 0 36px !important;}
        [data-testid="stSidebar"] [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:nth-child(3)) > [data-testid="stColumn"]:first-child {flex: 1 1 0 !important;}
        [data-testid="stSidebar"] [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]:nth-child(3)) > [data-testid="stColumn"]:nth-child(n+2) button {padding: 0.25rem !important;}
        </style>""")
        page = st.radio("Navigation", NAVIGATION, key="navigation")
        st.divider()
        st.subheader("Chats")
        st.button("Chat hinzufügen", use_container_width=True, key="add_chat", on_click=add_new_chat)
        with st.container(height=min(360, 80 + 54 * len(chats)), border=False, key="sidebar_chats"):
            for chat in chats:
                select, pin, remove = st.columns([7, 1, 1])
                with select:
                    st.button(chat["title"], key=f"chat_{chat['id']}",
                        type="primary" if chat["id"] == st.session_state.chat_id else "secondary",
                        use_container_width=True, on_click=activate_chat, args=(chat["id"],))
                with pin:
                    if st.button("📌" if chat["pinned"] else "📍", key=f"pin_chat_{chat['id']}",
                                 help="Entpinnen" if chat["pinned"] else "Anpinnen"):
                        set_chat_pinned(chat["id"], not chat["pinned"])
                        st.rerun()
                with remove:
                    if st.button("×", key=f"delete_chat_{chat['id']}", help="Chat löschen"):
                        st.session_state.delete_chat_id = chat["id"]
                if st.session_state.get("delete_chat_id") == chat["id"]:
                    st.warning(f'Chat „{chat["title"]}“ mit allen Nachrichten löschen?')
                    yes, no = st.columns(2)
                    if yes.button("Löschen bestätigen", key=f"confirm_chat_{chat['id']}"):
                        delete_chat(chat["id"])
                        st.session_state.pop("delete_chat_id", None)
                        st.rerun()
                    if no.button("Abbrechen", key=f"cancel_chat_{chat['id']}"):
                        st.session_state.pop("delete_chat_id", None)
                        st.rerun()
    pages = {"Unterlagen": show_documents_page, "Einstellungen": show_settings_page,
             "Generierte Notes": show_knowledge_page, "Wissensatlas": show_brain_page}
    # A fresh page container prevents translated DOM nodes from an older page
    # from being reused with a stale heading during Streamlit navigation.
    with st.container(key=f"page_{NAVIGATION.index(page)}"):
        page_heading(page)
        if page in pages:
            pages[page]()
        else:
            show_learning_page(st.session_state.chat_id, LEARNING_MODES[page])

try:
    main()
except (sqlite3.Error, OSError):
    st.error("Die lokalen Daten konnten nicht geöffnet oder gespeichert werden. Bitte Speicherplatz und Schreibrechte prüfen.")
