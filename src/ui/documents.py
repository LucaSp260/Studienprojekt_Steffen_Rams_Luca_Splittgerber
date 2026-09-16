"""Upload und Übersicht der lokal gespeicherten Originalunterlagen."""

import streamlit as st

from src.data_layer.document_manager import add_document
from src.persistence.document_repository import load_documents


def show_documents_page():
    st.subheader("Neue Unterlagen hinzufügen")
    with st.form("document_upload"):
        course = st.text_input("Kurs", placeholder="Zum Beispiel Datenbanken")
        uploads = st.file_uploader("PDF-Dateien", type=["pdf"], accept_multiple_files=True)
        submitted = st.form_submit_button("Unterlagen hinzufügen")
    if submitted:
        if not course.strip():
            st.error("Bitte einen Kurs angeben.")
        elif not uploads:
            st.error("Bitte mindestens eine PDF-Datei auswählen.")
        else:
            for upload in uploads:
                result = add_document(upload, course)
                if result["status"] == "success":
                    st.success(result["message"])
                elif result["status"] == "duplicate":
                    st.info(result["message"])
                else:
                    st.error(f"{upload.name}: {result['message']}")
                if result.get("warning"):
                    st.warning(result["warning"])

    st.subheader("Meine Unterlagen")
    documents = load_documents()
    if not documents:
        st.info("Noch keine Unterlagen vorhanden. Lade oben deine erste PDF hoch.")
    current_course = None
    for document in documents:
        course = document["course"] or "Ohne Kurs"
        if course != current_course:
            st.subheader(course)
            current_course = course
        pages = f"{document['page_count']} Seiten" if document["page_count"] is not None else "Seitenzahl unbekannt"
        st.text(f"{document['filename']} – {pages}")
        status = "Verarbeitet" if document["processed"] else "Noch nicht in Wissensbasis verarbeitet"
        st.caption(f"Hochgeladen: {document['created_at']} UTC · {status}")
