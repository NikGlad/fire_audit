import unittest
from copy import deepcopy

from fire_audit.validation import validate_registry


class ActualizationValidationTests(unittest.TestCase):
    def fixture(self):
        graphic = lambda h: {"handle": h, "layer": "экспертиза устранено",
                             "colorIndex": 256, "linetype": "ByLayer", "lineWeight": "ByLayer"}
        target = {"ray_id": "R", "status": "resolved", "graphic_ids": ["D"], "reason": "Обмер",
                  "evidence": {"project_entity_ids": ["W"], "method": "между гранями", "result": "1300 >= 1200 мм"}}
        return {"mode": "actualization_protocol", "actualization_scope": {"issued_main_text_ids": ["T"]},
                "final_numbering": {"status": "preserved"},
                "findings": [{"original_number": 19, "status": "resolved", "reason": "Фактически расширено",
                              "source": {"main_text_id": "T", "number_id": "N", "frame_id": "F", "full_text": "Требование", "ray_ids": ["R"]},
                              "targets": [target], "changed_entity_ids": ["T", "N", "F", "R", "D"]}],
                "changed_entity_ids": ["T", "N", "F", "R", "D"], "created_entity_ids": [],
                "verified_graphics": [graphic(h) for h in ["T", "N", "F", "R", "D"]],
                "graphics_layer_check": {"resolved_layer_color": 3, "project_geometry_unchanged": True,
                                         "original_texts_and_numbers_unchanged": True, "excluded_frame_unchanged": True,
                                         "no_entities_deleted": True, "changes_re_read": True},
                "dwg_actions": {"work_completed": True, "source_path": "C:/a.dwg", "result_copy_path": "C:/b.dwg",
                                "result_copy_created": True, "source_document_kept_unchanged": True,
                                "source_document_contains_results": False, "final_fresh_read_verified": True,
                                "copy_did_not_change_open_source_identity": True}}

    def test_complete_green_group_keeps_original_number(self):
        self.assertTrue(validate_registry(self.fixture(), True)["ok"])

    def test_distinct_existing_groups_keep_duplicate_source_number(self):
        registry = self.fixture()
        duplicate = deepcopy(registry["findings"][0])
        duplicate["source"] = {**duplicate["source"], "main_text_id": "T2", "number_id": "N2",
                               "frame_id": "F2", "ray_ids": ["R2"]}
        duplicate["targets"][0].update(ray_id="R2", graphic_ids=["D2"])
        duplicate["changed_entity_ids"] = []
        registry["findings"].append(duplicate)
        registry["actualization_scope"]["issued_main_text_ids"].append("T2")
        registry["verified_graphics"].extend(
            {**registry["verified_graphics"][0], "handle": handle}
            for handle in ("T2", "N2", "F2", "R2", "D2"))
        result = validate_registry(registry, True)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(any("повторяющиеся исходные номера" in item for item in result["warnings"]))

    def test_stu_group_keeps_blue_layer_when_resolved(self):
        for layer in ("экспертиза СТУ", "экспертиза СТУ протокол"):
            with self.subTest(layer=layer):
                registry = self.fixture()
                finding = registry["findings"][0]
                finding["source"]["original_layer"] = layer
                finding["changed_entity_ids"] = []
                registry["changed_entity_ids"] = []
                registry["graphics_before"] = []
                for entity in registry["verified_graphics"]:
                    if entity["handle"] != "D":
                        entity["layer"] = layer
                        entity["lineWeight"] = "LineWeight000"
                    registry["graphics_before"].append(dict(entity))
                registry["verified_graphics"][-1]["lineWeight"] = "LineWeight050"
                registry["graphics_before"][-1]["lineWeight"] = "LineWeight050"
                result = validate_registry(registry, True)
                self.assertTrue(result["ok"], result["errors"])
                registry["verified_graphics"][0]["layer"] = "экспертиза устранено"
                self.assertFalse(validate_registry(registry, True)["ok"])

    def test_miscolored_stu_ray_can_be_restored_without_resolving_target(self):
        registry = self.fixture()
        finding = registry["findings"][0]
        finding["source"]["original_layer"] = "экспертиза СТУ"
        finding["status"] = "insufficient_data"
        finding["targets"][0]["status"] = "insufficient_data"
        finding["changed_entity_ids"] = ["R"]
        registry["changed_entity_ids"] = ["R"]
        registry["graphics_before"] = [dict(entity) for entity in registry["verified_graphics"]]
        for entity in registry["verified_graphics"]:
            entity["layer"] = "экспертиза СТУ"
        registry["verified_graphics"][-1]["layer"] = "экспертиза устранено"
        result = validate_registry(registry, True)
        self.assertTrue(result["ok"], result["errors"])

    def test_historical_green_group_with_unconfirmed_current_target_is_preserved(self):
        registry = self.fixture()
        finding = registry["findings"][0]
        finding["source"]["original_layer"] = "экспертиза устранено"
        finding["status"] = "insufficient_data"
        finding["targets"][0]["status"] = "insufficient_data"
        finding["changed_entity_ids"] = []
        registry["changed_entity_ids"] = []
        result = validate_registry(registry, True)
        self.assertTrue(result["ok"], result["errors"])

    def returned_fixture(self, source_layer="экспертиза устранено"):
        registry = self.fixture()
        finding = registry["findings"][0]
        finding["status"] = "unresolved"
        finding["targets"][0]["status"] = "unresolved"
        finding["targets"][0]["reason"] = "Фактически 900 мм при требуемых 1200 мм"
        finding["targets"][0]["returned_entity_ids"] = ["R", "D"]
        finding["returned_entity_ids"] = ["T", "N", "F", "R", "D"]
        finding["return_evidence"] = {"project_entity_ids": ["W"], "method": "обмер",
                                      "result": "900 < 1200 мм", "normative_basis": "Применимый пункт нормы и исходное требование"}
        registry["graphics_before"] = [dict(item, layer=source_layer) for item in registry["verified_graphics"]]
        for item in registry["verified_graphics"]:
            item["layer"] = "экспертиза"
        return registry

    def test_return_from_both_resolved_layers(self):
        for layer in ("экспертиза устранено", "экспертиза устранено протокол"):
            with self.subTest(layer=layer):
                result = validate_registry(self.returned_fixture(layer), True)
                self.assertTrue(result["ok"], result["errors"])

    def test_return_requires_before_state_and_normative_evidence(self):
        for missing in ("before", "norm", "target"):
            registry = self.returned_fixture()
            if missing == "before":
                registry["graphics_before"] = []
            elif missing == "norm":
                registry["findings"][0]["return_evidence"]["normative_basis"] = ""
            else:
                registry["findings"][0]["targets"][0]["returned_entity_ids"] = []
            self.assertFalse(validate_registry(registry, True)["ok"])

    def test_insufficient_data_does_not_prove_return(self):
        registry = self.returned_fixture()
        registry["findings"][0]["status"] = "insufficient_data"
        registry["findings"][0]["targets"][0]["status"] = "insufficient_data"
        self.assertFalse(validate_registry(registry, True)["ok"])

    def test_partial_return_preserves_resolved_target(self):
        registry = self.returned_fixture("экспертиза устранено протокол")
        finding = registry["findings"][0]
        finding["status"] = "partially_resolved"
        finding["source"]["ray_ids"].append("R2")
        finding["targets"].append({"ray_id": "R2", "status": "resolved", "reason": "Другая цель устранена",
                                   "evidence": {"project_entity_ids": ["W2"], "method": "обмер", "result": "1300 >= 1200 мм"}})
        registry["verified_graphics"].append({"handle": "R2", "layer": "экспертиза устранено",
                                              "colorIndex": 256, "linetype": "ByLayer", "lineWeight": "ByLayer"})
        result = validate_registry(registry, True)
        self.assertTrue(result["ok"], result["errors"])
        registry["verified_graphics"][-1]["layer"] = "экспертиза"
        self.assertFalse(validate_registry(registry, True)["ok"])

    def test_unresolved_target_cannot_remain_in_protocol_layer(self):
        registry = self.returned_fixture()
        registry["verified_graphics"][-1]["layer"] = "экспертиза устранено протокол"
        self.assertFalse(validate_registry(registry, True)["ok"])

    def test_shared_cloud_returns_without_returning_resolved_ray(self):
        registry = self.returned_fixture()
        finding = registry["findings"][0]
        finding["status"] = "partially_resolved"
        finding["source"]["ray_ids"].append("R2")
        finding["source"]["shared_graphic_ids"] = ["D"]
        finding["targets"].append({"ray_id": "R2", "graphic_ids": ["D"], "status": "resolved",
                                   "reason": "Вторая цель устранена, облако общее",
                                   "evidence": {"project_entity_ids": ["W2"], "method": "обмер", "result": "1300 >= 1200 мм"}})
        registry["verified_graphics"].append({"handle": "R2", "layer": "экспертиза устранено",
                                              "colorIndex": 256, "linetype": "ByLayer", "lineWeight": "ByLayer"})
        result = validate_registry(registry, True)
        self.assertTrue(result["ok"], result["errors"])

    def test_missing_evidence_blocks_green(self):
        registry = self.fixture()
        registry["findings"][0]["targets"][0]["evidence"] = {}
        self.assertFalse(validate_registry(registry, True)["ok"])

    def test_remaining_target_blocks_complete_group(self):
        registry = self.fixture()
        registry["findings"][0]["targets"].append({"ray_id": "R2", "status": "insufficient_data", "reason": "Нет данных"})
        registry["findings"][0]["source"]["ray_ids"].append("R2")
        self.assertFalse(validate_registry(registry, True)["ok"])

    def test_partial_group_leaves_header_unchanged(self):
        registry = self.fixture()
        finding = registry["findings"][0]
        finding["status"] = "partially_resolved"
        finding["targets"].append({"ray_id": "R2", "status": "unresolved", "reason": "900 < 1200 мм"})
        finding["source"]["ray_ids"].append("R2")
        finding["changed_entity_ids"] = ["R", "D"]
        registry["changed_entity_ids"] = ["R", "D"]
        for entity in registry["verified_graphics"]:
            if entity["handle"] in {"T", "N", "F"}:
                entity["layer"] = "экспертиза"
        self.assertTrue(validate_registry(registry, True)["ok"])

    def test_wrong_layer_or_color_blocks_application(self):
        for key, value in [("layer", "экспертиза"), ("colorIndex", 3), ("lineWeight", "LineWeight000")]:
            registry = self.fixture()
            registry["verified_graphics"][0][key] = value
            self.assertFalse(validate_registry(registry, True)["ok"])

    def test_excluded_group_cannot_be_changed(self):
        registry = self.fixture()
        registry["findings"][0]["status"] = "excluded_fire_safety_frame"
        self.assertFalse(validate_registry(registry, True)["ok"])

    def test_missing_ray_or_group_blocks_completion(self):
        for change in ("ray", "group"):
            registry = self.fixture()
            if change == "ray":
                registry["findings"][0]["source"]["ray_ids"].append("R2")
            else:
                registry["actualization_scope"]["issued_main_text_ids"].append("T2")
            self.assertFalse(validate_registry(registry, True)["ok"])

    def test_saveas_requires_independent_source_preservation(self):
        registry = self.fixture()
        registry["dwg_actions"]["copy_did_not_change_open_source_identity"] = False
        self.assertFalse(validate_registry(registry, True)["ok"])
        registry["dwg_actions"]["source_preservation"] = {
            "sha256_before_edits": "a" * 64, "sha256_after_edits": "a" * 64, "source_last_write_unchanged": True}
        self.assertTrue(validate_registry(registry, True)["ok"])
        registry["dwg_actions"]["source_preservation"]["sha256_after_edits"] = "b" * 64
        self.assertFalse(validate_registry(registry, True)["ok"])

    def test_cannot_write_source_or_use_same_path(self):
        for key, value in [("source_overwritten", True), ("delete_requested", True), ("result_copy_path", "c:\\A.dwg")]:
            registry = self.fixture()
            registry["dwg_actions"][key] = value
            self.assertFalse(validate_registry(registry, True)["ok"])

    def addition_fixture(self):
        registry = self.fixture()
        finding = registry["findings"][0]
        finding.update(status="partially_resolved", floor=1,
                       reopened_for_added_targets_ids=["T", "N", "F"],
                       created_entity_ids=["AR", "AD", "AC"],
                       addition_comment={"entity_ids": ["AC"], "text": "Добавились новые места: дверь Х",
                                         "mentions_new_places": True, "history_preserved": True})
        finding["targets"].append({
            "origin": "added_target", "floor": 1, "ray_id": "AR", "graphic_ids": ["AD"],
            "status": "unresolved", "reason": "Дополнительно выявлен недостаточный просвет",
            "evidence": {"project_entity_ids": ["AW"], "method": "между гранями", "result": "900 < 1200 мм",
                         "normative_basis": "Применимый пункт нормы"},
            "deduplication": {"floor": 1, "project_target": "AW", "parameter": "clear_width",
                              "requirement": "exit_width", "same_target_already_covered": False,
                              "checked_main_text_ids": ["T"]}})
        registry["graphics_before"] = [dict(e) for e in registry["verified_graphics"]]
        for entity in registry["verified_graphics"]:
            if entity["handle"] in {"T", "N", "F"}:
                entity["layer"] = "экспертиза"
        registry["created_entity_ids"] = ["AR", "AD", "AC"]
        for handle in registry["created_entity_ids"]:
            registry["verified_graphics"].append({"handle": handle,
                "layer": "экспертиза пояснения" if handle == "AC" else "экспертиза",
                "colorIndex": 256, "linetype": "ByLayer", "lineWeight": "ByLayer"})
        return registry

    def test_new_target_reopens_header_but_keeps_old_target_green(self):
        result = validate_registry(self.addition_fixture(), True)
        self.assertTrue(result["ok"], result["errors"])

    def test_addition_requires_proof_comment_and_deduplication(self):
        for missing in ("norm", "comment", "duplicate", "floor", "scope", "green"):
            registry = self.addition_fixture()
            finding = registry["findings"][0]
            target = finding["targets"][-1]
            if missing == "norm":
                target["evidence"]["normative_basis"] = ""
            elif missing == "comment":
                finding["addition_comment"] = {}
            elif missing == "duplicate":
                target["deduplication"]["same_target_already_covered"] = True
            elif missing == "floor":
                target["floor"] = 2
            elif missing == "scope":
                target["deduplication"]["checked_main_text_ids"] = []
            else:
                registry["verified_graphics"][-2]["layer"] = "экспертиза устранено"
            with self.subTest(missing=missing):
                self.assertFalse(validate_registry(registry, True)["ok"])

    def test_unified_review_requires_complete_individual_coverage(self):
        registry = self.fixture()
        registry["actualization_scope"].update(unified_review_required=True, required_review_keys=["block2:floor1"])
        self.assertFalse(validate_registry(registry, True)["ok"])
        registry["new_plan_review"] = {"items": [{"key": "block2:floor1", "status": "already_covered",
                                                 "reason": "Конкретное место охвачено замечанием №19",
                                                 "second_review_confirmed": True}]}
        result = validate_registry(registry, True)
        self.assertTrue(result["ok"], result["errors"])

    def test_new_group_needs_full_template_and_next_number(self):
        from copy import deepcopy
        registry = self.addition_fixture()
        old = registry["findings"][0]
        new = deepcopy(old)
        new.update(origin="new_group", original_number=20, status="unresolved", changed_entity_ids=[],
                   reopened_for_added_targets_ids=[], created_entity_ids=["NT", "NN", "NF", "NR", "ND", "NC"])
        new["source"] = {"main_text_id": "NT", "number_id": "NN", "frame_id": "NF", "full_text": "Шаблон", "ray_ids": []}
        new["targets"] = [deepcopy(old["targets"][-1])]
        new["targets"][0].update(ray_id="NR", graphic_ids=["ND"])
        new["targets"][0]["deduplication"]["project_target"] = "NW"
        new["addition_comment"]["entity_ids"] = ["NC"]
        new["template_copy"] = {"source_kind": "source_approved_block", "source_entity_ids": ["ST", "SN", "SF"],
                                "source_group_fully_detected": True, "left_explanation_checked": True,
                                "left_explanation_copied_when_present": True, "text_styles_preserved": True,
                                "relative_positions_preserved": True, "repeated_floor_group_copied_from_actualized_group": True}
        registry["findings"].append(new)
        registry["created_entity_ids"].extend(new["created_entity_ids"])
        registry["verified_graphics"].extend({"handle": h, "layer": "экспертиза пояснения" if h == "NC" else "экспертиза",
            "colorIndex": 256, "linetype": "ByLayer", "lineWeight": "ByLayer"} for h in new["created_entity_ids"])
        result = validate_registry(registry, True)
        self.assertTrue(result["ok"], result["errors"])
        new["template_copy"]["source_group_fully_detected"] = False
        self.assertFalse(validate_registry(registry, True)["ok"])
        new["template_copy"]["source_group_fully_detected"] = True
        new["original_number"] = 22
        self.assertFalse(validate_registry(registry, True)["ok"])


if __name__ == "__main__":
    unittest.main()
