import unittest
from pathlib import Path
from fire_audit.evacuation_sequences import admissible_sequence
from fire_audit.knowledge import KnowledgeBase
from fire_audit.paths import AppPaths


class ReviewFeedbackTests(unittest.TestCase):
    def test_two_vestibules_require_outside_terminal(self):
        zones = list('RVCVE')
        self.assertTrue(admissible_sequence(zones, terminal_is_outside=True))
        self.assertFalse(admissible_sequence(zones))
        self.assertFalse(admissible_sequence(zones, terminal_is_outside=True, same_floor=False))

    def test_hazardous_neighbor_exception_is_conjunctive(self):
        kw = dict(terminal_is_outside=True, neighbor_is_f5_ab=True)
        self.assertFalse(admissible_sequence(list('RVCVE'), **kw))
        self.assertFalse(admissible_sequence(list('RVCVE'), **kw,
                                            technical_without_permanent_workplaces=True))
        self.assertTrue(admissible_sequence(list('RVCVE'), **kw,
                                           technical_without_permanent_workplaces=True,
                                           equipment_services_that_hazardous_room=True))
        self.assertFalse(admissible_sequence(list('RVCVE'), terminal_is_outside=True,
                                            neighbor_is_f5_ab=None))

    def test_no_blanket_relaxation(self):
        for zones in ('RRVCVE', 'RRRE', 'RCVCE', 'RVCVCE'):
            self.assertFalse(admissible_sequence(list(zones), terminal_is_outside=True))
        self.assertTrue(admissible_sequence(list('RTVCCVTE'), terminal_is_outside=True))

    def test_rules_loaded_in_standalone_and_combined_protocol(self):
        kb = KnowledgeBase(AppPaths(Path(__file__).resolve().parents[1]))
        for mode in ('protocol', 'actualization_protocol', 'compliance', 'article89'):
            text = kb.snapshot_for_mode(mode).context
            for n in range(19, 26):
                self.assertTrue('[Правило error_ro_'+str(n)+']' in text, (mode, n))
            self.assertIn('R-V-C-V-E', text)
            self.assertIn('5.3.5', text)

    def test_primary_issue_rules_defer_to_accepted_history(self):
        kb = KnowledgeBase(AppPaths(Path(__file__).resolve().parents[1]))
        text = kb.resolved_document('25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md')
        self.assertGreaterEqual(text.count('обязательная первичная выдача не отменяет'), 2)


if __name__ == '__main__':
    unittest.main()
