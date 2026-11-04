import unittest

from fire_audit.validation import _validate_compliance_source_copy, _validate_created_graphics


class SourceCopyCaseTests(unittest.TestCase):
    def finding(self, case):
        return {"id": "copy", "mode": "compliance", "decision": {"status": "violation"},
                "autocad": {"source_group": {"copy_case": case}}}

    def test_first_copy_does_not_claim_previous_floor(self):
        errors = _validate_compliance_source_copy(self.finding("first_occurrence"))
        self.assertFalse(any("repeated_floor_group_copied" in e for e in errors))
        self.assertTrue(any("заголовка утверждённого блока" in e for e in errors))
        self.assertTrue(any("полной группы" in e for e in errors))

    def test_repeated_floor_still_requires_verified_previous_copy(self):
        finding = self.finding("repeated_floor")
        self.assertTrue(any("repeated_floor_group_copied" in e for e in _validate_compliance_source_copy(finding)))
        finding["autocad"]["source_group"]["repeated_floor_group_copied_from_actualized_group"] = True
        errors = _validate_compliance_source_copy(finding)
        self.assertFalse(any("repeated_floor_group_copied" in e for e in errors))
        self.assertTrue(any("ранее актуализированная" in e for e in errors))

    def test_existing_explanation_is_recognized_and_provenance_still_checked(self):
        finding = self.finding("first_occurrence")
        finding["autocad"].update(status="post_checked", reused_objects=[{
            "entity_id": "existing", "role": "purple_explanation", "action": "reused"
        }])
        finding["autocad"]["source_group"]["left_explanation_present"] = True
        errors = _validate_compliance_source_copy(finding)
        self.assertFalse(any("найденное пояснение слева отсутствует" in e for e in errors))
        self.assertTrue(any("происхождение" in e for e in _validate_created_graphics(finding)))
