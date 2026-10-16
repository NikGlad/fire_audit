import unittest
from pathlib import Path
from fire_audit.knowledge import KnowledgeBase, MODE_FILES
from fire_audit.paths import AppPaths


class ErrorControlRulesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.knowledge = KnowledgeBase(AppPaths(self.root))

    def test_new_module_is_loaded_and_valid_in_every_mode(self):
        for mode in MODE_FILES:
            snapshot = self.knowledge.snapshot_for_mode(mode)
            self.assertIn('30_КОНТРОЛЬ_ОШИБОК_И_ПРОПУСКОВ.md', [d.name for d in snapshot.documents])
            self.assertIn('КО-11. Фасады', snapshot.context)

    def test_request_numbering_conflict_removed(self):
        for name in ('03_ОЦЕНКА_СООТВЕТСТВИЯ.md', '08_САМОПРОВЕРКА.md'):
            text = self.knowledge.load_document(name).text
            self.assertNotIn('Запросы, пояснения, зелёные обоснования отказа', text)
            self.assertNotIn('запросы, пояснения и зелёные обоснования отдельно не пронумерованы', text)
            self.assertIn('сквозн', text)

    def test_templates_and_read_only_are_protected(self):
        text = self.knowledge.load_document('30_КОНТРОЛЬ_ОШИБОК_И_ПРОПУСКОВ.md').text
        for phrase in ('не разрешает запись в режиме только чтения', 'Исходный шаблон в блоке сохранять полностью', 'Старые реестры не переписывать'):
            self.assertIn(phrase, text)

    def test_assets_and_scope(self):
        for name in ('corridor_single_side.jpg', 'corridor_two_sides.png'):
            self.assertTrue((self.root/'knowledge/assets'/name).is_file())
        text = self.knowledge.load_document('30_КОНТРОЛЬ_ОШИБОК_И_ПРОПУСКОВ.md').text
        self.assertNotIn('Орехово', text)
        self.assertNotIn('377BE', text)
        self.assertEqual([], self.knowledge.scope_issues('30_КОНТРОЛЬ_ОШИБОК_И_ПРОПУСКОВ.md', text))

    def test_all_twelve_control_sections(self):
        text = self.knowledge.load_document('30_КОНТРОЛЬ_ОШИБОК_И_ПРОПУСКОВ.md').text
        for index in range(1, 13):
            self.assertIn(f'## КО-{index:02}.', text)


if __name__ == '__main__':
    unittest.main()
