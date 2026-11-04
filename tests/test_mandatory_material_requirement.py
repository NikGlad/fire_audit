import copy
import unittest

from fire_audit.validation import _issued_graphics_required, validate_finding


class MandatoryMaterialRequirementTests(unittest.TestCase):
    def requirement(self):
        return {
            "id": "material", "mode": "compliance", "source_text": "Требование к материалу",
            "norm": {"document": "Применимый СП", "point": "пункт"},
            "applicability": {"status": "applicable"},
            "decision": {"status": "insufficient_data", "reason": "Материал неизвестен"},
            "output": {
                "issued": True, "is_general_requirement": True,
                "mandatory_requirement": {
                    "rule_id": "doc_25_079", "scope": "material_or_fire_resistance",
                    "applicability_verified": True,
                    "unknown_characteristic_not_asserted_as_defect": True,
                    "reason": "Применимое обязательное требование по прямому правилу пользователя",
                },
            },
            "autocad": {"by_layer": True, "layer": "экспертиза", "common_ray_origin": True,
                        "ray_required": True, "ray_entity_ids": ["R"]},
        }

    def test_policy_exception_requires_graphics_without_changing_verdict(self):
        finding = self.requirement()
        self.assertTrue(_issued_graphics_required(finding))
        errors, _ = validate_finding(finding)
        self.assertFalse(any("луч строить запрещено" in e or "графика запрещена" in e for e in errors))
        self.assertTrue(any("цель обязательного луча не подтверждена" in e for e in errors))
        self.assertEqual(finding["decision"]["status"], "insufficient_data")

    def test_unknown_population_or_geometry_does_not_use_material_exception(self):
        for scope in ["population", "route_distance", "geometry", ""]:
            finding = self.requirement()
            finding["output"]["mandatory_requirement"]["scope"] = scope
            self.assertFalse(_issued_graphics_required(finding))
            self.assertTrue(any("луч строить запрещено" in e for e in validate_finding(finding)[0]))

    def test_general_flag_alone_or_unverified_policy_cannot_issue(self):
        base = self.requirement()
        for change in [
            {"rule_id": "invented"}, {"applicability_verified": False},
            {"unknown_characteristic_not_asserted_as_defect": False}, {"reason": ""},
        ]:
            finding = copy.deepcopy(base)
            finding["output"]["mandatory_requirement"].update(change)
            self.assertFalse(_issued_graphics_required(finding))
        finding = copy.deepcopy(base)
        del finding["output"]["mandatory_requirement"]
        self.assertFalse(_issued_graphics_required(finding))

    def test_exception_does_not_bypass_source_reading(self):
        errors, _ = validate_finding(self.requirement(), for_application=True)
        self.assertTrue(any("полное чтение" in e for e in errors))

    def test_compliant_or_inapplicable_is_not_material_exception(self):
        for status in ["compliant", "not_applicable"]:
            finding = self.requirement()
            finding["decision"]["status"] = status
            self.assertFalse(_issued_graphics_required(finding))


if __name__ == "__main__":
    unittest.main()
