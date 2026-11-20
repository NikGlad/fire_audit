"""Evidence-based exceptions for requirement updates after changed inputs."""
from __future__ import annotations

import re
from typing import Any
from .annotation_revisions import authorized_annotation_layout


def _changed_input_evidence(record: dict[str, Any], *, require_comment: bool = True) -> bool:
    inputs = record.get('changed_inputs')
    if (record.get('norms_reread') is not True or not record.get('normative_edition')
            or not isinstance(inputs, list) or not inputs):
        return False
    for item in inputs:
        if (not isinstance(item, dict) or not item.get('parameter') or not item.get('source')
                or 'before' not in item or 'after' not in item or item['before'] == item['after']):
            return False
    if not require_comment:
        return True
    explanation = record.get('consultant_explanation')
    appendix = record.get('consultant_appendix')
    return (isinstance(explanation, str) and isinstance(appendix, str)
            and bool(re.match(r'\d{2}\.\d{2}\.\d{4} пояснение пожарного консультанта: '
                              r'Замечание изменено в связи с .+', explanation))
            and explanation in appendix)


def _replacement_text(record: dict[str, Any], *, semantic: bool) -> str | None:
    text = record.get('before')
    replacements = record.get('replacements')
    if not isinstance(text, str) or not isinstance(replacements, list) or not replacements:
        return None
    for change in replacements:
        if not isinstance(change, dict):
            return None
        old, new = change.get('from'), change.get('to')
        if (not isinstance(old, str) or not isinstance(new, str) or not old or old == new
                or text.count(old) != 1):
            return None
        if not semantic and (not re.search(r'\d', old) or not re.search(r'\d', new)
                             or '\\' in old or '\\' in new):
            return None
        markers = ['ответ заказчика', 'пояснени']
        if not semantic or record.get('variants_reviewed') is not True:
            markers.insert(0, 'вариант')
        protected = [text.lower().find(marker) for marker in markers]
        protected.extend(m.start() for m in re.finditer(
            r'\d{2}\.\d{2}\.\d{2}(?:\d{2})?\s+(?:ответ заказчика|пояснени)', text.lower()))
        boundary = min([p for p in protected if p >= 0], default=len(text))
        if text.index(old) + len(old) > boundary:
            return None
        text = text.replace(old, new, 1)
    return text


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
    layout_ids = authorized_annotation_layout(registry)
    geometry = ('location', 'position', 'startPoint', 'endPoint', 'vertices', 'rotation',
                'height', 'width', 'widthFactor', 'style', 'closed')
    for h in source_ids:
        if h not in before or h not in after:
            return False
        a, b = before[h], after[h]
        if h not in layout_ids and any(a.get(k) != b.get(k) for k in geometry):
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
        kind = r.get('revision_kind', 'numeric')
        if kind not in ('numeric', 'changed_inputs'):
            return False
        semantic = kind == 'changed_inputs'
        if semantic and not _changed_input_evidence(r):
            return False
        text = _replacement_text(r, semantic=semantic)
        if text is None:
            return False
        if semantic:
            text += r['consultant_appendix']
        if text != r['after']:
            return False
        validated.add(h)
    return actual_changes == validated
