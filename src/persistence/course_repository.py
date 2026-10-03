"""Course names with case-insensitive identity, using the existing SQLite store."""
import unicodedata
from contextlib import closing
from src.persistence.database import get_connection

def clean_course(name):
    name = " ".join(unicodedata.normalize("NFKC", name).split())
    if not name or len(name) > 120 or any(ord(c) < 32 for c in name):
        raise ValueError("Bitte einen gültigen Kursnamen mit höchstens 120 Zeichen eingeben.")
    return name

def list_courses(connection=None):
    def values(conn):
        rows = conn.execute("""SELECT name FROM courses UNION SELECT course FROM documents
            UNION SELECT course FROM brain_concepts UNION SELECT course FROM brain_proposals
            UNION SELECT course FROM generated_artifacts""").fetchall()
        names = {}
        for row in rows:
            if row[0] and row[0].strip():
                names.setdefault(row[0].casefold(), row[0])
        return sorted(names.values(), key=str.casefold)
    if connection is not None:
        return values(connection)
    with closing(get_connection()) as conn:
        return values(conn)

def resolve_course(name, connection=None):
    name = clean_course(name)
    for existing in list_courses(connection):
        if existing.casefold() == name.casefold():
            return existing
    return name

def ensure_course(name, connection):
    name = resolve_course(name, connection)
    connection.execute("INSERT OR IGNORE INTO courses(name,name_key) VALUES (?,?)",
                       (name, name.casefold()))
    return name

def create_course(name):
    name = clean_course(name)
    with closing(get_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if any(value.casefold() == name.casefold() for value in list_courses(conn)):
            raise ValueError("Ein Kurs mit diesem Namen existiert bereits.")
        return ensure_course(name, conn)
