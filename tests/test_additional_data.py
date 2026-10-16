import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fire_audit.barrier_inputs import defaults, inputs_for, snapshot_attachment, additional_inputs_markdown
from fire_audit.barrier_ui import AdditionalDataPanel, BarrierPanel
from fire_audit.runs import MODE_LABELS
from mcp_server import MCPServer


class AdditionalDataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(__file__).resolve().parents[1]
        for name in ('config', 'knowledge', 'templates'):
            shutil.copytree(source / name, self.root / name)
        self.server = MCPServer(self.root)
        self.app = self.server.app
        project = self.app.projects.create('Общий источник — тест', 'shared-inputs')
        project['dates']['gpzu'] = '2026-04-23'
        document = self.root / 'customer.txt'
        document.write_text('PRIVATE-ATTACHMENT — ответ заказчика', encoding='utf-8')
        data = defaults()
        data.update(
            rooms=[{'floor': '2', 'number': 'PRIVATE-ROOM', 'name': 'Склад',
                    'category': 'В3', 'area_m2': 42, 'host_part_functional_class': 'Ф5.2'}],
            category_approval_source='PRIVATE-CATEGORY-SOURCE',
            special_conditions='PRIVATE-CONDITION',
            lifts_and_hoists='PRIVATE-LIFT',
            corridor_division='PRIVATE-CORRIDOR',
            slabs_and_openings='PRIVATE-SLAB',
            storage_and_equipment='PRIVATE-STORAGE',
            fire_pump_rooms_text='PRIVATE-PUMP — общая насосная АУП и ВПВ',
            refrigeration_present=True,
            refrigeration_equipment_rooms=['PRIVATE-ROOM'],
            attachments=[snapshot_attachment(self.app.paths, 'shared-inputs', document, 'additional')],
        )
        project['barrier_assessment'] = data
        self.project = self.app.projects.save('shared-inputs', project)['project']
        self.data = copy.deepcopy(data)
        self.project_path = self.app.projects.project_dir('shared-inputs') / 'project.json'

    def test_existing_storage_and_attachment_files_are_preserved(self):
        before = self.project_path.read_bytes()
        attachment = Path(self.data['attachments'][0]['path'])
        contents = attachment.read_bytes()
        result = inputs_for(self.app.projects.load('shared-inputs'))
        self.assertEqual(result, self.data)
        result['rooms'][0]['number'] = 'changed'
        self.assertEqual(inputs_for(self.app.projects.load('shared-inputs')), self.data)
        self.assertEqual(self.project_path.read_bytes(), before)
        self.assertEqual(attachment.read_bytes(), contents)
        self.assertIs(BarrierPanel, AdditionalDataPanel)

    def test_all_modes_and_both_task_variants_include_shared_data(self):
        before = self.project_path.read_bytes()
        for mode in MODE_LABELS:
            with self.subTest(mode=mode):
                context = self.server.dispatch_tool('get_project_context',
                                                    {'slug': 'shared-inputs', 'mode': mode})
                self.assertEqual(context['additional_data'], self.data)
                self.assertEqual(context['additional_data_storage_key'], 'barrier_assessment')
                self.assertEqual(context['additional_data_scope'], 'all_work_modes_of_selected_project')
            for variant in ('full_execution', 'analysis_word'):
                with self.subTest(mode=mode, variant=variant):
                    result = self.app.runs.create('shared-inputs', mode, variant)
                    self.assertEqual(result['run']['project_snapshot']['barrier_assessment'], self.data)
                    text = Path(result['task_file']).read_text(encoding='utf-8')
                    self.assertIn('## Дополнительные данные объекта — для всех видов проверок', text)
                    self.assertIn(json.dumps(self.project, ensure_ascii=False, indent=2), text)
                    self.assertIn('- Сведения пользователя: PRIVATE-PUMP', text)
                    self.assertIn('Открой относящиеся к задаче файлы', text)
                    self.assertIn('независимо от состояния галочки', text)
                    self.assertIn('Не требуй поставить галочку для начала работы', text)
                    self.assertNotIn('Не завершён учёт дополнительных документов и ответов заказчика',
                                     result['run'].get('preparation', {}).get('errors', []))
                    self.assertEqual(
                        result['run']['hard_constraints']['autocad_actions_enabled'],
                        (variant == 'full_execution' or
                         (mode in {'compliance', 'actualization_protocol'} and variant == 'analysis_word'))
                        and context['instructions_ready'])
        self.assertEqual(self.project_path.read_bytes(), before)

    def test_review_instruction_does_not_depend_on_user_checkbox(self):
        for flag in (False, True, None):
            with self.subTest(flag=flag):
                project = copy.deepcopy(self.project)
                project['barrier_assessment']['additional_documents_reviewed'] = flag
                text = additional_inputs_markdown(project)
                self.assertIn('Во всех видах работ самостоятельно проверяй и учитывай', text)
                self.assertIn('независимо от состояния галочки', text)
                self.assertIn('снятая галочка не блокирует оценку преград', text)

    def test_new_data_does_not_rewrite_old_tasks_or_leak_to_other_projects(self):
        old = self.app.runs.create('shared-inputs', 'article89')
        paths = [Path(old['task_file']), Path(old['folder']) / 'run.json',
                 Path(old['registry_file'])]
        original = {path: path.read_bytes() for path in paths}
        project = self.app.projects.load('shared-inputs')
        project['barrier_assessment']['special_conditions'] = 'PRIVATE-NEW-CONDITION'
        self.app.projects.save('shared-inputs', project)
        new = self.app.runs.create('shared-inputs', 'compliance', 'analysis_word')
        self.assertIn('PRIVATE-NEW-CONDITION', Path(new['task_file']).read_text(encoding='utf-8'))
        self.assertEqual(original, {path: path.read_bytes() for path in paths})
        self.assertFalse(self.app.runs.chat_handoff('shared-inputs', old['run']['run_id'])['project_current'])
        self.app.projects.create('Другой объект', 'other')
        context = self.server.dispatch_tool('get_project_context', {'slug': 'other', 'mode': 'article89'})
        self.assertNotIn('PRIVATE-', json.dumps(context, ensure_ascii=False))
        self.assertEqual(context['additional_data']['attachments'], [])
        self.assertEqual(context['additional_data']['rooms'], [])

    def test_empty_data_does_not_activate_unconfigured_modes(self):
        project = self.app.projects.create('Пустой объект', 'empty')
        project['dates']['gpzu'] = '2026-04-23'
        self.app.projects.save('empty', project)
        for mode in MODE_LABELS:
            with self.subTest(mode=mode):
                result = self.app.runs.create('empty', mode)
                text = Path(result['task_file']).read_text(encoding='utf-8')
                self.assertIn('Помещений в таблице: 0.', text)
                self.assertIn('Дополнительных документов: 0.', text)
                if self.app.knowledge.instructions_pending(mode):
                    self.assertFalse(result['run']['hard_constraints']['autocad_actions_enabled'])


class AdditionalDataReadinessTests(unittest.TestCase):
    def panel(self, mode):
        gui = SimpleNamespace(
            current_slug='test', _update_project_from_form=Mock(return_value={}),
            mode_values={'Выбранная проверка': mode}, mode_var=Mock(),
            app=SimpleNamespace(paths=SimpleNamespace(knowledge=Path('unused'))))
        gui.mode_var.get.return_value = 'Выбранная проверка'
        return SimpleNamespace(slug='test', gui=gui, collect=Mock(return_value=defaults()))

    def test_other_modes_do_not_require_barrier_readiness(self):
        for mode in MODE_LABELS:
            if mode == 'barrier_assessment':
                continue
            with self.subTest(mode=mode), patch('fire_audit.barrier_ui.preparation_errors') as checks, \
                    patch('fire_audit.barrier_ui.read_json') as profile, \
                    patch('fire_audit.barrier_ui.messagebox.showinfo') as info, \
                    patch('fire_audit.barrier_ui.messagebox.showerror') as error:
                AdditionalDataPanel.readiness(self.panel(mode))
                checks.assert_not_called()
                profile.assert_not_called()
                error.assert_not_called()
                self.assertIn('не подтверждение полноты', info.call_args.args[1])

    def test_barrier_mode_keeps_its_existing_preparation_checks(self):
        with patch('fire_audit.barrier_ui.preparation_errors', return_value=['Не хватает исходных данных']) as checks, \
                patch('fire_audit.barrier_ui.read_json', return_value={}), \
                patch('fire_audit.barrier_ui.messagebox.showinfo') as info:
            AdditionalDataPanel.readiness(self.panel('barrier_assessment'))
            checks.assert_called_once()
            self.assertIn('Не хватает исходных данных', info.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
