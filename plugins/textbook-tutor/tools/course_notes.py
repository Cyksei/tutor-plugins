"""Two-file course storage. Progress is authoritative; notes acknowledge its revision."""
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
import fcntl

PAIR = '<!-- tutor-notebook:v1 '
CLOSE = ' -->'


def metadata(text):
    if PAIR not in text:
        return None
    if text.count(PAIR) != 1:
        raise ValueError('Damaged notebook link')
    raw = text.split(PAIR, 1)[1].split(CLOSE, 1)[0]
    data = json.loads(raw)
    if not isinstance(data.get('progress'), str) or not isinstance(data.get('revision'), int):
        raise ValueError('Invalid notebook link')
    return data


def marker(data):
    return PAIR + json.dumps(data, ensure_ascii=False).replace('-->', '\\u002d\\u002d>') + CLOSE


@contextmanager
def locked(path, digest):
    directory = Path(tempfile.gettempdir()) / 'textbook-tutor-locks'
    directory.mkdir(mode=0o700, exist_ok=True)
    with (directory / digest(str(path).encode())).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def replace(path, raw, text):
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            temp = handle.name
            os.chmod(temp, path.stat().st_mode & 0o777)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != raw:
            raise ValueError('CONFLICT: file changed; reload and merge')
        os.replace(temp, path)
        if path.read_bytes() != text.encode():
            raise ValueError('POST_WRITE_CONFLICT: reload before retrying')
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def resolve(server, note):
    path = server.locate(note, '.md')
    meta = metadata(path.read_text())
    if meta:
        progress = server.locate(str(path.parent / meta['progress']), '.md')
        if progress == path or metadata(progress.read_text()):
            raise ValueError('Invalid progress link')
        return progress, path
    # The conventional sibling is accepted only with a reciprocal link.
    if path.stem.startswith('进度：'):
        candidate = path.with_name('笔记：' + path.stem[len('进度：'):] + '.md')
        if candidate.is_file():
            candidate = server.locate(str(candidate), '.md')
            meta = metadata(candidate.read_text())
            if meta and server.locate(str(candidate.parent / meta['progress']), '.md') == path:
                return path, candidate
    suffix = '-课程进度'
    if path.stem.endswith(suffix):
        candidate = path.with_name(path.stem[:-len(suffix)] + '.md')
        if candidate.is_file():
            meta = metadata(candidate.read_text())
            if meta and server.locate(str(candidate.parent / meta['progress']), '.md') == path:
                return path, candidate
    return path, None


def status(server, notebook, revision):
    if notebook is None:
        return {'needs_split': True, 'knowledge_needs_update': True}
    raw = notebook.read_bytes()
    meta = metadata(raw.decode())
    return {'needs_split': False, 'knowledge_note': str(notebook),
            'knowledge_sha256': server.digest(raw), 'knowledge_revision': meta['revision'],
            'knowledge_needs_update': meta['revision'] < revision}


def split_course(note, expected_sha256, knowledge_text):
    import course_server as server
    path, notebook = resolve(server, note)
    if notebook is not None:
        return {'split': True, 'duplicate': True, **server.read_course(str(notebook))}
    if not knowledge_text.strip() or any(x in knowledge_text for x in [PAIR, server.START, server.END, '<!-- tutor-progress:']):
        raise ValueError('Provide curated knowledge only, with sources and preserved personal notes; no state blocks')
    with locked(path, server.digest):
        raw = path.read_bytes()
        if server.digest(raw) != expected_sha256:
            raise ValueError('CONFLICT: reread legacy course before splitting')
        data = server.unpack(raw.decode())
        progress = path.with_name(('进度：' + path.stem[len('笔记：'):] if path.stem.startswith('笔记：') else path.stem + '-课程进度') + '.md')
        if progress.is_symlink():
            raise ValueError('CONFLICT: progress destination is a symbolic link')
        # Exclusive creation, with recovery for interruption between copy and replacement.
        try:
            with progress.open('xb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if progress.read_bytes() != raw:
                raise ValueError('CONFLICT: progress destination already exists; preserve both files')
        meta = {'progress': progress.name, 'revision': data['revision'], 'updates': {}}
        body = knowledge_text.rstrip() + '\n\n[课程进度记录](' + quote(progress.name) + ')\n\n' + marker(meta) + '\n'
        replace(path, raw, body)
    return {'split': True, 'duplicate': False, **server.read_course(str(path))}


def read_knowledge(note):
    import course_server as server
    path, notebook = resolve(server, note)
    if notebook is None:
        raise ValueError('SPLIT_REQUIRED: use split_course first')
    raw = notebook.read_bytes()
    return {'knowledge_note': str(notebook), 'knowledge_sha256': server.digest(raw),
            'text': raw.decode(), 'progress_revision': server.unpack(path.read_text())['revision']}


def update_knowledge(note, expected_sha256, progress_revision, update_id, sections):
    """Upsert stable knowledge blocks, independently from immutable event history."""
    import course_server as server
    from course_layout import append_section
    path, notebook = resolve(server, note)
    if notebook is None:
        raise ValueError('SPLIT_REQUIRED: use split_course first')
    if not isinstance(update_id, str) or not 1 <= len(update_id) <= 128 or not isinstance(sections, list):
        raise ValueError('Provide a stable update_id and sections array')
    payload = server.digest(json.dumps([progress_revision, sections], sort_keys=True, ensure_ascii=False).encode())
    # Same order as progress saves: progress lock then notebook lock.
    with locked(path, server.digest), locked(notebook, server.digest):
        raw = notebook.read_bytes()
        text = raw.decode()
        meta = metadata(text)
        updates = meta.setdefault('updates', {})
        if update_id in updates:
            if updates[update_id] != payload:
                raise ValueError('UPDATE_ID_CONFLICT')
            return {'saved': True, 'duplicate': True, **status(server, notebook, server.unpack(path.read_text())['revision'])}
        if server.digest(raw) != expected_sha256:
            raise ValueError('CONFLICT: reread knowledge notes and preserve student edits')
        revision = server.unpack(path.read_text())['revision']
        if progress_revision != revision:
            raise ValueError('CONFLICT: progress advanced; inspect latest evidence first')
        ids = set()
        for item in sections:
            key, content, route = item.get('id'), item.get('content'), item.get('section_path')
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key) or key in ids:
                raise ValueError('Invalid or duplicate stable knowledge ID')
            ids.add(key)
            if not isinstance(content, str) or not content.strip() or len(content) > 50000 or '<!--' in content:
                raise ValueError('Knowledge content must be substantive Markdown without control comments')
            if not isinstance(route, list) or not 1 <= len(route) <= 5:
                raise ValueError('Provide an exact knowledge section_path')
            begin, end = '<!-- tutor-knowledge:' + key + ':start -->', '<!-- tutor-knowledge:' + key + ':end -->'
            block = begin + '\n' + content.strip() + '\n' + end
            if begin in text or end in text:
                if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) >= text.index(end):
                    raise ValueError('Damaged knowledge block')
                # Full-file optimistic hash ensures the teacher has read any handwritten edits.
                a, b = text.index(begin), text.index(end) + len(end)
                text = text[:a] + block + text[b:]
            else:
                text = append_section(text, route, block)
        old_marker = PAIR + text.split(PAIR, 1)[1].split(CLOSE, 1)[0] + CLOSE
        meta['revision'] = revision
        updates[update_id] = payload
        text = text.replace(old_marker, marker(meta), 1)
        replace(notebook, raw, text)
        return {'saved': True, 'duplicate': False, **status(server, notebook, revision)}


def create_course(title):
    """Create a complete course folder only after the Lessons directory is confirmed."""
    import course_server as server
    import shutil
    import uuid
    if not isinstance(title, str) or not title.strip() or title != title.strip() or len(title) > 100 or any(c in title for c in '/\\<>:"|?*') or any(ord(c) < 32 for c in title) or title in ('.', '..') or title.endswith('.'):
        raise ValueError('Use a plain course title without path separators or reserved filename characters')
    base = server.root()
    target = base / ('课程：' + title)
    with locked(base, server.digest):
        if target.exists() or target.is_symlink():
            raise ValueError('COURSE_EXISTS: inspect and resume the existing folder; never overwrite or create a duplicate course')
        stage = Path(tempfile.mkdtemp(prefix='.tutor-create-', dir=base))
        try:
            for name in ('Note', 'Text', 'Picture'):
                (stage / name).mkdir()
            knowledge = '笔记：' + title + '.md'
            progress = '进度：' + title + '.md'
            data = {'revision': 0, 'state': {}, 'events': {}, 'course_title': title, 'course_id': str(uuid.uuid4())}
            (stage/'Note'/progress).write_text('# ' + title + ' · 课程进度\n\n[课堂知识笔记](' + quote(knowledge) + ')\n\n尚未授课；备课未开始。\n\n' + server.START + json.dumps(data,ensure_ascii=False).replace('-->', '\\u002d\\u002d>') + server.END + '\n')
            meta = {'progress': progress, 'revision': 0, 'updates': {}}
            (stage/'Note'/knowledge).write_text('# ' + title + '\n\n## 教材与课程信息\n\n尚未导入教材。\n\n## 全书概览\n\n待实际备课后整理。\n\n[课程进度记录](' + quote(progress) + ')\n\n' + marker(meta) + '\n')
            os.rename(stage, target)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    return {'created': True, 'course_dir': str(target), 'text_dir': str(target/'Text'), 'picture_dir': str(target/'Picture'), **server.read_course(str(target/'Note'/knowledge))}
