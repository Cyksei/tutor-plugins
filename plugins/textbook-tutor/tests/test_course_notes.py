import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import course_server as server
import course_notes


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        config = self.base / 'settings.json'
        config.write_text(json.dumps({'lessons_dir': str(self.base)}))
        env = patch.dict(os.environ, {'TUTOR_SETTINGS_FILE': str(config)})
        env.start(); self.addCleanup(env.stop)
        self.note = self.base / '课程.md'
        self.note.write_text('# 课程\ncourse_id: example\n\n旧知识与手工记录\n')
        self.raw = self.note.read_bytes()
        self.initial = server.read_course(str(self.note))
        self.state = dict(position='第一章',pending_question='为什么？',hint_stage='awaiting_answer',pace={},next_step='等待',review_points=[],current_content='已教知识',section_path=['第一章'],question_options=[],hints_given=[],sources=[])

    def split(self):
        return server.split_course(str(self.note), self.initial['note_sha256'], '# 课程\n\n旧知识与手工记录\n')

    def teach(self):
        cur=server.read_course(str(self.note))
        return server.record_event(str(self.note),cur['note_sha256'],'teach',{'outcome':'taught'},self.state.copy(),'学生尚未回答')

    def update(self, uid='knowledge', sections=None):
        read=server.read_knowledge(str(self.note))
        return server.update_knowledge(str(self.note), read['knowledge_sha256'], read['progress_revision'], uid, sections if sections is not None else [{'id':'concept','section_path':['第一章','课堂知识要点'],'content':'### 概念\n条件与原因。'}])

    def test_split_preserves_old_bytes_and_inventory_identity(self):
        out=self.split()
        self.assertEqual(Path(out['note']).read_bytes(),self.raw)
        self.assertNotIn(server.START,self.note.read_text())
        self.assertIn('旧知识与手工记录',self.note.read_text())
        self.assertEqual(len(server.list_courses()['courses']),1)
        self.assertTrue(server.split_course(str(self.note),'stale','# ignored')['duplicate'])

    def test_events_only_touch_progress_and_knowledge_only_touches_notes(self):
        self.split(); before=self.note.read_bytes()
        event=self.teach()
        self.assertEqual(self.note.read_bytes(),before)
        self.assertTrue(event['knowledge_needs_update'])
        progress=Path(server.read_course(str(self.note))['note'])
        raw=progress.read_bytes()
        self.assertFalse(self.update()['knowledge_needs_update'])
        self.assertEqual(raw,progress.read_bytes())
        self.assertNotIn('学生尚未回答',self.note.read_text())
        self.assertEqual(server.resume_course()['state']['pending_question'],'为什么？')
        self.assertEqual(server.read_course(str(self.note))['note'],str(progress))

    def test_revise_concept_without_duplicates_and_preserve_manual_text(self):
        self.split(); self.teach(); self.update()
        self.note.write_text(self.note.read_text()+'\n学生自己的补充\n')
        self.update('second',[{'id':'concept','section_path':['第一章','课堂知识要点'],'content':'### 概念\n完整原因和新的对比例子。'}])
        text=self.note.read_text()
        self.assertEqual(text.count('### 概念'),1)
        self.assertIn('学生自己的补充',text)
        self.assertIn('新的对比例子',text)
        self.assertNotIn('条件与原因。',text)

    def test_stale_knowledge_and_progress_rejected_without_losing_edits(self):
        self.split(); read=server.read_knowledge(str(self.note)); self.teach()
        with self.assertRaisesRegex(ValueError,'progress advanced'):
            server.update_knowledge(str(self.note),read['knowledge_sha256'],read['progress_revision'],'old',[])
        read=server.read_knowledge(str(self.note))
        self.note.write_text(self.note.read_text()+'\n手动编辑\n')
        with self.assertRaisesRegex(ValueError,'CONFLICT'):
            server.update_knowledge(str(self.note),read['knowledge_sha256'],read['progress_revision'],'old',[])
        self.assertTrue(server.read_course(str(self.note))['knowledge_needs_update'])
        self.assertIn('手动编辑',self.note.read_text())

    def test_retry_is_idempotent_and_empty_update_can_acknowledge(self):
        self.split(); self.teach()
        read=server.read_knowledge(str(self.note))
        args=(str(self.note),read['knowledge_sha256'],read['progress_revision'],'one',[])
        server.update_knowledge(*args)
        self.assertTrue(server.update_knowledge(*args)['duplicate'])
        with self.assertRaisesRegex(ValueError,'UPDATE_ID_CONFLICT'):
            server.update_knowledge(*args[:-1],[{'id':'different'}])
        self.assertFalse(server.read_course(str(self.note))['knowledge_needs_update'])

    def test_failed_split_retries_without_overwriting_progress(self):
        with patch.object(course_notes,'replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.split()
        self.assertEqual(self.note.read_bytes(),self.raw)
        self.assertEqual((self.base/'课程-课程进度.md').read_bytes(),self.raw)
        self.split()
        other=self.base/'其他.md';other.write_text('# 其他')
        (self.base/'其他-课程进度.md').write_text('another course')
        with self.assertRaisesRegex(ValueError,'CONFLICT'):
            server.split_course(str(other),server.read_course(str(other))['note_sha256'],'# 其他')
        self.assertEqual((self.base/'其他-课程进度.md').read_text(),'another course')

    def test_legacy_checkpoint_survives_migration_and_nested_course_is_found(self):
        data={'revision':7,'state':{**self.state,'hint_stage':'hint_given','hints_given':['实际提示']},'events':{'old':{'outcome':'incorrect','payload':'old'}}}
        self.note.write_text('# 课程\n'+server.START+json.dumps(data)+server.END)
        result=server.split_course(str(self.note),server.read_course(str(self.note))['note_sha256'],'# 课程\n已讲概念')
        self.assertEqual(result['revision'],7)
        self.assertEqual(result['state']['hints_given'],['实际提示'])
        self.assertIn('old',result['recent_events'])
        sub=self.base/'existing course';sub.mkdir()
        for path in list(self.base.glob('课程*.md')): path.rename(sub/path.name)
        resumed=server.resume_course()
        self.assertEqual(resumed['state']['pending_question'],'为什么？')
        self.assertEqual(resumed['knowledge_note'],str((sub/'课程.md').resolve()))

    def test_unsplit_event_is_rejected_and_external_link_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'SPLIT_REQUIRED'): self.teach()
        self.note.write_text('# 课程\n'+course_notes.marker({'progress':'../outside.md','revision':0}))
        with self.assertRaisesRegex(ValueError,'inside'): server.read_course(str(self.note))

    def test_create_course_layout_and_pair_round_trip(self):
        result = server.create_course('测试新课')
        folder = self.base/'课程：测试新课'
        self.assertEqual({p.name for p in folder.iterdir()}, {'Note','Text','Picture'})
        self.assertEqual({p.name for p in (folder/'Note').iterdir()}, {'笔记：测试新课.md','进度：测试新课.md'})
        self.assertEqual(server.read_course(result['note'])['knowledge_note'], result['knowledge_note'])
        self.assertFalse(result['needs_split'])
        self.assertIn('测试新课', [c['title'] for c in server.list_courses()['courses']])
        with self.assertRaisesRegex(ValueError, 'COURSE_EXISTS'): server.create_course('测试新课')
        self.assertEqual(len(list(folder.rglob('*.md'))), 2)

    def test_create_course_rejects_path_escape(self):
        for title in ['../outside', '/tmp/a', '', 'a/b', 'a\\b']:
            with self.assertRaises(ValueError): server.create_course(title)
        self.assertFalse(list(self.base.glob('课程：*')))

    def test_named_legacy_note_splits_to_progress_prefix(self):
        path=self.base/'笔记：原课程.md';path.write_bytes(self.raw)
        result=server.split_course(str(path),server.read_course(str(path))['note_sha256'],'# 原课程\n保留知识')
        self.assertEqual(Path(result['note']).name, '进度：原课程.md')
        self.assertEqual(server.read_course(result['note'])['knowledge_note'],str(path.resolve()))

if __name__=='__main__': unittest.main()
