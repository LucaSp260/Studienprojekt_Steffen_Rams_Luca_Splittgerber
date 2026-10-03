"""Upload, processing and consistent course management for original PDFs."""
import sqlite3
import streamlit as st
from src.data_layer.document_manager import add_document
from src.data_layer.document_deletion import delete_document
from src.knowledge_layer.knowledge_manager import process_document
from src.knowledge_layer.course_manager import rename_course
from src.persistence.course_repository import list_courses, resolve_course
from src.persistence.document_repository import load_documents
from src.persistence.brain_repository import pending_brain_notes

NEW_COURSE = "+ Neuen Kurs anlegen"

def show_documents_page():
    courses = list_courses()
    st.subheader("Neue Unterlagen hinzufügen")
    choice = st.selectbox("Kurs für neue Unterlagen", courses + [NEW_COURSE], key="upload_course")
    with st.form("document_upload"):
        course = st.text_input("Neuer Kursname", placeholder="Zum Beispiel Datenbanken", key="new_course_name") if choice == NEW_COURSE else choice
        uploads = st.file_uploader("PDF-Dateien", type=["pdf"], accept_multiple_files=True)
        submitted = st.form_submit_button("Unterlagen hinzufügen")
    if submitted:
        try:
            upload_course = resolve_course(course)
            if not uploads:
                raise ValueError("Bitte mindestens eine PDF-Datei auswählen.")
            for upload in uploads:
                result = add_document(upload, upload_course)
                {"success": st.success, "duplicate": st.info}.get(result["status"], st.error)(result["message"])
                if result.get("warning"):
                    st.warning(result["warning"])
                if result["status"] == "success":
                    with st.status(f"{upload.name} wird verarbeitet …", expanded=True) as status:
                        try:
                            processed = process_document(
                                result["document_id"], progress=lambda text: status.update(label=text))
                            status.update(label=processed["message"], state="complete")
                        except Exception as error:
                            status.update(label="PDF gespeichert; Verarbeitung noch nicht abgeschlossen.", state="error")
                            st.error(str(error) or "Die Verarbeitung konnte nicht abgeschlossen werden.")
                            st.caption("Du kannst die Verarbeitung unten bei diesem Dokument fortsetzen.")
        except ValueError as error:
            st.error(str(error))
    if courses:
        with st.expander("Kurs umbenennen"):
            with st.form("rename_course"):
                old = st.selectbox("Bestehender Kurs", courses, key="rename_course_old")
                new = st.text_input("Neuer Name")
                submitted = st.form_submit_button("Kurs umbenennen")
            if submitted:
                try:
                    rename_course(old, new)
                    # Clear stale selections; widgets will reinitialize from current courses.
                    for key in list(st.session_state):
                        if key.endswith("course") or key in {"opened_artifact_id", "brain_selected_concept"}:
                            st.session_state.pop(key, None)
                    st.session_state.course_renamed = "Der Kurs wurde in allen lokalen Metadaten umbenannt."
                    st.rerun()
                except Exception as error:
                    st.error(str(error) if isinstance(error, (ValueError, RuntimeError))
                             else "Umbenennung fehlgeschlagen und zurückgenommen. Bitte lokale Schreibrechte prüfen.")
    if message := st.session_state.pop("course_renamed", None):
        st.success(message)
    st.subheader("Meine Unterlagen")
    selected = st.selectbox("Kursfilter", [None] + list_courses(), key="documents_course",
                           format_func=lambda value: "Alle Kurse" if value is None else value)
    documents = load_documents(course=selected)
    if not documents:
        st.info("Noch keine Unterlagen in dieser Auswahl.")
    grouped = {}
    for document in documents:
        grouped.setdefault(document["course"] or "Ohne Kurs", []).append(document)
    for course, course_documents in grouped.items():
        with st.container(key=f"documents_group_{course}"):
            st.subheader(course)
            for document in course_documents:
                with st.container(key=f"document_row_{document['id']}"):
                    pages = (f"{document['page_count']} {'Seite' if document['page_count'] == 1 else 'Seiten'}"
                             if document["page_count"] is not None else "Seitenzahl unbekannt")
                    st.markdown(f"**{document['filename']}** · {pages}")
                    st.caption("Verarbeitet" if document["processed"] else "Noch nicht verarbeitet")
                    retry = bool(document["processed"] and pending_brain_notes(document["id"]))
                    if not document["processed"] or retry:
                        label = "Wissensatlas ergänzen" if retry else "Verarbeitung fortsetzen"
                        if st.button(label, key=f"process_{document['id']}"):
                            try:
                                with st.status("Dokument wird verarbeitet …", expanded=True) as status:
                                    result = process_document(document["id"], progress=lambda text: status.update(label=text))
                                    status.update(label=result["message"], state="complete")
                                st.rerun()
                            except Exception as error:
                                st.error(str(error) or "Die Verarbeitung konnte nicht abgeschlossen werden.")
                                st.caption("Bereits erzeugte Notes bleiben erhalten. Ein erneuter Versuch setzt die Verarbeitung fort.")
                    if st.button("PDF löschen", key=f"delete_document_{document['id']}"):
                        st.session_state.delete_document_id = document["id"]
                    if st.session_state.get("delete_document_id") == document["id"]:
                        st.warning(f"„{document['filename']}“ aus {course} löschen? Zugehörige Notes, "
                                   "Suchindexeinträge und unbelegte Verbindungen werden ebenfalls entfernt.")
                        yes, no = st.columns(2)
                        if yes.button("Löschen bestätigen", key=f"confirm_document_{document['id']}"):
                            try:
                                result = delete_document(document["id"])
                                st.session_state.pop("delete_document_id", None)
                                st.session_state.document_deleted = (
                                    f"„{document['filename']}“ und {result['deleted_notes']} zugehörige Notes wurden gelöscht."
                                )
                                st.rerun()
                            except Exception as error:
                                st.error(str(error) or "Das Dokument konnte nicht gelöscht werden.")
                        if no.button("Abbrechen", key=f"cancel_document_{document['id']}"):
                            st.session_state.pop("delete_document_id", None)
                            st.rerun()
    if message := st.session_state.pop("document_deleted", None):
        st.success(message)
