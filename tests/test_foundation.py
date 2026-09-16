import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from src.persistence import database as db

ROOT = Path(__file__).resolve().parents[1]

class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'data' / 'application.db'
        self.override = patch.object(db, 'DATABASE_PATH', self.path)
        self.override.start()
        self.chat_override = patch(
            'src.ui.learning.answer_question',
            return_value={'answer': 'Gespeicherte Testantwort', 'sources': [],
                          'insufficient_information': False},
        )
        self.chat_override.start()
        db.initialize_database()

    def tearDown(self):
        self.chat_override.stop()
        self.override.stop()
        self.temp.cleanup()

    def test_schema(self):
        db.initialize_database()
        self.assertTrue(self.path.exists())
        connection = db.get_connection()
        try:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {
                'chats', 'messages', 'documents', 'exams', 'knowledge_notes', 'generated_artifacts'
            })
            self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO messages (chat_id, role, content) VALUES (999, 'user', 'test')")
        finally:
            connection.close()

    def test_process_restart(self):
        chat_id = db.create_chat()
        content = "Nachricht mit Umlauten äöü und ' Apostroph"
        db.save_message(chat_id, content)
        code = ('from pathlib import Path; from src.persistence import database as d; '
                f'd.DATABASE_PATH = Path({str(self.path)!r}); d.initialize_database(); '
                f'assert d.list_chats()[0]["id"] == {chat_id}; '
                f'assert d.load_messages({chat_id})[0]["content"] == {content!r}')
        subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True)
        with self.assertRaises(ValueError):
            db.save_message(chat_id, '   ')

    def test_ui_restart_and_chat_selection(self):
        app = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        app.chat_input[0].set_value('Erste Nachricht').run()
        first_id = db.list_chats()[0]['id']
        app.button[0].click().run()
        self.assertEqual(len(db.list_chats()), 2)
        app.chat_input[0].set_value('Zweiter Chat').run()
        app.button(key=f'chat_{first_id}').click().run()
        self.assertEqual(app.chat_message[0].markdown[0].value, 'Erste Nachricht')
        restarted = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=30)
        self.assertEqual(len(restarted.exception), 0)
        self.assertEqual(restarted.chat_message[0].markdown[0].value, 'Zweiter Chat')
        restarted.button(key=f'chat_{first_id}').click().run()
        self.assertEqual(restarted.chat_message[0].markdown[0].value, 'Erste Nachricht')
        for page in ['Unterlagen', 'Wissensbasis', 'Einstellungen']:
            restarted.radio[0].set_value(page).run()
            self.assertEqual(len(restarted.exception), 0)

if __name__ == '__main__':
    unittest.main()
