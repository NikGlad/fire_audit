import copy
import unittest

from fire_audit.validation import validate_final_numbering


class SpatialNumberingTests(unittest.TestCase):
    def items(self):
        # The stage frame is physically below the first-floor remark.
        return [
            {"id": "frame", "final_number": 1, "category": "stage_frame",
             "placement_sequence": 1, "number_entity_ids": ["N1"], "remark_entity_ids": ["R1"], "old_number": 150},
            {"id": "ground", "final_number": 2, "category": "lowest_floor", "floor_order": 0,
             "placement_sequence": 2, "number_entity_ids": ["N2"], "remark_entity_ids": ["R2"], "old_number": 150},
            {"id": "roof", "final_number": 3, "category": "upper_floor", "floor_order": 20,
             "placement_sequence": 3, "number_entity_ids": ["N3"], "remark_entity_ids": ["R3"], "old_number": None},
        ]

    def test_spatial_order_overrides_categories_and_old_numbers(self):
        self.assertTrue(validate_final_numbering(list(reversed(self.items())))["ok"])

    def test_category_order_cannot_override_spatial_order(self):
        items = self.items()
        items[0]["final_number"], items[1]["final_number"] = 2, 1
        result = validate_final_numbering(items)
        self.assertFalse(result["ok"])
        self.assertTrue(any("Нарушен порядок" in error for error in result["errors"]))

    def test_sequence_cannot_restart_on_each_floor_or_be_missing(self):
        for value in (1, None, "3", 0):
            with self.subTest(value=value):
                items = copy.deepcopy(self.items())
                items[-1]["placement_sequence"] = value
                self.assertFalse(validate_final_numbering(items)["ok"])

    def test_final_numbers_cannot_repeat_or_skip(self):
        for value in (2, 4):
            items = self.items()
            items[-1]["final_number"] = value
            self.assertFalse(validate_final_numbering(items)["ok"])

    def test_historical_category_order_keeps_its_original_validation(self):
        items = self.items()
        items[0]["final_number"], items[1]["final_number"] = 2, 1
        for item in items:
            item["placement_sequence"] = 1
        self.assertTrue(validate_final_numbering(items, order_policy="legacy_categories")["ok"])
        self.assertFalse(validate_final_numbering(items)["ok"])

    def test_unknown_policy_is_not_silently_accepted(self):
        self.assertFalse(validate_final_numbering(self.items(), order_policy="unknown")["ok"])
