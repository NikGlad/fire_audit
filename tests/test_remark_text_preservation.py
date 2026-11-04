import unittest
from pathlib import Path
from fire_audit.knowledge import KnowledgeBase, MODE_FILES
from fire_audit.paths import AppPaths


class RemarkTextPreservationTests(unittest.TestCase):
    def setUp(self):
        self.kb = KnowledgeBase(AppPaths(Path(__file__).resolve().parents[1]))

    def test_protected_systems_and_minimal_edit_rule_load_in_every_mode(self):
        for mode in MODE_FILES:
            text = self.kb.snapshot_for_mode(mode).context
            for marker in ('[Правило doc_00_008]',
                           '[Правило shared_remark_parameter_only_20261104]'):
                self.assertTrue(marker in text, (mode, marker))
            self.assertTrue('СОУЭ и противодымной вентиляции сохранять целиком' in text, mode)
            self.assertTrue('Если корректный вывод можно выразить заменой ключевых значений' in text, mode)

    def test_stage_does_not_override_protected_systems(self):
        text = self.kb.resolved_document('06_ЭТАПИЗАЦИЯ.md')
        self.assertIn('с прямым исключением doc_00_008', text)
        self.assertIn('кроме неизменяемых общих требований СОУЭ и противодымной вентиляции', text)

    def test_formatting_rule_preserves_necessary_conditions_and_variants(self):
        text = self.kb.snapshot_for_mode('compliance').context
        self.assertIn('суммарной шириной и шириной каждого выхода', text)
        self.assertIn('Не распространять разрешение минимальных дополнений на эти два общих требования', text)
        self.assertIn('не менять варианты решения ради стиля', text)


if __name__ == '__main__':
    unittest.main()
