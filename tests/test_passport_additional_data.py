"""Regression tests for saving both live Tk forms to a temporary project."""
import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import FireAuditGUI
from fire_audit.barrier_inputs import snapshot_attachment


class PassportAdditionalDataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(__file__).resolve().parents[1]
        for name in ('config', 'knowledge', 'templates'):
            shutil.copytree(source / name, self.root / name)
        root_patch = patch('app.ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        with patch.object(FireAuditGUI, 'refresh_norm_status'):
            self.gui = FireAuditGUI()
        self.gui.withdraw()
        self.addCleanup(self.gui.destroy)
        for name in ('showinfo', 'showerror', 'showwarning'):
            p = patch('app.messagebox.' + name)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        self.gui.app.projects.create('Тест сохранения', 'save-test')
        self.gui.refresh_projects()
        self.gui._select_project_by_slug('save-test')
        self.gui.update()
        self.panel = self.gui.additional_data_panel
        self.project_file = self.gui.app.projects.project_dir('save-test') / 'project.json'

    def fill_draft(self):
        self.panel.values['special_conditions'].set('Уточнение заказчика')
        self.panel.values['category_approval_source'].set('Письмо заказчика')
        self.panel.values['refrigeration_present'].set('Есть')
        self.panel.values['refrigeration_equipment_rooms'].set('101')
        self.panel.pump_text.set('Насосная АУП на первом этаже')
        for key, widget in self.panel.special_texts.items():
            widget.insert('1.0', 'Уточнение: ' + key)
        self.panel.data['rooms'] = [
            {'floor': '1', 'number': '101', 'name': 'Склад',
             'category': 'В3', 'area_m2': 42, 'host_part_functional_class': 'Ф5.2'}]
        self.panel.categories_dirty = True
        document = self.root / 'customer.txt'
        document.write_text('Документ заказчика', encoding='utf-8')
        self.panel.data['attachments'].append(snapshot_attachment(
            self.gui.app.paths, 'save-test', document, 'additional'))
        self.panel.refresh_tables()

    def test_passport_save_persists_draft_and_repeated_save_keeps_all_data(self):
        self.fill_draft()
        self.gui.fields['name'].set('Новое название')
        self.gui.save_project()
        self.gui.update()
        self.showerror.assert_not_called()
        first = self.gui.app.projects.load('save-test')
        data = first['barrier_assessment']
        self.assertEqual(first['name'], 'Новое название')
        self.assertEqual(data['special_conditions'], 'Уточнение заказчика')
        self.assertEqual(data['fire_pump_rooms_text'], 'Насосная АУП на первом этаже')
        self.assertEqual(data['rooms'][0]['category'], 'В3')
        self.assertEqual(data['refrigeration_equipment_rooms'], ['101'])
        self.assertTrue(data['category_source']['manual'])
        self.assertTrue(Path(data['category_source']['path']).is_file())
        attachment = Path(data['attachments'][0]['path'])
        self.assertEqual(attachment.read_text(encoding='utf-8'), 'Документ заказчика')
        for key in self.panel.special_texts:
            self.assertEqual(data[key], 'Уточнение: ' + key)
        for _ in range(3):
            self.gui.save_project()
            self.gui.update()
            self.assertEqual(self.gui.app.projects.load('save-test')['barrier_assessment'], data)
            self.assertEqual(self.panel.collect(), data)
        self.showerror.assert_not_called()
        self.assertFalse(self.panel.categories_dirty)

    def test_additional_save_then_passport_save_keeps_saved_data(self):
        self.fill_draft()
        self.panel.save()
        expected = copy.deepcopy(self.gui.app.projects.load('save-test')['barrier_assessment'])
        self.gui.fields['fire_degree'].set('III')
        self.gui.save_project()
        self.gui.update()
        self.showerror.assert_not_called()
        self.assertEqual(self.gui.app.projects.load('save-test')['barrier_assessment'], expected)

    def test_invalid_draft_preserves_disk_and_both_forms(self):
        before = self.project_file.read_bytes()
        self.gui.fields['name'].set('Несохранённое название')
        self.panel.values['special_conditions'].set('Несохранённое уточнение')
        self.panel.flags['categories_approved'].set(True)
        self.panel.values['category_approval_source'].set('')
        self.gui.save_project()
        self.gui.update()
        self.showerror.assert_called_once()
        self.assertEqual(self.project_file.read_bytes(), before)
        self.assertEqual(self.gui.fields['name'].get(), 'Несохранённое название')
        self.assertEqual(self.panel.values['special_conditions'].get(), 'Несохранённое уточнение')

    def test_external_change_is_not_overwritten(self):
        self.fill_draft()
        external = self.gui.app.projects.load('save-test')
        external['notes'] = 'Сведения, сохранённые другим окном'
        self.gui.app.projects.save('save-test', external)
        before = self.project_file.read_bytes()
        self.gui.save_project()
        self.gui.update()
        self.showerror.assert_called_once()
        self.assertEqual(self.project_file.read_bytes(), before)
        self.assertEqual(self.panel.values['special_conditions'].get(), 'Уточнение заказчика')

    def fill_change_draft(self):
        self.gui.change_date.set('2026-10-12')
        self.gui.change_description.insert('1.0', 'Изменена планировка второго этажа')
        self.gui.change_scope.set('Этаж 2, оси А–Б')
        self.gui.change_source.set('Письмо заказчика от 12.10.2026')
        self.gui.change_impact.insert('1.0', 'Перепроверить пути эвакуации')
        self.gui.change_category_vars['Вводные'].set(True)

    def test_heights_accept_and_preserve_free_text_after_repeated_saves(self):
        expected = {
            'height_sp1': 'не более 28 м; по п.3.1 СП1, уточнение заказчика.',
            'f5_floor_to_ceiling_height': '3,2–4.5 м; у балки ниже, см. разрез 1.'}
        for key, value in expected.items():
            self.gui.fields[key].set(value)
        for _ in range(2):
            self.gui.save_project()
            self.gui.update()
            for key, value in expected.items():
                self.assertEqual(self.gui.fields[key].get(), value)
        saved = self.gui.app.projects.load('save-test')['geometry']
        self.assertEqual(saved['height_m'], expected['height_sp1'])
        self.assertEqual(saved['floor_to_ceiling_height_m'], expected['f5_floor_to_ceiling_height'])
        self.showerror.assert_not_called()

    def test_heights_can_be_cleared_and_legacy_numbers_still_load(self):
        project = self.gui.app.projects.load('save-test')
        project['geometry'].update(height_m=12.4, floor_to_ceiling_height_m=3.6)
        self.gui.app.projects.save('save-test', project)
        self.gui._select_project_by_slug('save-test')
        self.assertEqual(self.gui.fields['height_sp1'].get(), '12,4')
        self.assertEqual(self.gui.fields['f5_floor_to_ceiling_height'].get(), '3,6')
        for field in ('height_sp1', 'f5_floor_to_ceiling_height'):
            self.gui.fields[field].set('')
        self.gui.save_project()
        saved = self.gui.app.projects.load('save-test')['geometry']
        self.assertIsNone(saved['height_m'])
        self.assertIsNone(saved['floor_to_ceiling_height_m'])
        self.showerror.assert_not_called()

    def test_passport_saves_change_once_and_preserves_existing_history(self):
        prior = self.gui.app.projects.add_change(
            'save-test', event_date='2026-10-01', category='Планы',
            description='Получены планы', source_reference='Письмо 1')
        self.gui._select_project_by_slug('save-test')
        self.fill_change_draft()
        self.fill_draft()
        self.gui.save_project()
        first = self.gui.app.projects.load('save-test')
        self.assertEqual(first['change_log'][0], prior)
        self.assertEqual(len(first['change_log']), 2)
        self.assertEqual(first['change_log'][1]['categories'], ['Планы', 'Вводные'])
        self.assertEqual(first['change_log'][1]['affected_scope'], 'Этаж 2, оси А–Б')
        self.assertEqual(self.gui.change_description.get('1.0', 'end').strip(), '')
        for _ in range(3):
            self.gui.save_project()
            self.gui.update()
        self.assertEqual(self.gui.app.projects.load('save-test')['change_log'], first['change_log'])
        self.assertEqual(len(self.gui.change_tree.get_children()), 2)
        self.showerror.assert_not_called()

    def test_incomplete_change_prevents_save_without_losing_drafts(self):
        before = self.project_file.read_bytes()
        self.fill_change_draft()
        self.gui.change_source.set('')
        self.gui.fields['name'].set('Новое имя')
        self.panel.values['special_conditions'].set('Уточнение')
        self.gui.save_project()
        self.showerror.assert_called_once()
        self.assertEqual(self.project_file.read_bytes(), before)
        self.assertIn('Изменена планировка', self.gui.change_description.get('1.0', 'end'))
        self.assertEqual(self.gui.fields['name'].get(), 'Новое имя')
        self.assertEqual(self.panel.values['special_conditions'].get(), 'Уточнение')

    def test_add_change_button_preserves_all_tabs_and_allows_next_passport_save(self):
        self.fill_change_draft()
        self.fill_draft()
        self.gui.fields['name'].set('Изменённое название')
        self.gui.add_project_change()
        saved = self.gui.app.projects.load('save-test')
        self.assertEqual(saved['name'], 'Изменённое название')
        self.assertEqual(len(saved['change_log']), 1)
        self.assertEqual(saved['barrier_assessment']['special_conditions'], 'Уточнение заказчика')
        self.gui.save_project()
        self.gui.update()
        self.assertEqual(self.gui.app.projects.load('save-test')['change_log'], saved['change_log'])
        self.showerror.assert_not_called()

    def test_failed_write_keeps_change_draft_for_retry(self):
        self.fill_change_draft()
        before = self.project_file.read_bytes()
        with patch.object(self.gui.app.projects, 'save', side_effect=OSError('Ошибка записи')):
            self.gui.save_project()
        self.assertEqual(self.project_file.read_bytes(), before)
        self.assertIn('Изменена планировка', self.gui.change_description.get('1.0', 'end'))
        self.gui.save_project()
        self.assertEqual(len(self.gui.app.projects.load('save-test')['change_log']), 1)

    def test_new_object_without_selection_can_save_additional_fields(self):
        before = self.project_file.read_bytes()
        self.gui.current_slug = ''
        self.gui.current_project = {}
        self.gui._load_project_form()
        self.gui.fields['name'].set('Новый объект')
        self.panel.values['special_conditions'].set('Вводные нового объекта')
        self.gui.save_project()
        self.gui.update()
        self.showerror.assert_not_called()
        self.assertEqual(self.gui.app.projects.load(self.gui.current_slug)[
            'barrier_assessment']['special_conditions'], 'Вводные нового объекта')
        self.assertEqual(self.project_file.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
