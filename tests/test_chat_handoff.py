import shutil
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from collections import defaultdict

from fire_audit.core import FireAuditApp


class ChatHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        for name in ('config', 'knowledge', 'templates'):
            shutil.copytree(source / name, self.root / name)
        self.app = FireAuditApp(self.root)
        project = self.app.projects.create('Другой объект', 'other')
        project['dates']['gpzu'] = '2026-04-23'
        self.app.projects.save('other', project)

    def create_run(self, mode='barrier_assessment', task_variant='full_execution'):
        return self.app.runs.create('other', mode, task_variant)

    def test_full_barrier_task_can_be_passed_to_a_new_chat(self):
        result = self.create_run()
        run = result['run']
        handoff = self.app.runs.chat_handoff('other', run['run_id'])
        self.assertEqual(handoff['warnings'], [])
        self.assertTrue(handoff['rules_intact'])
        task = Path(result['task_file']).read_text(encoding='utf-8')
        self.assertTrue(handoff['text'].endswith(task))
        self.assertIn('Идентификатор объекта: `other`', task)
        self.assertIn(run['run_id'], task)
        self.assertIn(str(self.root), task)
        for name in ('14_РЕГЛАМЕНТ_ПОИСКА_ДОКУМЕНТОВ.md',
                     '18_ОЦЕНКА_ПРЕГРАД.md', '20_КЛАССИФИКАЦИЯ_ЧАСТЕЙ_ЗДАНИЯ.md',
                     '21_ПОСТРОЕНИЕ_И_ПРОВЕРКА_ПП.md',
                     '22_ПРЯМАЯ_РАБОТА_ПОМОЩНИКА.md',
                     '23_НОРМАТИВНАЯ_МАТРИЦА_ПП.md',
                     '25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md', 'barrier_drawing_profile.json'):
            self.assertIn('===== ' + name, task)
        prefix = handoff['text'].split('\n---\n', 1)[0]
        self.assertNotIn('get_next_review_task', prefix)
        self.assertNotIn('prepare_cad_plan', prefix)
        self.assertIn('get_project_context', prefix)
        self.assertIn('РЕЕСТР_ПРОВЕРКИ.json', prefix)
        self.assertIn('не повторяй уже выполненные изменения', prefix)

    def test_old_run_is_not_rewritten_after_rule_update(self):
        old = self.create_run()
        files = [Path(old['task_file']), Path(old['folder']) / 'ПРАВИЛА_ЗАПУСКА.md']
        original = [f.read_bytes() for f in files]
        rules = self.root / 'knowledge/rule_blocks.json'
        catalogue = json.loads(rules.read_text(encoding='utf-8'))
        rule = next(row for row in catalogue['rules'] if row['id'] == 'doc_18_001')
        rule['text'] += '\nНовое подтверждённое правило 999.\n'
        rules.write_text(json.dumps(catalogue, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        handoff = self.app.runs.chat_handoff('other', old['run']['run_id'])
        self.assertFalse(handoff['rules_current'])
        self.assertTrue(handoff['rules_intact'])
        self.assertIn('ЗАДАНИЕ ТРЕБУЕТ ОБНОВЛЕНИЯ', handoff['text'])
        self.assertEqual(original, [f.read_bytes() for f in files])
        new = self.create_run()
        self.assertNotEqual(new['run']['run_id'], old['run']['run_id'])
        self.assertIn('Новое подтверждённое правило 999', Path(new['task_file']).read_text(encoding='utf-8'))
        self.assertEqual(self.app.runs.chat_handoff('other', new['run']['run_id'])['warnings'], [])

    def test_changed_passport_and_damaged_rules_are_reported(self):
        result = self.create_run()
        project = self.app.projects.load('other')
        project['classification']['fire_resistance_degree'] = 'III'
        self.app.projects.save('other', project)
        handoff = self.app.runs.chat_handoff('other', result['run']['run_id'])
        self.assertFalse(handoff['project_current'])
        self.assertTrue(handoff['rules_current'])
        self.assertTrue(any('Паспорт' in w for w in handoff['warnings']))
        frozen = Path(result['folder']) / 'ПРАВИЛА_ЗАПУСКА.md'
        frozen.write_text('Изменённое содержимое', encoding='utf-8')
        handoff = self.app.runs.chat_handoff('other', result['run']['run_id'])
        self.assertFalse(handoff['rules_intact'])
        self.assertTrue(any('не соответствует' in w for w in handoff['warnings']))

    def test_another_object_does_not_inherit_private_facts(self):
        first = self.app.projects.create('Первый объект', 'first')
        first['dates']['gpzu'] = '2025-11-12'
        first['classification']['fire_resistance_degree'] = 'IV'
        first['barrier_assessment'] = {'categories_approved': True, 'stu_status': 'absent',
                                      'rooms': [{'number': '129', 'rack_storage_over_5_5_m': True}]}
        self.app.projects.save('first', first)
        first_run = self.app.runs.create('first', 'barrier_assessment')['run']
        run = self.create_run()['run']
        snapshot = run['project_snapshot']
        self.assertEqual(first_run['knowledge_digest'], run['knowledge_digest'])
        self.assertEqual(snapshot['dates']['gpzu'], '2026-04-23')
        self.assertNotEqual(snapshot['classification']['fire_resistance_degree'], 'IV')
        self.assertFalse(snapshot.get('barrier_assessment', {}).get('categories_approved'))
        self.assertNotEqual(snapshot.get('barrier_assessment', {}).get('stu_status'), 'absent')
        self.assertEqual(snapshot.get('barrier_assessment', {}).get('rooms', []), [])
        self.assertIn('утверждение категорий', ' '.join(run['preparation']['errors']))

    def test_shared_knowledge_rejects_project_specific_facts(self):
        issues = self.app.knowledge.scope_issues(
            'example.md',
            'Помещение 129. Дата ГПЗУ 12.11.2025. СТУ нет. Замечание №72.',
        )
        self.assertEqual(len(issues), 4)
        with self.assertRaisesRegex(ValueError, 'действуют для всех объектов'):
            self.app.knowledge.validate_shared_scope(
                'example.md', 'Помещение 129. Дата ГПЗУ 12.11.2025.'
            )
        self.assertEqual(
            self.app.knowledge.scope_issues(
                'example.md',
                'Если складское помещение граничит с производственным, проверить применимое требование.',
            ),
            [],
        )

    def test_global_drawing_profile_contains_no_reference_object_links(self):
        profile = (self.root / 'knowledge/barrier_drawing_profile.json').read_text(encoding='utf-8')
        for forbidden in ('Горшково', 'source_dwg', 'source_evidence', 'source_handle', 'line_handles', 'text_handles'):
            self.assertNotIn(forbidden, profile)

    def test_run_exposes_full_task_and_quality_contract(self):
        run = self.create_run()['run']
        loaded = self.app.runs.load('other', run['run_id'])
        self.assertIn('Оценка Преград', loaded['task'])
        self.assertIn('AUTOM8LABS', loaded['task'])
        self.assertIn('Контроль качества', loaded['quality_contract'])
        self.assertNotIn('get_next_review_task', loaded['task'])
        self.assertNotIn('prepare_cad_plan', loaded['task'])

    def test_other_mode_uses_direct_assistant_workflow(self):
        run = self.create_run('compliance')['run']
        prefix = self.app.runs.chat_handoff('other', run['run_id'])['text'].split('\n---\n', 1)[0]
        self.assertIn('напрямую через AUTOM8LABS', prefix)
        self.assertNotIn('get_next_review_task', prefix)
        self.assertNotIn('prepare_cad_plan', prefix)

    def test_all_modes_offer_analysis_with_human_readable_word_result(self):
        from fire_audit.runs import MODE_LABELS

        for mode in MODE_LABELS:
            with self.subTest(mode=mode):
                result = self.create_run(mode, 'analysis_word')
                run = result['run']
                task = Path(result['task_file']).read_text(encoding='utf-8')
                self.assertEqual(run['workflow'], 'assistant_analysis_word_report')
                self.assertEqual(run['task_variant_label'], 'Анализ и задание в Word')
                source_selection = mode in {'compliance', 'actualization_protocol'}
                self.assertEqual(run['hard_constraints']['autocad_actions_enabled'], source_selection)
                self.assertEqual(run['hard_constraints']['source_remark_selection_word_only'], source_selection)
                self.assertEqual(run['hard_constraints']['save_separate_result_dwg'], source_selection)
                self.assertTrue(run['hard_constraints']['word_report_required'])
                if source_selection:
                    self.assertIn('внешняя зелёная пометка', task)
                    self.assertEqual(run['hard_constraints']['source_remark_selection_numbering'],
                                     'consecutive_selected_source_groups_excluding_stage')
                    self.assertIn('Номера замечаний этапизации в этот ряд не включай', task)
                    self.assertIn('либо новое подлежащее выдаче замечание с рабочим', task)
                    self.assertIn('внутренние красные и фиолетовые цвета', task)
                    self.assertIn('что конкретно нарушено', task)
                    self.assertIn('зарегистрируй копию через `record_result_dwg`', task)
                    if mode == 'actualization_protocol':
                        self.assertIn('реестр всех ранее выданных замечаний проверяемого объёма', task)
                        self.assertIn('существующий номер на плане', task)
                        self.assertIn('дополнительно выявленные места нарушения', task)
                        self.assertIn('Не присваивай этим группам рабочие номера', task)
                    else:
                        self.assertNotIn('реестр всех ранее выданных замечаний проверяемого объёма', task)
                else:
                    self.assertEqual(run['hard_constraints']['source_remark_selection_numbering'], 'none')
                    self.assertIn('Не создавай, не изменяй, не удаляй и не сохраняй объекты AutoCAD', task)
                self.assertIn('ЗАДАНИЕ_ПО_РЕЗУЛЬТАТАМ_АНАЛИЗА.docx', task)
                self.assertIn('готовый текст замечания или запроса', task)
                self.assertIn('render_docx.py', task)
                self.assertIn('record_word_report', task)
                if not source_selection:
                    self.assertNotIn('зарегистрируй копию через `record_result_dwg`', task)
                self.assertIn('===== 24_ДВА_ВИДА_ЗАДАНИЯ.md =====', task)

    def test_ar_and_actualization_word_register_marked_copy_without_touching_source(self):
        source = self.root / 'source.dwg'
        source.write_bytes(b'source fixture')
        project = self.app.projects.load('other')
        project['dwg']['path'] = str(source)
        self.app.projects.save('other', project)
        for mode in ('compliance', 'actualization_protocol'):
            with self.subTest(mode=mode):
                result = self.create_run(mode, 'analysis_word')
                run = result['run']
                self.assertTrue(run['hard_constraints']['create_result_copy_before_writes'])
                self.assertIn('не зарегистрирована отдельная проверенная копия DWG',
                              ' '.join(self.app.runs.validate('other', run['run_id'], for_application=True)['errors']))
                marked = source.with_name(f'{source.stem}__{mode}__{run["run_id"]}.dwg')
                marked.write_bytes(b'marked copy fixture')
                self.addCleanup(marked.unlink)
                actions = self.app.runs.record_result_dwg(
                    'other', run['run_id'], str(marked),
                    source_document_contains_results=False,
                    copy_did_not_change_open_source_identity=True,
                )
                self.assertTrue(actions['result_copy_created'])
                self.assertEqual(source.read_bytes(), b'source fixture')
                self.assertNotIn('не зарегистрирована отдельная проверенная копия DWG',
                                 ' '.join(self.app.runs.validate('other', run['run_id'], for_application=True)['errors']))

    def test_analysis_word_report_has_separate_completion_control(self):
        result = self.create_run('stage', 'analysis_word')
        run_id = result['run']['run_id']
        before = self.app.runs.validate('other', run_id, for_application=True)
        self.assertFalse(before['ok'])
        self.assertTrue(any('Word-файл' in value for value in before['errors']))

        report = Path(result['folder']) / 'ЗАДАНИЕ_ПО_РЕЗУЛЬТАТАМ_АНАЛИЗА.docx'
        report.write_bytes(b'PK synthetic docx fixture')
        recorded = self.app.runs.record_word_report(
            'other', run_id, str(report), rendered_page_count=3,
            visual_review_complete=True, source_dwg_unchanged=True,
        )
        self.assertEqual(recorded['status'], 'complete')
        checks = {
            'scope_complete': True, 'sources_verified': True, 'mode_rules_verified': True,
            'source_materials_verified': True, 'conclusions_traceable': True,
            'word_structure_complete': True, 'visual_review_complete': True,
            'source_dwg_unchanged': True, 'registry_complete': True,
        }
        self.app.runs.record_quality_control('other', run_id, {
            'checks': checks, 'created_or_changed_entity_ids': [], 'unresolved': [],
        })
        complete = self.app.runs.validate('other', run_id, for_application=True)
        self.assertTrue(complete['ok'], complete['errors'])
        with self.assertRaisesRegex(ValueError, 'итоговый DWG не создаётся'):
            self.app.runs.record_result_dwg(
                'other', run_id, str(report.with_suffix('.dwg')),
                source_document_contains_results=False,
                copy_did_not_change_open_source_identity=True,
            )

    def test_object_norms_task_is_project_scoped_read_only_and_clause_specific(self):
        result = self.create_run('compliance', 'object_norms')
        run = result['run']
        task = Path(result['task_file']).read_text(encoding='utf-8')
        self.assertEqual(run['mode_label'], 'Нормы для объекта')
        self.assertEqual(run['hard_constraints']['word_report_filename'], 'НОРМЫ_ДЛЯ_ОБЪЕКТА.docx')
        self.assertFalse(run['hard_constraints']['autocad_actions_enabled'])
        self.assertFalse(run['hard_constraints']['save_separate_result_dwg'])
        self.assertNotIn('Создать отдельную копию результата', task)
        self.assertIn('пункта/части', task)
        self.assertIn('перечню Росстандарта № 2594', task)
        self.assertIn('===== 28_НОРМЫ_ДЛЯ_ОБЪЕКТА.md =====', task)
        self.assertIn('НОРМЫ_ДЛЯ_ОБЪЕКТА.docx', task)
        self.assertIn('Нормы для объекта', self.app.runs.list_runs('other')[0]['mode_label'])
        handoff = self.app.runs.chat_handoff('other', run['run_id'])
        self.assertIn('AutoCAD используй только для чтения', handoff['text'])
        self.assertTrue(handoff['rules_intact'])
        self.assertNotIn('Не зарегистрирован полный перечень исходных замечаний',
                         self.app.runs.validate('other', run['run_id'], for_application=True)['errors'])

        report = Path(result['folder']) / 'НОРМЫ_ДЛЯ_ОБЪЕКТА.docx'
        report.write_bytes(b'PK synthetic docx fixture')
        recorded = self.app.runs.record_word_report(
            'other', run['run_id'], str(report), rendered_page_count=1,
            visual_review_complete=True, source_dwg_unchanged=True,
        )
        self.assertEqual(recorded['status'], 'complete')
        from fire_audit.runs import WORD_QUALITY_CHECKS
        self.app.runs.record_quality_control('other', run['run_id'], {
            'checks': {name: True for name in WORD_QUALITY_CHECKS},
            'created_or_changed_entity_ids': [], 'unresolved': [],
        })
        self.assertTrue(self.app.runs.validate('other', run['run_id'], for_application=True)['ok'])
        with self.assertRaisesRegex(ValueError, 'итоговый DWG не создаётся'):
            self.app.runs.record_result_dwg(
                'other', run['run_id'], str(report.with_suffix('.dwg')),
                source_document_contains_results=False,
                copy_did_not_change_open_source_identity=True,
            )

    def test_object_norms_rejects_other_work_mode(self):
        with self.assertRaisesRegex(ValueError, 'отдельное задание'):
            self.create_run('stage', 'object_norms')

    def test_object_norms_dropdown_creates_chat_task_for_selected_project(self):
        from app import FireAuditGUI
        gui = SimpleNamespace(
            current_slug='other', app=self.app,
            _update_project_from_form=Mock(return_value=self.app.projects.load('other')),
            mode_values={'Этапизация': 'stage'}, mode_var=Mock(),
            task_variant_values={'Нормы для объекта': 'object_norms'}, task_variant_var=Mock(),
            refresh_runs=Mock(), run_tree=Mock(), run_info=Mock(),
            clipboard_clear=Mock(), clipboard_append=Mock(), update_idletasks=Mock(),
        )
        gui.mode_var.get.return_value = 'Этапизация'
        gui.task_variant_var.get.return_value = 'Нормы для объекта'
        with patch('app.messagebox.showinfo') as info, patch('app.messagebox.showerror') as error:
            FireAuditGUI.create_run(gui)
        error.assert_not_called()
        info.assert_called_once()
        newest = self.app.runs.list_runs('other')[0]
        self.assertEqual(newest['task_variant'], 'object_norms')
        self.assertEqual(newest['mode'], 'compliance')
        self.assertIn('Нормы для объекта', gui.clipboard_append.call_args.args[0])

    def test_object_norms_uses_only_selected_project_snapshot(self):
        first = self.create_run('compliance', 'object_norms')
        second_project = self.app.projects.create('Особый объект Б', 'second-object')
        second_project['dates']['gpzu'] = '2025-05-14'
        self.app.projects.save('second-object', second_project)
        second = self.app.runs.create('second-object', 'compliance', 'object_norms')
        first_task = Path(first['task_file']).read_text(encoding='utf-8')
        second_task = Path(second['task_file']).read_text(encoding='utf-8')
        self.assertIn('2026-04-23', first_task)
        self.assertNotIn('Особый объект Б', first_task)
        self.assertIn('Особый объект Б', second_task)
        self.assertIn('2025-05-14', second_task)
        self.assertNotIn('2026-04-23', second_task)

    def test_editable_instruction_activates_mode_without_code_change(self):
        rules = self.root / 'knowledge' / 'rule_blocks.json'
        catalogue = json.loads(rules.read_text(encoding='utf-8'))
        catalogue['rules'].append({
            'id': 'fixture_actualization_algorithm',
            'title': 'Утверждённый алгоритм актуализации',
            'text': 'Алгоритм утверждён пользователем. Выполнить проверку по переданным шагам.',
            'modes': ['actualization_protocol'], 'requires': [], 'status': 'active',
        })
        rules.write_text(json.dumps(catalogue, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        path = self.root / 'knowledge' / '11_АКТУАЛИЗАЦИЯ.md'
        path.write_text('# Актуализация\n\n{{rule:fixture_actualization_algorithm}}\n', encoding='utf-8')
        run = self.create_run('actualization_protocol')['run']
        self.assertTrue(run['hard_constraints']['workflow_instructions_ready'])
        self.assertTrue(run['hard_constraints']['autocad_actions_enabled'])
        self.assertNotIn('Режим пока не активирован', Path(self.app.runs.run_dir('other', run['run_id']) / 'ЗАДАНИЕ_ДЛЯ_ЧАТА.md').read_text(encoding='utf-8'))

    def test_open_form_cannot_overwrite_new_customer_confirmations(self):
        from app import FireAuditGUI
        cached = self.app.projects.load('other')
        changed = copy.deepcopy(cached)
        changed['barrier_assessment'] = {'categories_approved': True, 'stu_status': 'absent'}
        self.app.projects.save('other', changed)
        gui = SimpleNamespace(app=self.app, current_slug='other', current_project=cached)
        with self.assertRaisesRegex(ValueError, 'Паспорт обновлён вне этого окна'):
            FireAuditGUI._update_project_from_form(gui)
        self.assertTrue(self.app.projects.load('other')['barrier_assessment']['categories_approved'])

    def test_reading_form_does_not_modify_cached_passport(self):
        from app import FireAuditGUI
        cached = self.app.projects.load('other')
        original = copy.deepcopy(cached)
        fields = defaultdict(lambda: SimpleNamespace(get=lambda: ''))
        fields['name'] = SimpleNamespace(get=lambda: 'Изменено в форме')
        gui = SimpleNamespace(app=self.app, current_slug='other', current_project=cached,
                              fields=fields, notes=SimpleNamespace(get=lambda *a: ''))
        proposed = FireAuditGUI._update_project_from_form(gui)
        self.assertEqual(cached, original)
        self.assertEqual(proposed['name'], 'Изменено в форме')

    def test_creating_task_refreshes_the_forms_own_saved_passport(self):
        from app import FireAuditGUI
        cached = self.app.projects.load('other')
        proposed = copy.deepcopy(cached)
        proposed['name'] = 'Новое название в форме'
        gui = SimpleNamespace(app=self.app, current_slug='other', current_project=cached,
            _update_project_from_form=lambda: proposed, mode_values={'ПП': 'barrier_assessment'},
            mode_var=SimpleNamespace(get=lambda: 'ПП'), refresh_runs=Mock(),
            run_tree=Mock(), run_info=Mock())
        with patch('app.messagebox.showinfo'), patch('app.messagebox.showerror') as error:
            FireAuditGUI.create_run(gui)
        error.assert_not_called()
        self.assertEqual(gui.current_project, self.app.projects.load('other'))
        self.assertEqual(gui.current_project['name'], 'Новое название в форме')


if __name__ == '__main__':
    unittest.main()
