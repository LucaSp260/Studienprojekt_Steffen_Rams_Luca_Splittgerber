"""Lernseite für Chat, explizite Agent-Läufe und gespeicherte Artefakte."""

import sqlite3
from contextlib import closing
from pathlib import Path

import streamlit as st

from src.agent_layer.exam_agent import create_exam
from src.agent_layer.exercise_agent import create_exercises
from src.agent_layer.learning_chat import answer_question
from src.agent_layer.models import AgentError, ExamRequest, ExerciseRequest
from src.data_layer.pdf_loader import render_pdf_page
from src.knowledge_layer.embedding_service import EmbeddingError
from src.knowledge_layer.markdown_store import load_note
from src.knowledge_layer.vector_store import SearchError
from src.llm.base_provider import LLMError
from src.persistence.artifact_repository import load_artifact, load_artifacts
from src.persistence.database import get_connection, load_messages, message_metadata, save_chat_exchange
from src.persistence.knowledge_repository import load_notes

DIFFICULTIES = ["leicht", "mittel", "schwer", "gemischt"]
EXERCISE_TYPES = ["Offene Frage", "Verständnisfrage", "Anwendungsaufgabe", "Multiple Choice", "Gemischt"]


def show_learning_page(chat_id):
    mode = st.selectbox(
        "Lernmodus", ["Lernchat", "Übungen erstellen", "Probeklausur erstellen", "Meine Inhalte"]
    )
    if mode == "Lernchat":
        _show_chat(chat_id)
    elif mode == "Übungen erstellen":
        _show_exercise_form()
    elif mode == "Probeklausur erstellen":
        _show_exam_form()
    else:
        _show_history()


def _show_chat(chat_id):
    courses = _courses()
    selected = st.selectbox("Kurs", [None] + courses, key="chat_course",
                            format_func=lambda value: "Alle Kurse" if value is None else value)
    messages = load_messages(chat_id)
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            metadata = message_metadata(message)
            _render_chat_sources(metadata.get("sources", []))
    if content := st.chat_input("Frage an deine Wissensbasis"):
        if content.strip():
            try:
                with st.spinner("Wissensbasis wird durchsucht und die Antwort erstellt..."):
                    result = answer_question(content, selected, messages)
                    save_chat_exchange(
                        chat_id, content, result["answer"],
                        {"sources": result["sources"], "course": selected,
                         "insufficient_information": result["insufficient_information"]},
                    )
                st.rerun()
            except Exception as error:
                _show_agent_error(error)
        else:
            st.warning("Bitte eine Nachricht eingeben.")


def _render_chat_sources(sources):
    if not sources:
        return
    st.caption("Quellen:")
    for source in sources:
        pages = ", ".join(str(page) for page in source.get("source_pages", []))
        label = f"{source.get('source_file', 'Unbekannte Quelle')} · Seiten {pages}"
        with st.expander(label):
            try:
                details = _load_chat_source(source["knowledge_note_id"])
                st.markdown(f"**Knowledge Note: {details['title']}**")
                st.caption("Zusammengefasster Kontext, den der Chat erhalten hat")
                st.text(details["note_text"])
                st.markdown("**Originalfolie aus der PDF**")
                st.caption("Angezeigt werden die in der Knowledge Note referenzierten Seiten. Eine einzelne Textstelle ist nicht automatisch markiert.")
                for page_number in source.get("source_pages", []):
                    st.image(
                        _render_source_pdf_page(details["pdf_path"], details["modified_ns"], page_number),
                        caption=f"PDF-Seite {page_number}", use_container_width=True,
                    )
            except (KeyError, OSError, ValueError, sqlite3.Error):
                st.warning("Die Quelle ist gespeichert, aber die Knowledge Note oder PDF-Fundstelle kann gerade nicht gelesen werden.")


def _load_chat_source(note_id):
    with closing(get_connection()) as connection:
        row = connection.execute(
            """SELECT n.title, n.markdown_path, d.file_path
               FROM knowledge_notes n JOIN documents d ON d.id = n.document_id
               WHERE n.id = ?""", (note_id,)
        ).fetchone()
    if row is None:
        raise ValueError("Die Knowledge Note wurde nicht gefunden.")
    _, note_text = load_note(row["markdown_path"])
    project_path = Path(__file__).resolve().parents[2]
    pdf_path = (project_path / row["file_path"]).resolve()
    if not pdf_path.is_relative_to((project_path / "user_data").resolve()):
        raise ValueError("Die PDF-Quelle liegt nicht im Unterlagen-Ordner.")
    if not pdf_path.is_file():
        raise OSError("Die PDF-Quelle fehlt.")
    return {"title": row["title"], "note_text": note_text, "pdf_path": str(pdf_path),
            "modified_ns": pdf_path.stat().st_mtime_ns}


@st.cache_data(show_spinner=False, max_entries=32)
def _render_source_pdf_page(pdf_path, modified_ns, page_number):
    # Cache key includes the file modification time and page number.
    del modified_ns
    return render_pdf_page(pdf_path, page_number)


def _courses():
    return sorted({row["course"] for row in load_notes() if row["course"]})


def _show_exercise_form():
    st.subheader("Übungen erstellen")
    courses = _courses()
    if not courses:
        st.info("Noch keine Knowledge Notes vorhanden. Verarbeite und indexiere zuerst Unterlagen.")
        return
    with st.form("exercise_agent"):
        course = st.selectbox("Kurs", courses, key="exercise_course")
        topic = st.text_input("Thema oder Beschreibung")
        count = st.number_input("Anzahl Aufgaben", min_value=1, max_value=20, value=3, step=1)
        difficulty = st.selectbox("Schwierigkeit", DIFFICULTIES, index=1, key="exercise_difficulty")
        exercise_type = st.selectbox("Aufgabentyp", EXERCISE_TYPES, index=1)
        submitted = st.form_submit_button("Übungen erstellen")
    if submitted:
        try:
            request = ExerciseRequest(course=course, topic=topic, count=int(count),
                                      difficulty=difficulty, exercise_type=exercise_type)
            with st.status("Wissensbasis wird durchsucht...", expanded=True) as status:
                result = create_exercises(request, progress=lambda text: status.update(label=text))
                status.update(label="Übungen wurden erstellt und gespeichert.", state="complete")
            st.session_state.last_artifact_id = result["artifact_id"]
        except Exception as error:
            _show_agent_error(error)
    _show_last("exercise")


def _show_exam_form():
    st.subheader("Probeklausur erstellen")
    courses = _courses()
    if not courses:
        st.info("Noch keine Knowledge Notes vorhanden. Verarbeite und indexiere zuerst Unterlagen.")
        return
    with st.form("exam_agent"):
        course = st.selectbox("Kurs", courses, key="exam_course")
        duration = st.number_input("Dauer in Minuten", min_value=10, max_value=300, value=60, step=5)
        count = st.number_input("Anzahl Aufgaben", min_value=1, max_value=20, value=6, step=1,
                                key="exam_count")
        difficulty = st.selectbox("Schwierigkeit", DIFFICULTIES, index=3, key="exam_difficulty")
        focus = st.text_input("Thematischer Fokus (optional)")
        submitted = st.form_submit_button("Probeklausur erstellen")
    if submitted:
        try:
            request = ExamRequest(course=course, duration_minutes=int(duration), task_count=int(count),
                                  difficulty=difficulty, focus=focus)
            with st.status("Wissensbasis wird durchsucht...", expanded=True) as status:
                result = create_exam(request, progress=lambda text: status.update(label=text))
                status.update(label="Probeklausur wurde erstellt und gespeichert.", state="complete")
            st.session_state.last_artifact_id = result["artifact_id"]
        except Exception as error:
            _show_agent_error(error)
    _show_last("exam")


def _show_agent_error(error):
    if isinstance(error, (AgentError, LLMError, EmbeddingError, SearchError, ValueError)):
        st.error(str(error))
    elif isinstance(error, (sqlite3.Error, OSError)):
        st.error("Das Lernartefakt konnte nicht gespeichert oder geladen werden.")
    else:
        st.error("Die Generierung ist unerwartet fehlgeschlagen. Bitte erneut versuchen.")


def _show_last(expected_type):
    artifact_id = st.session_state.get("last_artifact_id")
    if artifact_id is None:
        return
    try:
        artifact = load_artifact(artifact_id)
    except (sqlite3.Error, ValueError, OSError):
        st.error("Das gespeicherte Lernartefakt konnte nicht geladen werden.")
        return
    if artifact and artifact["artifact_type"] == expected_type:
        _render_artifact(artifact)


def _show_history():
    st.subheader("Meine Probeklausuren")
    try:
        exams = load_artifacts("exam")
        exercises = load_artifacts("exercise")
    except (sqlite3.Error, ValueError, OSError):
        st.error("Gespeicherte Lernartefakte konnten nicht geladen werden.")
        return
    _history_group(exams, "exam")
    st.subheader("Meine Übungen")
    _history_group(exercises, "exercise")
    artifact_id = st.session_state.get("opened_artifact_id")
    if artifact_id is not None:
        artifact = load_artifact(artifact_id)
        if artifact:
            _render_artifact(artifact)


def _history_group(artifacts, prefix):
    if not artifacts:
        st.info("Noch keine gespeicherten Inhalte vorhanden.")
    for artifact in artifacts:
        label = f"{artifact['title']} · {artifact['created_at']} UTC"
        if st.button(label, key=f"open_{prefix}_{artifact['id']}"):
            st.session_state.opened_artifact_id = artifact["id"]


def _render_artifact(row):
    content = row["content"]
    st.divider()
    st.subheader(content["title"])
    status = content.get("critic", {}).get("status")
    if status == "approved":
        st.success("Vom Critic Agent geprüft.")
    elif status == "needs_revision":
        st.info("Der Critic Agent hat eine überarbeitete Fassung geliefert.")
    else:
        st.warning("Die Qualitätsprüfung ist fehlgeschlagen; dieser Entwurf ist ungeprüft.")
    sources = {source["source_id"]: source for source in content.get("sources", [])}
    if row["artifact_type"] == "exam":
        estimated = content.get("estimated_total_minutes")
        if estimated is None:
            estimated = sum(item.get("estimated_minutes", 0) for item in content["tasks"])
        st.text(f"Kurs: {content['course']} · Geplant: {content['duration_minutes']} Minuten · "
                f"Geschätzt: {estimated} Minuten · Gesamtpunkte: {content['total_points']}")
        items = content["tasks"]
    else:
        st.text(f"Kurs: {content['course']}")
        items = content["exercises"]
    for item in items:
        st.markdown(f"### Aufgabe {item['number']} · {item['title']}")
        details = f"{item['difficulty'].capitalize()} · {item['exercise_type']}"
        if item.get("estimated_minutes"):
            details += f" · ca. {item['estimated_minutes']} Min."
        if row["artifact_type"] == "exam":
            details += f" · {item['points']} Punkte"
        st.caption(details)
        st.write(item["task"])
        for index, subtask in enumerate(item.get("subtasks", [])):
            if isinstance(subtask, dict):
                label, text = subtask.get("id", str(index + 1)), subtask.get("text", "")
            else:
                label, text = (chr(ord("a") + index) if index < 26 else str(index + 1)), subtask
            st.markdown(f"**{label})** {text}")
        if item.get("choices"):
            for choice in item["choices"]:
                st.write(f"- {choice}")
        _render_sources(item["source_ids"], sources)
        with st.expander(f"Kurzlösung zu Aufgabe {item['number']} anzeigen"):
            answer_items = item.get("short_answer_items", [])
            if answer_items:
                for answer in answer_items:
                    st.markdown(f"**{answer['id']})** {answer['answer']}")
            else:
                short_solution = item.get("short_solution") or item.get(
                    "solution", "Keine Lösung gespeichert."
                )
                st.write(short_solution)
        with st.expander(f"Erklärung zu Aufgabe {item['number']} anzeigen"):
            explanation_items = item.get("explanation_items", [])
            if explanation_items:
                for explanation in explanation_items:
                    st.markdown(f"**{explanation['id']})**")
                    st.write(explanation["explanation"])
            else:
                explanation = item.get("explanation") or item.get(
                    "solution", "Keine Erklärung gespeichert."
                )
                st.write(explanation)


def _render_sources(source_ids, sources):
    st.caption("Quellen:")
    for source_id in source_ids:
        source = sources.get(source_id)
        if source:
            pages = ", ".join(str(page) for page in source["source_pages"])
            st.caption(f"- {source['source_file']} · Seiten {pages}")
