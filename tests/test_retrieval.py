"""Semantische Suche mit echten lokalen Chroma-Dateien, aber Mock-Embeddings."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from test_documents import make_pdf, upload
from test_knowledge import note_data
from src.data_layer import document_manager
from src.knowledge_layer import markdown_store, knowledge_manager, vector_store, indexing_service
from src.knowledge_layer.embedding_service import EmbeddingService, EmbeddingError, split_embedding_text
from src.knowledge_layer.indexing_service import index_existing_notes, prepare_record
from src.knowledge_layer.models import KnowledgeNote
from src.knowledge_layer.retrieval_service import retrieve
from src.knowledge_layer.vector_store import VectorStore, SearchError, collection_name
from src.llm import config
from src.llm.embedding_providers import openai_embeddings, gemini_embeddings
from src.persistence import database as db
from src.persistence.knowledge_repository import load_notes, register_note
from src.persistence.document_repository import load_documents

ROOT = Path(__file__).resolve().parents[1]


class FakeEmbeddings:
    def __init__(self, model='mock-embedding', dimensions=3):
        self.config = config.EmbeddingConfig('openai', model, 'fake-key', dimensions)
        self.document_calls = 0
        self.query_calls = 0

    def vector(self, text):
        if 'architektur' in text.lower():
            return [0.0, 1.0, 0.1]
        return [1.0, 0.0, 0.1]

    def embed_documents(self, texts):
        self.document_calls += 1
        return [self.vector(text) for text in texts]

    def embed_query(self, text):
        self.query_calls += 1
        return self.vector(text)


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(db, 'DATABASE_PATH', self.root/'data/application.db'),
                        patch.object(document_manager, 'USER_DATA_PATH', self.root/'user_data'),
                        patch.object(markdown_store, 'KNOWLEDGE_BASE_PATH', self.root/'knowledge_base'),
                        patch.object(vector_store, 'CHROMA_PATH', self.root/'chroma_db'),
                        patch.object(knowledge_manager, 'PROJECT_PATH', self.root),
                        patch.object(config, 'ENV_PATH', self.root/'.env'),
                        patch.dict(os.environ, {'LLM_PROVIDER':'openai', 'OPENAI_API_KEY':'', 'GEMINI_API_KEY':''})]
        for override in self.patches:
            override.start()
        db.initialize_database()
        self.embeddings = FakeEmbeddings()
        self.add_note('Datenbanken', 'SQL Transaktionen')
        self.add_note('SWA', 'Architektur')

    def tearDown(self):
        for override in reversed(self.patches):
            override.stop()
        self.temp.cleanup()

    def add_note(self, course, title, filename='test.pdf', pages=None):
        result = document_manager.add_document(upload(make_pdf(f'{title} {filename}'), filename), course)
        note = KnowledgeNote(**note_data(title, pages), course=course, source_file=filename)
        path = markdown_store.save_note(note)
        with closing(db.get_connection()) as connection, connection:
            return register_note(connection, result['document_id'], note, path.relative_to(self.root).as_posix())

    def test_embedding_text_excludes_technical_metadata(self):
        record = prepare_record(load_notes()[0])
        self.assertIn('Definition', record['text'])
        self.assertIn('Eindeutigkeit', record['text'])
        self.assertIn('Tags:', record['text'])
        self.assertNotIn('source_file:', record['text'])
        self.assertNotIn('markdown_path:', record['text'])
        self.assertNotIn('test.pdf', record['text'])
        self.assertIn('related_topics', record['metadata'])

    def test_index_stable_ids_skip_and_status(self):
        first = index_existing_notes(embedding_service=self.embeddings)
        second = index_existing_notes(embedding_service=self.embeddings)
        self.assertEqual(first, {'indexed':2, 'skipped':0, 'total':2})
        self.assertEqual(second, {'indexed':0, 'skipped':2, 'total':2})
        self.assertEqual(self.embeddings.document_calls, 1)
        self.assertTrue(all(row['processed'] for row in load_documents()))
        with VectorStore(self.embeddings.config) as store:
            self.assertEqual(set(store.get()['ids']), {'knowledge_note_1','knowledge_note_2'})
            rows = [prepare_record(row) for row in load_notes()]
            store.upsert(rows, [self.embeddings.vector(row['text']) for row in rows])
            self.assertEqual(len(store.get()['ids']), 2)

    def test_retrieval_top_k_course_sources(self):
        index_existing_notes(embedding_service=self.embeddings)
        results = retrieve('Architektur', embedding_service=self.embeddings)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['title'], 'Architektur')
        self.assertEqual(results[0]['source_file'], 'test.pdf')
        self.assertEqual(results[0]['source_pages'], [1])
        self.assertIn('Primärschlüssel', results[0]['content'])
        self.assertIn('Fremdschlüssel', results[0]['related_topics'])
        self.assertEqual(len(retrieve('SQL', top_k=1, embedding_service=self.embeddings)),1)
        filtered = retrieve('Architektur', course='Datenbanken', embedding_service=self.embeddings)
        self.assertEqual(len(filtered),1)
        self.assertEqual(filtered[0]['course'],'Datenbanken')
        calls = self.embeddings.query_calls
        self.assertEqual(retrieve('SQL', course='Unknown', embedding_service=self.embeddings),[])
        self.assertEqual(self.embeddings.query_calls,calls)

    def test_same_title_in_two_documents_keeps_exact_sources_in_all_stores(self):
        first_id = self.add_note('SWA', 'Architekturrollen', 'introduction.pdf', [12, 13, 14, 15])
        second_id = self.add_note('SWA', 'Architekturrollen', 'eam.pdf', [4, 6, 7])
        rows = {row['id']: row for row in load_notes()}
        documents = {row['id']: row for row in load_documents()}

        first_path = rows[first_id]['markdown_path']
        second_path = rows[second_id]['markdown_path']
        self.assertNotEqual(first_path, second_path)
        self.assertTrue(second_path.endswith('_1.md'))

        index_existing_notes(embedding_service=self.embeddings)
        with VectorStore(self.embeddings.config) as store:
            stored = store.get(course='SWA')
        chroma = {item['knowledge_note_id']: item for item in stored['metadatas']}

        expected = {
            first_id: ('introduction.pdf', [12, 13, 14, 15]),
            second_id: ('eam.pdf', [4, 6, 7]),
        }
        for note_id, (source_file, source_pages) in expected.items():
            row = rows[note_id]
            markdown, _ = markdown_store.load_note(row['markdown_path'])
            vector = chroma[note_id]
            self.assertEqual(documents[row['document_id']]['filename'], source_file)
            self.assertEqual(json.loads(row['source_pages']), source_pages)
            self.assertEqual(markdown['source_file'], source_file)
            self.assertEqual(markdown['source_pages'], source_pages)
            self.assertEqual(vector['source_file'], source_file)
            self.assertEqual(json.loads(vector['source_pages']), source_pages)

        results = retrieve('Architekturrollen', top_k=10, course='SWA',
                           embedding_service=self.embeddings)
        matching = {item['knowledge_note_id']: item for item in results
                    if item['title'] == 'Architekturrollen'}
        self.assertEqual(set(matching), {first_id, second_id})
        for note_id, (source_file, source_pages) in expected.items():
            self.assertEqual(matching[note_id]['source_file'], source_file)
            self.assertEqual(matching[note_id]['source_pages'], source_pages)

    def test_empty_index_and_input_no_api_calls(self):
        self.assertEqual(retrieve('SQL',embedding_service=self.embeddings),[])
        self.assertEqual(self.embeddings.query_calls,0)
        for query, k in [('',5),('test',0),('test',True)]:
            with self.assertRaises(SearchError):
                retrieve(query,k,embedding_service=self.embeddings)

    def test_model_and_dimension_collections_separate(self):
        index_existing_notes(embedding_service=self.embeddings)
        other = FakeEmbeddings(model='other-model')
        self.assertNotEqual(collection_name(other.config),collection_name(self.embeddings.config))
        self.assertNotEqual(collection_name(FakeEmbeddings(dimensions=4).config),collection_name(self.embeddings.config))
        self.assertEqual(retrieve('SQL',embedding_service=other),[])
        index_existing_notes(embedding_service=other)
        self.assertEqual(other.document_calls,1)
        self.assertEqual(index_existing_notes(embedding_service=self.embeddings)['skipped'],2)

    def test_missing_and_empty_markdown_no_paid_embedding(self):
        path = self.root/load_notes()[0]['markdown_path']
        original = path.read_text(encoding='utf-8')
        path.unlink()
        with self.assertRaises(SearchError):
            index_existing_notes(embedding_service=self.embeddings)
        self.assertEqual(self.embeddings.document_calls,0)
        path.write_text(original.split('\n---\n')[0]+'\n---\n# Only a title\n',encoding='utf-8')
        with self.assertRaises(SearchError):
            index_existing_notes(embedding_service=self.embeddings)
        self.assertEqual(self.embeddings.document_calls,0)

    def test_changed_markdown_is_refreshed(self):
        index_existing_notes(embedding_service=self.embeddings)
        path = self.root/load_notes()[0]['markdown_path']
        path.write_text(path.read_text(encoding='utf-8')+'\nEine neue Ergänzung.\n',encoding='utf-8')
        with self.assertRaises(SearchError):
            retrieve('SQL',embedding_service=self.embeddings)
        self.assertEqual(index_existing_notes(embedding_service=self.embeddings)['indexed'],1)
        self.assertEqual(len(retrieve('SQL',embedding_service=self.embeddings)),2)

    def test_real_process_restart_without_reembedding(self):
        index_existing_notes(embedding_service=self.embeddings)
        code = ('from pathlib import Path; from src.knowledge_layer import vector_store as v; '
                'from src.llm.config import EmbeddingConfig; '
                f'v.CHROMA_PATH=Path({str(self.root/"chroma_db")!r}); '
                's=v.VectorStore(EmbeddingConfig("openai","mock-embedding","",3)); '
                'assert len(s.get()["ids"])==2; '
                'r=s.query([0.,1.,0.1],1); assert r["metadatas"][0][0]["title"]=="Architektur"; s.close()')
        subprocess.run([sys.executable,'-c',code],cwd=ROOT,check=True)
        self.assertEqual(index_existing_notes(embedding_service=self.embeddings)['indexed'],0)

    def test_chroma_failure_keeps_document_unprocessed(self):
        with patch.object(VectorStore,'upsert',side_effect=SearchError('Write failed')):
            with self.assertRaises(SearchError):
                index_existing_notes(embedding_service=self.embeddings)
        self.assertTrue(all(not row['processed'] for row in load_documents()))
        self.assertEqual(len(load_notes()),2)

    def test_new_notes_index_and_retry_without_extraction(self):
        result = document_manager.add_document(upload(make_pdf('new PDF')), 'New course')
        llm = Mock()
        llm.generate.return_value = {'notes':[note_data('Neues Thema')]}
        failing = FakeEmbeddings()
        failing.embed_documents = Mock(side_effect=EmbeddingError('Rate limit'))
        with self.assertRaises(EmbeddingError):
            knowledge_manager.process_document(result['document_id'], llm, embedding_service=failing)
        self.assertEqual(len(load_notes(document_id=result['document_id'])),1)
        self.assertFalse(next(row for row in load_documents() if row['id']==result['document_id'])['processed'])
        knowledge_manager.process_document(result['document_id'], llm, embedding_service=self.embeddings)
        llm.generate.assert_called_once()
        self.assertTrue(next(row for row in load_documents() if row['id']==result['document_id'])['processed'])
        with VectorStore(self.embeddings.config) as store:
            self.assertEqual(len(store.get()['ids']),1)

    def test_search_ui_only_explicit_buttons(self):
        def index_action():
            return index_existing_notes(embedding_service=self.embeddings)
        def search_action(query, top_k, course):
            return retrieve(query,top_k,course,embedding_service=self.embeddings)
        with patch('src.ui.search.index_existing_notes',side_effect=index_action), patch('src.ui.search.retrieve',side_effect=search_action):
            app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
            app.radio[0].set_value('Wissensbasis').run()
            app.run()
            self.assertEqual(self.embeddings.document_calls,0)
            self.assertEqual(self.embeddings.query_calls,0)
            app.button(key='index_existing').click().run(timeout=30)
            app.text_input[0].set_value('Architektur')
            app.button(key='FormSubmitter:semantic_search-Suchen').click().run(timeout=30)
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any('1. Architektur' in item.value for item in app.markdown))
            app.run()
            self.assertEqual(self.embeddings.query_calls,1)
            self.assertEqual(self.embeddings.document_calls,1)


class EmbeddingTests(unittest.TestCase):
    def test_service_normalizes_batches_and_long_text(self):
        service=EmbeddingService(config.EmbeddingConfig('openai','test','key',3))
        service._generate=Mock(side_effect=lambda texts,*args,**kwargs:[[3.,4.,0.] for _ in texts])
        vectors=service.embed_documents(['Kurzer Text','ä'*8000])
        self.assertEqual(len(vectors),2)
        self.assertAlmostEqual(vectors[0][0],0.6)
        self.assertAlmostEqual(vectors[1][1],0.8)
        self.assertEqual(''.join(split_embedding_text('ä'*8000)),'ä'*8000)
        self.assertTrue(all(len(part.encode())<=6000 for part in split_embedding_text('ä'*8000)))
        service.embed_query('query')
        self.assertTrue(service._generate.call_args.kwargs['is_query'])

    def test_invalid_vectors_and_missing_key(self):
        service=EmbeddingService(config.EmbeddingConfig('openai','test','',3))
        with self.assertRaises(EmbeddingError):
            service.embed_query('test')
        service.config.api_key='fake-key'
        for value in [[[1.,2.]], [[0.,0.,0.]], [[float('nan'),1.,2.]], []]:
            service._generate=Mock(return_value=value)
            with self.assertRaises(EmbeddingError):
                service.embed_documents(['text'])
        with self.assertRaises(EmbeddingError):
            service.embed_query(' ')

    def test_sdk_parameters_and_separate_models(self):
        settings=config.EmbeddingConfig('openai','test-embedding','fake-key',3)
        with patch('src.llm.embedding_providers.openai.OpenAI') as factory:
            client=factory.return_value.__enter__.return_value
            client.embeddings.create.return_value=SimpleNamespace(data=[SimpleNamespace(index=0,embedding=[1.,2.,3.])])
            self.assertEqual(openai_embeddings(['text'],settings),[[1.,2.,3.]])
            self.assertEqual(client.embeddings.create.call_args.kwargs['model'],'test-embedding')
        with patch('src.llm.embedding_providers.genai.Client') as factory:
            client=factory.return_value.__enter__.return_value
            client.models.embed_content.return_value=SimpleNamespace(embeddings=[SimpleNamespace(values=[1.,2.,3.])])
            settings.provider='gemini'
            settings.model=config.DEFAULT_EMBEDDING_MODELS['gemini']
            gemini_embeddings(['query'],settings,is_query=True)
            args=client.models.embed_content.call_args.kwargs
            self.assertTrue(args['contents'][0].parts[0].text.startswith('task: search result'))
            self.assertIsNone(args['config'].task_type)

    def test_embedding_config_is_separate_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config,'ENV_PATH',Path(directory)/'.env'):
            config.save_config('openai','my-llm','fake-key','my-embedding')
            self.assertEqual(config.load_config().model,'my-llm')
            self.assertEqual(config.load_embedding_config().model,'my-embedding')
            config.save_config('openai','next-llm')
            self.assertEqual(config.load_embedding_config().model,'my-embedding')


if __name__=='__main__':
    unittest.main()
