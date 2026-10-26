from copy import deepcopy
import unittest

from fire_audit.validation import validate_registry, _validate_created_graphics, _validate_compliance_source_copy
from fire_audit.protocol_validation import protocol_validation_state
from fire_audit.instruction_contracts import approved_rule_text_errors, cycle_marker_state
from tests import test_actualization_validation as actualization_fixtures
from tests import test_protocol_validation as protocol_fixtures


class SelectedARContractsTests(unittest.TestCase):
    def inapplicable(self):
        r = actualization_fixtures.ActualizationValidationTests().fixture()
        r["instruction_policy_version"] = 2
        r["cycle_id"] = "cycle-a"
        r["changed_entity_ids"] = []
        for row in r["verified_graphics"]:
            row["layer"] = "экспертиза"
        r["graphics_before"] = deepcopy(r["verified_graphics"])
        f = r["findings"][0]
        f.update(status="not_applicable_after_change", status_breakdown={"not_applicable_after_change": 1}, changed_entity_ids=[])
        f["targets"][0].update(status="not_applicable_after_change", applicability_change={
            "before": "Прежнее условие применения", "after": "Условие отсутствует по изменённому плану",
            "project_entity_ids": ["W"], "change_reference": "Изменение текущего плана",
            "normative_basis": "Условие применения проверенного требования"})
        return r

    def test_inapplicable_preserves_graphics_without_claiming_resolution(self):
        r = self.inapplicable()
        self.assertTrue(validate_registry(r, True)["ok"])
        for field in ("before", "after", "project_entity_ids", "change_reference", "normative_basis"):
            broken = deepcopy(r)
            broken["findings"][0]["targets"][0]["applicability_change"].pop(field)
            self.assertFalse(validate_registry(broken, True)["ok"], field)
        for handle in ("T", "R", "D"):
            broken = deepcopy(r)
            next(row for row in broken["verified_graphics"] if row["handle"] == handle)["layer"] = "экспертиза устранено"
            self.assertFalse(validate_registry(broken, True)["ok"], handle)

    def test_mixed_targets_keep_header_and_do_not_mask_remaining_statuses(self):
        r = self.inapplicable()
        f = r["findings"][0]
        second = deepcopy(f["targets"][0])
        second.update(ray_id="R2", graphic_ids=["D2"], status="resolved")
        f["targets"].append(second)
        f["source"]["ray_ids"].append("R2")
        f.update(status="mixed_after_change", status_breakdown={"not_applicable_after_change": 1, "resolved": 1}, changed_entity_ids=["R2", "D2"])
        r["changed_entity_ids"] = ["R2", "D2"]
        for handle in ("R2", "D2"):
            row = {"handle": handle, "layer": "экспертиза", "colorIndex": 256, "linetype": "ByLayer", "lineWeight": "ByLayer"}
            r["graphics_before"].append(row)
            r["verified_graphics"].append({**row, "layer": "экспертиза устранено"})
        result = validate_registry(r, True)
        self.assertTrue(result["ok"], result["errors"])
        for status in ("resolved", "partially_resolved", "not_applicable_after_change"):
            broken = deepcopy(r)
            broken["findings"][0]["status"] = status
            self.assertFalse(validate_registry(broken, True)["ok"])
        broken = deepcopy(r)
        broken["findings"][0]["targets"][1]["status"] = "not_reviewed"
        broken["findings"][0]["status_breakdown"] = {"not_applicable_after_change": 1, "not_reviewed": 1}
        self.assertFalse(validate_registry(broken, True)["ok"])

    def test_inapplicable_does_not_move_blue_stu_group(self):
        r = self.inapplicable()
        for container in ("graphics_before", "verified_graphics"):
            for row in r[container]:
                row["layer"] = "экспертиза СТУ"
        self.assertTrue(validate_registry(r, True)["ok"])

    def test_new_plans_without_answers_can_have_explicit_empty_protocol_scope(self):
        r = self.inapplicable()
        r["protocol"] = {"required": True, "scope_policy_version": 2, "scope_main_text_ids": [], "scope_entries": [], "items": [],
                         "scope_source": {"kind": "new_plans", "reference": "Поступление планов текущего цикла"},
                         "empty_scope_reason": "new_plans_without_responses", "new_plans_received": True, "responses_received": False}
        result = validate_registry(r, True)
        self.assertTrue(result["ok"], result["errors"])
        for field in ("empty_scope_reason", "responses_received", "scope_source"):
            broken = deepcopy(r)
            broken["protocol"].pop(field)
            self.assertFalse(validate_registry(broken, True)["ok"])
        broken = deepcopy(r)
        broken["mode"] = "protocol"
        self.assertTrue(protocol_validation_state(broken, strict=True, standalone=True)["errors"])

    def test_no_answer_comment_requires_current_cycle_reply_obligation(self):
        r = protocol_fixtures.ProtocolValidationTests().missing_fixture("no_answer")
        r["protocol"].update(scope_policy_version=2, scope_source={"kind": "agreed_scope", "reference": "Согласованный перечень"},
                             scope_entries=[{"main_text_id": "T", "inclusion_reason": "Согласованный ответ",
                                             "source_reference": "Поручение", "reply_required": False}])
        result = protocol_validation_state(r, strict=True, standalone=True)
        self.assertTrue(any("reply_required" in error for error in result["errors"]))

    def test_reply_not_required_does_not_create_comment_or_protocol_exemption(self):
        r = protocol_fixtures.ProtocolValidationTests().fixture("none")
        r["protocol"].update(scope_policy_version=2, scope_source={"kind": "agreed_scope", "reference": "Поручение"},
                             scope_entries=[{"main_text_id": "T", "inclusion_reason": "Сохранить учёт группы",
                                             "source_reference": "Поручение", "reply_required": False}], text_color_checks=[])
        item = r["protocol"]["items"][0]
        item["response"] = {"status": "not_required", "entity_ids": []}
        item["consultant"] = {"comment_required": False, "omission_reason": "reply_not_required", "entity_ids": []}
        item["transition"] = {"kind": "none", "entity_ids": []}
        result = protocol_validation_state(r, strict=True, standalone=True)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["owned_entity_ids"], set())
        self.assertEqual(result["layers_by_main_id"], {})

    def test_only_novelty_cloud_has_aci2_exception_and_old_cloud_is_not_new(self):
        r = {"cycle_id": "a", "created_entity_ids": ["C", "T"], "verified_graphics": [
            {"handle": "T", "layer": "экспертиза"},
            {"handle": "C", "layer": "экспертиза", "colorIndex": 2, "linetype": "ByLayer", "lineWeight": "ByLayer"}],
            "new_finding_cycle_markers": [{"entity_id": "C", "main_text_id": "T", "cycle_id": "a",
                                            "role": "new_finding_cycle_marker", "encloses_complete_group_verified": True}]}
        self.assertEqual(cycle_marker_state(r, strict=True)[1], [])
        for field, value in (("colorIndex", 256), ("layer", "экспертиза изменения")):
            broken = deepcopy(r)
            broken["verified_graphics"][1][field] = value
            self.assertTrue(cycle_marker_state(broken, strict=True)[1])
        r["new_finding_cycle_markers"][0]["cycle_id"] = "old"
        self.assertTrue(cycle_marker_state(r, strict=True)[1])

    def test_approved_text_requires_immutable_eligible_source_and_complete_format_sample(self):
        eligible = {"exception_key": "n2_fire_doors", "rule_id": "doc_11_026", "full_text": "Утверждённый полный текст",
                    "source_reference": "snapshot#doc_11_026", "source_sha256": "a" * 64}
        trace = {"approved_rule_text": {**eligible, "source_approval_reference": "Прямое поручение пользователя",
                                         "verified_against_source": True, "format_verified": True,
                                         "format_sample_entity_ids": ["S1", "S2", "S3"]}}
        self.assertEqual(approved_rule_text_errors(trace, [eligible]), [])
        self.assertTrue(approved_rule_text_errors(trace, []))
        for change in ({"full_text": "Придуманный текст"}, {"source_sha256": "b" * 64}, {"exception_key": "other"}):
            broken = deepcopy(trace)
            broken["approved_rule_text"].update(change)
            self.assertTrue(approved_rule_text_errors(broken, [eligible]))
        trace["source_group_fully_detected"] = True
        self.assertTrue(approved_rule_text_errors(trace, [eligible]))

    def test_missing_approved_421_text_is_not_invented(self):
        trace = {"approved_rule_text": {"exception_key": "sp1_4_2_21", "rule_id": "some_id", "full_text": "Самостоятельный текст"}}
        self.assertTrue(approved_rule_text_errors(trace, []))

    def test_new_actualization_group_uses_real_approved_text_and_cycle_cloud(self):
        r = actualization_fixtures.ActualizationValidationTests().fixture()
        r.update(instruction_policy_version=2, cycle_id="cycle-a")
        eligible = {"exception_key": "n2_fire_doors", "rule_id": "doc_11_026", "full_text": "Полный утверждённый текст Н2",
                    "source_reference": "knowledge/rule_blocks.json#doc_11_026", "source_sha256": "a" * 64}
        r["eligible_rule_text_sources"] = [eligible]
        added_ids = ["NT", "NN", "NF", "NR", "ND", "NC", "NE"]
        new = {"origin": "new_group", "original_number": 20, "floor": "1", "status": "unresolved", "reason": "Новое относящееся требование",
               "source": {"main_text_id": "NT", "number_id": "NN", "frame_id": "NF", "full_text": eligible["full_text"], "ray_ids": []},
               "template_copy": {"source_kind": "approved_rule_text", "approved_rule_text": {**eligible,
                    "source_approval_reference": "Прямое поручение", "verified_against_source": True, "format_verified": True,
                    "format_sample_entity_ids": ["ST", "SN", "SF"]}},
               "targets": [{"origin": "added_target", "floor": "1", "ray_id": "NR", "graphic_ids": ["ND"], "status": "unresolved", "reason": "Подтверждённое новое место",
                    "evidence": {"project_entity_ids": ["W2"], "method": "Проверка проекта", "result": "Требование применимо", "normative_basis": "Полный пункт"},
                    "deduplication": {"floor": "1", "project_target": "W2", "parameter": "Дверь Н2", "requirement": "Огнестойкость",
                                      "same_target_already_covered": False, "checked_main_text_ids": ["T"]}}],
               "addition_comment": {"entity_ids": ["NE"], "text": "Добавилось новое место", "mentions_new_places": True, "history_preserved": True},
               "created_entity_ids": added_ids, "changed_entity_ids": []}
        r["findings"].append(new)
        r["created_entity_ids"] = added_ids
        r["new_finding_cycle_markers"] = [{"entity_id": "NC", "main_text_id": "NT", "cycle_id": "cycle-a",
                                           "role": "new_finding_cycle_marker", "encloses_complete_group_verified": True}]
        for handle in added_ids:
            r["verified_graphics"].append({"handle": handle, "layer": "экспертиза пояснения" if handle == "NE" else "экспертиза",
                                           "colorIndex": 2 if handle == "NC" else 256, "linetype": "ByLayer", "lineWeight": "ByLayer"})
        result = validate_registry(r, True)
        self.assertTrue(result["ok"], result["errors"])
        for mutation in ("text", "eligible", "cloud"):
            broken = deepcopy(r)
            if mutation == "text":
                broken["findings"][-1]["source"]["full_text"] = "Иной текст"
            elif mutation == "eligible":
                broken["eligible_rule_text_sources"] = []
            else:
                broken["new_finding_cycle_markers"] = []
            self.assertFalse(validate_registry(broken, True)["ok"], mutation)

    def test_old_novelty_cloud_remains_yellow_when_group_is_resolved(self):
        r = actualization_fixtures.ActualizationValidationTests().fixture()
        r.update(cycle_id="new-cycle")
        cloud = {"handle": "CLOUD", "layer": "экспертиза устранено", "colorIndex": 2, "linetype": "ByLayer", "lineWeight": "ByLayer"}
        r["graphics_before"] = [{**cloud, "layer": "экспертиза"}]
        r["verified_graphics"].append(cloud)
        r["changed_entity_ids"].append("CLOUD")
        r["findings"][0]["changed_entity_ids"].append("CLOUD")
        r["findings"][0]["source"]["related_graphic_ids"] = ["CLOUD"]
        r["new_finding_cycle_markers"] = [{"entity_id": "CLOUD", "main_text_id": "T", "cycle_id": "previous-cycle",
                                           "role": "new_finding_cycle_marker", "encloses_complete_group_verified": True}]
        result = validate_registry(r, True)
        self.assertTrue(result["ok"], result["errors"])

    def test_existing_object_cannot_be_counted_as_created_or_registered_twice(self):
        row = {"entity_id": "D", "entity_type": "Dimension", "related_finding_id": "F", "floor": "1",
               "purpose": "Повторный обмер", "role": "dimension", "layer": "экспертиза обмеры", "original_layer": "экспертиза обмеры",
               "action": "reused", "existing_object_verified": True, "properties_re_read": True,
               "usage": "result_graphic", "source_kind": "existing_measurement",
               "color_by_layer": True, "linetype_by_layer": True, "lineweight_by_layer": True,
               "properties_before": {"layer": "экспертиза обмеры", "color_by_layer": True,
                                     "linetype_by_layer": True, "lineweight_by_layer": True}}
        finding = {"id": "F", "autocad": {"floor": "1", "reused_objects": [row]}}
        self.assertEqual(_validate_created_graphics(finding), [])
        finding["autocad"]["target_entity_ids"] = ["D"]
        self.assertTrue(_validate_created_graphics(finding))
        finding["autocad"]["target_entity_ids"] = []
        finding["autocad"]["created_objects"] = [deepcopy(row)]
        self.assertTrue(_validate_created_graphics(finding))

    def test_reused_target_inherits_verified_source_layer_and_keeps_same_id(self):
        properties = {"color_by_layer": True, "linetype_by_layer": True, "lineweight_by_layer": True}
        source = {"entity_id": "S", "entity_type": "MText", "related_finding_id": "F", "floor": "1",
                  "purpose": "Исходное пояснение", "role": "main_text", "text_style": "Standard",
                  "layer": "экспертиза пояснения для заказчика", "original_layer": "экспертиза пояснения для заказчика",
                  "action": "modified", "existing_object_verified": True, "properties_re_read": True,
                  **properties, "text": "Уточнённый текст",
                  "properties_before": {"layer": "экспертиза пояснения для заказчика", **properties, "text": "Исходный текст"},
                  "changes": [{"property": "text", "before": "Исходный текст", "after": "Уточнённый текст"}]}
        target = {"entity_id": "D", "entity_type": "Dimension", "related_finding_id": "F", "floor": "1",
                  "purpose": "Цель луча", "role": "dimension", "source_kind": "existing_measurement",
                  "source_entity_id": "S", "source_layer": source["layer"], "layer": source["layer"],
                  "original_layer": "экспертиза обмеры", "action": "modified", "existing_object_verified": True,
                  "properties_re_read": True, **properties,
                  "properties_before": {"layer": "экспертиза обмеры", **properties},
                  "changes": [{"property": "layer", "before": "экспертиза обмеры", "after": source["layer"]}]}
        finding = {"id": "F", "autocad": {"floor": "1", "reused_objects": [source, target], "target_entity_ids": ["D"]}}
        self.assertEqual(_validate_created_graphics(finding), [])
        self.assertNotIn("created_objects", finding["autocad"])
        for mutation in ("unverified_source", "wrong_source_layer", "target_not_moved", "main_moved"):
            broken = deepcopy(finding)
            main, dimension = broken["autocad"]["reused_objects"]
            if mutation == "unverified_source":
                dimension["source_entity_id"] = "MISSING"
            elif mutation == "wrong_source_layer":
                dimension["source_layer"] = "экспертиза"
            elif mutation == "target_not_moved":
                dimension.update(action="unchanged", layer="экспертиза обмеры", changes=[])
            else:
                main["layer"] = "экспертиза"
                main["changes"].append({"property": "layer", "before": main["original_layer"], "after": "экспертиза"})
            self.assertTrue(_validate_created_graphics(broken), mutation)

    def test_modified_existing_graphic_requires_exact_changes_and_normal_layer_checks(self):
        row = {"entity_id": "D", "entity_type": "Dimension", "related_finding_id": "F", "floor": "1",
               "purpose": "Проверенная цель луча", "role": "dimension", "layer": "экспертиза",
               "original_layer": "экспертиза обмеры", "action": "modified", "existing_object_verified": True,
               "properties_re_read": True, "color_by_layer": True, "linetype_by_layer": True, "lineweight_by_layer": True,
               "properties_before": {"layer": "экспертиза обмеры", "color_by_layer": True,
                                     "linetype_by_layer": True, "lineweight_by_layer": True},
               "changes": [{"property": "layer", "before": "экспертиза обмеры", "after": "экспертиза"}]}
        finding = {"id": "F", "autocad": {"floor": "1", "reused_objects": [row]}}
        self.assertEqual(_validate_created_graphics(finding), [])
        for mutation in ("arbitrary_layer", "truthy_changes", "wrong_before", "missing_change", "non_bylayer", "project"):
            broken = deepcopy(finding)
            item = broken["autocad"]["reused_objects"][0]
            if mutation == "arbitrary_layer":
                item["layer"] = item["changes"][0]["after"] = "0"
            elif mutation == "truthy_changes":
                item["changes"] = True
            elif mutation == "wrong_before":
                item["changes"][0]["before"] = "0"
            elif mutation == "missing_change":
                item["color_by_layer"] = False
            elif mutation == "non_bylayer":
                item["color_by_layer"] = False
                item["changes"].append({"property": "color_by_layer", "before": True, "after": False})
            else:
                item["source_kind"] = "project_geometry"
            self.assertTrue(_validate_created_graphics(broken), mutation)

    def test_unchanged_analysis_graphic_cannot_exempt_changed_properties_or_issued_role(self):
        before = {"layer": "Проект", "color_by_layer": False, "linetype_by_layer": False, "lineweight_by_layer": True}
        row = {"entity_id": "D", "entity_type": "Line", "related_finding_id": "F", "floor": "1",
               "purpose": "Исходная геометрия", "role": "measurement_line", **before, "original_layer": "Проект",
               "action": "unchanged", "existing_object_verified": True, "properties_re_read": True,
               "usage": "analysis_only", "source_kind": "project_geometry", "properties_before": before}
        finding = {"id": "F", "autocad": {"floor": "1", "reused_objects": [row]}}
        self.assertEqual(_validate_created_graphics(finding), [])
        for mutation in ("layer", "color", "issued_role", "usage"):
            broken = deepcopy(finding)
            item = broken["autocad"]["reused_objects"][0]
            if mutation == "layer":
                item["layer"] = "0"
            elif mutation == "color":
                item["color_by_layer"] = True
            elif mutation == "issued_role":
                item["role"] = "main_text"
                item["text_style"] = "Standard"
            else:
                item.pop("usage")
            self.assertTrue(_validate_created_graphics(broken), mutation)


if __name__ == "__main__":
    unittest.main()
