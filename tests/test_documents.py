"""Tests mit selbst erzeugten PDFs, ohne persönliche Studienunterlagen."""
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf
from streamlit.testing.v1 import AppTest

from src.data_layer import document_manager as manager
from src.data_layer.file_hash import calculate_file_hash
from src.data_layer.pdf_loader import read_pdf, get_page_count, extract_page_texts, extract_text
from src.persistence import database as db
from src.persistence import document_repository as repository

ROOT = Path(__file__).resolve().parents[1]


def make_pdf(text='Datenbanken: SELECT und Tabellen', pages=2):
    with pymupdf.open() as document:
        for _ in range(pages):
            page = document.new_page()
            if text:
                page.insert_text((72, 72), text)
        return document.tobytes()


def upload(content, name='test.pdf'):
    return SimpleNamespace(name=name, getvalue=lambda: content)


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / 'data' / 'application.db'
        self.storage = self.root / 'user_data'
        self.db_patch = patch.object(db, 'DATABASE_PATH', self.database_path)
        self.storage_patch = patch.object(manager, 'USER_DATA_PATH', self.storage)
        self.db_patch.start()
        self.storage_patch.start()
        db.initialize_database()
        self.pdf = make_pdf()

    def tearDown(self):
        self.storage_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def files(self):
        return list(self.storage.rglob('*.pdf'))

    def test_hash_and_pdf_text(self):
        path = self.root / 'test.pdf'
        path.write_bytes(self.pdf)
        self.assertEqual(calculate_file_hash(path), calculate_file_hash(self.pdf))
        self.assertNotEqual(calculate_file_hash(self.pdf), calculate_file_hash(make_pdf('Anderer Inhalt')))
        self.assertEqual(get_page_count(path), 2)
        self.assertEqual(len(extract_page_texts(self.pdf)), 2)
        self.assertIn('SELECT', extract_text(path))

    def test_upload_and_duplicates(self):
        result = manager.add_document(upload(self.pdf), 'Datenbanken')
        self.assertEqual(result['status'], 'success')
        for name in ['test.pdf', 'Umbenannt.pdf']:
            result = manager.add_document(upload(self.pdf, name), 'Anderer Kurs')
            self.assertEqual(result['status'], 'duplicate')
            self.assertIn('nicht erneut gespeichert', result['message'])
        documents = repository.load_documents()
        self.assertEqual(len(documents), 1)
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(self.files()[0].read_bytes(), self.pdf)
        self.assertEqual(documents[0]['page_count'], 2)
        self.assertEqual(documents[0]['processed'], 0)
        self.assertTrue((self.root / documents[0]['file_path']).exists())

    def test_same_name_and_separate_courses(self):
        for content, course in [(self.pdf, 'Datenbanken'), (make_pdf('Neu'), 'Datenbanken'),
                                (make_pdf('Software'), 'Software Engineering')]:
            self.assertEqual(manager.add_document(upload(content), course)['status'], 'success')
        self.assertEqual(len(self.files()), 3)
        self.assertEqual(len(repository.load_documents('Datenbanken')), 2)
        self.assertEqual(len(repository.load_documents('Software Engineering')), 1)
        self.assertTrue((self.storage / 'datenbanken' / 'test_1.pdf').exists())
        self.assertTrue((self.storage / 'software_engineering' / 'test.pdf').exists())

    def test_paths_are_safe(self):
        result = manager.add_document(upload(self.pdf, '../../CON.pdf'), '../../CON')
        self.assertEqual(result['status'], 'success')
        self.assertTrue(self.files()[0].resolve().is_relative_to(self.storage.resolve()))
        self.assertEqual(manager.safe_name('NUL', 'kurs'), '_NUL')
        self.assertNotIn('/', manager.safe_name('../a/b:*?', 'kurs'))

    def test_invalid_empty_and_password_pdf(self):
        for content, name, course in [(b'not a PDF', 'test.pdf', 'Kurs'),
                                       (b'%PDF-1.7\nbroken', 'test.pdf', 'Kurs'),
                                       (b'', 'test.pdf', 'Kurs'),
                                       (self.pdf, 'test.txt', 'Kurs'),
                                       (self.pdf, 'test.pdf', '  ')]:
            with self.subTest(name=name, content=content[:20]):
                result = manager.add_document(upload(content, name), course)
                self.assertEqual(result['status'], 'error')
        with pymupdf.open(stream=self.pdf, filetype='pdf') as document:
            encrypted = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                                        owner_pw='owner', user_pw='secret')
        result = manager.add_document(upload(encrypted), 'Kurs')
        self.assertEqual(result['status'], 'error')
        self.assertIn('passwortgeschützt', result['message'])
        self.assertEqual(repository.load_documents(), [])
        self.assertEqual(self.files(), [])

    def test_pdf_without_text(self):
        content = make_pdf('', pages=1)
        self.assertTrue(read_pdf(content)['warning'])
        result = manager.add_document(upload(content), 'Scans')
        self.assertEqual(result['status'], 'success')
        self.assertIn('keinen extrahierbaren Text', result['warning'])
        self.assertEqual(repository.load_documents()[0]['processed'], 0)

    def test_write_and_database_errors(self):
        with patch.object(manager, 'store_new_file', side_effect=PermissionError):
            result = manager.add_document(upload(self.pdf), 'Kurs')
        self.assertEqual(result['status'], 'error')
        with patch.object(manager, 'save_document', side_effect=sqlite3.OperationalError):
            result = manager.add_document(upload(self.pdf), 'Kurs')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(repository.load_documents(), [])
        self.assertEqual(self.files(), [])

    def test_simultaneous_duplicate(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: manager.add_document(upload(self.pdf), 'Kurs'), range(2)))
        self.assertEqual(sorted(result['status'] for result in results), ['duplicate', 'success'])
        self.assertEqual(len(repository.load_documents()), 1)
        self.assertEqual(len(self.files()), 1)

    def test_restart_in_another_process(self):
        manager.add_document(upload(self.pdf), 'Datenbanken')
        code = ('from pathlib import Path; from src.persistence import database as d; '
                f'd.DATABASE_PATH = Path({str(self.database_path)!r}); d.initialize_database(); '
                'from src.persistence.document_repository import load_documents; '
                'rows = load_documents(); assert len(rows) == 1; '
                f'assert (Path({str(self.root)!r}) / rows[0]["file_path"]).exists(); '
                'assert rows[0]["processed"] == 0')
        subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True)

    def test_legacy_migration_preserves_records(self):
        old_path = self.root / 'legacy.db'
        connection = sqlite3.connect(old_path)
        connection.executescript('''
            CREATE TABLE documents (id INTEGER PRIMARY KEY, filename TEXT NOT NULL,
                file_hash TEXT NOT NULL, course TEXT, file_path TEXT NOT NULL,
                processed INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO documents (filename, file_hash, course, file_path)
                VALUES ('alt.pdf', 'legacy', 'Kurs', 'user_data/kurs/alt.pdf');
        ''')
        connection.close()
        with patch.object(db, 'DATABASE_PATH', old_path):
            db.initialize_database()
            db.initialize_database()
            row = repository.load_documents()[0]
            self.assertEqual(row['filename'], 'alt.pdf')
            self.assertIsNone(row['page_count'])
            repository.update_document_status(row['id'], True)
            self.assertEqual(repository.load_documents()[0]['processed'], 1)

    def test_upload_ui_and_restart(self):
        # AppTest unterstützt Datei-Uploads noch nicht als bedienbares Widget.
        # Nur die Dateiauswahl wird ersetzt; Formular, Speichern und UI laufen real.
        with patch('src.ui.documents.st.file_uploader', return_value=[upload(self.pdf)]), \
             patch('src.ui.documents.process_document', return_value={'message':'Verarbeitet'}) as process:
            app = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=30)
            app.radio[0].set_value('Unterlagen').run()
            app.text_input[0].set_value('Datenbanken')
            app.button(key='FormSubmitter:document_upload-Unterlagen hinzufügen').click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.success), 1)
            process.assert_called_once()
            self.assertEqual(len(self.files()), 1)
            restarted = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=30)
            restarted.radio[0].set_value('Unterlagen').run()
            self.assertTrue(any('test.pdf' in item.value and '2 Seiten' in item.value for item in restarted.markdown))
            restarted.text_input[0].set_value('Datenbanken')
            restarted.button(key='FormSubmitter:document_upload-Unterlagen hinzufügen').click().run()
            self.assertTrue(any('bereits hochgeladen' in item.value for item in restarted.info))
            self.assertEqual(len(repository.load_documents()), 1)
            self.assertEqual(len(self.files()), 1)
            process.assert_called_once()


if __name__ == '__main__':
    unittest.main()
