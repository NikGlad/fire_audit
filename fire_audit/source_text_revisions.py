"""Evidence-based exception for targeted numeric updates after new client inputs."""
from __future__ import annotations

import re
from typing import Any


def authorized_source_text_revisions(registry: dict[str, Any]) -> bool:
    records = registry.get('targeted_source_text_revisions')
    if not isinstance(records, list) or not records or any(not isinstance(r, dict) for r in records):
        return False
    items = registry.get('protocol', {}).get('items', [])
    mains = {i.get('source', {}).get('main_text_id'): i.get('source', {}) for i in items}
    source_ids = {h for s in mains.values() for h in s.get('entity_ids', [])}
    before = {e.get('handle'): e for e in registry.get('graphics_before', [])}
    after = {e.get('handle'): e for e in registry.get('verified_graphics', [])}
    actual_changes = set()
    geometry = ('location', 'position', 'startPoint', 'endPoint', 'vertices', 'rotation',
                'height', 'width', 'widthFactor', 'style', 'closed')
    for h in source_ids:
        if h not in before or h not in after:
            return False
        a, b = before[h], after[h]
        if any(a.get(k) != b.get(k) for k in geometry):
            return False
        if a.get('textString') != b.get('textString'):
            return False  # never authorize renumbering
        if a.get('contents') != b.get('contents'):
            actual_changes.add(h)
    validated = set()
    for r in records:
        h = r.get('entity_id')
        if (h not in mains or h in validated or h not in before or h not in after
                or r.get('user_authorized') is not True or r.get('fresh_read_verified') is not True
                or r.get('history_preserved') is not True or not r.get('reason') or not r.get('normative_basis')
                or r.get('number') != mains[h].get('original_number')
                or before[h].get('type') != 'MText' or after[h].get('type') != 'MText'
                or r.get('before') != before[h].get('contents')
                or r.get('after') != after[h].get('contents')
                or mains[h].get('full_text') != r.get('before')
                or h not in registry.get('changed_entity_ids', [])):
            return False
        text = r['before']
        replacements = r.get('replacements')
        if not isinstance(replacements, list) or not replacements:
            return False
        for change in replacements:
            if not isinstance(change, dict):
                return False
            old, new = change.get('from'), change.get('to')
            if (not isinstance(old, str) or not isinstance(new, str) or not old or old == new
                    or not re.search(r'\d', old) or not re.search(r'\d', new)
                    or '\\' in old or '\\' in new or text.count(old) != 1):
                return False
            protected = [text.lower().find(marker) for marker in ('вариант', 'ответ заказчика', 'пояснени')]
            boundary = min([p for p in protected if p >= 0], default=len(text))
            if text.index(old) + len(old) > boundary:
                return False  # original solutions and conversation history are immutable
            text = text.replace(old, new, 1)
        if text != r['after']:
            return False
        validated.add(h)
    return actual_changes == validated
