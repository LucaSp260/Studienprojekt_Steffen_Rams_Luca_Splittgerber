"""PDFs technisch lesen, ohne Text als Knowledge Notes zu speichern."""

from pathlib import Path

import pymupdf


def read_pdf(source):
    """Liefert Seitenzahl, Seitentexte, Gesamttext und ggf. einen Hinweis."""
    try:
        content = source if isinstance(source, bytes) else Path(source).read_bytes()
    except OSError as error:
        raise ValueError("Die PDF-Datei konnte nicht gelesen werden.") from error
    if not content:
        raise ValueError("Die Datei ist leer und enthält keine PDF.")
    if b"%PDF-" not in content[:1024]:
        raise ValueError("Die Datei ist keine gültige PDF.")
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass:
                raise ValueError("Die PDF ist passwortgeschützt und kann nicht gelesen werden.")
            if document.page_count == 0:
                raise ValueError("Die PDF enthält keine Seiten.")
            pages = [page.get_text() for page in document]
            text = "\n".join(pages)
            return {
                "page_count": document.page_count,
                "pages": pages,
                "text": text,
                "warning": "Die PDF enthält keinen extrahierbaren Text (z. B. ein Scan). "
                           "Das Original wird gespeichert; Texterkennung ist noch nicht verfügbar."
                           if not text.strip() else "",
            }
    except (RuntimeError, pymupdf.FileDataError) as error:
        raise ValueError("Fehler beim Lesen: Die PDF ist beschädigt oder nicht lesbar.") from error


def get_page_count(source):
    return read_pdf(source)["page_count"]


def extract_page_texts(source):
    return read_pdf(source)["pages"]


def extract_text(source):
    return read_pdf(source)["text"]
