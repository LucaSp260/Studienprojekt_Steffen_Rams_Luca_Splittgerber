"""Phase 3: nur Mock-LLM-Aufrufe und temporäre Daten."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from test_documents import make_pdf, upload
from src.data_layer import document_manager
from src.knowledge_layer import knowledge_manager as manager, markdown_store
from src.knowledge_layer.knowledge_extractor import build_chunks, extract_knowledge
from src.knowledge_layer.models import KnowledgeNote, ExtractionResult
from src.llm import config
from src.llm.base_provider import LLMError
from src.llm.llm_service import LLMService, ConnectionResult
from src.llm.openai_provider import OpenAIProvider
from src.llm.gemini_provider import GeminiProvider
from src.persistence import database as db
from src.persistence.document_repository import load_documents
from src.persistence.knowledge_repository import load_notes

ROOT = Path(__file__).resolve().parents[1]


def note_data(title='Primärschlüssel', pages=None):
    return dict(title=title, topic='Relationale Datenbanken', tags=['SQL', 'sql', 'Datenbanken'],
                difficulty='easy', source_pages=pages or [1], related_topics=['Fremdschlüssel'],
                definition='Ein Primärschlüssel identifiziert einen Datensatz eindeutig.',
                key_concepts=['Eindeutigkeit', 'Keine NULL-Werte'], example='Eine Kundennummer.',
                common_mistakes=['Primärschlüssel und Fremdschlüssel verwechseln.'],
                exam_relevance=['Geeignete Schlüssel erkennen und begründen.'])


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(manager, 'extend_brain', return_value={'concepts': 0, 'edges': 0, 'processed_notes': 0}),
                        patch.object(manager, 'index_existing_notes', side_effect=lambda document_id, **kwargs: self.mark_processed(document_id)), patch.object(db, 'DATABASE_PATH', self.root / 'data/application.db'),
                        patch.object(document_manager, 'USER_DATA_PATH', self.root / 'user_data'),
                        patch.object(manager, 'PROJECT_PATH', self.root),
                        patch.object(markdown_store, 'KNOWLEDGE_BASE_PATH', self.root / 'knowledge_base'),
                        patch.object(config, 'ENV_PATH', self.root / '.env'),
                        patch.dict(os.environ, {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "LLM_PROVIDER": "openai", "OPENAI_MODEL": "", "GEMINI_MODEL": ""})]
        for override in self.patches:
            override.start()
        db.initialize_database()
        self.document_id = document_manager.add_document(upload(make_pdf()), 'Datenbanken')['document_id']
        self.service = Mock(spec=LLMService)
        self.service.generate.return_value = {'notes': [note_data(), note_data('Fremdschlüssel')]}

    def mark_processed(self, document_id):
        # Phase-3-Tests isolieren die neue Embedding-Grenze. Die echten
        # Chroma-/Embedding-Abläufe werden in test_retrieval.py geprüft.
        from src.persistence.document_repository import update_document_status
        update_document_status(document_id, True)

    def tearDown(self):
        for override in reversed(self.patches):
            override.stop()
        self.temp.cleanup()

    def make_note(self, **changes):
        return KnowledgeNote(**(note_data() | {'course': 'Datenbanken', 'source_file': 'test.pdf'} | changes))

    def assert_unprocessed(self):
        self.assertFalse(load_documents()[0]['processed'])
        self.assertEqual(load_notes(), [])
        self.assertEqual(list((self.root / 'knowledge_base').rglob('*.md')), [])

    def test_validation_and_tags(self):
        self.assertEqual(self.make_note().tags, ['sql', 'datenbanken'])
        for field, value in [('title', ' '), ('topic', ''), ('tags', []), ('source_file', ''),
                             ('source_pages', [0]), ('source_pages', [True]), ('source_pages', ['1']),
                             ('definition', ''), ('difficulty', 'very hard')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                self.make_note(**{field: value})

    def test_markdown_yaml_safe_names_and_collisions(self):
        note = self.make_note(title='../CON: YAML # Thema', source_file='Quelle: ü.pdf')
        first = markdown_store.save_note(note)
        second = markdown_store.save_note(note)
        self.assertNotEqual(first, second)
        self.assertTrue(first.resolve().is_relative_to((self.root / 'knowledge_base').resolve()))
        metadata, body = markdown_store.load_note(first.relative_to(self.root))
        self.assertEqual(metadata['source_file'], 'Quelle: ü.pdf')
        self.assertEqual(metadata['source_pages'], [1])
        self.assertIn('## Definition', body)
        self.assertIn('## Prüfungsrelevante Aspekte', body)
        self.assertEqual(metadata['tags'], ['sql', 'datenbanken'])

    def test_chunk_limits_and_real_page_numbers(self):
        pages = ['a' * 45, '', 'b' * 30, 'c' * 10]
        chunks = build_chunks(pages, max_chars=20, max_pages=2)
        self.assertTrue(all(sum(len(text) for _, text in chunk) <= 20 for chunk in chunks))
        self.assertEqual(''.join(text for chunk in chunks for _, text in chunk), ''.join(pages))
        self.assertEqual({number for chunk in chunks for number, _ in chunk}, {1, 3, 4})
        chunks = build_chunks(['x'] * 20)
        self.assertTrue(all(len(chunk) <= 6 for chunk in chunks))

    def test_multiple_notes_registration_and_status(self):
        result = manager.process_document(self.document_id, self.service)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(load_notes(document_id=self.document_id)), 2)
        self.assertEqual(len(load_notes(course='Datenbanken')), 2)
        self.assertTrue(load_documents()[0]['processed'])
        for row in load_notes():
            metadata, body = markdown_store.load_note(row['markdown_path'])
            self.assertEqual(metadata['source_file'], 'test.pdf')
            self.assertTrue(body)
        again = manager.process_document(self.document_id, self.service)
        self.assertEqual(again['status'], 'already_processed')
        self.service.generate.assert_called_once()

    def test_invalid_json_or_pages_never_commits(self):
        for output in ['not JSON', {'notes': [note_data(pages=[99])]}, {'notes': [{'title': 'Only title'}]}, {'notes': []}]:
            self.service.generate.return_value = output
            with self.subTest(output=output), self.assertRaises(LLMError):
                manager.process_document(self.document_id, self.service)
            self.assert_unprocessed()

    def test_later_chunk_failure_and_merging(self):
        document = load_documents()[0]
        pages = ['Erklärung'] * 7
        self.service.generate.side_effect = [
            {'notes': [note_data(pages=[1])]}, {'notes': [note_data(pages=[7])]}]
        notes = extract_knowledge(pages, document, self.service)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].source_pages, [1, 7])
        self.assertIn('Primärschlüssel', self.service.generate.call_args.args[0])
        self.service.generate.side_effect = [{'notes': [note_data()]}, LLMError('Netzwerkfehler')]
        with patch.object(manager, 'read_pdf', return_value={'pages': pages, 'text': 'Erklärung'}):
            with self.assertRaises(LLMError):
                manager.process_document(self.document_id, self.service)
        self.assert_unprocessed()

    def test_same_title_from_different_documents_is_not_merged_or_overwritten(self):
        self.service.generate.return_value = {'notes': [note_data('Architekturrollen', [1, 2])]}
        manager.process_document(self.document_id, self.service)
        second_id = document_manager.add_document(
            upload(make_pdf('Andere Quelle'), 'second.pdf'), 'Datenbanken')['document_id']
        manager.process_document(second_id, self.service)

        rows = [row for row in load_notes() if row['title'] == 'Architekturrollen']
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['document_id'] for row in rows}, {self.document_id, second_id})
        self.assertEqual(len({row['markdown_path'] for row in rows}), 2)
        sources = {markdown_store.load_note(row['markdown_path'])[0]['source_file'] for row in rows}
        self.assertEqual(sources, {'test.pdf', 'second.pdf'})

    def test_file_and_database_rollback(self):
        real_save = markdown_store.save_note
        calls = 0
        def fail_second(note):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Disk full')
            return real_save(note)
        with patch.object(markdown_store, 'save_note', side_effect=fail_second):
            with self.assertRaises(OSError):
                manager.process_document(self.document_id, self.service)
        self.assert_unprocessed()
        with patch.object(manager, 'register_note', side_effect=sqlite3.OperationalError):
            with self.assertRaises(sqlite3.Error):
                manager.process_document(self.document_id, self.service)
        self.assert_unprocessed()
        manager.process_document(self.document_id, self.service)
        self.assertEqual(len(load_notes()), 2)

    def test_missing_key_no_text_and_processing_lock(self):
        with self.assertRaises(LLMError):
            manager.process_document(self.document_id)
        with patch.object(manager, 'read_pdf', return_value={'text': '', 'pages': ['']}):
            with self.assertRaises(ValueError):
                manager.process_document(self.document_id, self.service)
        manager._processing_lock.acquire()
        try:
            with self.assertRaises(ValueError):
                manager.process_document(self.document_id, self.service)
        finally:
            manager._processing_lock.release()
        self.service.generate.assert_not_called()
        self.assert_unprocessed()

    def test_restart_in_new_process(self):
        manager.process_document(self.document_id, self.service)
        code = ('from pathlib import Path; from src.persistence import database as d; '
                f'd.DATABASE_PATH=Path({str(self.root / "data/application.db")!r}); d.initialize_database(); '
                'from src.persistence.knowledge_repository import load_notes; '
                f'root=Path({str(self.root)!r}); rows=load_notes(); assert len(rows)==2; '
                'assert all((root/r["markdown_path"]).read_text(encoding="utf-8").startswith("---") for r in rows)')
        subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True)

    def test_settings_preserve_keys_and_no_plaintext_widget(self):
        config.save_config('gemini', 'custom-model', 'local-test-key')
        config.save_config('openai', 'other-model', 'second-test-key')
        config.save_config('gemini', 'updated-model', '')
        loaded = config.load_config()
        self.assertEqual(loaded.api_key, 'local-test-key')
        self.assertEqual(loaded.model, 'updated-model')
        self.assertNotIn('local-test-key', repr(loaded))
        self.assertEqual(config.load_config('openai').api_key, 'second-test-key')
        app = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=30)
        app.radio[0].set_value('Einstellungen').run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.text_input[0].value, '')
        self.assertNotIn('local-test-key', str(app))

    def test_ui_explicit_action_and_restart(self):
        with patch.object(manager, 'LLMService', return_value=self.service):
            app = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=30)
            app.radio[0].set_value('Wissensbasis').run()
            app.run()
            self.service.generate.assert_not_called()
            app.button(key=f'process_{self.document_id}').click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(load_notes()), 2)
            app.run()
            self.service.generate.assert_called_once()
            restarted = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=30)
            restarted.radio[0].set_value('Wissensbasis').run()
            self.assertEqual(len(restarted.exception), 0)
            self.assertTrue(any('Primärschlüssel' in item.value for item in restarted.markdown))
            self.service.generate.assert_called_once()

    def test_settings_buttons_only_test_explicitly(self):
        with patch('src.ui.settings.LLMService') as service_class:
            app = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=30)
            app.radio[0].set_value('Einstellungen').run()
            service_class.assert_not_called()
            app.text_input[0].set_value('only-test-key')
            app.text_input[1].set_value('test-model')
            app.button(key='FormSubmitter:llm_settings_openai-Speichern').click().run()
            self.assertEqual(len(app.exception), 0)
            service_class.assert_not_called()
            self.assertEqual(config.load_config().api_key, 'only-test-key')
            app.run()
            self.assertEqual(app.text_input[0].value, '')
            app.button(key='FormSubmitter:llm_settings_openai-Verbindung testen').click().run()
            service_class.return_value.test_connection.assert_called_once()
            app.run()
            service_class.return_value.test_connection.assert_called_once()

    def test_provider_errors_are_safe(self):
        settings = config.LLMConfig('gemini', 'test-model', 'test-key')
        class SDKFailure(Exception):
            status_code = 503
        with patch('src.llm.gemini_provider.genai.Client') as factory:
            client = factory.return_value.__enter__.return_value
            client.interactions.create.side_effect = SDKFailure('secret-test-key must not leak')
            with self.assertRaises(LLMError) as caught:
                GeminiProvider(settings).generate('ping', ConnectionResult)
            self.assertIn('überlastet', str(caught.exception))
            self.assertNotIn('secret-test-key', str(caught.exception))
            self.assertEqual(factory.call_args.kwargs['http_options'].retry_options.attempts, 0)
            client.interactions.create.side_effect = OSError('network')
            with self.assertRaises(LLMError) as caught:
                GeminiProvider(settings).generate('ping', ConnectionResult)
            self.assertIn('Netzwerk', str(caught.exception))

    def test_provider_sdk_contracts(self):
        settings = config.LLMConfig('openai', 'test-model', 'test-key')
        with patch('src.llm.openai_provider.openai.OpenAI') as factory:
            client = factory.return_value.__enter__.return_value
            client.responses.parse.return_value = SimpleNamespace(status='completed', output_parsed=ConnectionResult(ok=True))
            self.assertTrue(OpenAIProvider(settings).generate('ping', ConnectionResult).ok)
            self.assertFalse(client.responses.parse.call_args.kwargs['store'])
            self.assertEqual(factory.call_args.kwargs['max_retries'], 0)
        with patch('src.llm.gemini_provider.genai.Client') as factory:
            client = factory.return_value.__enter__.return_value
            client.interactions.create.return_value = SimpleNamespace(status='completed', output_text='{"ok":true}')
            self.assertTrue(GeminiProvider(settings).generate('ping', ConnectionResult).ok)
            self.assertEqual(client.interactions.create.call_args.kwargs['response_format']['mime_type'], 'application/json')
            client.interactions.create.return_value.output_text = 'invalid'
            with self.assertRaises(LLMError):
                GeminiProvider(settings).generate('ping', ConnectionResult)


if __name__ == '__main__':
    unittest.main()
