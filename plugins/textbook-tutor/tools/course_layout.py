"""Non-destructive Markdown section routing and live progress projection."""
import hashlib
import json
import re

PSTART = '<!-- tutor-progress:start -->'
PEND = '<!-- tutor-progress:end -->'


def headings(text):
    rows = []
    offset = 0
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None: fence = token
            elif token[0] == fence[0] and len(token) >= len(fence): fence = None
        elif fence is None:
            match = re.match(r'^(#{1,6})\s+(.+?)\s*#*\s*$', line.rstrip())
            if match: rows.append((offset, offset + len(line), len(match[1]), match[2]))
        offset += len(line)
    return rows


def section(text, path, create=True):
    begin, end, level = 0, len(text), 0
    for title in path:
        if not isinstance(title, str) or not title.strip() or '\n' in title or len(title) > 200:
            raise ValueError('Invalid section heading')
        rows = headings(text)
        matches = [r for r in rows if begin <= r[0] < end and r[3] == title and r[2] > level]
        if len(matches) > 1: raise ValueError('AMBIGUOUS_SECTION: provide a more specific section_path')
        if not matches:
            if not create: return text, None
            depth = max(2, level + 1)
            if depth > 6: raise ValueError('Section path too deep')
            chunk = '\n\n' + '#' * depth + ' ' + title + '\n\n'
            text = text[:end] + chunk + text[end:]
            begin = end + len(chunk)
            end = begin
            level = depth
        else:
            row = matches[0]
            begin, level = row[1], row[2]
            end = next((r[0] for r in rows if r[0] >= begin and r[2] <= level), len(text))
    return text, (begin, end, level)


def append_section(text, path, content):
    text, bounds = section(text, path)
    _, end, _ = bounds
    return text[:end].rstrip() + '\n\n' + content.strip() + '\n\n' + text[end:]


def fmt(value):
    if isinstance(value, str): return value.replace('\n', ' / ')
    return json.dumps(value, ensure_ascii=False)


def progress(text, state, timestamp, previous_hash=None):
    body = '\n'.join([PSTART, '**最近保存：' + timestamp + '**',
        '- 当前学习：' + fmt(state['position']), '- 待答问题：' + fmt(state['pending_question'] or '无'),
        '- 提示阶段：' + {'none':'无待答题','awaiting_answer':'等待作答','hint_given':'已给提示，等待重答','explained_awaiting_check':'已讲解，等待检查'}[state['hint_stage']],
        '- 学习速度：' + fmt(state['pace']), '- 下一步：' + fmt(state['next_step']), PEND])
    if PSTART in text or PEND in text:
        if text.count(PSTART) != 1 or text.count(PEND) != 1: raise ValueError('Damaged progress block')
        start, end = text.index(PSTART), text.index(PEND) + len(PEND)
        old = text[start:end]
        if previous_hash and hashlib.sha256(old.encode()).hexdigest() != previous_hash:
            raise ValueError('PROGRESS_EDITED: preserve and merge user edits before saving')
        text = text[:start] + body + text[end:]
    else:
        # Prefer an existing progress section; otherwise place a new one near the top.
        candidates = [r for r in headings(text) if r[2] == 2 and r[3] == '当前进度']
        if len(candidates) > 1: raise ValueError('Ambiguous current progress section')
        if candidates:
            row = candidates[0]
            end = next((r[0] for r in headings(text) if r[0] >= row[1] and r[2] <= 2), len(text))
            legacy = text[row[1]:end].strip()
            replacement = '\n' + body + '\n\n'
            if legacy: replacement += '### 原有进度与手工补充（保留参考）\n\n' + legacy + '\n\n'
            text = text[:row[1]] + replacement + text[end:]
        else:
            title = next((r for r in headings(text) if r[2] == 1), None)
            offset = title[1] if title else 0
            if not title and text.startswith('---\n'):
                end = text.find('\n---', 4)
                if end >= 0: offset = text.find('\n', end + 1) + 1
            text = text[:offset] + '\n## 当前进度\n\n' + body + '\n\n' + text[offset:]
    return text, hashlib.sha256(body.encode()).hexdigest()
