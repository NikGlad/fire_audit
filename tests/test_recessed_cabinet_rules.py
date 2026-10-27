import json
import unittest
from pathlib import Path

from fire_audit.rule_registry import RuleResolver
from tests import test_run_rule_snapshot as snapshot_tests


ROOT = Path(__file__).resolve().parents[1]
RULE = 'barrier_recessed_cabinets_not_communication_shafts_20261027'


class RecessedCabinetRunTests(unittest.TestCase):
    setUp = snapshot_tests.RunRuleSnapshotTests.setUp
    assert_consistent_artifacts = snapshot_tests.RunRuleSnapshotTests.assert_consistent_artifacts

    def test_cabinet_distinction_reaches_both_variants_and_independent_objects(self):
        catalogue = json.loads((ROOT / 'knowledge/rule_blocks.json').read_text(encoding='utf-8'))
        resolver = RuleResolver(catalogue, 'barrier_assessment')
        resolver.validate()
        rows = {row['id']: row for row in catalogue['rules']}
        for related in ('shared_018', 'barrier_recessed_cabinet_wall_route_20261023'):
            with self.subTest(related=related):
                self.assertIn(RULE, rows[related]['requires'])
                self.assertIn(rows[RULE]['text'], resolver.render('{{rule:' + related + '}}'))
                resolver = RuleResolver(catalogue, 'barrier_assessment')
        for slug in ('first', 'second'):
            for variant in ('full_execution', 'analysis_word'):
                with self.subTest(slug=slug, variant=variant):
                    result = self.app.runs.create(slug, 'barrier_assessment', task_variant=variant)
                    self.assert_consistent_artifacts(result)
                    constraints = result['run']['hard_constraints']
                    self.assertTrue(constraints['recessed_cabinets_not_communication_shafts_for_fire_resistance'])
                    self.assertTrue(constraints['recessed_cabinet_fire_resistance_requires_independent_basis'])
                    self.assertTrue(constraints['communication_shafts_excluded_from_barrier_assessment'])
                    task = Path(result['task_file']).read_text(encoding='utf-8-sig')
                    self.assertIn(rows[RULE]['text'], task)
                    self.assertIn('по внутренней конструктивной стороне ниши, огибающей шкаф', task)
                    profile = json.loads((self.root / 'knowledge/barrier_drawing_profile.json').read_text(encoding='utf-8'))
                    cabinet = profile['cabinet_wall_rule']
                    self.assertEqual(cabinet['classification_rule_ref'], RULE)
                    self.assertIn('"classification_rule_ref": "' + RULE + '"', task)
                    self.assertEqual(cabinet['fire_resistance_classification'], 'cabinet_or_recess_not_communication_shaft')
                    self.assertFalse(cabinet['shaft_requirements_define_fire_resistance'])
                    self.assertTrue(cabinet['communication_equipment_alone_is_not_a_shaft_basis'])
                    self.assertTrue(cabinet['independent_boundary_basis_required'])
                    self.assertEqual(cabinet['route'], 'actual_inner_constructive_sides_behind_recess')
                    self.assertFalse(cabinet['ordinary_corridor_line_across_access_front'])


if __name__ == '__main__':
    unittest.main()
