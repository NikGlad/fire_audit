"""Audited input changes are not customer responses or resolved violations.

Allow only exact text edits and explicitly journaled annotation translations /
ray-origin moves. Project geometry and ray targets never receive an exemption.
"""
from __future__ import annotations

import math
from typing import Any


GEOMETRY = ('location', 'position', 'startPoint', 'endPoint', 'vertices',
            'rotation', 'height', 'width', 'widthFactor', 'style', 'closed')
PROPERTIES = ('type', 'layer', 'color', 'colorIndex', 'linetype', 'linetypeScale',
              'lineWeight', 'transparency', 'visible', 'space')


def _point(value):
    return (isinstance(value, dict) and all(isinstance(value.get(k, 0), (int, float))
            and not isinstance(value.get(k, 0), bool) and math.isfinite(value.get(k, 0))
            for k in ('x', 'y', 'z')) and 'x' in value and 'y' in value)


def _translated(a, b, delta):
    return (_point(a) and _point(b)
            and {k: v for k, v in a.items() if k not in ('x', 'y', 'z')}
            == {k: v for k, v in b.items() if k not in ('x', 'y', 'z')}
            and all(
        abs(b.get(k, 0) - a.get(k, 0) - delta.get(k, 0)) <= .001
        for k in ('x', 'y', 'z')))


def _stable(a, b, exceptions=()):
    # Snapshots from get_entities may omit common properties. Preserve every
    # recorded property, and require actual geometry, type and layer evidence.
    return (bool(a.get('type')) and bool(a.get('layer'))
            and all(a.get(k) == b.get(k) for k in GEOMETRY if k not in exceptions)
            and all(a[k] == b.get(k) for k in PROPERTIES if k in a))


def authorized_annotation_layout(registry: dict[str, Any]) -> set[str]:
    rows = registry.get('annotation_layout_revisions', [])
    if not isinstance(rows, list):
        return set()
    before = {e.get('handle'): e for e in registry.get('graphics_before', [])}
    after = {e.get('handle'): e for e in registry.get('verified_graphics', [])}
    sources = {i.get('source', {}).get('main_text_id'): i.get('source', {})
               for i in registry.get('protocol', {}).get('items', [])}
    revised = {r.get('entity_id') for r in registry.get('targeted_source_text_revisions', [])
               if isinstance(r, dict) and r.get('revision_kind') == 'changed_inputs'}
    changed = set(registry.get('changed_entity_ids', []))
    allowed: set[str] = set()
    ray_origins: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return set()
        main = row.get('main_text_id'); source = sources.get(main, {})
        ids = row.get('entity_ids', [])
        if (main not in revised or not isinstance(ids, list) or not ids
                or any(not isinstance(h, str) for h in ids) or len(ids) != len(set(ids))
                or set(ids) & allowed or not set(ids) <= changed
                or not set(ids) <= set(source.get('entity_ids', []))
                or not set(ids) <= before.keys() & after.keys()
                or not row.get('reason') or not row.get('authorization_reference')
                or row.get('fresh_read_verified') is not True
                or row.get('visual_review_verified') is not True):
            return set()
        headers = {main, source.get('number_id'), source.get('frame_id')}
        if row.get('kind') == 'group_translation':
            delta = row.get('delta')
            if (None in headers or len(headers) != 3 or set(ids) != headers
                    or not _point(delta) or not any(delta.get(k, 0) for k in ('x', 'y', 'z'))):
                return set()
            for h in ids:
                a, b = before[h], after[h]
                field = {'MText': 'location', 'DBText': 'position', 'Polyline': 'vertices'}.get(a.get('type'))
                if not field or not _stable(a, b, (field,)):
                    return set()
                if field == 'vertices':
                    aa, bb = a.get(field), b.get(field)
                    if (not isinstance(aa, list) or not isinstance(bb, list) or not aa
                            or len(aa) != len(bb) or not all(_translated(u, v, delta) for u, v in zip(aa, bb))):
                        return set()
                elif not _translated(a.get(field), b.get(field), delta):
                    return set()
        elif row.get('kind') == 'ray_origin':
            if len(ids) != 1 or set(ids) & headers:
                return set()
            a, b = before[ids[0]], after[ids[0]]
            if (a.get('type') != 'Line' or not _stable(a, b, ('startPoint',))
                    or not _point(a.get('endPoint')) or not _point(a.get('startPoint')) or not _point(b.get('startPoint'))
                    or a.get('startPoint') == b.get('startPoint')):
                return set()
            # Origin must stay beside its own MText; target is exactly immutable.
            body = after.get(main, {}); p = body.get('location'); q = b['startPoint']
            width, height, text_height = (body.get(k) for k in ('actualWidth', 'actualHeight', 'height'))
            if (not _point(p) or not all(isinstance(n, (int, float)) and math.isfinite(n) and n > 0
                                        for n in (width, height, text_height))):
                return set()
            margin = 4 * text_height
            if not (p['x'] - margin <= q['x'] <= p['x'] + width + margin
                    and p['y'] - height - margin <= q['y'] <= p['y'] + margin):
                return set()
            ray_origins.setdefault(main, []).append(q)
        else:
            return set()
        allowed.update(ids)
    if any(any(p != points[0] for p in points) for points in ray_origins.values()):
        return set()
    return allowed


def changed_input_revision_state(registry: dict[str, Any]) -> dict[str, Any]:
    """Return ownership only after verifying the complete input-change journal."""
    from .source_text_revisions import authorized_source_text_revisions, _changed_input_evidence, _replacement_text

    review = registry.get('protocol', {}).get('changed_input_review')
    state = {'entity_ids': set(), 'text_revisions_verified': False, 'errors': []}
    if review is None:
        return state
    def fail(message):
        state['errors'].append(message)
        return state
    if (not isinstance(review, dict) or review.get('author_role') != 'contractor'
            or not review.get('instruction_reference') or not review.get('impact_matrix_reference')
            or review.get('all_dependencies_reviewed') is not True
            or registry.get('created_entity_ids') or registry.get('deleted_entity_ids')):
        return fail('Пересмотр вводных: нет основания, матрицы или сохранности состава объектов')
    records = registry.get('targeted_source_text_revisions', [])
    collections = ('targeted_source_text_revisions', 'explanation_text_revisions',
                   'annotation_layout_revisions', 'graphics_before', 'verified_graphics')
    if any(not isinstance(registry.get(k, []), list)
           or any(not isinstance(row, dict) for row in registry.get(k, [])) for k in collections):
        return fail('Пересмотр вводных: повреждён состав журнала или снимков объектов')
    items_list = registry.get('protocol', {}).get('items', [])
    if (not isinstance(items_list, list) or any(not isinstance(i, dict)
            or not isinstance(i.get('source'), dict) for i in items_list)
            or not isinstance(registry.get('changed_entity_ids'), list)
            or any(not isinstance(h, str) for h in registry['changed_entity_ids'])):
        return fail('Пересмотр вводных: повреждён перечень групп или изменённых объектов')
    if not authorized_source_text_revisions(registry):
        return fail('Пересмотр вводных: не подтверждены точечные правки требований и сохранность истории')
    items = {i.get('source', {}).get('main_text_id'): i for i in registry.get('protocol', {}).get('items', [])}
    before = {e.get('handle'): e for e in registry.get('graphics_before', [])}
    after = {e.get('handle'): e for e in registry.get('verified_graphics', [])}
    authorized = {r['entity_id'] for r in records}
    for r in records:
        item = items[r['entity_id']]
        if (r.get('revision_kind') != 'changed_inputs'
                or item.get('response', {}).get('status') != 'not_required'
                or item.get('transition', {}).get('kind') != 'none'
                or item.get('input_revision_explanation') != r.get('consultant_appendix')
                or '\\C30;' not in r.get('consultant_appendix', '')
                or not _stable(before[r['entity_id']], after[r['entity_id']], ('location',))):
            return fail('Пересмотр вводных: правка ошибочно оформлена как ответ, смена слоя или не имеет оранжевого пояснения')
    extras = registry.get('explanation_text_revisions', [])
    if not isinstance(extras, list):
        return fail('Пересмотр вводных: неверный перечень зависимых пояснений')
    for r in extras:
        if not isinstance(r, dict):
            return fail('Пересмотр вводных: неверная запись зависимого пояснения')
        h = r.get('entity_id'); a, b = before.get(h, {}), after.get(h, {})
        if (not h or h in authorized or r.get('revision_kind') != 'changed_inputs'
                or not _changed_input_evidence(r, require_comment=False)
                or r.get('user_authorized') is not True or r.get('fresh_read_verified') is not True
                or r.get('history_preserved') is not True or not r.get('reason') or not r.get('normative_basis')
                or not r.get('dependency_reference') or a.get('type') != 'MText'
                or a.get('layer') != 'экспертиза пояснения' or not _stable(a, b)
                or 'замечание' in a.get('contents', '').lower()
                or r.get('before') != a.get('contents') or r.get('after') != b.get('contents')
                or _replacement_text(r, semantic=True) != r.get('after')):
            return fail('Пересмотр вводных: не подтверждено зависимое пояснение ' + str(h))
        authorized.add(h)
    layout = authorized_annotation_layout(registry)
    claimed_layout = {h for row in registry.get('annotation_layout_revisions', []) for h in row.get('entity_ids', [])}
    if layout != claimed_layout:
        return fail('Пересмотр вводных: не подтверждён перенос целой группы или начала луча')
    authorized |= layout
    actual = set(registry.get('changed_entity_ids', []))
    if actual != authorized or not authorized <= before.keys() & after.keys():
        return fail('Пересмотр вводных: журнал не охватывает ровно все изменённые объекты')
    for h in authorized:
        if before[h].get('textString') != after[h].get('textString'):
            return fail('Пересмотр вводных: изменён номер или исходный однострочный текст')
    state.update(entity_ids=authorized, text_revisions_verified=True)
    return state
