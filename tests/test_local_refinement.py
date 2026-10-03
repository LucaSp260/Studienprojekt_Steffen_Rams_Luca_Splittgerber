"""Local UI/data refinements, isolated data and mocked providers only."""
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import ExitStack, closing
from unittest.mock import Mock, patch
from streamlit.testing.v1 import AppTest
from src.persistence import database as db
from src.persistence.course_repository import create_course, list_courses, resolve_course
from src.persistence.artifact_repository import save_artifact, load_artifacts, load_artifact, delete_artifact
from src.persistence.brain_repository import add_manual_concept, add_manual_edge, save_concept, save_edge, delete_concept, delete_edge, load_graph
from src.persistence.knowledge_repository import register_note, load_notes
from src.knowledge_layer import markdown_store, vector_store, obsidian_export, course_manager
from src.knowledge_layer.models import KnowledgeNote
from src.knowledge_layer.indexing_service import prepare_record
from src.knowledge_layer.tags import normalize_tags
from src.llm.config import LLMConfig, EmbeddingConfig, DEFAULT_EMBEDDING_MODELS
from src.llm.connection_status import connection_status, _cached_status
from src.llm import config
from src.ui.concept_details import concept_summary
from src.ui.brain_graph import build_graph_html, focus_subgraph
from test_agents import FakeLLM, SOURCES, exam_draft, APPROVED_EXAM
from src.agent_layer.exam_agent import create_exam
from src.agent_layer.models import ExamRequest

ROOT = Path(__file__).resolve().parents[1]

def new_note(title="Capability", course="SWA"):
    return KnowledgeNote(title=title, topic="Architektur", tags=["Business Capability"],
        difficulty="medium", source_pages=[1], related_topics=[], definition="Eine Fähigkeit beschreibt das Können eines Unternehmens.",
        key_concepts=[title], example="", common_mistakes=[], exam_relevance=["Begriff erklären"], course=course, source_file="test.pdf")

class LocalDataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.stack=ExitStack()
        for module, name, value in [(db,"DATABASE_PATH",self.root/"data/application.db"),
                (markdown_store,"KNOWLEDGE_BASE_PATH",self.root/"knowledge_base"),
                (obsidian_export,"KNOWLEDGE_BASE_PATH",self.root/"knowledge_base"),
                (vector_store,"CHROMA_PATH",self.root/"chroma_db"),
                (config,"ENV_PATH",self.root/".env")]:
            self.stack.enter_context(patch.object(module,name,value))
        self.stack.enter_context(patch.dict("os.environ", {"OPENAI_API_KEY":"", "GEMINI_API_KEY":""}))
        db.initialize_database()
        create_course("SWA")
        with closing(db.get_connection()) as conn, conn:
            self.doc=conn.execute("INSERT INTO documents(filename,file_hash,course,file_path,processed) VALUES ('test.pdf','hash','SWA','user_data/test.pdf',1)").lastrowid
            item=new_note()
            path=markdown_store.save_note(item)
            self.note=register_note(conn,self.doc,item,path.relative_to(self.root).as_posix())
            conn.execute("INSERT INTO brain_note_processing(note_id,processing_version,status) VALUES (?,1,'completed')",(self.note,))
        self.note_path=path
        self.a=add_manual_concept("SWA","Capability","",[self.note])
        self.b=add_manual_concept("SWA","Map","Persistente kurze Beschreibung.",[self.note])
        self.edge=add_manual_edge("SWA",self.a,self.b,"represented_by","Belegte Begründung",[self.note],relation_description="Capability wird als Map dargestellt.")
        self.chat=db.create_chat()
        db.save_chat_exchange(self.chat,"Frage","Antwort",{"course":"SWA","sources":[]})
        self.artifact=save_artifact("exam","Alt","SWA",{"course":"SWA","duration_minutes":30},{"course":"SWA","duration_minutes":30})

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def test_tags_short_deduplicated_local_reuse(self):
        self.assertEqual(normalize_tags(["Business_Capability","businesscapability","SWA","ein viel zu langer Tag Satz","One Two Three","Vier","Fünf","Sechs"],
                         existing=["business-capability"]),["business-capability","swa","one-two-three","vier","fünf"])
        self.assertEqual(normalize_tags(["abc"], existing=["ab-c", "a-bc"]),["abc"])
        item=new_note()
        self.assertLessEqual(len(item.tags),5)
        self.assertTrue(all(len(tag.split('-'))<=3 for tag in item.tags))

    def test_existing_database_is_backed_up_before_course_registry_migration(self):
        legacy=self.root/'legacy.db'
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute("CREATE TABLE chats(id INTEGER PRIMARY KEY,title TEXT,created_at TEXT,updated_at TEXT,pinned INTEGER DEFAULT 0)")
            connection.execute("INSERT INTO chats VALUES(1,'Legacy','2025','2025',1)")
        with patch.object(db,'DATABASE_PATH',legacy):
            db.initialize_database()
            self.assertEqual(db.list_chats()[0]['pinned'],1)
        backups=list(self.root.glob('legacy.before-ui-refinement-*.db'))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='courses'").fetchone())
            self.assertEqual(connection.execute('SELECT pinned FROM chats').fetchone()[0],1)

    def test_chat_pin_unpin_restart_order(self):
        other=db.create_chat()
        db.set_chat_pinned(self.chat,True)
        db.initialize_database()
        self.assertEqual(db.list_chats()[0]["id"],self.chat)
        code=("from pathlib import Path; from src.persistence import database as db; "
              f"db.DATABASE_PATH=Path({str(db.DATABASE_PATH)!r}); db.initialize_database(); assert db.list_chats()[0]['pinned']==1")
        subprocess.run([sys.executable,'-c',code],cwd=ROOT,check=True)
        db.set_chat_pinned(self.chat,False)
        self.assertEqual(db.list_chats()[0]["id"],other)

    def test_sidebar_chat_opens_chat_mode_from_history(self):
        app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
        app.selectbox(key='learning_mode').set_value('Meine Inhalte').run()
        app.button(key=f'chat_{self.chat}').click().run()
        self.assertEqual(app.selectbox(key='learning_mode').value,'Lernchat')
        self.assertEqual(app.radio(key='navigation').value,'Lernen')
        self.assertEqual(len(app.exception),0)

    def test_chat_delete_preserves_independent_contents(self):
        with closing(db.get_connection()) as conn, conn:
            conn.execute("INSERT INTO exams(chat_id,title,content) VALUES (?, 'legacy', '{}')",(self.chat,))
        db.delete_chat(self.chat)
        self.assertEqual(db.load_messages(self.chat),[])
        self.assertIsNotNone(load_artifact(self.artifact))
        self.assertEqual(len(load_notes()),1)
        with closing(db.get_connection()) as conn:
            self.assertIsNone(conn.execute("SELECT chat_id FROM exams").fetchone()[0])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_create_and_select_course_case_identity(self):
        self.assertEqual(resolve_course(" swa "),"SWA")
        self.assertEqual(create_course("  Datenbanken  "),"Datenbanken")
        self.assertIn("Datenbanken",list_courses())
        with self.assertRaises(ValueError): create_course("datenbanken")
        with self.assertRaises(ValueError): create_course(" ")

    def test_rename_all_stores_without_embeddings(self):
        vault=obsidian_export.export_course_to_obsidian("SWA")
        row=load_notes()[0]
        record=prepare_record(row)
        conf=EmbeddingConfig("openai","test-model","",3)
        before_body=markdown_store.load_note(row['markdown_path'])[1]
        with vector_store.VectorStore(conf) as store:
            store.upsert([record],[[1.,0.,0.]])
        with patch('src.llm.llm_service.LLMService.generate') as llm, patch('src.knowledge_layer.embedding_service.EmbeddingService.embed_documents') as embed:
            course_manager.rename_course("SWA","Softwarearchitektur")
            llm.assert_not_called(); embed.assert_not_called()
        self.assertEqual(load_notes()[0]['course'],'Softwarearchitektur')
        metadata,body=markdown_store.load_note(row['markdown_path'])
        self.assertEqual(metadata['course'],'Softwarearchitektur')
        self.assertEqual(body,before_body)
        concepts,edges=load_graph('Softwarearchitektur')
        self.assertEqual(len(concepts),2); self.assertEqual(len(edges),1)
        self.assertEqual(db.message_metadata(db.load_messages(self.chat)[1])['course'],'Softwarearchitektur')
        self.assertEqual(load_artifact(self.artifact)['content']['course'],'Softwarearchitektur')
        self.assertEqual(load_artifact(self.artifact)['configuration']['duration_minutes'],30)
        with vector_store.VectorStore(conf) as store:
            values=store.collection.get(include=['metadatas','embeddings'])
            self.assertEqual(values['metadatas'][0]['course'],'Softwarearchitektur')
            self.assertEqual(values['metadatas'][0]['content_hash'],prepare_record(load_notes()[0])['metadata']['content_hash'])
            self.assertEqual(list(values['embeddings'][0]),[1.,0.,0.])
        self.assertFalse(vault.exists())
        self.assertTrue(course_manager._vault_path('Softwarearchitektur').is_dir())
        with closing(db.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT status FROM brain_note_processing').fetchone()[0],'completed')
        self.assertTrue(list((self.root/'data').glob('*.before-course-rename-*.db')))

    def test_rename_conflict_and_file_failure_rollback(self):
        create_course('Anderer Kurs')
        original=self.note_path.read_bytes()
        with self.assertRaises(ValueError): course_manager.rename_course('SWA','anderer kurs')
        actual=course_manager._atomic_write
        calls=0
        def fail_once(path,content):
            nonlocal calls
            calls+=1
            if calls==1: raise OSError('Test failure')
            return actual(path,content)
        with patch.object(course_manager,'_atomic_write',side_effect=fail_once), self.assertRaises(OSError):
            course_manager.rename_course('SWA','Neu')
        self.assertEqual(self.note_path.read_bytes(),original)
        self.assertEqual(load_notes()[0]['course'],'SWA')

    def test_concept_edit_delete_and_edge_edit_delete_preserve_notes(self):
        original=self.note_path.read_bytes()
        save_concept(self.a,'Business Capability','Eine kurze Summary.',[self.note])
        save_edge(self.edge,self.a,self.b,'supports','Belegte Begründung',[self.note],relation_description='Unterstützt die Map.')
        concepts,edges=load_graph('SWA')
        self.assertEqual(edges[0]['relation_type'],'supports')
        delete_edge(self.edge)
        self.assertEqual(load_graph('SWA')[1],[])
        add_manual_edge('SWA',self.a,self.b,'supports','Belegt',[self.note])
        delete_concept(self.a)
        self.assertEqual(len(load_graph('SWA')[0]),1)
        self.assertEqual(load_graph('SWA')[1],[])
        self.assertEqual(self.note_path.read_bytes(),original)
        self.assertEqual(len(load_notes()),1)

    def test_summary_click_graph_and_focus_are_local(self):
        concepts,edges=load_graph('SWA')
        with patch('src.llm.llm_service.LLMService.generate') as llm:
            self.assertIn('Fähigkeit',concept_summary(concepts[0],load_notes()))
            graph=build_graph_html(concepts,edges,load_notes(),show_edge_labels=False)
            focus,links=focus_subgraph(concepts,edges,self.a)
            focused=build_graph_html(focus,links,load_notes(),show_edge_labels=True)
            self.assertIn('atlas-selection',graph)
            self.assertIn('wird dargestellt durch',focused)
            llm.assert_not_called()

    def test_artifact_filter_and_delete_only_selected(self):
        other=save_artifact('exercise','Andere Übung','DB',{}, {'value':1})
        self.assertEqual(len(load_artifacts(course='SWA')),1)
        delete_artifact(self.artifact)
        self.assertIsNotNone(load_artifact(other))
        self.assertEqual(len(load_notes()),1)

    def test_new_exam_without_duration_and_legacy_readable(self):
        draft=exam_draft()
        draft.pop('duration_minutes')
        for task in draft['tasks']: task.pop('estimated_minutes')
        llm=FakeLLM(draft,APPROVED_EXAM)
        result=create_exam(ExamRequest(course='SWA',task_count=6,difficulty='gemischt'),retriever=Mock(return_value=SOURCES),llm_service=llm,persist=False)
        self.assertNotIn('duration_minutes',result)
        self.assertNotIn('duration_minutes',result['configuration'])
        self.assertTrue(all('estimated_minutes' not in task for task in result['tasks']))
        self.assertTrue(all('Minuten' not in call[0] and 'duration_minutes' not in call[0] for call in llm.calls))
        self.assertEqual(load_artifact(self.artifact)['content']['duration_minutes'],30)

    def test_notes_sidebar_settings_and_sticky_ui(self):
        with patch('src.ui.settings.connection_status',return_value=(False,'Kein Schlüssel')), patch('src.ui.learning.answer_question') as agent:
            app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
            self.assertEqual(len(app.exception),0)
            self.assertEqual(app.sidebar.radio[0].label,'Navigation')
            self.assertEqual(app.button(key='add_chat').label,'Chat hinzufügen')
            self.assertTrue(any(widget.label=='Lernmodus' for widget in app.selectbox))
            app.radio(key='navigation').set_value('Generierte Notes').run()
            self.assertIn('>Generierte Notes</h1>', next(item.value for item in app.get('html') if '<h1' in item.value))
            self.assertNotIn('index_existing',[b.key for b in app.button])
            self.assertEqual(app.selectbox(key='notes_course').value,None)
            app.radio(key='navigation').set_value('Einstellungen').run()
            self.assertIn('>Einstellungen</h1>', next(item.value for item in app.get('html') if '<h1' in item.value))
            self.assertTrue(any('keine gültige Verbindung' in e.value for e in app.error))
            self.assertNotIn('Embedding-Modell',[w.label for w in app.text_input])
            app.radio(key='navigation').set_value('Wissensatlas').run()
            self.assertEqual(len(app.exception),0)
            self.assertIn('>Wissensatlas</h1>', next(item.value for item in app.get('html') if '<h1' in item.value))
            self.assertFalse(app.toggle(key='brain_show_edge_labels').value)
            self.assertNotIn('brain_propose',[b.key for b in app.button])
            agent.assert_not_called()

    def test_no_graph_rag_or_internal_context_in_ui(self):
        for name in ('learning_chat.py','exercise_agent.py','exam_agent.py'):
            code=(ROOT/'src/agent_layer'/name).read_text(encoding='utf-8')
            self.assertNotIn('brain_repository',code)
            self.assertNotIn('focus_subgraph',code)
        ui=(ROOT/'src/ui/learning.py').read_text(encoding='utf-8')
        self.assertNotIn('Zusammengefasster Kontext, den der Chat erhalten hat',ui)
        self.assertIn('_render_source_pdf_page',ui)
        self.assertIn('with st.bottom:',ui)

    def test_extraction_reuses_existing_short_tags_without_extra_prompt_list(self):
        from src.knowledge_layer.knowledge_extractor import extract_knowledge, merge_notes
        doc={"id":self.doc,"course":"SWA","filename":"new.pdf"}
        before=self.note_path.read_bytes()
        data=new_note("Neu").model_dump(exclude={"course","source_file"})
        data["tags"]=["BusinessCapability"]
        llm=FakeLLM({"notes":[data]})
        notes=extract_knowledge(["Neuer fachlicher Text"],doc,llm)
        self.assertEqual(notes[0].tags,["business-capability"])
        self.assertEqual(len(llm.calls),1)
        self.assertNotIn('business-capability',llm.calls[0][0])
        self.assertEqual(self.note_path.read_bytes(),before)
        copies=[notes[0].model_copy(deep=True),notes[0].model_copy(deep=True)]
        copies[0].tags=["one","two","three","four","five"]
        copies[1].tags=["six","seven"]
        self.assertEqual(len(merge_notes(copies)[0].tags),5)

    def test_rename_preserves_proposals_review_history_and_usage(self):
        from src.agent_layer.connection_agent import ConnectionDraft, ConceptProposal
        from src.persistence.brain_repository import save_proposals, load_proposals
        draft=ConnectionDraft(concepts=[ConceptProposal(name="Neu",description="Kurz.",note_ids=[self.note])])
        save_proposals('SWA',draft)
        with closing(db.get_connection()) as conn, conn:
            conn.execute("INSERT INTO llm_usage(operation,provider,model,input_tokens) VALUES ('connection_agent','test','mock',13)")
        old=load_proposals('SWA')[0]
        course_manager.rename_course('SWA','Neu benannt')
        new=load_proposals('Neu benannt')[0]
        self.assertEqual(old['original_payload'],new['original_payload'])
        self.assertEqual(save_proposals('Neu benannt',draft),0)
        with closing(db.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT input_tokens FROM llm_usage').fetchone()[0],13)

    def test_rename_failure_rolls_back_chroma_and_markdown(self):
        record=prepare_record(load_notes()[0])
        conf=EmbeddingConfig('openai','test','',3)
        with vector_store.VectorStore(conf) as store:
            store.upsert([record],[[1.,0.,0.]])
        original=self.note_path.read_bytes()
        real=course_manager._atomic_write
        count=0
        def failure(path,content):
            nonlocal count
            count+=1
            if count==1: raise OSError('Failed local replacement')
            return real(path,content)
        with patch.object(course_manager,'_atomic_write',side_effect=failure), self.assertRaises(OSError):
            course_manager.rename_course('SWA','Failed rename')
        self.assertEqual(self.note_path.read_bytes(),original)
        with vector_store.VectorStore(conf) as store:
            self.assertEqual(store.get()['metadatas'][0],record['metadata'])

    def test_graph_click_selects_editor_without_agent(self):
        with patch('src.ui.brain.atlas_graph',return_value={'kind':'concept','id':self.b,'event_id':'test'}), patch('src.llm.llm_service.LLMService.generate') as llm:
            app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
            app.radio(key='navigation').set_value('Wissensatlas').run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(app.selectbox(key='brain_selected_concept').value,self.b)
            self.assertTrue(any('Persistente kurze Beschreibung' in item.value for item in app.markdown))
            llm.assert_not_called()

    def test_settings_green_and_upload_choices(self):
        with patch('src.ui.settings.connection_status',return_value=(True,'Model metadata OK')):
            app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
            app.radio(key='navigation').set_value('Einstellungen').run()
            self.assertTrue(any(item.value=='Eine gültige Verbindung ist vorhanden.' for item in app.success))
            app.radio(key='navigation').set_value('Unterlagen').run()
            self.assertIn('>Unterlagen</h1>', next(item.value for item in app.get('html') if '<h1' in item.value))
            self.assertEqual(app.selectbox(key='documents_course').value,None)
            self.assertIn('SWA',app.selectbox(key='upload_course').options)
            app.selectbox(key='upload_course').set_value('+ Neuen Kurs anlegen').run()
            self.assertTrue(any(item.label=='Neuer Kursname' for item in app.text_input))

    def test_original_source_pages_render_without_context_text(self):
        from src.ui.learning import _render_chat_sources
        sources=[{'knowledge_note_id':self.note,'source_file':'test.pdf','source_pages':[1]}]
        code="from src.ui.learning import _render_chat_sources; _render_chat_sources("+repr(sources)+")"
        with patch('src.ui.learning._load_chat_source',return_value={'title':'Capability','pdf_path':'test.pdf','modified_ns':1,'note_text':'INTERNAL RAG BLOCK'}), patch('src.ui.learning._render_source_pdf_page',return_value=b'bad'):
            # Assert call routing without relying on a mock image byte decoder.
            with patch('src.ui.learning.st.image') as image:
                app=AppTest.from_string(code).run(timeout=30)
                self.assertEqual(len(app.exception),0)
                image.assert_called_once()
                self.assertEqual(image.call_args.kwargs['caption'],'PDF-Seite 1')
                self.assertNotIn('INTERNAL RAG BLOCK',[item.value for item in app.text])

    def test_history_filter_includes_artifact_only_course(self):
        from src.persistence.artifact_repository import save_artifact
        save_artifact("exercise", "Ohne Notes", "Historischer Kurs", {}, {"title":"Ohne Notes","course":"Historischer Kurs","exercises":[]})
        app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
        app.selectbox(key='learning_mode').set_value('Meine Inhalte').run()
        self.assertIn('Historischer Kurs',app.selectbox(key='learning_course').options)
        app.selectbox(key='learning_course').set_value('Historischer Kurs').run()
        self.assertTrue(any('Ohne Notes' in item.label for item in app.button))
        self.assertEqual(len(app.exception),0)

    def test_new_exam_ui_has_no_duration(self):
        app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
        app.selectbox(key='learning_mode').set_value('Probeklausur erstellen').run()
        app.selectbox(key='learning_course').set_value('SWA').run()
        self.assertEqual(len(app.exception),0)
        self.assertNotIn('Dauer in Minuten',[item.label for item in app.number_input])
        self.assertTrue(any(item.label=='Anzahl Aufgaben' for item in app.number_input))

    def test_document_deletion_keeps_shared_graph_and_cleans_all_sources(self):
        from src.data_layer import document_manager
        from src.data_layer.document_deletion import delete_document
        from src.persistence.document_repository import find_document_by_hash
        pdf=self.root/'user_data/test.pdf'
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b'test pdf bytes')
        other_pdf=self.root/'user_data/other.pdf'
        other_pdf.write_bytes(b'other pdf bytes')
        with closing(db.get_connection()) as conn, conn:
            other_doc=conn.execute(
                "INSERT INTO documents(filename,file_hash,course,file_path,processed) "
                "VALUES ('other.pdf','other-hash','SWA','user_data/other.pdf',1)"
            ).lastrowid
            item=new_note('Architecture Map').model_copy(update={'source_file':'other.pdf'})
            other_path=markdown_store.save_note(item)
            other_note=register_note(conn,other_doc,item,other_path.relative_to(self.root).as_posix())
            conn.execute("INSERT INTO brain_note_processing(note_id,processing_version,status) "
                         "VALUES (?,1,'completed')",(other_note,))
            for concept in (self.a,self.b):
                conn.execute("INSERT INTO brain_concept_sources VALUES (?,?)",(concept,other_note))
            conn.execute("INSERT INTO brain_edge_sources VALUES (?,?)",(self.edge,other_note))
            conn.execute("UPDATE messages SET metadata_json=? WHERE chat_id=? AND role='assistant'",
                (json.dumps({'sources':[{'knowledge_note_id':self.note,'source_file':'test.pdf',
                                         'source_pages':[1]}]}),self.chat))
            conn.execute("UPDATE generated_artifacts SET content_json=? WHERE id=?",
                (json.dumps({'title':'Alt','course':'SWA','sources':[{'source_id':'N1',
                    'knowledge_note_id':self.note,'source_file':'test.pdf','source_pages':[1]}],
                    'tasks':[]}),self.artifact))
            conn.execute("INSERT INTO llm_usage(operation,provider,model,document_id,note_ids_json) "
                         "VALUES ('knowledge_extraction','test','test',?,?)",
                         (self.doc,json.dumps([self.note,other_note])))
            conn.execute("INSERT INTO brain_proposals(course,proposal_type,fingerprint,payload_json) "
                         "VALUES ('SWA','concept','delete-test',?)",
                         (json.dumps({'name':'Removed','note_ids':[self.note]}),))
        transient=add_manual_concept('SWA','Temporary','',[self.note])
        transient_edge=add_manual_edge('SWA',self.b,transient,'supports','Only deleted PDF',
                                       [self.note],relation_description='Temporary evidence')
        conf=EmbeddingConfig('openai','delete-test','',3)
        records=[prepare_record(row) for row in load_notes()]
        with vector_store.VectorStore(conf) as store:
            store.upsert(records,[[1.,0.,0.],[0.,1.,0.]])
        vault=obsidian_export.export_course_to_obsidian('SWA')
        self.assertTrue((vault/'notes'/f'note-{self.note}.md').exists())
        with (
            patch.object(document_manager,'USER_DATA_PATH',self.root/'user_data'),
            patch('src.llm.llm_service.LLMService.generate') as llm,
            patch('src.knowledge_layer.embedding_service.EmbeddingService.embed_documents') as embed,
        ):
            result=delete_document(self.doc)
            llm.assert_not_called()
            embed.assert_not_called()
        self.assertEqual(result['deleted_notes'],1)
        self.assertFalse(pdf.exists())
        self.assertFalse(self.note_path.exists())
        self.assertTrue(other_pdf.exists())
        self.assertTrue(other_path.exists())
        self.assertIsNone(find_document_by_hash('hash'))
        self.assertEqual([row['id'] for row in load_notes()],[other_note])
        concepts,edges=load_graph('SWA')
        self.assertEqual({row['id'] for row in concepts},{self.a,self.b})
        self.assertEqual({row['id'] for row in edges},{self.edge})
        self.assertNotIn(transient_edge,{row['id'] for row in edges})
        with vector_store.VectorStore(conf) as store:
            self.assertEqual(store.get()['ids'],[f'knowledge_note_{other_note}'])
        self.assertFalse((vault/'notes'/f'note-{self.note}.md').exists())
        self.assertTrue((vault/'notes'/f'note-{other_note}.md').exists())
        self.assertEqual(obsidian_export.validate_obsidian_export(vault)['unresolvable_wikilinks'],0)
        with closing(db.get_connection()) as conn:
            source=db.message_metadata(db.load_messages(self.chat)[1])['sources'][0]
            self.assertTrue(source['deleted'])
            self.assertIsNone(source['knowledge_note_id'])
            old=load_artifact(self.artifact)['content']['sources'][0]
            self.assertTrue(old['deleted'])
            self.assertIsNone(old['knowledge_note_id'])
            usage=conn.execute('SELECT document_id,note_ids_json FROM llm_usage').fetchone()
            self.assertIsNone(usage['document_id'])
            self.assertEqual(json.loads(usage['note_ids_json']),[other_note])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM brain_proposals').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM brain_note_processing').fetchone()[0],1)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_document_delete_rolls_back_when_export_fails(self):
        from src.data_layer import document_manager
        from src.data_layer.document_deletion import delete_document
        pdf=self.root/'user_data/test.pdf'
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b'original PDF')
        conf=EmbeddingConfig('openai','rollback-test','',3)
        with vector_store.VectorStore(conf) as store:
            store.upsert([prepare_record(load_notes()[0])],[[1.,0.,0.]])
        vault=obsidian_export.export_course_to_obsidian('SWA')
        before={p.relative_to(vault):p.read_bytes() for p in vault.rglob('*') if p.is_file()}
        with (
            patch.object(document_manager,'USER_DATA_PATH',self.root/'user_data'),
            patch('src.data_layer.document_deletion.export_course_to_obsidian',
                  side_effect=OSError('Simulierter Exportfehler')),
            self.assertRaises(OSError),
        ):
            delete_document(self.doc)
        self.assertEqual(pdf.read_bytes(),b'original PDF')
        self.assertTrue(self.note_path.exists())
        self.assertEqual(len(load_notes()),1)
        self.assertEqual(len(load_graph('SWA')[1]),1)
        self.assertEqual(before,{p.relative_to(vault):p.read_bytes() for p in vault.rglob('*') if p.is_file()})
        with vector_store.VectorStore(conf) as store:
            self.assertEqual(store.get()['ids'],[f'knowledge_note_{self.note}'])
        with closing(db.get_connection()) as conn:
            self.assertIsNotNone(conn.execute('SELECT id FROM documents WHERE id=?',(self.doc,)).fetchone())
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_navigation_headings_grouped_documents_and_confirmed_delete(self):
        from src.data_layer import document_manager
        with closing(db.get_connection()) as conn, conn:
            other=conn.execute("INSERT INTO documents(filename,file_hash,course,file_path,processed) "
                               "VALUES ('db.pdf','db-hash','Datenbanken','user_data/db.pdf',0)").lastrowid
        pdf=self.root/'user_data/test.pdf'
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b'test pdf')
        with (
            patch('src.ui.settings.connection_status',return_value=(False,'offline')),
            patch('src.ui.brain.atlas_graph',return_value=None),
            patch.object(document_manager,'USER_DATA_PATH',self.root/'user_data'),
        ):
            app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
            for page in ('Unterlagen','Wissensatlas','Einstellungen','Generierte Notes','Lernen'):
                app.radio(key='navigation').set_value(page).run(timeout=30)
                headings=[item.value for item in app.get('html') if '<h1' in item.value]
                self.assertEqual(len(headings),1,(page,headings))
                self.assertIn(f'>{page}</h1>',headings[0])
                self.assertIn('translate="no"',headings[0])
                self.assertEqual(len(app.exception),0)
            app.radio(key='navigation').set_value('Unterlagen').run(timeout=30)
            titles=[item.value for item in app.subheader]
            self.assertIn('SWA',titles)
            self.assertIn('Datenbanken',titles)
            self.assertTrue(any(item.key==f'delete_document_{self.doc}' for item in app.button))
            app.button(key=f'delete_document_{self.doc}').click().run(timeout=30)
            self.assertTrue(any(item.key==f'confirm_document_{self.doc}' for item in app.button))
            app.button(key=f'cancel_document_{self.doc}').click().run(timeout=30)
            self.assertEqual(len(load_notes()),1)
            app.button(key=f'delete_document_{self.doc}').click().run(timeout=30)
            app.button(key=f'confirm_document_{self.doc}').click().run(timeout=30)
            self.assertFalse(pdf.exists())
            self.assertEqual(len(load_notes()),0)
            self.assertTrue(any(item.key==f'delete_document_{other}' for item in app.button))
            self.assertEqual(len(app.exception),0)


    def test_atlas_concept_click_renders_original_pdf_without_llm(self):
        with (
            patch('src.ui.brain.atlas_graph', return_value={'kind':'concept','id':self.a,'event_id':'pdf-test'}),
            patch('src.ui.learning._load_chat_source', return_value={
                'title':'Capability','pdf_path':'test.pdf','modified_ns':1,'note_text':'INTERNAL CONTEXT'
            }) as loader,
            patch('src.ui.learning._render_source_pdf_page', return_value=b'png') as render,
            patch('src.ui.learning.st.image') as image,
            patch('src.llm.llm_service.LLMService.generate') as llm,
        ):
            app=AppTest.from_file(str(ROOT/'app.py')).run(timeout=30)
            app.radio(key='navigation').set_value('Wissensatlas').run(timeout=30)
            self.assertEqual(len(app.exception),0)
            loader.assert_called_once_with(self.note)
            render.assert_called_once_with('test.pdf',1,1)
            self.assertEqual(image.call_args.kwargs['caption'],'PDF-Seite 1')
            llm.assert_not_called()
            self.assertNotIn('INTERNAL CONTEXT',[item.value for item in app.text])

class ProviderStatusTests(unittest.TestCase):
    def tearDown(self): _cached_status.clear()

    def test_no_key_no_request(self):
        with patch('src.llm.connection_status.openai.OpenAI') as client:
            self.assertFalse(connection_status(LLMConfig('openai','test',''))[0])
            client.assert_not_called()

    def test_provider_metadata_only_cache_and_invalid(self):
        _cached_status.clear()
        with patch('src.llm.connection_status.openai.OpenAI') as factory:
            client=factory.return_value.__enter__.return_value
            conf=LLMConfig('openai','test','fake-key')
            self.assertTrue(connection_status(conf)[0])
            self.assertTrue(connection_status(conf)[0])
            client.models.retrieve.assert_called_once_with('test')
            client.responses.parse.assert_not_called()
            client.models.retrieve.side_effect=RuntimeError('private-key not exposed')
            ok,detail=connection_status(LLMConfig('openai','different','fake-key'))
            self.assertFalse(ok); self.assertNotIn('private-key',detail)
        with patch('src.llm.connection_status.genai.Client') as factory:
            client=factory.return_value.__enter__.return_value
            self.assertTrue(connection_status(LLMConfig('gemini','model','fake-key'))[0])
            client.models.get.assert_called_once_with(model='model')
            client.interactions.create.assert_not_called()

    def test_embedding_defaults_and_existing_override(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config,'ENV_PATH',Path(folder)/'.env'), patch.dict('os.environ',{'OPENAI_EMBEDDING_MODEL':'','GEMINI_EMBEDDING_MODEL':''}):
            self.assertEqual(config.load_embedding_config('openai').model,DEFAULT_EMBEDDING_MODELS['openai'])
            config.ENV_PATH.write_text('OPENAI_EMBEDDING_MODEL=compatible-existing\n',encoding='utf-8')
            self.assertEqual(config.load_embedding_config('openai').model,'compatible-existing')

