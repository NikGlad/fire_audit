import unittest
from pathlib import Path
from fire_audit.knowledge import KnowledgeBase
from fire_audit.paths import AppPaths


class NormFeedbackRulesTests(unittest.TestCase):
    def test_rules_resolve_in_all_assessment_modes(self):
        kb = KnowledgeBase(AppPaths(Path(__file__).resolve().parents[1]))
        for mode in ('stage', 'compliance', 'article89', 'protocol',
                     'actualization_protocol', 'barrier_assessment'):
            context = kb.snapshot_for_mode(mode).context
            for number in range(26, 37):
                self.assertIn(f'[Правило error_ro_{number}]', context, (mode, number))
            self.assertNotIn('{{rule:error_ro_', context)

    def test_dimension_and_corridor_exceptions_remain_distinct(self):
        kb = KnowledgeBase(AppPaths(Path(__file__).resolve().parents[1]))
        context = kb.snapshot_for_mode('compliance').context
        self.assertIn('луч вести к подтверждённому размеру', context)
        self.assertIn('одну линию маркировки на каждый', context)
        self.assertIn('Две двери в одну лестницу не создают две независимые лестницы', context)
        self.assertIn('локальный порог не снимает общезданного требования', context)


if __name__ == '__main__':
    unittest.main()
