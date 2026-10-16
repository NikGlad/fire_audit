import json
import unittest
from tests import test_run_rule_snapshot as snapshot_tests


class StairwallTaskUpdateTests(unittest.TestCase):
    setUp = snapshot_tests.RunRuleSnapshotTests.setUp
    assert_consistent_artifacts = snapshot_tests.RunRuleSnapshotTests.assert_consistent_artifacts

    def test_updated_stair_rule_reaches_new_tasks_and_old_snapshot_is_immutable(self):
        first = self.app.runs.create('first', 'barrier_assessment')
        flags = first['run']['hard_constraints']
        self.assertTrue(flags['draw_stair_enclosures_as_separate_boundaries'])
        self.assertTrue(flags['internal_stairwell_walls_table21_required'])
        self.assertFalse(flags['stairwell_walls_excluded_on_both_sides'])
        self.assertFalse(flags['stairwell_wall_exception_requires_higher_performance_than_table21'])
        self.assertTrue(flags['exterior_walls_only_with_explicit_explanation_requirement'])
        self.assertTrue(flags['one_barrier_per_shared_boundary'])
        self.assert_consistent_artifacts(first)
        from pathlib import Path
        frozen = Path(first['folder']) / 'run.json'
        original = frozen.read_bytes()
        profile_path = self.root / 'knowledge/barrier_drawing_profile.json'
        profile = json.loads(profile_path.read_text(encoding='utf-8-sig'))
        profile['stair_enclosure_rule']['draw_separate_enclosure'] = False
        profile_path.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
        second = self.app.runs.create('first', 'barrier_assessment')
        self.assertFalse(second['run']['hard_constraints']['draw_stair_enclosures_as_separate_boundaries'])
        self.assertEqual(original, frozen.read_bytes())
