import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import course_server as server
from teaching_policy import decide


class LessonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        config = self.base / 'settings.json'
        config.write_text(json.dumps({'lessons_dir': str(self.base)}))
        previous = os.environ.get('TUTOR_SETTINGS_FILE')
        os.environ['TUTOR_SETTINGS_FILE'] = str(config)
        self.addCleanup(lambda: os.environ.pop('TUTOR_SETTINGS_FILE', None) if previous is None else os.environ.__setitem__('TUTOR_SETTINGS_FILE', previous))
        (self.base / '课程.md').write_text('# 课程\n\n手工笔记不丢失。\n')
        server.split_course('课程.md', server.read_course('课程.md')['note_sha256'], '# 课程\n\n手工笔记不丢失。\n')
        self.counter = 0
        self.state = dict(position='1.1', pending_question='结构与功能有什么联系？', question_options=['甲','乙'], hints_given=[], sources=[], hint_stage='awaiting_answer', pace={'default':'standard','auto_adjust':True}, next_step='等待回答', review_points=[], current_content='结构影响功能', section_path=['章节重点与课堂笔记','第一章','课堂记录'])
        self.save({'outcome':'taught'}, self.state)

    def save(self, event, state, event_id=None):
        self.counter += 1
        before = server.read_course('课程.md')
        return server.record_event('课程.md', before['note_sha256'], event_id or str(self.counter), event, state.copy(), '本轮实际记录。')

    def answer(self, outcome='incorrect', support='independent', concept='structure'):
        current=server.read_course('课程.md')['state']
        return {'outcome':outcome, 'assessment':dict(concept_id=concept,question=current['pending_question'],answer='学生实际回答',evidence='回答中的联系有误' if outcome=='incorrect' else '独立说明了结构与功能的关系',support=support,demonstration='reasoning')}

    def next_state(self):
        return {**self.state, 'pending_question':'换一个情境如何应用？', 'position':'1.2'}

    def test_first_error_blocks_advance_without_writing(self):
        old=(self.base/'课程-课程进度.md').read_bytes()
        with self.assertRaisesRegex(ValueError,'WAIT_REQUIRED'):
            self.save(self.answer(), self.next_state())
        self.assertEqual(old,(self.base/'课程-课程进度.md').read_bytes())

    def test_hint_resume_and_hinted_success(self):
        event=self.answer()
        hint={**self.state,'hint_stage':'hint_given','hints_given':['先看结构的特点。']}
        self.save(event,hint)
        restored=server.resume_course('课程.md')
        self.assertEqual(restored['state']['hints_given'],hint['hints_given'])
        with self.assertRaisesRegex(ValueError,'HINTED_IS_NOT_INDEPENDENT'):
            self.save(self.answer('independent_correct'),self.next_state())
        self.save(self.answer('hinted_correct','hinted'),self.next_state())
        item=server.read_course('课程.md')['learning']['concepts']['structure']
        self.assertEqual(item['independent_streak'],0)
        self.assertEqual(item['latest_status'],'hinted_correct')

    def test_repeat_error_explains_and_transfer_can_be_independent(self):
        self.save(self.answer(),{**self.state,'hint_stage':'hint_given','hints_given':['检查联系']})
        e=self.answer(support='hinted')
        plan=server.plan_teaching_turn('课程.md',server.read_course('课程.md')['note_sha256'],e)
        self.assertEqual(plan['action'],'explain')
        self.save(e,{**self.state,'pending_question':'新变式','question_options':[],'hint_stage':'explained_awaiting_check'})
        self.save(self.answer('independent_correct'),self.next_state())

    def test_skip_and_preferences_do_not_create_mastery(self):
        self.save({'outcome':'preference'},{**self.state,'pace':{'auto_adjust':False}})
        self.save({'outcome':'skipped','student_request':'skip','request_evidence':'这题跳过'},self.next_state())
        self.assertEqual(server.read_course('课程.md')['learning']['concepts'],{})

    def test_checkpoint_cannot_drop_pending_question(self):
        with self.assertRaisesRegex(ValueError,'WAIT_REQUIRED'):
            self.save({'outcome':'checkpoint'},{**self.state,'pending_question':None})
        current=server.read_course('课程.md')
        server.save_checkpoint('课程.md',current['note_sha256'],'checkpoint',self.state.copy())
        self.assertIn('手工笔记不丢失',(self.base/'课程.md').read_text())

    def test_stale_question_and_hash_rejected(self):
        event=self.answer()
        event['assessment']['question']='另一道题'
        with self.assertRaisesRegex(ValueError,'STALE_QUESTION'):
            server.plan_teaching_turn('课程.md',server.read_course('课程.md')['note_sha256'],event)
        with self.assertRaisesRegex(ValueError,'CONFLICT'):
            server.plan_teaching_turn('课程.md','old',self.answer())

    def test_retry_does_not_duplicate_evidence(self):
        event=self.answer('independent_correct')
        new=self.next_state()
        self.save(event,new,'stable')
        result=self.save(event,new,'stable')
        self.assertTrue(result['duplicate'])
        self.assertEqual(len(server.read_course('课程.md')['learning']['concepts']['structure']['history']),1)

    def test_pace_is_local_and_fixed_choice_wins(self):
        learning={}
        for i in range(3):
            e=self.answer('independent_correct')
            result=decide(self.state,learning,e)
            learning=result['learning']
        self.assertEqual(result['pace_advice']['recommendation'],'fast')
        self.assertEqual(decide(self.state,learning,self.answer('independent_correct',concept='new'))['pace_advice']['recommendation'],'standard')
        self.assertEqual(decide({**self.state,'pace':{'auto_adjust':False}},learning,self.answer('independent_correct'))['pace_advice']['recommendation'],'keep_student_choice')

    def test_self_report_and_recognition_do_not_trigger_fast_pace(self):
        e=self.answer('independent_correct')
        e['assessment']['demonstration']='self_report'
        with self.assertRaises(ValueError): decide(self.state,{},e)
        e['assessment']['demonstration']='recognition'
        learning={}
        for i in range(4):
            r=decide(self.state,learning,e)
            learning=r['learning']
        self.assertEqual(r['pace_advice']['recommendation'],'standard')

    def test_explicit_explain_and_question_repair(self):
        self.save({'outcome':'explained','student_request':'direct_explain','request_evidence':'直接告诉我原因'}, {**self.state,'hint_stage':'explained_awaiting_check'})
        self.save({'outcome':'unanswered','student_request':'question_repair','request_evidence':'这题有歧义'}, {**self.state,'pending_question':'修正后的题'})

    def test_legacy_checkpoint_does_not_invent_evidence(self):
        (self.base/'旧课.md').write_text('# 旧课\n已讲第一节，尚无作答记录。\n')
        server.split_course('旧课.md',server.read_course('旧课.md')['note_sha256'],'# 旧课\n已讲第一节，尚无作答记录。')
        old=server.read_course('旧课.md')
        server.save_checkpoint('旧课.md',old['note_sha256'],'migrate',self.state.copy())
        self.assertEqual(server.resume_course('旧课.md')['learning']['concepts'],{})

if __name__=='__main__': unittest.main()
