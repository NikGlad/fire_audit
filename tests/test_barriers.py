import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from fire_audit.barriers import (CATEGORY_LAYER, LAYER, barrier_completion_errors, category_label,
    covers_fire_resistance, preparation_errors, prepare_rooms, room_purpose_letter,
    validate_created_entity)
from fire_audit.core import FireAuditApp
from fire_audit.runs import MODE_LABELS, PENDING_INSTRUCTION_MODES


class BarrierRulesTests(unittest.TestCase):
    def test_user_roots_do_not_inherit_other_modes(self):
        for name in ['Склад сырья', 'Кладовая', 'Помещение хранения', 'Хранилище']:
            self.assertEqual(room_purpose_letter(name), 'С')
        for name in ['Архив', 'Запас', 'Холодильная камера', 'Техническое помещение']:
            self.assertEqual(room_purpose_letter(name), 'П')
        self.assertEqual(category_label('В2', 'Склад'), 'В2С')
        with self.assertRaises(ValueError):
            category_label('', 'Склад')

    def test_matching_is_floor_and_number_not_number_only(self):
        first = {'floor': '1', 'number': '101', 'name': 'Цех', 'category': 'В2'}
        self.assertEqual(len(prepare_rooms([first, {**first, 'floor': '2'}])), 2)
        with self.assertRaisesRegex(ValueError, 'Повтор'):
            prepare_rooms([first, first])

    def test_lineweight_and_legend_are_explicit(self):
        legend = [{'key': 'wall_1', 'color_index': 2}]
        good = {'layer': LAYER, 'lineWeight': 'LineWeight050', 'colorIndex': 2}
        self.assertEqual(validate_created_entity(good, 'barrier', legend, 'wall_1'), [])
        for patch in [{'layer': 'экспертиза'}, {'lineWeight': 'ByLayer'}, {'colorIndex': 30}]:
            self.assertTrue(validate_created_entity({**good, **patch}, 'barrier', legend, 'wall_1'))
        self.assertTrue(validate_created_entity(good, 'barrier', legend, 'missing'))

    def test_callout_rays_are_strictly_by_layer(self):
        good = {'layer': LAYER, 'colorIndex': 256, 'linetype': 'ByLayer', 'lineWeight': 'ByLayer'}
        self.assertEqual(validate_created_entity(good, 'ray', []), [])
        for patch in ({'colorIndex': 1}, {'linetype': 'Continuous'},
                      {'lineWeight': 'LineWeight050'}, {'lineWeight': 50}):
            self.assertTrue(validate_created_entity({**good, **patch}, 'ray', []), patch)

    def test_category_requires_its_own_layer(self):
        self.assertEqual(validate_created_entity({'layer': CATEGORY_LAYER}, 'category', []), [])
        self.assertTrue(validate_created_entity({'layer': LAYER}, 'category', []))

    def test_more_minutes_does_not_replace_missing_resistance_criterion(self):
        self.assertFalse(covers_fire_resistance({'E': 60, 'I': 60}, {'R': 45, 'E': 45, 'I': 45}))
        self.assertTrue(covers_fire_resistance({'R': 150, 'E': 150, 'I': 150}, {'R': 45, 'E': 45, 'I': 45}))

    def test_preparation_cannot_infer_approval_or_missing_stu(self):
        self.assertTrue(preparation_errors({}, {}))
        data = {'categories_approved': True, 'category_approval_source': 'user confirmation',
                'category_source': {'sha256': 'a' * 64}, 'stu_status': 'absent',
                'additional_documents_reviewed': True, 'preparation_completed': True,
                'rooms': [{'floor': '1', 'number': '1', 'name': 'Цех', 'category': 'В2'}]}
        profile = {'status': 'verified', 'unresolved_fields': []}
        self.assertEqual(preparation_errors({'barrier_assessment': data}, profile), [])
        modified = {**data, 'stu_status': 'provided', 'stu_documents': ['stu.pdf'],
                    'refrigeration_present': True}
        self.assertTrue(preparation_errors({'barrier_assessment': modified}, profile))
        self.assertTrue(preparation_errors({'barrier_assessment': data}, {**profile, 'unresolved_fields': ['font']}))

    def test_completion_gates_distinguish_legend_fillings_from_specific_door_callouts(self):
        registers = {
            'large_volume_boundaries': {'status': 'complete', 'items': [
                {'separation_required': True, 'entity_ids': ['A1']}]},
            'fire_openings': {'status': 'complete', 'items': [
                {'fire_rating_required': True, 'address_callout_required': True,
                 'target_entity_id': 'D1', 'leader_entity_ids': ['L1'],
                 'callout_separate_from_explanation_graphics': True},
                {'fire_rating_required': True, 'address_callout_required': False,
                 'legend_entity_ids': ['LEG1'], 'leader_entity_ids': []}]},
            'special_slabs': {'status': 'complete', 'items': [
                {'table21_baseline_minutes': 45, 'special_required_minutes': 150,
                 'table21_criteria': {'R':45,'E':45,'I':45},
                 'special_criteria': {'R':150,'E':150,'I':150},
                 'basis':'special norm', 'scope':'над встроенным помещением',
                 'independent_purpose_review':True, 'callout_entity_ids': ['S1']}]},
            'independent_normative_scan': {'status':'complete', 'all_floors_reviewed':True,
                'independent_of_source_remarks':True, 'items':[{'floor':'1',
                'source_entity_ids':['D1'], 'sp1_4_2_25_result':'applicable', 'slab_review_result':'complete'}]},
            'callout_layout': {'status':'complete','items':[{'floor':'1','text_entity_id':'T1',
                'leader_entity_ids':['L1'], 'text_above_actual_plan_bounds':True,
                'no_text_overlaps':True,'single_start_adjacent_to_own_text':True,
                'targets_verified':True,'same_floor':True,'visual_verified':True,
                'rays_all_properties_by_layer':True}]},
            'abk_public_parts_f4_3': {'status': 'complete', 'items': [
                {'functional_class': 'Ф4.3', 'boundary_review_complete': True}]},
            'escape_routes_by_gpzu_date': {'status': 'complete', 'items': [
                {'gpzu_branch': 'before_2026', 'space_type': 'общий коридор',
                 'full_contour_review_complete': True,
                 'toilet_and_sanitary_boundaries_reviewed': True}]},
            'legend_actualization': {'status': 'complete', 'used_keys': ['wall_1'],
                                     'legend_keys': ['wall_1'], 'all_frames_match': True},
        }
        registry = {'barrier_assessment': {'completion_registers': registers}}
        self.assertEqual(barrier_completion_errors(registry), [])
        broken = copy.deepcopy(registry)
        broken['barrier_assessment']['completion_registers']['fire_openings']['items'][0]['leader_entity_ids'] = []
        broken['barrier_assessment']['completion_registers']['special_slabs']['items'][0]['callout_entity_ids'] = []
        broken['barrier_assessment']['completion_registers']['legend_actualization']['legend_keys'] = ['wall_2']
        errors = barrier_completion_errors(broken)
        self.assertTrue(any('выноски' in error for error in errors))
        self.assertTrue(any('перекрытия' in error for error in errors))
        self.assertTrue(any('легенды' in error for error in errors))
        broken = copy.deepcopy(registry)
        broken['barrier_assessment']['completion_registers']['independent_normative_scan']['all_floors_reviewed'] = False
        broken['barrier_assessment']['completion_registers']['callout_layout']['items'][0]['single_start_adjacent_to_own_text'] = False
        errors = barrier_completion_errors(broken)
        self.assertTrue(any('всех этажей' in error for error in errors))
        self.assertTrue(any('single_start' in error for error in errors))
        broken = copy.deepcopy(registry)
        broken['barrier_assessment']['completion_registers']['callout_layout']['items'][0]['rays_all_properties_by_layer'] = False
        self.assertTrue(any('ByLayer' in error for error in barrier_completion_errors(broken)))

    def test_mode_task_and_every_mode_receive_shared_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = Path(__file__).resolve().parents[1]
            for folder in ('config', 'knowledge', 'templates'):
                shutil.copytree(source / folder, root / folder)
            app = FireAuditApp(root)
            for mode in MODE_LABELS:
                self.assertIn('14_РЕГЛАМЕНТ_ПОИСКА_ДОКУМЕНТОВ.md', app.knowledge.context_for_mode(mode))
                self.assertIn('20_КЛАССИФИКАЦИЯ_ЧАСТЕЙ_ЗДАНИЯ.md', app.knowledge.context_for_mode(mode))
                self.assertIn('Ф4.3', app.knowledge.context_for_mode(mode))
                self.assertIn('27_НЕЗАВИСИМЫЕ_ТРЕБОВАНИЯ_ДВЕРИ_ПЕРЕКРЫТИЯ.md', app.knowledge.context_for_mode(mode))
                self.assertIn('Покрытия помещений, расположенных в объёме другого помещения', app.knowledge.context_for_mode(mode))
                self.assertIn('Правила о перекрытиях не изменяются.', app.knowledge.context_for_mode(mode))
                self.assertIn('Покрытие малого помещения внутри большого объёма всегда обозначать выноской, независимо от таблицы 21 № 123-ФЗ.', app.knowledge.context_for_mode(mode))
            project = app.projects.create('Test', 'test')
            project['dates']['gpzu'] = '2026-04-23'
            app.projects.save('test', project)
            result = app.runs.create('test', 'barrier_assessment')
            run = result['run']
            self.assertNotIn('barrier_assessment', PENDING_INSTRUCTION_MODES)
            self.assertTrue(run['hard_constraints']['workflow_instructions_ready'])
            self.assertTrue(run['hard_constraints']['autocad_actions_enabled'])
            self.assertEqual(run['hard_constraints']['executor'], 'assistant_via_autom8labs')
            self.assertTrue(run['hard_constraints']['assistant_executes_autocad_directly'])
            self.assertEqual(run['hard_constraints']['violation_graphics_layer'], LAYER)
            self.assertEqual(run['hard_constraints']['category_layer'], CATEGORY_LAYER)
            self.assertEqual(run['hard_constraints']['abk_functional_fire_hazard_class'], 'Ф4.3')
            self.assertTrue(run['hard_constraints']['same_category_non_separation_rule_excludes_public_parts'])
            self.assertTrue(run['hard_constraints']['barrier_normative_matrix_included'])
            self.assertTrue(run['hard_constraints']['barrier_normative_matrix_primary_working_reference'])
            self.assertTrue(run['hard_constraints']['embedded_room_cover_callout_always_required'])
            self.assertTrue(run['hard_constraints']['embedded_room_cover_callout_independent_of_table21'])
            self.assertTrue(run['hard_constraints']['slab_callout_when_special_limit_exceeds_table21_baseline'])
            task = Path(result['task_file']).read_text(encoding='utf-8')
            self.assertIn('Вес линий преград 50', task)
            self.assertIn('===== 23_НОРМАТИВНАЯ_МАТРИЦА_ПП.md =====', task)
            self.assertIn('Таблица 23 № 123-ФЗ', task)
            self.assertIn('СП 4, п. 6.3.7', task)
            self.assertIn('экспертиза категории', task)
            self.assertNotIn('===== room_classification.json', task)
            self.assertNotIn('Алгоритм этого вида проверки ещё не передан', task)
            validation = app.runs.validate('test', run['run_id'], for_application=True)
            self.assertFalse(validation['ok'])
            self.assertTrue(any('утверждение категорий' in e for e in validation['errors']))
            with self.assertRaisesRegex(ValueError, 'Специальный исполнитель'):
                app.cad_guard.validate_actions({'mode': 'barrier_assessment'}, {}, [])


if __name__ == '__main__':
    unittest.main()
