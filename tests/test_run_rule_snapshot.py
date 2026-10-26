import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fire_audit.core import FireAuditApp
from fire_audit.io_utils import read_json
from fire_audit.runs import MODE_LABELS
from fire_audit.rule_registry import RuleResolver


class RunRuleSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(__file__).resolve().parents[1]
        for name in ('config', 'knowledge', 'templates'):
            shutil.copytree(source / name, self.root / name)
        self.app = FireAuditApp(self.root)
        for slug, date, degree, number, category in (
            ('first', '2025-11-12', 'IV', 'PRIVATE-101', 'В3'),
            ('second', '2026-04-23', 'II', 'PRIVATE-909', 'В1'),
        ):
            project = self.app.projects.create(slug, slug)
            project['dates']['gpzu'] = date
            project['classification']['fire_resistance_degree'] = degree
            project.setdefault('barrier_assessment', {})['rooms'] = [{'number': number, 'category': category}]
            self.app.projects.save(slug, project)

    def append_canonical_text(self, document, marker):
        path = self.root / 'knowledge/rule_blocks.json'
        catalogue = read_json(path)
        source = (self.root / 'knowledge' / document).read_text(encoding='utf-8')
        rule_id = next(key for kind, key in RuleResolver.references(source) if kind == 'rule')
        rule = next(row for row in catalogue['rules'] if row['id'] == rule_id)
        if marker not in rule['text']:
            rule['text'] += '\n' + marker
            path.write_text(json.dumps(catalogue, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def assert_consistent_artifacts(self, result):
        run = result['run']
        folder = Path(result['folder'])
        rules = (folder / 'ПРАВИЛА_ЗАПУСКА.md').read_text(encoding='utf-8-sig')
        revision = read_json(folder / 'ВЕРСИЯ_ПРАВИЛ.json')
        history = read_json(self.root / 'data' / 'rule_versions' / (run['knowledge_digest'] + '.json'))
        task = Path(result['task_file']).read_text(encoding='utf-8-sig')
        self.assertEqual(hashlib.sha256(rules.encode('utf-8')).hexdigest(), run['knowledge_digest'])
        self.assertEqual(revision['context'], rules)
        self.assertEqual(revision['digest'], run['knowledge_digest'])
        self.assertEqual(history['context'], rules)
        self.assertEqual(revision['files'], run['knowledge_documents'])
        self.assertIn(rules, task)
        self.assertIn(json.dumps(run['knowledge_documents'], ensure_ascii=False, indent=2), task)
        names = [doc['name'] for doc in run['knowledge_documents']]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn('25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md', names)
        self.assertIn('rule_overrides.json', names)
        if run['mode'] == 'barrier_assessment':
            self.assertIn('barrier_drawing_profile.json', names)
            self.assertIn('26_СОСТАВ_СХЕМЫ_ПП.md', names)
            self.assertNotIn('room_classification.json', names)
        else:
            self.assertIn('room_classification.json', names)

    def test_barrier_sequence_approval_in_both_task_variants_and_projects(self):
        for slug in ('first', 'second'):
            for variant in ('full_execution', 'analysis_word'):
                with self.subTest(slug=slug, variant=variant):
                    result = self.app.runs.create(slug, 'barrier_assessment', task_variant=variant)
                    self.assert_consistent_artifacts(result)
                    constraints = result['run']['hard_constraints']
                    self.assertTrue(constraints['barrier_sequence_user_approval_before_stage1'])
                    self.assertTrue(constraints['barrier_stage_all_plans_all_floors_before_next'])
                    self.assertTrue(constraints['barrier_missing_data_blocks_next_stage'])
                    self.assertTrue(constraints['barrier_sequence_category_reconciliation_summary_required'])
                    self.assertTrue(constraints['barrier_sequence_algorithm_without_room_addresses'])
                    self.assertTrue(constraints['barrier_sequence_all_outputs_and_queries_required'])
                    self.assertTrue(constraints['barrier_sequence_applicability_before_word_required'])
                    self.assertTrue(constraints['barrier_sequence_each_item_applicability_required'])
                    self.assertTrue(constraints['barrier_sequence_not_required_items_skip_execution'])
                    self.assertEqual(constraints['barrier_sequence_applicability_statuses'],
                                     ['required', 'not_required', 'needs_clarification'])
                    self.assertEqual(constraints['barrier_fixed_first_five_stages'], [
                        'fire_compartment_walls_REI150', 'stu', 'source_explanation_remarks',
                        'lift_shafts_and_halls', 'categorized_rooms'])
                    self.assertEqual(constraints['barrier_categories_before_sequence_approval'],
                                     variant == 'full_execution')
                    task = Path(result['task_file']).read_text(encoding='utf-8-sig')
                    self.assertIn('32_ПОСЛЕДОВАТЕЛЬНОСТЬ_ОЦЕНКИ_ПРЕГРАД.md', task)
                    self.assertIn('ОБЯЗАТЕЛЬНАЯ ОСТАНОВКА', task)
                    self.assertIn('ПОСЛЕДОВАТЕЛЬНОСТЬ_НАНЕСЕНИЯ_ПРЕГРАД.docx', task)
                    rules = (Path(result['folder']) / 'ПРАВИЛА_ЗАПУСКА.md').read_text(encoding='utf-8-sig')
                    self.assertIn('barrier_sequence_all_floors_with_word_approval_20261017', rules)
                    self.assertIn('barrier_sequence_general_algorithm_and_category_reconciliation_20261019', rules)
                    self.assertIn('barrier_sequence_item_applicability_before_word_20261020', rules)
                    self.assertIn('Актуальность для объекта', rules)
                    self.assertIn('Требуется / Не требуется / Требует уточнения', task)
                    self.assertIn('Одного отсутствия стен на плане недостаточно', rules)
                    self.assertIn('Сверка не завершена', rules)
                    self.assertIn('без привязки к конкретным помещениям', task)
                    self.assertNotIn('все охватываемые этажи, планы, помещения/оси, границы и фрагменты', rules)
                    self.assertNotIn('Продолжать до конца проверку всех остальных границ и конструкций.', rules)
                    quality = Path(result['quality_file']).read_text(encoding='utf-8-sig')
                    self.assertIn('явно согласован до этапа 1', quality)
                    self.assertIn('каждый этап закрыт на всех планах', quality)
                    self.assertIn('итог сверки номеров и наименований', quality)
                    self.assertIn('выноски к перекрытиям, дверям, окнам, воротам, шторам', quality)
                    self.assertIn('До формирования Word проанализирована необходимость каждого пункта', quality)
        other = self.app.runs.create('first', 'compliance')
        self.assertNotIn('barrier_sequence_user_approval_before_stage1', other['run']['hard_constraints'])
        self.assertNotIn('ОБЯЗАТЕЛЬНАЯ ОСТАНОВКА', Path(other['task_file']).read_text(encoding='utf-8-sig'))

    def test_all_modes_freeze_one_snapshot_for_two_independent_objects(self):
        for mode in MODE_LABELS:
            with self.subTest(mode=mode):
                results = []
                for slug in ('first', 'second'):
                    with patch.object(self.app.knowledge, 'snapshot_for_mode',
                                      wraps=self.app.knowledge.snapshot_for_mode) as capture:
                        result = self.app.runs.create(slug, mode)
                    capture.assert_called_once_with(mode)
                    constraints = result['run']['hard_constraints']
                    self.assertIs(constraints['explain_every_non_issued_source_in_green'], False)
                    for key in (
                        'routine_review_comments_on_drawing_forbidden',
                        'individual_review_evidence_in_registry_required',
                        'full_scope_tracking_required',
                        'continue_independent_work_when_local_data_missing',
                        'intermediate_save_is_not_completion',
                        'verify_active_document_before_and_after_each_write',
                    ):
                        self.assertIs(constraints[key], True)
                    self.assert_consistent_artifacts(result)
                    for doc in result['run']['knowledge_documents']:
                        self.assertEqual(doc['sha256'], hashlib.sha256(Path(doc['path']).read_bytes()).hexdigest())
                    results.append(result)
                first, second = (result['run'] for result in results)
                self.assertEqual(first['knowledge_digest'], second['knowledge_digest'])
                self.assertNotEqual(first['project_snapshot'], second['project_snapshot'])
                task = Path(results[1]['task_file']).read_text(encoding='utf-8')
                self.assertIn('PRIVATE-909', task)
                self.assertNotIn('PRIVATE-101', task)
                self.assertNotIn('door_callout_basis_includes_object_remark_76', second['hard_constraints'])

    def test_barrier_graphic_exceptions_are_shared_and_frozen(self):
        result = self.app.runs.create('first', 'barrier_assessment')
        constraints = result['run']['hard_constraints']
        self.assertNotIn('required_fire_doors_have_callouts', constraints)
        for key in (
            'general_fire_opening_requirements_belong_in_legend',
            'generic_barrier_door_callouts_forbidden',
            'specific_door_callout_requires_independent_remark_or_norm',
            'specific_door_callout_must_not_start_from_explanation_graphics',
            'independent_door_and_slab_normative_scan_required',
            'shaft_opening_callouts_forbidden',
            'exterior_walls_only_with_explicit_explanation_requirement',
            'internal_stairwell_walls_table21_required',
            'tambour_escape_route_rule_only_at_escape_route_interface',
            'corridor_cross_barrier_requires_independent_basis',
            'callouts_above_related_floor_without_overlaps',
            'all_rays_of_one_callout_share_one_start_point',
            'callout_rays_all_properties_by_layer',
            'callout_ray_lineweight_50_forbidden',
            'detached_and_embedded_fragments_require_verified_geometry_mapping',
            'fire_compartment_slab_callouts_must_be_reviewed',
            'large_storage_volume_adjacencies_must_be_reviewed',
            'same_category_production_rooms_need_no_shared_barrier_only_under_sp4_6_1_47',
            'vent_chambers_full_internal_boundary_ei45_including_corridors',
            'vent_chamber_served_and_protected_fire_compartments_must_be_identified',
            'vent_chamber_outside_served_or_protected_compartment_enclosure_ei150',
            'vent_chamber_outside_served_or_protected_compartment_doors_type1',
            'sp7_6_8_6_9_cross_compartment_conditions_must_be_checked',
            'sp2_5_3_6_checked_after_all_barriers_drawn',
            'sp2_5_3_6_actual_dimensions_required_for_all_checked_piers',
            'sp2_5_3_6_keep_all_measurements_in_final_dwg',
            'sp2_5_3_6_rays_target_dimension_objects',
        ):
            self.assertIs(constraints[key], True)
        self.assertIs(constraints['unconfirmed_opening_infills_are_not_fire_rated'], True)
        self.assertIs(constraints['stairwell_walls_excluded_on_both_sides'], False)
        self.assertIs(constraints['stairwell_wall_exception_requires_explicit_fire_resistance'], False)
        self.assertIs(constraints['opening_fire_rating_requires_object_specific_documentary_evidence'], True)
        self.assertIn('8.2', constraints['vent_chamber_cross_compartment_basis'])
        self.assert_consistent_artifacts(result)

    def test_update_during_creation_does_not_mix_rule_versions(self):
        mode = 'barrier_assessment'
        snapshot = self.app.knowledge.snapshot_for_mode(mode)
        marker = 'Дополнительный общий контроль согласованности нового правила.'
        quality_markdown = self.app.runs._quality_markdown

        def update_then_render(run):
            self.append_canonical_text('18_ОЦЕНКА_ПРЕГРАД.md', marker)
            return quality_markdown(run)

        with patch.object(self.app.runs, '_quality_markdown', side_effect=update_then_render):
            result = self.app.runs.create('first', mode)
        self.assert_consistent_artifacts(result)
        self.assertEqual(result['run']['knowledge_digest'], snapshot.digest)
        self.assertEqual(result['run']['knowledge_documents'], [
            {'name': doc.name, 'path': str(doc.path), 'sha256': doc.sha256}
            for doc in snapshot.documents
        ])
        self.assertNotIn(marker, Path(result['task_file']).read_text(encoding='utf-8'))
        self.assertNotEqual(snapshot.digest, self.app.knowledge.digest_for_mode(mode))
        updated = self.app.runs.create('first', mode)
        self.assertIn(marker, Path(updated['task_file']).read_text(encoding='utf-8'))

    def test_shared_ar_update_reaches_both_projects_and_task_variants_without_rewriting_history(self):
        project_paths = [self.app.projects.project_dir(slug) / 'project.json'
                         for slug in ('first', 'second')]
        passports = {path: path.read_bytes() for path in project_paths}
        old_runs = [self.app.runs.create(slug, 'compliance', variant)
                    for slug in ('first', 'second')
                    for variant in ('full_execution', 'analysis_word')]
        old_paths = [path for result in old_runs for path in (
            Path(result['folder']) / 'run.json',
            Path(result['registry_file']),
            Path(result['task_file']),
            Path(result['folder']) / 'ПРАВИЛА_ЗАПУСКА.md',
        )]
        history = {path: path.read_bytes() for path in old_paths}
        marker = 'Общее уточнение: адресат определяется по геометрии текущего плана.'
        self.append_canonical_text('03_ОЦЕНКА_СООТВЕТСТВИЯ.md', marker)
        digests = set()
        for slug, own, other in (('first', 'PRIVATE-101', 'PRIVATE-909'),
                                 ('second', 'PRIVATE-909', 'PRIVATE-101')):
            for variant in ('full_execution', 'analysis_word'):
                with self.subTest(project=slug, variant=variant):
                    result = self.app.runs.create(slug, 'compliance', variant)
                    self.assert_consistent_artifacts(result)
                    task = Path(result['task_file']).read_text(encoding='utf-8')
                    self.assertIn(marker, task)
                    self.assertIn(own, task)
                    self.assertNotIn(other, task)
                    self.assertIn('Сначала определить фактический адресат требования', task)
                    self.assertIn('Сначала установить схему эвакуации, затем выбрать предел', task)
                    self.assertIn('Шкафчики и ячейки являются сведениями о возможной вместимости', task)
                    rules = (Path(result['folder']) / 'ПРАВИЛА_ЗАПУСКА.md').read_text(encoding='utf-8')
                    self.assertNotIn(own, rules)
                    self.assertNotIn(other, rules)
                    digests.add(result['run']['knowledge_digest'])
                    self.assertEqual(result['run']['hard_constraints']['autocad_actions_enabled'],
                                     variant in ('full_execution', 'analysis_word'))
        self.assertEqual(len(digests), 1)
        self.assertNotIn(old_runs[0]['run']['knowledge_digest'], digests)
        self.assertEqual(history, {path: path.read_bytes() for path in old_paths})
        self.assertEqual(passports, {path: path.read_bytes() for path in project_paths})

    def test_invalid_rules_do_not_leave_a_partial_run(self):
        path = self.root / 'knowledge/rule_overrides.json'
        path.write_text('{"rules": [{"id": "bad"}]}', encoding='utf-8')
        runs = self.app.projects.project_dir('first') / 'runs'
        before = list(runs.iterdir()) if runs.exists() else []
        with self.assertRaises(ValueError):
            self.app.runs.create('first', 'barrier_assessment')
        after = list(runs.iterdir()) if runs.exists() else []
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
