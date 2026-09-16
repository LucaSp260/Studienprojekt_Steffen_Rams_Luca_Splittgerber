"""Expliziter, eventuell kostenpflichtiger Integrationstest mit synthetischer PDF.

Aufruf im Projektordner: .venv/Scripts/python.exe tests/manual_api_check.py
Verwendet den gespeicherten Anbieter oder einen anderen vorhandenen Schlüssel.
"""
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf
from streamlit.testing.v1 import AppTest

from src.data_layer import document_manager
from src.knowledge_layer import knowledge_manager, markdown_store
from src.llm.base_provider import LLMError
from src.llm.config import load_config, load_embedding_config
from src.knowledge_layer.embedding_service import EmbeddingService, EmbeddingError
from src.knowledge_layer.vector_store import SearchError
from src.knowledge_layer import vector_store
from src.llm.llm_service import LLMService
from src.persistence import database
from src.persistence.knowledge_repository import load_notes


def main():
    selected = load_config()
    if not selected.api_key:
        selected = next((candidate for name in ('openai', 'gemini')
                         if (candidate := load_config(name)).api_key), None)
    if selected is None:
        print('Übersprungen: Kein API-Key vorhanden.')
        return
    with tempfile.TemporaryDirectory(prefix='companion-live-') as temporary, ExitStack() as stack:
        root = Path(temporary)
        for module, attribute, value in [
            (database, 'DATABASE_PATH', root / 'data/application.db'),
            (document_manager, 'USER_DATA_PATH', root / 'user_data'),
            (knowledge_manager, 'PROJECT_PATH', root),
            (markdown_store, 'KNOWLEDGE_BASE_PATH', root / 'knowledge_base'),
            (vector_store, 'CHROMA_PATH', root / 'chroma_db'),
        ]:
            stack.enter_context(patch.object(module, attribute, value))
        database.initialize_database()
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), 'Relationale Datenbanken\n'
                'Eine Tabelle besteht aus Zeilen und Spalten. Eine Zeile beschreibt einen Datensatz.\n'
                'Ein Primaerschluessel identifiziert jeden Datensatz eindeutig und darf nicht NULL sein.\n'
                'Beispiel: Die Kundennummer ist ein Primaerschluessel der Tabelle Kunde.\n'
                'Ein Fremdschluessel verweist auf einen Schluessel einer anderen Tabelle.\n'
                'Beispiel: Eine Bestellung verweist mit ihrer Kundennummer auf den Kunden.\n'
                'Primaerschluessel und Fremdschluessel haben unterschiedliche Aufgaben.')
            content = pdf.tobytes()
        result = document_manager.add_document(SimpleNamespace(name='integration.pdf', getvalue=lambda: content), 'Datenbanken')
        result = knowledge_manager.process_document(result['document_id'], LLMService(selected), progress=print,
            embedding_service=EmbeddingService(load_embedding_config(selected.provider)))
        rows = load_notes()
        assert rows
        for row in rows:
            metadata, body = markdown_store.load_note(row['markdown_path'])
            assert metadata['source_pages'] == [1]
            assert metadata['source_file'] == 'integration.pdf'
            assert body and metadata['tags']
        database.initialize_database()
        app_path = str(Path(__file__).resolve().parents[1] / 'app.py')
        app = AppTest.from_file(app_path).run(timeout=30)
        app.radio[0].set_value('Wissensbasis').run()
        assert not app.exception
        assert len(load_notes()) == len(rows)
        print(f'Echter API-Test erfolgreich: {selected.provider}, {selected.model}, {len(rows)} Notes; YAML, Quellen und neue UI-Sitzung geprüft.')


if __name__ == '__main__':
    try:
        main()
    except (LLMError, EmbeddingError, SearchError) as error:
        print(str(error))
        sys.exit(1)
