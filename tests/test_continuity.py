import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fire_audit.core import FireAuditApp
from fire_audit.io_utils import read_json, write_json
from mcp_server import MCPServer


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(__file__).resolve().parents[1]
        for name in ('config', 'knowledge', 'templates'):
            shutil.copytree(source / name, self.root / name)
        self.app = FireAuditApp(self.root)
        project = self.app.projects.create('Тестовый объект', 'continuity-test')
        project['dates']['gpzu'] = '2026-04-23'
        self.app.projects.save('continuity-test', project)

    def test_first_run_creates_project_history_and_later_run_reads_evidence(self):
        first = self.app.runs.create('continuity-test', 'compliance')
        first_id = first['run']['run_id']
        project_dir = self.app.projects.project_dir('continuity-test')
        history_file = project_dir / 'ИСТОРИЯ_ОЦЕНОК.json'
        self.assertTrue(history_file.is_file())
        self.assertEqual(first['run']['plan_revision']['status'], 'initial')
        self.assertEqual(read_json(Path(first['folder']) / 'ПРЕДЫДУЩИЕ_ПРОВЕРКИ.json')['runs'], [])
        self.app.runs.record_continuity('continuity-test', first_id, [{
            'id': 'stair-A', 'kind': 'architecture', 'description': 'Лестница А обслуживает этажи',
            'source': 'Лист АР-1, AutoCAD-id 10', 'location': 'оси А-Б',
        }])
        self.app.runs.add_finding('continuity-test', first_id, {
            'id': 'remark-1', 'decision': {'status': 'violation'},
            'norm': {'document': 'СП 1', 'point': '4.4.1'}, 'applicability': {'floor': '2'},
        })
        old_run_bytes = (Path(first['folder']) / 'run.json').read_bytes()
        old_registry_bytes = (Path(first['folder']) / 'РЕЕСТР_ПРОВЕРКИ.json').read_bytes()

        revised_project = self.app.projects.load('continuity-test')
        revised_project['classification']['fire_resistance_degree'] = 'IV'
        self.app.projects.save('continuity-test', revised_project)

        second = self.app.runs.create('continuity-test', 'compliance', plan_change_status='new',
                                      plan_change_note='Изменён второй этаж')
        snapshot = read_json(Path(second['folder']) / 'ПРЕДЫДУЩИЕ_ПРОВЕРКИ.json')
        self.assertEqual(snapshot['runs'][0]['run_id'], first_id)
        self.assertEqual(snapshot['runs'][0]['findings_count'], 1)
        self.assertEqual(snapshot['runs'][0]['continuity_items'][0]['id'], 'stair-A')
        self.assertEqual(second['run']['plan_revision']['comparison_run_id'], first_id)
        self.assertEqual(second['run']['plan_revision']['status'], 'new')
        self.assertIn('classification.fire_resistance_degree',
                      [item['field'] for item in second['run']['passport_changes']['items']])
        task = Path(second['task_file']).read_text(encoding='utf-8')
        self.assertIn('Изменён второй этаж', task)
        self.assertIn('матрицу влияния', task)
        self.assertIn('record_continuity', task)
        self.assertEqual((Path(first['folder']) / 'run.json').read_bytes(), old_run_bytes)
        self.assertEqual((Path(first['folder']) / 'РЕЕСТР_ПРОВЕРКИ.json').read_bytes(), old_registry_bytes)
        self.assertEqual(len(read_json(history_file)['runs']), 2)

    def test_stage_task_includes_existing_on_plan_inventory_rule(self):
        snapshot = self.app.knowledge.snapshot_for_mode('stage')
        names = {document.name for document in snapshot.documents}
        self.assertIn('06_ЭТАПИЗАЦИЯ.md', names)
        self.assertIn('10_ЭТАПИЗАЦИЯ_ДЕТАЛЬНЫЕ_УСЛОВИЯ.md', names)
        expected = 'До создания любого элемента этапизации обследовать сами проверяемые планы каждого этажа'
        self.assertIn(expected, snapshot.context)
        self.assertIn('актуальные не дублировать, нуждающиеся в правке точечно исправлять на месте',
                      snapshot.context)
        run = self.app.runs.create('continuity-test', 'stage')
        self.assertIn(expected, Path(run['task_file']).read_text(encoding='utf-8-sig'))

    def test_unconfirmed_repeat_requires_question_and_is_project_isolated(self):
        first = self.app.runs.create('continuity-test', 'stage')
        repeated = self.app.runs.create('continuity-test', 'stage')
        self.assertEqual(repeated['run']['plan_revision']['status'], 'unconfirmed')
        self.assertEqual(repeated['run']['plan_revision']['previous_same_work_run_id'], first['run']['run_id'])
        self.assertIn('спроси заказчика', Path(repeated['task_file']).read_text(encoding='utf-8'))
        other = self.app.projects.create('Другой объект', 'other')
        other['dates']['gpzu'] = '2025-01-01'
        self.app.projects.save('other', other)
        self.assertEqual(self.app.runs.project_history('other')['runs'], [])
        with self.assertRaisesRegex(ValueError, 'источник'):
            self.app.runs.record_continuity('continuity-test', repeated['run']['run_id'], [{
                'id': 'unsupported', 'kind': 'architecture', 'description': 'Неподтверждённый факт',
            }])

    def test_each_work_kind_uses_immediate_predecessor_even_when_incomplete(self):
        first = self.app.runs.create('continuity-test', 'compliance')
        stage = self.app.runs.create('continuity-test', 'stage')
        second = self.app.runs.create('continuity-test', 'compliance', plan_change_status='same')
        barrier = self.app.runs.create('continuity-test', 'barrier_assessment')
        third = self.app.runs.create('continuity-test', 'compliance', plan_change_status='new')
        protocol = self.app.runs.create('continuity-test', 'protocol')
        fourth = self.app.runs.create('continuity-test', 'compliance', plan_change_status='same')

        ids = [item['run']['run_id'] for item in (first, second, third, fourth)]
        for number, item in enumerate((first, second, third, fourth), start=1):
            revision = item['run']['plan_revision']
            self.assertEqual(revision['work_iteration'], number)
            self.assertEqual(revision['comparison_run_id'], ids[number - 2] if number > 1 else '')
            self.assertEqual(revision['comparison_iteration'], number - 1)
        self.assertEqual(third['run']['plan_revision']['previous_same_work_run_id'], second['run']['run_id'])
        self.assertEqual(fourth['run']['plan_revision']['comparison_run_id'], third['run']['run_id'])
        self.assertEqual(stage['run']['plan_revision']['status'], 'initial')
        self.assertEqual(barrier['run']['plan_revision']['status'], 'initial')
        self.assertEqual(protocol['run']['plan_revision']['status'], 'initial')
        self.assertTrue(any('не зарегистрирована как завершённая' in warning
                            for warning in fourth['run']['warnings']))

        history = self.app.runs.project_history('continuity-test')
        self.assertEqual([entry['run_id'] for entry in history['by_work_kind']['compliance']], ids[::-1])
        self.assertEqual([entry['run_id'] for entry in history['by_work_kind']['stage']], [stage['run']['run_id']])
        self.assertEqual([entry['run_id'] for entry in history['by_work_kind']['barrier_assessment']],
                         [barrier['run']['run_id']])
        self.assertEqual([entry['run_id'] for entry in history['by_work_kind']['protocol']],
                         [protocol['run']['run_id']])
        snapshot = read_json(Path(fourth['folder']) / 'ПРЕДЫДУЩИЕ_ПРОВЕРКИ.json')
        self.assertEqual(snapshot['by_work_kind']['compliance'][0]['run_id'], third['run']['run_id'])
        task = Path(fourth['task_file']).read_text(encoding='utf-8')
        self.assertIn('итерация 3 опирается на 2, итерация 4 — на 3', task)
        self.assertIn('Другие виды работ, сгруппированные отдельно', task)
        listed = self.app.runs.list_runs('continuity-test')
        self.assertEqual(listed[0]['work_iteration'], 4)
        self.assertEqual(listed[0]['work_kind'], 'compliance')

    def test_object_norms_has_independent_chain_from_compliance(self):
        compliance = self.app.runs.create('continuity-test', 'compliance')
        norms = self.app.runs.create('continuity-test', 'compliance', 'object_norms')
        self.assertEqual(norms['run']['plan_revision']['status'], 'initial')
        self.assertEqual(norms['run']['plan_revision']['comparison_run_id'], '')
        repeated = self.app.runs.create('continuity-test', 'compliance', 'object_norms',
                                        plan_change_status='same')
        self.assertEqual(repeated['run']['plan_revision']['comparison_run_id'], norms['run']['run_id'])
        self.assertNotEqual(repeated['run']['plan_revision']['comparison_run_id'], compliance['run']['run_id'])

    def test_legacy_same_second_runs_are_ordered_by_numeric_suffix_without_rewrite(self):
        project_dir = self.app.projects.project_dir('continuity-test')
        ids = ['20260101-120000-compliance', '20260101-120000-compliance-2',
               '20260101-120000-compliance-10']
        for run_id in ids:
            folder = project_dir / 'runs' / run_id
            folder.mkdir(parents=True)
            write_json(folder / 'run.json', {
                'run_id': run_id, 'project_slug': 'continuity-test', 'mode': 'compliance',
                'task_variant': 'full_execution', 'created_at': '2026-01-01T12:00:00+00:00',
            })
        before = [(project_dir / 'runs' / run_id / 'run.json').read_bytes() for run_id in ids]
        history = self.app.runs.project_history('continuity-test')
        self.assertEqual([entry['run_id'] for entry in history['by_work_kind']['compliance']], ids[::-1])
        self.assertEqual([entry['work_iteration'] for entry in history['by_work_kind']['compliance']], [3, 2, 1])
        self.assertEqual(self.app.runs.comparison_baseline('continuity-test', 'compliance')['run_id'], ids[-1])
        self.assertEqual([(project_dir / 'runs' / run_id / 'run.json').read_bytes() for run_id in ids], before)

    def test_gui_asks_about_plans_and_mcp_exposes_history(self):
        from app import FireAuditGUI
        self.app.runs.create('continuity-test', 'compliance')
        gui = SimpleNamespace(
            current_slug='continuity-test', app=self.app,
            _update_project_from_form=Mock(return_value=self.app.projects.load('continuity-test')),
            mode_values={'Оценка соответствия АР': 'compliance'}, mode_var=Mock(),
            task_variant_values={'Полная проверка с AutoCAD': 'full_execution'}, task_variant_var=Mock(),
            refresh_runs=Mock(), run_tree=Mock(), run_info=Mock(),
            clipboard_clear=Mock(), clipboard_append=Mock(), update_idletasks=Mock(),
        )
        gui.mode_var.get.return_value = 'Оценка соответствия АР'
        gui.task_variant_var.get.return_value = 'Полная проверка с AutoCAD'
        with patch('app.messagebox.askyesnocancel', return_value=False) as ask, \
             patch('app.messagebox.showinfo'), patch('app.messagebox.showerror') as error:
            FireAuditGUI.create_run(gui)
        ask.assert_called_once()
        error.assert_not_called()
        self.assertEqual(self.app.runs.list_runs('continuity-test')[0]['plan_change_status'], 'same')
        server = MCPServer(self.root)
        history = server.dispatch_tool('get_project_history', {'slug': 'continuity-test'})
        self.assertEqual(len(history['runs']), 2)
        self.assertEqual(history['runs'][0]['plan_revision']['status'], 'same')

    def test_gui_history_shows_all_work_kinds_without_filter_choice(self):
        from app import FireAuditGUI
        self.app.runs.create('continuity-test', 'stage')
        self.app.runs.create('continuity-test', 'protocol')
        tree = Mock()
        tree.get_children.return_value = ()
        gui = SimpleNamespace(current_slug='continuity-test', app=self.app, run_tree=tree)
        FireAuditGUI.refresh_runs(gui)
        self.assertEqual(tree.insert.call_count, 2)
        labels = {call.kwargs['values'][0] for call in tree.insert.call_args_list}
        self.assertEqual(labels, {'Этапизация', 'Протокол'})

    def test_cancelled_plan_question_creates_no_run_or_passport_write(self):
        from app import FireAuditGUI
        self.app.runs.create('continuity-test', 'stage')
        project_file = self.app.projects.project_dir('continuity-test') / 'project.json'
        original = project_file.read_bytes()
        gui = SimpleNamespace(
            current_slug='continuity-test', app=self.app,
            _update_project_from_form=Mock(return_value=self.app.projects.load('continuity-test')),
            mode_values={'Этапизация': 'stage'}, mode_var=Mock(),
            task_variant_values={'Полная проверка с AutoCAD': 'full_execution'}, task_variant_var=Mock(),
        )
        gui.mode_var.get.return_value = 'Этапизация'
        gui.task_variant_var.get.return_value = 'Полная проверка с AutoCAD'
        with patch('app.messagebox.askyesnocancel', return_value=None), patch('app.messagebox.showerror') as error:
            FireAuditGUI.create_run(gui)
        error.assert_not_called()
        self.assertEqual(len(self.app.runs.list_runs('continuity-test')), 1)
        self.assertEqual(project_file.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
