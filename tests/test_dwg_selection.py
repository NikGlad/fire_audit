"""Open-drawing defaults must work without treating user prose as a filename."""
import shutil
import tempfile
import unittest
from pathlib import Path

from fire_audit.dwg_selection import explicit_dwg_path
from fire_audit.runs import MODE_LABELS
from mcp_server import MCPServer


class DwgSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(__file__).resolve().parents[1]
        for folder in ('config', 'knowledge', 'templates'):
            shutil.copytree(source / folder, self.root / folder)
        self.server = MCPServer(self.root)
        self.app = self.server.app
        project = self.app.projects.create('Тест выбора чертежей', 'dwg-test')
        project['dates']['gpzu'] = '2026-04-23'
        self.app.projects.save('dwg-test', project)

    def test_prose_directories_and_multiple_paths_are_not_a_bound_source(self):
        for text in ('', 'Оба открытых файла; сравнить замечания', r'C:\drawings',
                     'В AutoCAD открыт исходник. Использовать его.',
                     'C:\\a.dwg\nC:\\b.dwg', r'C:\a.dwg и C:\b.dwg'):
            with self.subTest(text=text):
                self.assertEqual(explicit_dwg_path(text), '')
        for text in (r'C:\project folder\plan.dwg', r'\\server\share\plan.DWG',
                     r'C:\project;part\plan.dwg'):
            self.assertEqual(explicit_dwg_path('"' + text + '"'), text)

    def test_empty_location_is_not_a_warning_and_every_mode_gets_the_default(self):
        project = self.app.projects.load('dwg-test')
        errors, warnings = self.app.projects.validate(project, require_ready=True)
        self.assertFalse(errors)
        self.assertFalse(any('DWG' in item for item in warnings))
        before = (self.app.projects.project_dir('dwg-test') / 'project.json').read_bytes()
        for mode in MODE_LABELS:
            with self.subTest(mode=mode):
                result = self.app.runs.create('dwg-test', mode)
                run = result['run']
                text = Path(result['task_file']).read_text(encoding='utf-8')
                self.assertIn('По умолчанию работай с открытым файлом или файлами DWG', text)
                self.assertIn('Пустое поле не блокирует начало работы', text)
                self.assertIn('более позднее явное указание пользователя в чате', text)
                self.assertEqual(run['hard_constraints']['default_dwg_selection'], 'open_autocad_drawings')
                self.assertFalse(run['hard_constraints']['blank_dwg_location_blocks_work'])
                registry = self.app.runs.load('dwg-test', run['run_id'])['registry']
                self.assertEqual(registry['dwg_actions']['source_path'], '')
        self.assertEqual((self.app.projects.project_dir('dwg-test') / 'project.json').read_bytes(), before)

    def test_free_text_survives_save_and_all_task_variants(self):
        project = self.app.projects.load('dwg-test')
        note = 'Сравнить два открытых файла.\nОсновной — первый; пояснения — во втором.'
        project['dwg']['path'] = note
        self.app.projects.save('dwg-test', project)
        for variant in ('full_execution', 'analysis_word', 'object_norms'):
            with self.subTest(variant=variant):
                result = self.app.runs.create('dwg-test', 'compliance', variant)
                text = Path(result['task_file']).read_text(encoding='utf-8')
                for line in note.splitlines():
                    self.assertIn('> ' + line, text)
                self.assertEqual(result['run']['project_snapshot']['dwg']['path'], note)
                self.assertEqual(result['run']['source_file_stamp']['path'], '')
                registry = self.app.runs.load('dwg-test', result['run']['run_id'])['registry']
                self.assertEqual(registry['dwg_actions']['source_path'], '')

    def test_actual_source_can_be_recorded_without_editing_passport(self):
        project = self.app.projects.load('dwg-test')
        project['dwg']['path'] = 'Работать с открытым файлом по указанию в чате'
        self.app.projects.save('dwg-test', project)
        before = (self.app.projects.project_dir('dwg-test') / 'project.json').read_bytes()
        created = self.app.runs.create('dwg-test', 'compliance')
        run_id = created['run']['run_id']
        source = self.root / 'source.dwg'
        source.write_bytes(b'synthetic source fixture')
        result = source.with_name(f'source__compliance__{run_id}.dwg')
        result.write_bytes(b'synthetic result fixture')
        record = self.server.dispatch_tool('record_result_dwg', {
            'slug': 'dwg-test', 'run_id': run_id, 'result_path': str(result),
            'source_path': str(source), 'source_document_contains_results': False,
            'copy_did_not_change_open_source_identity': True})
        self.assertEqual(record['source_path'], str(source))
        self.assertEqual(source.read_bytes(), b'synthetic source fixture')
        self.assertEqual((self.app.projects.project_dir('dwg-test') / 'project.json').read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'source_path'):
            self.app.runs.record_result_dwg('dwg-test', run_id, str(result),
                source_path='Использовать текущий файл', source_document_contains_results=False,
                copy_did_not_change_open_source_identity=True)


if __name__ == '__main__':
    unittest.main()
