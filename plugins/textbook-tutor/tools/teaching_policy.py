"""Deterministic teaching guards; semantic grading remains the teacher's job."""
from copy import deepcopy

GRADED = {'independent_correct', 'hinted_correct', 'incorrect'}
CONTROLS = {'skip', 'direct_explain', 'pause', 'preference', 'question_repair'}


def decide(previous, learning, event):
    outcome = event.get('outcome')
    pending = previous.get('pending_question')
    stage = previous.get('hint_stage', 'none')
    assessment = event.get('assessment')
    control = event.get('student_request')
    if control is not None:
        if control not in CONTROLS or not isinstance(event.get('request_evidence'), str) or not event['request_evidence'].strip():
            raise ValueError('STUDENT_REQUEST needs a supported control and actual request_evidence')
        expected = {'skip': 'skipped', 'direct_explain': 'explained', 'pause': 'unanswered',
                    'preference': 'preference', 'question_repair': 'unanswered'}[control]
        if outcome != expected or assessment is not None:
            raise ValueError('Student control must use its matching outcome without a graded assessment')
    if outcome == 'skipped' and control != 'skip':
        raise ValueError('Skipping requires the actual student request')
    if outcome in GRADED:
        if not pending:
            raise ValueError('NO_PENDING_QUESTION: cannot grade a question that was not saved')
        if not isinstance(assessment, dict):
            raise ValueError('ASSESSMENT_REQUIRED: concept_id, question, answer, evidence, support, demonstration')
        for key in ['concept_id', 'question', 'answer', 'evidence']:
            if not isinstance(assessment.get(key), str) or not assessment[key].strip():
                raise ValueError('Assessment needs actual ' + key)
        if assessment['question'] != pending:
            raise ValueError('STALE_QUESTION: assessment must match the saved pending question exactly')
        support = assessment.get('support')
        if support not in ['independent', 'hinted', 'explained']:
            raise ValueError('Invalid assessment support')
        if assessment.get('demonstration') not in ['recognition', 'reasoning', 'application', 'transfer', 'recall']:
            raise ValueError('Invalid demonstration; self-report is not a demonstrated answer')
        if outcome == 'independent_correct' and (support != 'independent' or stage == 'hint_given'):
            raise ValueError('HINTED_IS_NOT_INDEPENDENT: use hinted_correct after a hint')
        if outcome == 'hinted_correct' and support == 'independent':
            raise ValueError('hinted_correct requires hinted or explained support')
    elif assessment is not None:
        raise ValueError('Only graded outcomes may carry an assessment')

    if control:
        action = {'skip': 'advance', 'direct_explain': 'explain', 'pause': 'wait',
                  'preference': 'wait', 'question_repair': 'repair_question'}[control]
    elif outcome == 'incorrect':
        action = 'explain' if stage in ['hint_given', 'explained_awaiting_check'] else 'hint_then_wait'
    elif outcome in ['independent_correct', 'hinted_correct']:
        action = 'advance'
    elif outcome == 'explained':
        if stage != 'hint_given':
            raise ValueError('FIRST_ERROR_NEEDS_HINT: direct explanation requires an explicit student request')
        action = 'explain'
    elif outcome in ['preference', 'checkpoint', 'resume', 'unanswered']:
        action = 'wait'
    elif outcome == 'taught':
        if pending:
            raise ValueError('UNANSWERED: resolve the pending question or record an explicit skip first')
        action = 'teach'
    else:
        raise ValueError('Invalid learning outcome')

    result = deepcopy(learning or {'schema_version': 1, 'concepts': {}})
    concepts = result.setdefault('concepts', {})
    pace = 'standard'
    reason = 'No sufficient evidence for a local speed change'
    if assessment:
        cid = assessment['concept_id']
        item = concepts.setdefault(cid, {'history': [], 'independent_streak': 0})
        meaningful = assessment['demonstration'] != 'recognition'
        strong = outcome == 'independent_correct' and meaningful
        item['independent_streak'] = item.get('independent_streak', 0) + 1 if strong else 0
        item['history'] = (item.get('history', []) + [{**assessment, 'outcome': outcome}])[-12:]
        item['latest_status'] = ('transfer_demonstrated' if strong and assessment['demonstration'] == 'transfer'
                                 else 'independent_demonstrated' if strong else outcome)
        if outcome in ['incorrect', 'hinted_correct']:
            item['review_trigger'] = 'next_related_application_or_next_session'
            pace, reason = 'slow', 'Local error or dependence on support; change explanation or split this concept'
        elif item['independent_streak'] >= 3:
            pace, reason = 'fast', 'Three consecutive substantive independent answers for this concept'
            item['review_trigger'] = 'later_related_application'
        else:
            item['review_trigger'] = 'next_session' if strong else 'next_related_application'
        result['last_concept_id'] = cid
    preferences = previous.get('pace', {})
    if isinstance(preferences, dict) and (preferences.get('auto_adjust') is False or preferences.get('fixed') is True):
        pace, reason = 'keep_student_choice', 'Student requested fixed pace'
    return {'action': action, 'pace_advice': {'scope': assessment['concept_id'] if assessment else None,
            'recommendation': pace, 'reason': reason, 'override_student_preference': False},
            'learning': result, 'semantic_judgment': 'Teacher supplied; the tool checks consistency, not factual correctness'}


def validate_transition(previous, state, event, decision):
    action = decision['action']
    if action == 'advance' and event.get('outcome') in GRADED and state.get('pending_question') == previous.get('pending_question'):
        raise ValueError('RESOLVED_QUESTION: save a new question or clear the answered question')
    if action in ['wait', 'hint_then_wait']:
        for key in ['position', 'pending_question', 'question_options']:
            if previous and state.get(key) != previous.get(key, [] if key == 'question_options' else None):
                raise ValueError('WAIT_REQUIRED: preserve ' + key)
        if action == 'wait' and previous:
            for key in ['hint_stage', 'hints_given']:
                if state.get(key) != previous.get(key, [] if key == 'hints_given' else 'none'):
                    raise ValueError('WAIT_REQUIRED: preserve ' + key)
        if action == 'hint_then_wait':
            old = previous.get('hints_given', [])
            new = state.get('hints_given', [])
            if state.get('hint_stage') != 'hint_given' or new[:len(old)] != old or len(new) <= len(old) or not all(isinstance(x, str) and x.strip() for x in new):
                raise ValueError('FIRST_ERROR_NEEDS_HINT: append an actual hint and wait for another answer')
    if action == 'explain':
        if state.get('hint_stage') != 'explained_awaiting_check' or state.get('position') != previous.get('position'):
            raise ValueError('EXPLAIN_LOCALLY: save the explanation/check at the same concept before advancing')
    if action == 'repair_question' and state.get('position') != previous.get('position'):
        raise ValueError('Question repair must stay at the current position')
    if action in ['advance', 'teach', 'repair_question']:
        expected = 'awaiting_answer' if state.get('pending_question') else 'none'
        if state.get('hint_stage') != expected:
            raise ValueError('New question needs awaiting_answer; no question needs none')
        if state.get('hints_given'):
            raise ValueError('New question must not inherit hints from the old question')
    if event.get('outcome') in ['checkpoint', 'resume'] and previous:
        for key in ['pace', 'next_step', 'review_points', 'current_content']:
            if key in previous and state.get(key) != previous[key]:
                raise ValueError('Checkpoint/resume cannot silently change ' + key)
