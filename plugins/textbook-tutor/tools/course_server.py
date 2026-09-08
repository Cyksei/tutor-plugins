"""Local, newline-delimited stdio MCP tools. No network or extra dependencies."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote
import fcntl
sys.path.insert(0, str(Path(__file__).resolve().parent))
from course_layout import append_section, progress, headings
from teaching_policy import decide, validate_transition
import course_notes
from course_notes import split_course, read_knowledge, update_knowledge, create_course

START = '<!-- textbook-tutor-state:v1\n'
END = '\ntextbook-tutor-state:end -->'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def root():
    config = Path(os.environ.get('TUTOR_SETTINGS_FILE', str(Path.home() / '.config/textbook-tutor/settings.json')))
    if not config.is_file():
        raise ValueError('STORAGE_NOT_CONFIGURED: complete the clickable directory setup first')
    data = json.loads(config.read_text())
    directory = Path(data['lessons_dir'])
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError('STORAGE_UNAVAILABLE: ask the user; do not create a replacement directory')
    return directory.resolve()


def locate(name, suffix=None):
    base = root()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or path == base:
        raise ValueError('Path must be inside the selected Lessons directory')
    if suffix and path.suffix.lower() != suffix:
        raise ValueError('Unexpected file type')
    if not path.is_file():
        raise ValueError('File does not exist; create a course only after directory confirmation')
    return path


def unpack(text):
    if START not in text:
        if END in text:
            raise ValueError('Damaged course state; no changes made')
        return {'revision': 0, 'state': {}, 'events': {}}
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError('Ambiguous course state; no changes made')
    data = json.loads(text.split(START, 1)[1].split(END, 1)[0])
    if not isinstance(data, dict) or not isinstance(data.get('revision'), int) or data['revision'] < 0 or not isinstance(data.get('state'), dict) or not isinstance(data.get('events'), dict):
        raise ValueError('Invalid course state')
    return data


def read_course(note):
    path, notebook = course_notes.resolve(sys.modules[__name__], note)
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    data = unpack(text)
    return {'note': str(path), 'note_sha256': digest(raw), **course_notes.status(sys.modules[__name__], notebook, data['revision']), **{k: v for k, v in data.items() if k != 'events'}, 'recent_events': dict(list(data['events'].items())[-20:]),
            'needs_initial_state': data['revision'] == 0,
            'note_excerpt': text[:18000] if data['revision'] == 0 else None}


def _coerce_question_state(previous_state, state):
    previous = previous_state if isinstance(previous_state, dict) else {}

    if 'question_options' not in state:
        if state.get('pending_question') == previous.get('pending_question'):
            state['question_options'] = previous.get('question_options', [])
        elif state.get('pending_question'):
            state['question_options'] = []
        else:
            state['question_options'] = []
    if 'hints_given' not in state:
        state['hints_given'] = previous.get('hints_given', []) if state.get('pending_question') == previous.get('pending_question') else []
    if 'sources' not in state:
        state['sources'] = previous.get('sources', [])

    for key in ['question_options', 'hints_given', 'sources']:
        if not isinstance(state[key], list):
            raise ValueError(key + ' must be an array')


def plan_teaching_turn(note, expected_sha256, event):
    """Read-only preflight; saving re-evaluates under the course lock."""
    current = read_course(note)
    if current['note_sha256'] != expected_sha256:
        raise ValueError('CONFLICT: reload the course before planning')
    result = decide(current['state'], current.get('learning'), event)
    return {'note_sha256': expected_sha256, **{k: v for k, v in result.items() if k != 'learning'}}


def record_event(note, expected_sha256, event_id, event, state, note_entry, section_path=None):
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
        raise ValueError('Provide a stable event_id for safe retries')
    if not isinstance(event, dict) or not isinstance(state, dict) or not isinstance(note_entry, str):
        raise ValueError('Invalid event, state or note entry')
    if any(marker in note_entry for marker in [START, END, '<!-- tutor-progress:']) or len(note_entry) > 50000:
        raise ValueError('Invalid note entry')
    required = {'position', 'pending_question', 'hint_stage', 'pace', 'next_step', 'review_points'}
    if not required.issubset(state):
        raise ValueError('State requires position, pending_question, hint_stage, pace, next_step, review_points')
    if state.get('hint_stage') not in ['none', 'awaiting_answer', 'hint_given', 'explained_awaiting_check']:
        raise ValueError('Invalid hint_stage')
    if event.get('outcome') not in ['taught', 'unanswered', 'independent_correct', 'hinted_correct', 'incorrect', 'explained', 'skipped', 'preference', 'resume', 'checkpoint']:
        raise ValueError('Invalid learning outcome')

    if 'current_content' in state and not isinstance(state.get('current_content'), str):
        raise ValueError('current_content must be text')

    path, notebook = course_notes.resolve(sys.modules[__name__], note)
    if notebook is None:
        raise ValueError('SPLIT_REQUIRED: curate existing knowledge with split_course before saving new events')
    # A local advisory lock coordinates separate Codex tasks, independent of inode replacement.
    lockdir = Path(tempfile.gettempdir()) / 'textbook-tutor-locks'
    lockdir.mkdir(mode=0o700, exist_ok=True)
    with (lockdir / digest(str(path).encode())).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        raw = path.read_bytes()
        text = raw.decode('utf-8')
        data = unpack(text)
        _coerce_question_state(data.get('state'), state)
        if 'current_content' in state and not isinstance(state.get('current_content'), str):
            raise ValueError('current_content must be text')
        payload = digest(json.dumps([event, state, note_entry, section_path], sort_keys=True, ensure_ascii=False).encode())
        if event_id in data['events']:
            legacy_payload = digest(json.dumps([event, state, note_entry], sort_keys=True, ensure_ascii=False).encode())
            if data['events'][event_id]['payload'] != payload and not (section_path is None and data['events'][event_id]['payload'] == legacy_payload):
                raise ValueError('EVENT_ID_CONFLICT: this ID was used for different content')
            return {'saved': True, 'duplicate': True, 'revision': data['revision'], 'note_sha256': digest(raw)}
        if digest(raw) != expected_sha256:
            raise ValueError('CONFLICT: read the latest note and merge before retrying')
        decision = decide(data.get('state', {}), data.get('learning'), event)
        validate_transition(data.get('state', {}), state, event, decision)
        data['learning'] = decision['learning']
        timestamp = datetime.now(timezone.utc).isoformat()
        data['revision'] += 1
        data['state'] = state
        data['events'][event_id] = {**event, 'payload': payload, 'at': timestamp}
        data['updated_at'] = timestamp
        section_path = ['课堂事件记录', str(state['position'])]
        if not isinstance(section_path, list) or not 1 <= len(section_path) <= 5:
            raise ValueError('Provide an exact section_path')
        if note_entry.strip():
            text = append_section(text, section_path, '**' + timestamp + '**\n\n' + note_entry)
        error_id = event.get('error_id')
        if error_id:
            if not isinstance(error_id, str) or not error_id.replace('-', '').replace('_', '').isalnum():
                raise ValueError('Invalid error_id')
            evidence = event.get('error_evidence', '')
            if not isinstance(evidence, str) or not evidence.strip():
                raise ValueError('An error update needs actual error_evidence')
            matches = [r[3] for r in headings(text) if r[3] == error_id or r[3].startswith(error_id + '：') or r[3].startswith(error_id + ':')]
            if len(matches) > 1: raise ValueError('AMBIGUOUS_ERROR_ID: merge duplicated error headings first')
            text = append_section(text, ['错题与复习', matches[0] if matches else error_id], '**' + timestamp + '**\n\n' + evidence)
        text, data['progress_hash'] = progress(text, state, timestamp, data.get('progress_hash'))
        block = START + json.dumps(data, ensure_ascii=False, indent=2).replace('-->', '\\u002d\\u002d>') + END
        if START in text:
            left, remaining = text.split(START, 1)
            _, right = remaining.split(END, 1)
            text = left + block + right
        else:
            text = text.rstrip() + '\n\n' + block + '\n'
        temp = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
                temp = handle.name
                os.chmod(temp, path.stat().st_mode & 0o777)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if path.read_bytes() != raw:
                raise ValueError('CONFLICT: note changed during saving; read and retry')
            os.replace(temp, path)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
        after = path.read_bytes()
        if after != text.encode('utf-8'):
            raise ValueError('POST_WRITE_CONFLICT: reload note before any retry')
        return {'saved': True, 'duplicate': False, 'revision': data['revision'], 'note_sha256': digest(after), 'teaching_decision': {k: v for k, v in decision.items() if k != 'learning'}, **course_notes.status(sys.modules[__name__], notebook, data['revision'])}


def list_courses():
    courses, warnings = [], []
    for path in root().rglob('*.md'):
        try:
            path = locate(str(path), '.md')
            text = path.read_text()
            if course_notes.metadata(text):
                continue
            if START not in text and 'course_id:' not in text[:3000]:
                continue
            data = unpack(text)
            courses.append({'note': str(path), 'title': data.get('course_title') or path.stem.removesuffix('-课程进度').removeprefix('进度：').removeprefix('Progress - '), 'updated_at': data.get('updated_at'),
                            'position': data['state'].get('position'), 'has_checkpoint': data['revision'] > 0})
        except (ValueError, OSError) as exc:
            warnings.append({'note': path.name, 'error': str(exc)})
    courses.sort(key=lambda x: x['updated_at'] or '', reverse=True)
    return {'courses': courses, 'warnings': warnings}


def resume_course(note=None):
    if not note:
        inventory = list_courses()
        saved = [c for c in inventory['courses'] if c['has_checkpoint']]
        if not saved:
            return {'needs_selection_or_legacy_read': True, **inventory}
        note = saved[0]['note']
    result = read_course(note)
    text = locate(result.get('knowledge_note', result['note']), '.md').read_text()
    result['chapter_headings'] = [r[3] for r in headings(text) if r[2] in (2, 3)]
    result['resume_instruction'] = 'Resume the saved pending question and actual hint stage. Do not mark unanswered work correct or restart the chapter.'
    return result


def save_checkpoint(note, expected_sha256, event_id, state):
    required = {'current_content', 'section_path', 'question_options', 'hints_given', 'sources'}
    if not required.issubset(state):
        raise ValueError('Checkpoint needs current_content, section_path, question_options, hints_given and sources in addition to normal state')
    return record_event(note, expected_sha256, event_id, {'outcome': 'checkpoint'}, state, '', state['section_path'])


def classroom_panel(note):
    from classroom_ui import render_panel
    data = resume_course(note)
    out = Path(os.environ.get('TUTOR_PANEL_DIR', str(Path.home() / '.codex/visualizations/textbook-tutor')))
    out.mkdir(parents=True, exist_ok=True)
    path = out / ('classroom-' + digest((data['note'] + data['note_sha256']).encode())[:16] + '.html')
    path.write_text(render_panel(data))
    return {'path': str(path), 'note_sha256': data['note_sha256'],
            'display': 'Emit the Codex visualize content reference with this absolute path. Buttons send a user-confirmed follow-up; they do not silently save or grade.'}


def textbook(file, action, page=1, end_page=None, query='', image_index=0):
    from pypdf import PdfReader
    path = locate(file, '.pdf')
    reader = PdfReader(path)
    total = len(reader.pages)
    if action == 'info':
        return {'file': str(path), 'pdf_pages': total, 'sha256': digest(path.read_bytes()),
                'page_numbering': '1-based PDF file pages; not printed page numbers', 'read_status': 'Metadata inspection is not full textbook reading'}
    if not 1 <= page <= total:
        raise ValueError('PDF page out of range')
    if action == 'image':
        images = reader.pages[page - 1].images
        if not 0 <= image_index < len(images):
            raise ValueError('Embedded image unavailable; vector diagrams may require page rendering')
        im = images[image_index]
        from PIL import Image
        import io
        output = io.BytesIO()
        Image.open(io.BytesIO(im.data)).save(output, format='PNG')
        data = output.getvalue()
        if len(data) > 10 * 1024 * 1024:
            raise ValueError('Image too large for inline response; use the PDF page viewer')
        # Extraction does not prove that this embedded object is the complete textbook figure.
        return {'content': [{'type': 'text', 'text': json.dumps({'source': str(path), 'pdf_page': page, 'image_index': image_index, 'type': '教材嵌入图片提取', 'verify': 'Check completeness, caption and masks before teaching; no edits or invented figure number'}, ensure_ascii=False)},
                            {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(data).decode()}]}
    if action not in ['read', 'search']:
        raise ValueError('Unknown textbook action')
    if action == 'search' and not query.strip():
        raise ValueError('Search query is required')
    stop = min(end_page if end_page is not None else page + 9, total)
    if stop < page or stop - page >= 30:
        raise ValueError('Read/search at most 30 pages per call')
    pages = []
    for index in range(page - 1, stop):
        try:
            text = reader.pages[index].extract_text() or ''
            status = 'text_extracted' if text.strip() else 'no_text_requires_visual_read_or_OCR'
            if action == 'read' or query.casefold() in text.casefold() or not text.strip():
                match = text.casefold().find(query.casefold()) if action == 'search' else 0
                start = max(0, match - 250)
                pages.append({'pdf_page': index + 1, 'status': status,
                              'text': text[start:start + 14000] if action == 'read' else text[start:start + 1500],
                              'truncated': len(text) > (14000 if action == 'read' else 1500)})
        except Exception as exc:
            pages.append({'pdf_page': index + 1, 'status': 'extraction_failed', 'error': str(exc)})
    return {'file': str(path), 'pages': pages, 'checked_range': [page, stop],
            'next_page': stop + 1 if stop < total else None, 'total_pages': total,
            'warning': 'Extraction and search do not establish that the teacher read or understood the full textbook'}


STRING = {'type': 'string'}
def definition(name, description, properties, required, readonly=True):
    return {'name': name, 'description': description, 'inputSchema': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False},
            'annotations': {'readOnlyHint': readonly, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}}

TOOLS = [
    definition('create_course', 'After the student confirms the Lessons root for this new course, create Course - storage_name/Note, Text and Picture with paired Notes - storage_name.md and Progress - storage_name.md in Note. Use an English storage_name for filenames; title may stay in the learner’s language. Returns exact paths. Existing folders are never overwritten; inspect them to resume after an uncertain retry. Does not copy textbooks or start teaching.', {'title': STRING, 'storage_name': STRING}, ['title'], False),
    definition('split_course', 'Separate one confirmed course into a curated knowledge notebook at its original path and a sibling -课程进度.md holding the complete legacy record. Read all relevant legacy knowledge and preserve personal notes before providing knowledge_text. Safe retry; never bulk migrate.', {'note': STRING, 'expected_sha256': STRING, 'knowledge_text': STRING}, ['note', 'expected_sha256', 'knowledge_text'], False),
    definition('read_knowledge', 'Read the separate knowledge notebook and hash for targeted curation. No writes.', {'note': STRING}, ['note']),
    definition('update_knowledge', 'After record_event, curate knowledge at stable concept IDs from actual teaching and evidence. Upsert explanations, conditions, examples and sources; do not append transcript or reveal pending solutions. Empty sections acknowledge a hints/control-only turn with no safe knowledge changes. Progress and knowledge saves are separate; retry pending updates before advancing.', {'note': STRING, 'expected_sha256': STRING, 'progress_revision': {'type': 'integer'}, 'update_id': STRING, 'sections': {'type': 'array', 'items': {'type': 'object', 'properties': {'id': STRING, 'section_path': {'type': 'array', 'items': STRING}, 'content': STRING}, 'required': ['id', 'section_path', 'content'], 'additionalProperties': False}}}, ['note', 'expected_sha256', 'progress_revision', 'update_id', 'sections'], False),
    definition('plan_teaching_turn', 'Before replying, check an actual answer/control against the saved question. Returns allowed teaching action and concept-local pace advice. Does not grade facts or save; record_event rechecks.', {'note': STRING, 'expected_sha256': STRING, 'event': {'type': 'object'}}, ['note', 'expected_sha256', 'event']),
    definition('list_courses', 'List saved courses in the chosen directory; no writes.', {}, []),
    definition('resume_course', 'Resume a specific course or the most recently saved checkpoint across Codex tasks. Legacy courses require evidence-based reading.', {'note': STRING}, []),
    definition('save_checkpoint', 'Explicitly save the exact current lesson, question, options, hints, sources and progress for another Codex task.', {'note': STRING, 'expected_sha256': STRING, 'event_id': STRING, 'state': {'type': 'object'}}, ['note','expected_sha256','event_id','state'], False),
    definition('classroom_panel', 'Create a Codex inline classroom panel from the saved state. Answers, pace and save controls request a follow-up through the Codex host.', {'note': STRING}, ['note'], False),
    definition('read_course', 'Read current course state and note hash before teaching or saving. Paths stay inside the configured Lessons folder.', {'note': STRING}, ['note']),
    definition('record_event', 'Save an actual learning event and full current state atomically in the separate progress file; never writes knowledge notes. Follow with update_knowledge. Preserve handwritten content. Never store an unanswered question solution. Reuse the same event_id and payload on retry.',
               {'note': STRING, 'expected_sha256': STRING, 'event_id': STRING,
                'event': {'type': 'object', 'description': 'Actual outcome and evidence; outcome: taught/unanswered/independent_correct/hinted_correct/incorrect/explained/skipped/preference/resume'},
                'state': {'type': 'object', 'description': 'Full snapshot: position, pending_question, hint_stage (none/awaiting_answer/hint_given/explained_awaiting_check), pace, next_step, review_points'},
                'section_path': {'type': 'array', 'items': STRING, 'description': 'Legacy compatibility only; logs always route into 课堂事件记录 / position. Knowledge uses update_knowledge.'},
                'note_entry': {'type': 'string', 'description': 'Progress log: actual answers, outcomes, hints and current position; no pending answer spoilers'}},
               ['note', 'expected_sha256', 'event_id', 'event', 'state', 'note_entry'], False),
    definition('textbook', 'Read/search a bounded PDF page range or return an embedded image inline. Page numbers are 1-based PDF pages. No OCR or complete-figure guarantee; inspect the returned image.',
               {'file': STRING, 'action': {'type': 'string', 'enum': ['info', 'read', 'search', 'image']}, 'page': {'type': 'integer', 'minimum': 1},
                'end_page': {'type': 'integer', 'minimum': 1}, 'query': STRING, 'image_index': {'type': 'integer', 'minimum': 0}}, ['file', 'action'])
]


def serve():
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if 'id' not in request:
                continue
            method = request.get('method')
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': {'name': 'textbook-tutor-course-tools', 'version': '1.0.0'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': TOOLS}
            elif method == 'tools/call':
                params = request['params']
                try:
                    functions = {'create_course': create_course, 'split_course': split_course, 'read_knowledge': read_knowledge, 'update_knowledge': update_knowledge, 'plan_teaching_turn': plan_teaching_turn, 'read_course': read_course, 'record_event': record_event, 'textbook': textbook, 'list_courses': list_courses, 'resume_course': resume_course, 'save_checkpoint': save_checkpoint, 'classroom_panel': classroom_panel}
                    value = functions[params['name']](**params.get('arguments', {}))
                    result = value if 'content' in value else {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}
                except Exception as exc:
                    result = {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
            else:
                print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -32601, 'message': 'Method not found'}}), flush=True)
                continue
            print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({'jsonrpc': '2.0', 'id': request.get('id') if isinstance(request, dict) else None, 'error': {'code': -32700, 'message': str(exc)}}), flush=True)


if __name__ == '__main__':
    serve()
