import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fire_audit.barriers import LAYER, SOURCE_EXPLANATIONS_LAYER
from fire_audit.barrier_remarks import inventory_digest, source_remarks_errors
from fire_audit.barrier_remarks_service import (BASELINE_FILE, INVENTORY_FILE, capture,
    record_decisions, record_dimension_layer_changes, stored_errors)
from fire_audit.barrier_remarks_moves import JOURNAL_FILE, journal
from fire_audit.io_utils import read_json, write_json


class DimensionBridge:
    """Synthetic native read responses; any CAD write fails immediately."""
    def __init__(self, path):
        self.path = str(path)
        self.entities = [
            {"handle": "A1", "type": "MText", "layer": SOURCE_EXPLANATIONS_LAYER,
             "space": "Model", "text": "Исходное замечание и ответ заказчика"},
            {"handle": "D1", "type": "AcDbRotatedDimension", "layer": SOURCE_EXPLANATIONS_LAYER,
             "space": "Model", "measurement": 1200.0, "dimensionText": "",
             "xLine1Point": [0, 0, 0], "xLine2Point": [1200, 0, 0],
             "textPosition": [600, 300, 0], "textHeight": 100.0,
             "style": "ISO-25", "colorIndex": 256, "lineweight": 0},
            {"handle": "B1", "type": "MText", "layer": LAYER, "space": "Model",
             "text": "Расстояние между проёмами предусмотреть не менее требуемого нормой."},
            {"handle": "C1", "type": "Line", "layer": LAYER, "space": "Model",
             "startPoint": [-1400, 300, 0], "endPoint": [650, 300, 0],
             "colorIndex": 256, "linetype": "ByLayer", "lineweight": -1},
        ]
        self.boxes = {"D1": [-5, -5, 1250, 500], "B1": [-2000, 100, -1400, 500]}
        self.calls = []
        self.dimension_reads = 0
        self.mutate_on_second_read = False
        self.truncate_dimensions = False
        self.nested = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def entity(self, handle):
        return next(row for row in self.entities if row['handle'] == handle)

    def call(self, name, args=None):
        args = args or {}
        self.calls.append((name, copy.deepcopy(args)))
        if name == 'get_drawing_info':
            return {'path': self.path, 'modified': True, 'lastSavedDate': '2026-10-26', 'entityCount': len(self.entities)}
        if name == 'list_layers':
            return {'layers': [{'name': name} for name in ('0', SOURCE_EXPLANATIONS_LAYER, LAYER)], 'count': 3}
        if name == 'list_blocks':
            return {'blocks': [{'name': 'nested', 'entityCount': 1}] if self.nested else [], 'count': int(self.nested)}
        if name == 'get_block_definition':
            return {'name': 'nested', 'entityCount': 1, 'byLayer': {LAYER: 1}}
        if name == 'get_entities':
            if args.get('space') != 'all' or args.get('includeProperties') is not True:
                raise AssertionError('Full read with native properties required')
            rows = self.entities
            if args.get('layer'):
                rows = [row for row in rows if row['layer'] == args['layer']]
            if args.get('entityType') == 'Dimension':
                self.dimension_reads += 1
                if self.mutate_on_second_read and self.dimension_reads % 2 == 0:
                    self.entity('D1')['measurement'] += 1
                rows = [row for row in rows if row['type'].endswith('Dimension')]
            if args.get('entityTypes'):
                rows = [row for row in rows if row['type'] in args['entityTypes']]
            offset, limit = args['offset'], args['limit']
            page = rows[offset:offset + limit]
            result = {'entities': copy.deepcopy(page), 'count': len(page), 'matched': len(rows),
                      'hasMore': offset + len(page) < len(rows)}
            if self.truncate_dimensions and args.get('entityType'):
                result['truncated'] = True
            return result
        if name == 'get_entity_properties':
            rows = [row for row in self.entities if row['handle'] in args['handles']]
            return {'success': True, 'entities': copy.deepcopy(rows), 'count': len(rows), 'errors': []}
        if name == 'get_bounding_box':
            box = self.boxes[args['handles'][0]]
            return dict(zip(('minX', 'minY', 'maxX', 'maxY'), box), entitiesMeasured=1, excludedCount=0)
        raise AssertionError('No CAD write/save/switch permitted: ' + name)


class TinyStore:
    def __init__(self, folder, bridge):
        self.folder = folder
        self.source_remarks_bridge_factory = lambda: bridge

    def load(self, slug, run_id):
        return {'folder': str(self.folder), 'run': read_json(self.folder / 'run.json'),
                'registry': read_json(self.folder / 'РЕЕСТР_ПРОВЕРКИ.json')}

    def refresh_project_history(self, slug):
        pass


class DimensionLayerChangeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'source.dwg'
        self.source.write_bytes(b'synthetic source')
        self.result = self.root / 'source__barrier_assessment__run.dwg'
        self.result.write_bytes(b'synthetic result')
        self.folder = self.root / 'run'
        self.folder.mkdir()
        self.bridge = DimensionBridge(self.source)
        self.store = TinyStore(self.folder, self.bridge)
        write_json(self.folder / 'run.json', {'run_id': 'run', 'project_slug': 'test',
            'mode': 'barrier_assessment', 'task_variant': 'full_execution',
            'project_snapshot': {'dwg': {'path': str(self.source)}}})
        write_json(self.folder / 'РЕЕСТР_ПРОВЕРКИ.json', {'dwg_actions': {
            'source_path': str(self.source), 'result_copy_path': str(self.result), 'result_copy_created': True}})
        capture(self.store, 'test', 'run')
        self.baseline_bytes = (self.folder / BASELINE_FILE).read_bytes()
        self.classify()
        self.bridge.path = str(self.result)

    def load(self):
        return self.store.load('test', 'run')

    def classify(self):
        inv = self.load()['registry']['barrier_assessment']['source_remarks']['inventory']
        decisions = [
            {'id': 'remark', 'source_entity_ids': ['A1'], 'kind': 'remark', 'decision': 'already_satisfied',
             'status': 'resolved', 'summary': 'Содержание сохранено', 'basis': 'Синтетическое основание',
             'context_review': 'Замечание и ответ рассмотрены', 'all_requirements_reviewed': True,
             'result_refs': ['report:1'], 'inventory_digest': inv['digest']},
            {'id': 'measurement', 'source_entity_ids': ['D1'], 'kind': 'non_remark', 'decision': 'non_remark',
             'status': 'resolved', 'non_remark_kind': 'measurement', 'summary': 'Рабочий размер расстояния',
             'basis': 'Самостоятельный размер, не текст замечания', 'context_review': 'Параметр и исходная группа проверены',
             'result_refs': [], 'inventory_digest': inv['digest']},
        ]
        return record_decisions(self.store, 'test', 'run', decisions)

    def request(self, target=LAYER):
        inv = self.load()['registry']['barrier_assessment']['source_remarks']['inventory']
        original = read_json(self.folder / BASELINE_FILE)
        return {'handle': 'D1', 'baseline_digest': original['digest'], 'inventory_digest': inv['digest'],
            'classification_decision_id': 'measurement', 'target_layer': target, 'measurement_value': 1200,
            'measurement_points': [[0, 0, 0], [1200, 0, 0]],
            'parameter_review': 'Расстояние именно между двумя установленными проёмами',
            'absence_review': 'Все рабочие лучи прочитаны, адресующих эту размерную область нет',
            'ray_reviews': [{'ray_handle': 'C1', 'note_handle': 'B1',
                'note_excerpt': 'Расстояние между проёмами', 'parameter_review': 'Требование относится к измеренным проёмам',
                'geometry_review': 'Начало у текста требования, конец у фактически показанного размерного числа'}] if target == LAYER else []}

    def register(self, request=None):
        return record_dimension_layer_changes(self.store, 'test', 'run', [request or self.request()])

    def move(self):
        self.bridge.entity('D1')['layer'] = LAYER

    def assert_no_journal(self):
        self.assertFalse((self.folder / JOURNAL_FILE).exists())
        self.assertEqual((self.folder / BASELINE_FILE).read_bytes(), self.baseline_bytes)

    def test_same_handle_layer_only_passes_and_baseline_stays_immutable(self):
        self.move()
        result = self.register()
        self.assertEqual(result['change_count'], 1)
        captured = capture(self.store, 'test', 'run')
        self.assertEqual([row['handle'] for row in captured['inventory']['entities']], ['A1'])
        self.assertEqual([row['handle'] for row in captured['inventory']['tracked_dimensions']], ['D1'])
        self.assertFalse(captured['complete'])  # New digest requires renewed decisions.
        self.assertTrue(self.classify()['complete'])
        loaded = self.load()
        self.assertEqual(stored_errors(loaded['run'], loaded['registry'], self.folder), [])
        self.assertEqual((self.folder / BASELINE_FILE).read_bytes(), self.baseline_bytes)
        dimension_reads = [args for name, args in self.bridge.calls if name == 'get_entities' and args.get('entityType')]
        self.assertGreaterEqual(len(dimension_reads), 4)
        self.assertTrue(all('layer' not in args and args['space'] == 'all' for args in dimension_reads))

    def test_unregistered_move_still_blocks_original_guard(self):
        self.move()
        with self.assertRaisesRegex(ValueError, 'отсутствуют'):
            capture(self.store, 'test', 'run')
        self.assert_no_journal()

    def test_geometry_number_style_and_text_changes_are_not_layer_exception(self):
        original = copy.deepcopy(self.bridge.entity('D1'))
        for key, value in [('measurement', 1300), ('xLine2Point', [1300, 0, 0]),
                           ('style', 'OTHER'), ('dimensionText', 'Подменено'), ('colorIndex', 1),
                           ('textPosition', [600, 400, 0])]:
            with self.subTest(field=key):
                self.bridge.entities[1] = {**copy.deepcopy(original), 'layer': LAYER, key: value}
                with self.assertRaisesRegex(ValueError, 'только изменение слоя'):
                    self.register()
                self.assert_no_journal()

    def test_changed_protected_remark_blocks_even_valid_dimension_move(self):
        self.move()
        self.bridge.entity('A1')['text'] = 'Подменён исходный ответ'
        with self.assertRaisesRegex(ValueError, 'изменены исходные'):
            self.register()
        self.assert_no_journal()

    def test_text_entity_cannot_be_reclassified_into_layer_exception(self):
        request = self.request()
        request['handle'] = 'A1'
        request['classification_decision_id'] = 'remark'
        with self.assertRaisesRegex(ValueError, 'DIMENSION'):
            self.register(request)
        self.assert_no_journal()

    def test_missing_dimension_and_stale_digests_are_rejected(self):
        self.move()
        for key in ('baseline_digest', 'inventory_digest'):
            request = self.request()
            request[key] = '0' * 64
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Устаревшая'):
                self.register(request)
        self.bridge.entities = [row for row in self.bridge.entities if row['handle'] != 'D1']
        with self.assertRaisesRegex(ValueError, 'отсутствует'):
            self.register()
        self.assert_no_journal()

    def test_fake_ray_false_excerpt_and_wrong_parameter_do_not_authorize_move(self):
        self.move()
        requests = []
        request = self.request(); request['ray_reviews'][0]['ray_handle'] = 'FF'; requests.append(request)
        request = self.request(); request['ray_reviews'][0]['note_excerpt'] = 'Другое требование'; requests.append(request)
        request = self.request(); request['measurement_value'] = 999; requests.append(request)
        request = self.request(); request['measurement_points'][0] = [1, 1, 0]; requests.append(request)
        request = self.request(); request['ray_reviews'][0]['geometry_review'] = ''; requests.append(request)
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.register(request)
            self.assert_no_journal()

    def test_far_or_wrong_origin_ray_and_incomplete_native_reads_block(self):
        self.move()
        original = copy.deepcopy(self.bridge.entity('C1'))
        for field, value in [('endPoint', [9000, 9000, 0]), ('startPoint', [-5000, -5000, 0]), ('lineweight', 50)]:
            self.bridge.entities[3] = {**original, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.register()
        self.bridge.entities[3] = original
        self.bridge.truncate_dimensions = True
        with self.assertRaisesRegex(ValueError, 'Неполное'):
            self.register()
        self.assert_no_journal()

    def test_second_native_read_change_blocks_append(self):
        self.move()
        self.bridge.dimension_reads = 0
        self.bridge.mutate_on_second_read = True
        with self.assertRaises(ValueError):
            self.register()
        self.assert_no_journal()

    def test_nested_working_objects_are_explicitly_blocked(self):
        self.move()
        self.bridge.nested = True
        with self.assertRaisesRegex(ValueError, 'вложенные источники'):
            self.register()
        self.assert_no_journal()

    def test_return_without_rays_is_a_new_journal_entry_not_overwrite(self):
        self.move()
        self.register()
        capture(self.store, 'test', 'run')
        self.classify()
        first = (self.folder / JOURNAL_FILE).read_bytes()
        self.bridge.entity('D1')['layer'] = SOURCE_EXPLANATIONS_LAYER
        with self.assertRaises(ValueError):
            self.register(self.request(SOURCE_EXPLANATIONS_LAYER))  # Existing ray disproves absence.
        self.assertEqual((self.folder / JOURNAL_FILE).read_bytes(), first)
        self.bridge.entities = [row for row in self.bridge.entities if row['handle'] != 'C1']
        self.register(self.request(SOURCE_EXPLANATIONS_LAYER))
        self.assertTrue((self.folder / JOURNAL_FILE).read_bytes().startswith(first))
        self.assertEqual(len((self.folder / JOURNAL_FILE).read_text(encoding='utf-8').splitlines()), 2)
        captured = capture(self.store, 'test', 'run')
        self.assertEqual(captured['inventory']['tracked_dimensions'], [])
        self.assertEqual({row['handle'] for row in captured['inventory']['entities']}, {'A1', 'D1'})
        self.assertTrue(self.classify()['complete'])
        self.assertEqual((self.folder / BASELINE_FILE).read_bytes(), self.baseline_bytes)

    def test_empty_ray_claim_and_unknown_leader_geometry_are_not_absence_proof(self):
        self.move()
        request = self.request(); request['ray_reviews'] = []
        with self.assertRaisesRegex(ValueError, 'луч'):
            self.register(request)
        self.bridge.entities.append({'handle': 'C2', 'type': 'Leader', 'layer': LAYER, 'space': 'Model'})
        with self.assertRaisesRegex(ValueError, 'геометрия луча'):
            self.register()
        self.assert_no_journal()

    def test_added_ray_can_refresh_evidence_without_artificial_layer_moves(self):
        self.move(); self.register(); capture(self.store, 'test', 'run'); self.classify()
        first = (self.folder / JOURNAL_FILE).read_bytes()
        self.bridge.entities.extend([
            {'handle': 'B2', 'type': 'MText', 'layer': LAYER, 'space': 'Model',
             'text': 'Дополнительное самостоятельное требование к тому же расстоянию.'},
            {**copy.deepcopy(self.bridge.entity('C1')), 'handle': 'C2',
             'startPoint': [-1400, -700, 0], 'endPoint': [800, 300, 0]},
        ])
        self.bridge.boxes['B2'] = [-2000, -1000, -1400, -600]
        with self.assertRaisesRegex(ValueError, 'луч'):
            capture(self.store, 'test', 'run')
        request = self.request()
        request['ray_reviews'].append({
            'ray_handle': 'C2', 'note_handle': 'B2', 'note_excerpt': 'к тому же расстоянию',
            'parameter_review': 'Самостоятельное требование относится к тому же обмеру',
            'geometry_review': 'Новый луч идёт от второго текста к области числовой надписи',
        })
        self.register(request)
        self.assertEqual(self.bridge.entity('D1')['layer'], LAYER)
        self.assertTrue((self.folder / JOURNAL_FILE).read_bytes().startswith(first))
        records = [json.loads(line) for line in (self.folder / JOURNAL_FILE).read_text(encoding='utf-8').splitlines()]
        self.assertEqual(records[-1]['changes'][0]['kind'], 'evidence_refresh')
        capture(self.store, 'test', 'run')
        self.assertTrue(self.classify()['complete'])
        self.assertEqual((self.folder / BASELINE_FILE).read_bytes(), self.baseline_bytes)

    def test_parallel_registry_edit_is_preserved_and_prevents_journal_append(self):
        self.move()
        original_call = self.bridge.call
        changed = False

        def concurrent_edit(name, args=None):
            nonlocal changed
            result = original_call(name, args)
            if name == 'get_drawing_info' and self.bridge.dimension_reads >= 2 and not changed:
                registry = self.load()['registry']
                registry['parallel_note'] = 'Другая запись во время проверки'
                write_json(self.folder / 'РЕЕСТР_ПРОВЕРКИ.json', registry)
                changed = True
            return result

        with patch.object(self.bridge, 'call', side_effect=concurrent_edit):
            with self.assertRaisesRegex(ValueError, 'изменились во время проверки'):
                self.register()
        self.assertTrue(changed)
        self.assertEqual(self.load()['registry']['parallel_note'], 'Другая запись во время проверки')
        self.assert_no_journal()

    def test_existing_pinned_copy_cannot_be_replaced_by_another_matching_filename(self):
        self.move()
        self.bridge.path = str(self.root / 'source__barrier_assessment__run_extra.dwg')
        with self.assertRaisesRegex(ValueError, 'единственную копию'):
            self.register()
        self.assert_no_journal()

    def test_corrupt_journal_cannot_bypass_guard(self):
        self.move(); self.register(); capture(self.store, 'test', 'run'); self.classify()
        baseline = read_json(self.folder / BASELINE_FILE)
        row = json.loads((self.folder / JOURNAL_FILE).read_text(encoding='utf-8'))
        row['changes'][0]['target_layer'] = 'other'
        (self.folder / JOURNAL_FILE).write_text(json.dumps(row) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'повреждён'):
            journal(self.folder, baseline)
        loaded = self.load()
        self.assertTrue(stored_errors(loaded['run'], loaded['registry'], self.folder))

    def test_forged_tracked_text_does_not_turn_into_measurement_exception(self):
        self.move(); self.register(); capture(self.store, 'test', 'run'); self.classify()
        loaded = self.load()
        inventory = loaded['registry']['barrier_assessment']['source_remarks']['inventory']
        inventory['tracked_dimensions'][0]['type'] = 'MText'
        inventory['tracked_dimensions'][0]['text'] = 'Подменённое исходное замечание'
        inventory['digest'] = inventory_digest(inventory)
        write_json(self.folder / INVENTORY_FILE, inventory)
        self.assertTrue(source_remarks_errors(loaded['registry']))
        self.assertTrue(stored_errors(loaded['run'], loaded['registry'], self.folder))


class DrawingProfileScopeTests(unittest.TestCase):
    def test_verified_profile_preserves_keys_but_parameterizes_characteristics(self):
        path = Path(__file__).resolve().parents[1] / 'knowledge/barrier_drawing_profile.json'
        profile = json.loads(path.read_text(encoding='utf-8'))
        self.assertIn('drawing_format_only', profile['verification_scope'])
        legend = {row['key']: row for row in profile['legend']}
        self.assertEqual(legend['stairs']['color_index'], 30)
        self.assertEqual(legend['rated_partition_30']['color_index'], 4)
        for key in ('stairs', 'rated_partition_30'):
            self.assertTrue(legend[key]['example_only'])
            self.assertIn('{performance}', legend[key]['name_template'])
            self.assertIn('fire_hazard_class_applicability', legend[key]['required_parameters'])
        self.assertTrue(profile['dimension_text_rule']['preserve_shared_styles_used_by_source_or_other_entities'])
        self.assertFalse(profile['correction_checkpoint_rule']['save_as_for_checkpoint'])
        self.assertTrue(profile['gpzu_boundary_rule']['equal_or_unknown_date_requires_confirmed_edition_basis'])


if __name__ == '__main__':
    unittest.main()
