import json
import unittest
from pathlib import Path

from fire_audit.barrier_workflow import legend_color_errors
from fire_audit.rule_registry import RuleResolver
from tests import test_run_rule_snapshot as snapshot_tests


ROOT = Path(__file__).resolve().parents[1]
EXCLUSION = 'barrier_communication_shafts_excluded_20261027'
OLD_RULES = {
    'shared_009', 'doc_18_083', 'barrier_communication_shafts_EI45_20261021',
    'barrier_integrated_safety_zone_shafts_20261019',
    'barrier_stair_volume_shafts_20261023',
    'barrier_stair_internal_shafts_and_full_enclosure_20261023',
}


class CommunicationShaftExclusionTests(unittest.TestCase):
    def test_active_sources_have_no_obsolete_shaft_rules_or_references(self):
        catalogue = json.loads((ROOT / 'knowledge/rule_blocks.json').read_text(encoding='utf-8'))
        resolver = RuleResolver(catalogue)
        resolver.validate()
        rows = {row['id']: row for row in catalogue['rules']}
        self.assertFalse(OLD_RULES & rows.keys())
        for document in (ROOT / 'knowledge').glob('*.md'):
            text = document.read_text(encoding='utf-8')
            resolver.validate_text(text, document.name)
            self.assertFalse(OLD_RULES & {key for kind, key in resolver.references(text)})
        overrides = json.loads((ROOT / 'knowledge/rule_overrides.json').read_text(encoding='utf-8'))['rules']
        self.assertFalse(OLD_RULES & {row['id'] for row in overrides})
        self.assertIn(EXCLUSION, {row['text_ref'] for row in overrides if row.get('text_ref')})

    def test_profile_excludes_shafts_even_at_overlaps_and_preserves_lifts(self):
        profile = json.loads((ROOT / 'knowledge/barrier_drawing_profile.json').read_text(encoding='utf-8'))
        policy = profile['communication_shaft_rule']
        self.assertEqual(policy['assessment'], 'excluded')
        for key in ('draw_all_identified', 'build_enclosure', 'assign_fire_resistance',
                    'assign_color', 'consider_in_shared_boundary',
                    'consider_when_overlapping_other_barriers', 'include_in_legend',
                    'include_in_required_barrier_register', 'create_dimension_or_enclosure_query'):
            self.assertIs(policy[key], False, key)
        self.assertTrue(policy['stair_volume_excluded'])
        self.assertTrue(policy['NT_and_MGN_zones_excluded'])
        self.assertFalse(policy['lift_and_hoist_shafts_excluded'])
        for group in profile['color_equivalence_groups']:
            self.assertNotIn('communication_shaft', group['members'])
        self.assertIn('ordinary_lift', profile['color_equivalence_groups'][0]['members'])
        self.assertIn('interward', profile['color_equivalence_groups'][0]['members'])
        self.assertTrue(profile['completion_rules']['full_outer_stair_enclosure_reviewed'])

    def test_legend_rejects_communication_shaft_instead_of_merging_it_into_EI45(self):
        errors = legend_color_errors([dict(key='communication_shaft', name='EI 45', color_index=6)])
        self.assertTrue(any('исключены из оценки' in error for error in errors))
        self.assertFalse(any('объединить' in error for error in errors))


class CommunicationShaftRunSnapshotTests(unittest.TestCase):
    setUp = snapshot_tests.RunRuleSnapshotTests.setUp
    assert_consistent_artifacts = snapshot_tests.RunRuleSnapshotTests.assert_consistent_artifacts

    def test_new_run_receives_exclusion_without_old_drawing_requirements(self):
        result = self.app.runs.create('first', 'barrier_assessment')
        self.assert_consistent_artifacts(result)
        constraints = result['run']['hard_constraints']
        self.assertTrue(constraints['communication_shafts_excluded_from_barrier_assessment'])
        self.assertTrue(constraints['communication_shafts_not_considered_when_overlapping_other_barriers'])
        self.assertNotIn('communication_shafts_EI45_corridor_side_required', constraints)
        self.assertNotIn('stronger_shared_shaft_boundary_preserved_once', constraints)
        self.assertTrue(constraints['review_lift_shafts_halls_and_hoist_shafts'])
        context = (Path(result['folder']) / 'ПРАВИЛА_ЗАПУСКА.md').read_text(encoding='utf-8-sig')
        self.assertIn(EXCLUSION, context)
        self.assertIn('коммуникационные шахты не выделять, не отгораживать и никак не учитывать', context)
        for obsolete in OLD_RULES:
            self.assertNotIn('[Правило ' + obsolete + ']', context)
        for old_text in ('все распознаваемые коммуникационные шахты',
                         'Коммуникационную шахту, расположенную в объёме лестничной клетки, обозначать',
                         'Включение шахты в общий внешний контур зоны НТ/МГН не отменяет её обозначение'):
            self.assertNotIn(old_text, context)
        sequence = self.app.knowledge.resolved_document(
            '32_ПОСЛЕДОВАТЕЛЬНОСТЬ_ОЦЕНКИ_ПРЕГРАД.md', 'barrier_assessment')
        self.assertIn(EXCLUSION, sequence)


if __name__ == '__main__':
    unittest.main()
