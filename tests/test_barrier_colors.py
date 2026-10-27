import json
import unittest
from pathlib import Path

from fire_audit.barrier_workflow import legend_color_errors, validate_plan
from tests.test_barrier_workflow import fixture_plan
from tests import test_run_rule_snapshot as snapshot_tests


class BarrierColorTests(unittest.TestCase):
    def test_rejects_same_displayed_color_with_different_aci(self):
        for first, second in ((6, 6), (1, 10), (5, 170)):
            with self.subTest(first=first, second=second):
                entries = [dict(key="a", name="A", color_index=first),
                           dict(key="b", name="B", color_index=second)]
                self.assertTrue(any("одинаковые цвета" in e for e in legend_color_errors(entries)))

    def test_profile_has_distinct_displayed_colors_and_required_policy(self):
        root = Path(__file__).resolve().parents[1]
        profile = json.loads((root / "knowledge/barrier_drawing_profile.json").read_text(encoding="utf-8"))
        self.assertEqual(legend_color_errors(profile["legend"]), [])
        self.assertTrue(profile["color_policy"]["distinct_characteristics_require_unique_colors"])
        self.assertTrue(profile["completion_rules"]["barrier_colors_unique_by_resolved_rgb"])
        group = profile["color_equivalence_groups"][0]
        self.assertEqual(group["canonical_key"], "partition_1")
        self.assertEqual(group["color_index"], 6)
        self.assertTrue({"ordinary_lift", "interward"}.issubset(group["members"]))
        self.assertFalse({"ordinary_lift", "interward"}.intersection(r["key"] for r in profile["legend"]))

    def test_separate_EI45_subtype_rows_are_rejected_even_with_unique_colors(self):
        for key in ("ordinary_lift", "interward"):
            errors = legend_color_errors([dict(key=key, name="EI 45", color_index=90)])
            self.assertTrue(any("единой строкой" in e for e in errors))

    def test_plan_validation_blocks_aliased_color(self):
        plan = fixture_plan("SOURCE")
        plan["legend"].append(dict(key="different", name="Другая характеристика", color_index=170,
                                   source_handles=["LEGEND_SOURCE"]))
        project = {"dates": {"gpzu": "2025-11-12"}, "barrier_assessment": {"rooms": [
            dict(floor="1", number="101", name="Склад", category="В2")]}}
        self.assertTrue(any("одинаковые цвета" in e for e in validate_plan(plan, project)))


class BarrierColorSnapshotTests(unittest.TestCase):
    setUp = snapshot_tests.RunRuleSnapshotTests.setUp
    assert_consistent_artifacts = snapshot_tests.RunRuleSnapshotTests.assert_consistent_artifacts

    def test_new_task_contains_color_rule_and_constraints(self):
        result = self.app.runs.create("first", "barrier_assessment")
        self.assert_consistent_artifacts(result)
        for flag in ("barrier_distinct_characteristics_require_unique_colors",
                     "barrier_color_uniqueness_checked_by_resolved_rgb",
                     "barrier_plan_and_all_legend_colors_must_match"):
            self.assertTrue(result["run"]["hard_constraints"][flag])
        rules = (Path(result["folder"]) / "ПРАВИЛА_ЗАПУСКА.md").read_text(encoding="utf-8")
        self.assertIn("barrier_unified_EI45_color_group_20261021", rules)
        self.assertTrue(result["run"]["hard_constraints"]["firelift_hall_door_callout_when_no_MGN_zone"])
