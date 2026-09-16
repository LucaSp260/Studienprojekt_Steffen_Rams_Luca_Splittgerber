"""Expliziter API-Test: vorhandene Notes indexieren, niemals neu extrahieren."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')

from src.knowledge_layer import markdown_store
from src.knowledge_layer.embedding_service import EmbeddingService, EmbeddingError
from src.knowledge_layer.indexing_service import index_existing_notes
from src.knowledge_layer.retrieval_service import retrieve
from src.knowledge_layer.vector_store import VectorStore, SearchError, CHROMA_PATH
from src.llm.config import load_embedding_config
from src.persistence.knowledge_repository import load_notes

QUERIES = [
    'Welche fachlichen und technischen Bereiche muss man bei der Architektur eines Unternehmens betrachten?',
    'Wie kann man die bestehende IT-Landschaft eines Unternehmens systematisch erfassen?',
]
REPORT = CHROMA_PATH / 'retrieval_test_report.json'


class CountingEmbeddings(EmbeddingService):
    def __init__(self, settings):
        super().__init__(settings)
        self.document_calls = 0

    def embed_documents(self, texts):
        self.document_calls += 1
        return super().embed_documents(texts)


def snapshot():
    return {str(row['id']): hashlib.sha256((markdown_store.KNOWLEDGE_BASE_PATH.parent / row['markdown_path']).read_bytes()).hexdigest()
            for row in load_notes()}


def main():
    settings = load_embedding_config('openai')
    if not settings.api_key:
        print('Übersprungen: Kein OpenAI-Key konfiguriert.')
        return
    service = CountingEmbeddings(settings)
    before = snapshot()
    restarted = '--after-restart' in sys.argv
    if restarted:
        report = json.loads(REPORT.read_text(encoding='utf-8'))
        assert before == report['markdown_hashes'], 'Markdown-Dateien wurden verändert'
    with patch('src.llm.llm_service.LLMService.generate', side_effect=AssertionError('Knowledge Extraction ist in diesem Test verboten')):
        indexed = index_existing_notes(embedding_service=service)
        print('Nachindexierung:', indexed, flush=True)
        with VectorStore(settings) as store:
            assert len(store.get()['ids']) == len(before), 'Unerwartete Anzahl Indexeinträge'
        if restarted:
            assert indexed['indexed'] == 0 and service.document_calls == 0
        results = []
        for query in QUERIES[:1] if restarted else QUERIES:
            matches = retrieve(query, embedding_service=service)
            assert matches
            compact = [{key: match[key] for key in ['knowledge_note_id','title','course','source_file','source_pages','distance']}
                       for match in matches]
            results.append({'query':query,'results':compact})
            print(json.dumps(results[-1],ensure_ascii=False), flush=True)
        target = 'Architekturebenen einer Unternehmensarchitektur'
        assert target in [item['title'] for item in results[0]['results'][:3]], 'Erwartete Note nicht unter den Top 3'
        assert snapshot() == before, 'Knowledge Notes wurden verändert'
        if restarted:
            report['restart_verified'] = True
            report['restart_results'] = results
            REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print('Neustart: Index persistent, keine erneuten Dokument-Embeddings, keine Duplikate.', flush=True)
        else:
            report = {'provider':settings.provider,'model':settings.model,'dimensions':settings.dimensions,
                      'indexing':indexed,'markdown_hashes':before,'searches':results,'restart_verified':False}
            REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'--after-restart'],check=True)


if __name__=='__main__':
    try:
        main()
    except (EmbeddingError,SearchError) as error:
        print(str(error),flush=True)
        sys.exit(1)
