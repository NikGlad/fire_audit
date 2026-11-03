import copy
import unittest

from fire_audit.validation import validate_finding


class StageIssuedQueryTests(unittest.TestCase):
    def query(self):
        return {
            "id": "query", "mode": "stage", "source_text": "Уточнить исходные данные",
            "norm": {"document": "Правила этапизации", "point": "06"},
            "decision": {"status": "insufficient_data", "reason": "Нужны исходные данные"},
            "output": {"stage_is_issued": True, "stage_element_kind": "query"},
            "autocad": {
                "by_layer": True, "layer": "экспертиза", "floor": "1",
                "placement": {"relative_to_stage_frame": "right"},
                "ray_required": True, "ray_entity_ids": ["R1"],
                "ray_requirement_reason": "Запрос справа от рамки",
                "ray_rule": "stage_whole_building_first_floor_lower_left_corner",
                "target_semantics": "Нижний левый угол первого этажа",
                "target_scope": "whole_building", "target_floor": "1",
                "target_entity_ids": ["P1"], "target_confirmed": True,
                "common_ray_origin": True,
                "ray_origin": {
                    "rule": "outermost_group_point_toward_plan",
                    "coordinates": [10, 5], "full_group_bounds": [0, 0, 10, 10],
                    "plan_position_relative_to_group": "right", "side_toward_plan": "right",
                    "bounds_checked": True, "bounds_check_result": "verified", "path_clear_of_group": True,
                },
            },
        }

    def test_issued_stage_query_allows_required_ray(self):
        errors, _ = validate_finding(self.query())
        self.assertEqual(errors, [])

    def test_issued_query_still_requires_ray_and_confirmed_target(self):
        finding = self.query()
        finding["autocad"].update(ray_required=False, ray_entity_ids=[], target_confirmed=False)
        errors, _ = validate_finding(finding)
        self.assertTrue(any("обязательность луча" in error for error in errors))
        self.assertTrue(any("цель обязательного луча не подтверждена" in error for error in errors))

    def test_non_issued_and_other_modes_keep_ray_prohibition(self):
        for mode, issued in [("stage", False), ("compliance", True)]:
            finding = copy.deepcopy(self.query())
            finding["mode"] = mode
            finding["output"]["stage_is_issued"] = issued
            errors, _ = validate_finding(finding)
            self.assertTrue(any("луч строить запрещено" in error for error in errors), errors)

    def test_compliant_stage_explanation_is_an_explicit_deliverable(self):
        finding = self.query()
        finding["decision"]["status"] = "compliant"
        finding["output"]["stage_element_kind"] = "explanation"
        finding["autocad"]["layer"] = "экспертиза пояснения"
        self.assertEqual(validate_finding(finding)[0], [])
