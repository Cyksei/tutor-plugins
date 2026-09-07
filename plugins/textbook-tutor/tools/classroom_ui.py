import json
from pathlib import Path


def render_panel(data):
    # Exclude event payloads and unrelated notebook content from the UI.
    payload = {k: data.get(k) for k in ['note', 'note_sha256', 'state', 'updated_at', 'chapter_headings']}
    payload['title'] = Path(data['note']).stem
    literal = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    result = (Path(__file__).parent / 'classroom-panel.html').read_text().replace('__TUTOR_DATA__', literal)
    if len(result.encode()) >= 1024 * 1024:
        raise ValueError('Panel content too large; save a concise current lesson snapshot')
    return result
