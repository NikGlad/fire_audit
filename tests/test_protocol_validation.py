import unittest
from copy import deepcopy

from fire_audit.protocol_validation import (
    ACTIVE, CLIENT_EXPLANATIONS, EXPLANATIONS, PROTOCOL_RESOLVED, RESOLVED, STU, STU_PROTOCOL,
    validate_protocol_analysis_registry,
)
from fire_audit.validation import validate_registry
from tests import test_actualization_validation as actualization_tests


class ProtocolValidationTests(unittest.TestCase):
    def fixture(self, kind="accepted_commitment", mode="protocol"):
        entity = lambda handle, layer: {"handle": handle, "layer": layer, "colorIndex": 256,
                                        "linetype": "ByLayer", "lineWeight": "ByLayer"}
        source_ids = ["T", "N", "F", "R", "D", "E"]
        all_ids = source_ids + ["C", "P"]
        item = {
            "source": {"main_text_id": "T", "number_id": "N", "frame_id": "F", "original_number": 19, "full_text": "Исходное требование",
                       "issued_group": True, "inventory_complete": True, "entity_ids": source_ids},
            "response": {"status": "received", "text": "Обязуемся расширить дверь", "reference": "Ответ заказчика",
                         "entity_ids": ["C"], "inside_original_block": True, "claims_fixed": False},
            "consultant": {"date": "2026-10-23", "text": "Решение принято как обязательство, не как выполненная работа",
                           "decision": "accepted", "remaining_actions": "Выполнить расширение",
                           "basis": "Исходное замечание и проверенный план", "entity_ids": ["P"],
                           "inside_original_block": True},
            "plan": {"reference": "План текущей редакции", "checkability": "checkable", "physical_status": "unresolved",
                     "evidence": {"project_entity_ids": ["W"], "method": "обмер", "result": "900 < 1200 мм"}},
            "transition": {"kind": kind, "entity_ids": all_ids,
                           "evidence": {"response_kind": "commitment", "accepted_solution": "Расширение до нормы",
                                        "covers_all_requirements": True, "not_physical_resolution": True}},
        }
        registry = {
            "mode": mode, "protocol": {"required": True, "scope_main_text_ids": ["T"], "items": [item],
                                       "text_color_checks": [
                                           {"entity_id": "C", "role": "client_response", "color_index": 4,
                                            "inline_formatting_checked": True},
                                           {"entity_id": "P", "role": "consultant_explanation", "color_index": 30,
                                            "inline_formatting_checked": True}]},
            "final_numbering": {"status": "preserved"},
            "changed_entity_ids": list(source_ids), "created_entity_ids": ["C", "P"],
            "graphics_before": [entity(h, ACTIVE) for h in source_ids],
            "verified_graphics": [entity(h, PROTOCOL_RESOLVED) for h in all_ids],
            "graphics_layer_check": {"project_geometry_unchanged": True, "original_texts_and_numbers_unchanged": True,
                                     "no_entities_deleted": True, "changes_re_read": True},
            "dwg_actions": {"work_completed": True, "source_path": "C:/a.dwg", "result_copy_path": "C:/b.dwg",
                            "result_copy_created": True, "source_document_kept_unchanged": True,
                            "source_document_contains_results": False, "final_fresh_read_verified": True,
                            "copy_did_not_change_open_source_identity": True},
        }
        return registry

    def assertValid(self, registry):
        result = validate_registry(registry, True)
        self.assertTrue(result["ok"], result["errors"])

    def assertInvalid(self, registry):
        result = validate_registry(registry, True)
        self.assertFalse(result["ok"], result)

    def missing_fixture(self, kind="no_answer", from_layer=ACTIVE, to_layer=ACTIVE):
        registry = self.fixture(kind)
        item = registry["protocol"]["items"][0]
        item["response"] = {"status": "missing", "entity_ids": []}
        item["consultant"].update(decision="no_answer", text="Замечание осталось без внимания")
        item["transition"]["entity_ids"].remove("C")
        registry["created_entity_ids"].remove("C")
        registry["verified_graphics"] = [g for g in registry["verified_graphics"] if g["handle"] != "C"]
        registry["protocol"]["text_color_checks"] = [c for c in registry["protocol"]["text_color_checks"] if c["entity_id"] != "C"]
        for entity in registry["graphics_before"]:
            entity["layer"] = from_layer
        for entity in registry["verified_graphics"]:
            entity["layer"] = to_layer
        return registry

    def actualization_fixture(self, protocol_registry=None):
        registry = actualization_tests.ActualizationValidationTests().fixture()
        protocol_registry = protocol_registry or self.fixture()
        item = deepcopy(protocol_registry["protocol"]["items"][0])
        registry.update({key: deepcopy(protocol_registry[key]) for key in (
            "protocol", "graphics_before", "verified_graphics", "changed_entity_ids", "created_entity_ids")})
        finding = registry["findings"][0]
        physical = item["plan"]["physical_status"]
        finding["status"] = "insufficient_data" if physical == "not_rechecked" else physical
        finding["targets"][0]["status"] = finding["status"]
        finding["targets"][0]["reason"] = "Результат проверки или предел документарного подтверждения"
        finding["changed_entity_ids"] = list(registry["changed_entity_ids"])
        finding["created_entity_ids"] = list(registry["created_entity_ids"])
        finding["source"]["original_layer"] = registry["graphics_before"][0]["layer"]
        finding["source"]["related_graphic_ids"] = ["D", "E"]
        if physical == "not_rechecked":
            registry["protocol"]["items"][0]["plan"]["physical_status"] = "insufficient_data"
        return registry

    def test_standalone_commitment_has_protocol_layer_without_physical_resolution(self):
        registry = self.fixture()
        self.assertValid(registry)
        self.assertEqual(registry["protocol"]["items"][0]["plan"]["physical_status"], "unresolved")

    def test_protocol_namespace_is_required_only_for_standalone_or_opted_in_actualization(self):
        registry = self.fixture()
        registry.pop("protocol")
        self.assertInvalid(registry)
        self.assertValid(actualization_tests.ActualizationValidationTests().fixture())
        registry = actualization_tests.ActualizationValidationTests().fixture()
        registry["protocol"] = {"required": True, "scope_main_text_ids": [], "items": []}
        self.assertInvalid(registry)

    def test_protocol_scope_excludes_templates_and_cannot_omit_unanswered_group(self):
        for mutation in ("scope", "template", "inventory"):
            with self.subTest(mutation=mutation):
                registry = self.fixture()
                if mutation == "scope":
                    registry["protocol"]["scope_main_text_ids"].append("T2")
                elif mutation == "template":
                    registry["protocol"]["items"][0]["source"]["issued_group"] = False
                else:
                    registry["protocol"]["items"][0]["source"]["inventory_complete"] = False
                self.assertInvalid(registry)

    def test_dates_basis_remaining_actions_and_placement_are_required(self):
        for field, value in (("date", "2026-02-30"), ("basis", ""), ("remaining_actions", ""),
                             ("inside_original_block", False)):
            with self.subTest(field=field):
                registry = self.fixture()
                registry["protocol"]["items"][0]["consultant"][field] = value
                self.assertInvalid(registry)

    def test_whole_group_and_all_explanations_must_move(self):
        for mutation in ("omitted_id", "unmoved_explanation", "missing_before", "journal"):
            with self.subTest(mutation=mutation):
                registry = self.fixture()
                if mutation == "omitted_id":
                    registry["protocol"]["items"][0]["transition"]["entity_ids"].remove("E")
                elif mutation == "unmoved_explanation":
                    next(g for g in registry["verified_graphics"] if g["handle"] == "E")["layer"] = EXPLANATIONS
                elif mutation == "missing_before":
                    registry["graphics_before"] = [g for g in registry["graphics_before"] if g["handle"] != "E"]
                else:
                    registry["changed_entity_ids"].remove("E")
                self.assertInvalid(registry)

    def test_inline_reply_and_consultant_spans_may_share_original_mtext(self):
        registry = self.fixture()
        item = registry["protocol"]["items"][0]
        item["response"]["entity_ids"] = ["T"]
        item["consultant"]["entity_ids"] = ["T"]
        item["transition"]["entity_ids"] = list(item["source"]["entity_ids"])
        registry["created_entity_ids"] = []
        registry["verified_graphics"] = [g for g in registry["verified_graphics"] if g["handle"] not in {"C", "P"}]
        for check in registry["protocol"]["text_color_checks"]:
            check["entity_id"] = "T"
        self.assertValid(registry)
        registry["protocol"]["text_color_checks"][1]["color_index"] = 4
        self.assertInvalid(registry)

    def test_missing_inline_color_verification_or_incorrect_author_color_fails(self):
        for key, value in (("color_index", 3), ("inline_formatting_checked", False)):
            registry = self.fixture()
            registry["protocol"]["text_color_checks"][0][key] = value
            self.assertInvalid(registry)

    def test_commitment_requires_whole_accepted_solution_not_just_selection_of_layer(self):
        for field in ("accepted_solution", "covers_all_requirements", "not_physical_resolution"):
            registry = self.fixture()
            registry["protocol"]["items"][0]["transition"]["evidence"].pop(field)
            self.assertInvalid(registry)
        registry = self.fixture()
        registry["protocol"]["items"][0]["consultant"]["decision"] = "partially_accepted"
        self.assertInvalid(registry)

    def test_completed_correction_needs_plan_evidence(self):
        registry = self.fixture("plan_resolved")
        item = registry["protocol"]["items"][0]
        for entity in registry["verified_graphics"]:
            entity["layer"] = RESOLVED
        self.assertInvalid(registry)
        item["plan"]["physical_status"] = "resolved"
        self.assertValid(registry)
        item["plan"]["evidence"] = {}
        self.assertInvalid(registry)

    def test_no_answer_requires_exact_comment_and_active_layer_independent_of_fact(self):
        registry = self.missing_fixture()
        registry["protocol"]["items"][0]["plan"]["physical_status"] = "resolved"
        self.assertValid(registry)
        self.assertValid(self.actualization_fixture(registry))
        registry["protocol"]["items"][0]["consultant"]["text"] = "Нет сведений"
        self.assertInvalid(registry)

    def test_no_answer_cannot_close_by_plan_resolved_action(self):
        registry = self.missing_fixture("plan_resolved", to_layer=RESOLVED)
        item = registry["protocol"]["items"][0]
        item["plan"]["physical_status"] = "resolved"
        item["consultant"]["decision"] = "accepted"
        self.assertInvalid(registry)

    def test_unanswered_client_explanation_moves_its_whole_group(self):
        registry = self.missing_fixture("unanswered_explanation", CLIENT_EXPLANATIONS, EXPLANATIONS)
        registry["protocol"]["items"][0]["source"]["kind"] = "explanation"
        self.assertValid(registry)
        registry["graphics_before"][0]["layer"] = ACTIVE
        self.assertInvalid(registry)

    def documentary_fixture(self):
        registry = self.missing_fixture("documentary_carry_forward", PROTOCOL_RESOLVED, RESOLVED)
        item = registry["protocol"]["items"][0]
        item["consultant"].update(decision="accepted", text="Документарное принятие по предыдущему протоколу")
        item["plan"].update(checkability="not_checkable", physical_status="not_rechecked", evidence={})
        item["transition"]["evidence"] = {"previous_protocol_reference": "Протокол предыдущего цикла",
                                          "previous_acceptance_text": "Принято по ответу",
                                          "previous_acceptance_date": "2026-10-22", "requirement_unchanged": True,
                                          "no_conflicting_information": True, "documentary_only": True,
                                          "subsequent_cycle": True, "new_materials_received": True,
                                          "new_materials_reference": "Новое поступление планов и ответов"}
        return registry

    def test_documentary_carry_forward_does_not_falsify_physical_status(self):
        registry = self.documentary_fixture()
        self.assertValid(registry)
        self.assertValid(self.actualization_fixture(registry))
        registry["protocol"]["items"][0]["plan"]["physical_status"] = "resolved"
        self.assertInvalid(registry)

    def test_documentary_transfer_requires_previous_acceptance_and_correct_source_layer(self):
        for mutation in ("reference", "future_date", "conflict", "checkable", "layer"):
            with self.subTest(mutation=mutation):
                registry = self.documentary_fixture()
                item = registry["protocol"]["items"][0]
                if mutation == "reference":
                    item["transition"]["evidence"]["previous_protocol_reference"] = ""
                elif mutation == "future_date":
                    item["transition"]["evidence"]["previous_acceptance_date"] = "2026-10-24"
                elif mutation == "conflict":
                    item["transition"]["evidence"]["no_conflicting_information"] = False
                elif mutation == "checkable":
                    item["plan"]["checkability"] = "checkable"
                else:
                    registry["graphics_before"][0]["layer"] = ACTIVE
                self.assertInvalid(registry)

    def stu_fixture(self):
        registry = self.missing_fixture("stu_carry_forward", STU_PROTOCOL, STU)
        item = registry["protocol"]["items"][0]
        item["transition"]["evidence"] = {"previous_notice_reference": "Отправленное письмо предыдущего цикла",
                                          "previous_notice_date": "2026-10-22", "previous_notice_sent": True,
                                          "no_new_reply_about_requirement": True, "requirement_unchanged": True,
                                          "previous_protocol_reference": "Протокол предыдущего цикла",
                                          "subsequent_cycle": True, "new_materials_received": True,
                                          "new_materials_reference": "Новое поступление планов и ответов"}
        return registry

    def test_stu_transfer_requires_prior_sent_notice_and_absence_of_new_reply(self):
        registry = self.stu_fixture()
        self.assertValid(registry)
        self.assertValid(self.actualization_fixture(registry))
        for field in ("previous_notice_reference", "previous_notice_sent", "no_new_reply_about_requirement"):
            with self.subTest(field=field):
                broken = deepcopy(registry)
                broken["protocol"]["items"][0]["transition"]["evidence"].pop(field)
                self.assertInvalid(broken)
        registry["protocol"]["items"][0]["response"]["status"] = "received"
        self.assertInvalid(registry)

    def test_blue_group_cannot_be_recolored_by_ordinary_commitment(self):
        registry = self.fixture()
        for entity in registry["graphics_before"]:
            entity["layer"] = STU
        self.assertInvalid(registry)

    def test_carry_forward_cannot_be_triggered_by_same_cycle_rerun(self):
        for fixture in (self.documentary_fixture, self.stu_fixture):
            for field in ("subsequent_cycle", "new_materials_received", "new_materials_reference", "previous_protocol_reference"):
                with self.subTest(fixture=fixture.__name__, field=field):
                    registry = fixture()
                    registry["protocol"]["items"][0]["transition"]["evidence"].pop(field)
                    self.assertInvalid(registry)

    def test_actualization_keeps_factual_status_and_rejects_invalid_exception(self):
        registry = self.actualization_fixture()
        self.assertValid(registry)
        self.assertEqual(registry["findings"][0]["status"], "unresolved")
        self.assertEqual(registry["findings"][0]["targets"][0]["status"], "unresolved")
        registry["protocol"]["items"][0]["transition"]["evidence"]["covers_all_requirements"] = False
        self.assertInvalid(registry)

    def test_protocol_cannot_hide_conflicting_actualization_fact(self):
        registry = self.actualization_fixture()
        registry["protocol"]["items"][0]["plan"]["physical_status"] = "resolved"
        self.assertInvalid(registry)

    def test_excluded_frame_allows_protocol_but_not_plan_recheck(self):
        registry = self.actualization_fixture()
        finding = registry["findings"][0]
        finding["status"] = "excluded_fire_safety_frame"
        finding["targets"] = []
        item = registry["protocol"]["items"][0]
        item["plan"].update(checkability="excluded", physical_status="not_rechecked", evidence={})
        self.assertValid(registry)
        item["plan"]["physical_status"] = "resolved"
        self.assertInvalid(registry)

    def test_unowned_changes_and_unsafe_save_are_rejected(self):
        registry = self.fixture()
        registry["changed_entity_ids"].append("UNRELATED")
        self.assertInvalid(registry)
        registry = self.fixture()
        registry["dwg_actions"]["result_copy_path"] = "C:/a.dwg"
        self.assertInvalid(registry)
        registry = self.fixture()
        registry["dwg_actions"]["source_overwritten"] = True
        self.assertInvalid(registry)

    def test_fire_lift_circle_requires_approved_layer_color_and_source(self):
        registry = self.fixture()
        item = registry["protocol"]["items"][0]
        item["derived_objects"] = [{"kind": "fire_lift_circle", "entity_ids": ["L"],
                                    "source_reference": "Ответ о назначении лифта", "placement_verified": True}]
        registry["created_entity_ids"].append("L")
        registry["verified_graphics"].append({"handle": "L", "type": "Circle",
                                               "layer": "экспертиза лифты для пожарных", "colorIndex": 4,
                                               "linetype": "ByLayer", "lineWeight": "ByLayer"})
        self.assertValid(registry)
        registry["verified_graphics"][-1]["colorIndex"] = 5
        self.assertInvalid(registry)

    def test_claimed_fix_cannot_be_accepted_through_no_transition(self):
        registry = self.fixture("none")
        item = registry["protocol"]["items"][0]
        item["response"]["text"] = "Уточнено расположение двери"
        item["transition"] = {"kind": "none", "entity_ids": [],
                              "evidence": {"reason": "Слой сохранён", "response_kind": "information"}}
        registry["changed_entity_ids"] = []
        for entity in registry["verified_graphics"]:
            entity["layer"] = ACTIVE
        self.assertValid(registry)
        item["response"].update(text="Исправлено", claims_fixed=True)
        item["transition"]["evidence"]["response_kind"] = "completed_correction"
        self.assertInvalid(registry)
        item["consultant"]["decision"] = "rejected"
        self.assertValid(registry)

    def test_rejected_claimed_fix_cannot_remain_green_using_none(self):
        for layer in (RESOLVED, PROTOCOL_RESOLVED):
            registry = self.fixture("none")
            item = registry["protocol"]["items"][0]
            item["response"].update(text="Исправлено", claims_fixed=True)
            item["consultant"]["decision"] = "rejected"
            item["transition"] = {"kind": "none", "entity_ids": [],
                                  "evidence": {"reason": "Слой оставлен как был", "response_kind": "completed_correction"}}
            registry["changed_entity_ids"] = []
            for entity in registry["graphics_before"] + registry["verified_graphics"]:
                entity["layer"] = layer
            self.assertInvalid(registry)

    def test_accepted_promise_or_scheme_cannot_skip_transition_using_none(self):
        for response_kind in ("commitment", "corrective_scheme", None):
            registry = self.fixture("none")
            item = registry["protocol"]["items"][0]
            item["transition"] = {"kind": "none", "entity_ids": [],
                                  "evidence": {"reason": "Оставлено без переноса"}}
            if response_kind:
                item["transition"]["evidence"]["response_kind"] = response_kind
            registry["changed_entity_ids"] = []
            for entity in registry["verified_graphics"]:
                entity["layer"] = ACTIVE
            self.assertInvalid(registry)

    def mixed_result_fixture(self):
        protocol = self.fixture("none")
        item = protocol["protocol"]["items"][0]
        item["response"].update(text="Исправлено частично", claims_fixed=True)
        item["consultant"]["decision"] = "partially_accepted"
        item["plan"]["physical_status"] = "partially_resolved"
        item["transition"] = {"kind": "none", "entity_ids": [],
                              "evidence": {"reason": "Часть целей фактически устранена", "response_kind": "completed_correction"}}
        item["source"]["entity_ids"].extend(["R2", "D2"])
        protocol["changed_entity_ids"] = []
        for entity in protocol["verified_graphics"]:
            entity["layer"] = ACTIVE
        for container in (protocol["graphics_before"], protocol["verified_graphics"]):
            for handle in ("R2", "D2"):
                container.append({"handle": handle, "layer": RESOLVED, "colorIndex": 256,
                                  "linetype": "ByLayer", "lineWeight": "ByLayer"})
        registry = self.actualization_fixture(protocol)
        finding = registry["findings"][0]
        finding["targets"][0]["status"] = "unresolved"
        finding["source"]["ray_ids"].append("R2")
        finding["targets"].append({"ray_id": "R2", "graphic_ids": ["D2"], "status": "resolved", "reason": "Вторая дверь расширена",
                                   "evidence": {"project_entity_ids": ["W2"], "method": "Обмер", "result": "1300 >= 1200 мм"}})
        return protocol, registry

    def test_none_preserves_mixed_physical_results_but_does_not_bypass_target_checks(self):
        protocol, registry = self.mixed_result_fixture()
        self.assertValid(protocol)
        self.assertValid(registry)
        for container in (registry["graphics_before"], registry["verified_graphics"]):
            next(entity for entity in container if entity["handle"] == "R")["layer"] = RESOLVED
        self.assertInvalid(registry)

    def test_none_allows_target_physically_corrected_in_current_actualization_cycle(self):
        protocol, registry = self.mixed_result_fixture()
        for document in (protocol, registry):
            for handle in ("R2", "D2"):
                next(entity for entity in document["graphics_before"] if entity["handle"] == handle)["layer"] = ACTIVE
            document["changed_entity_ids"].extend(["R2", "D2"])
        registry["findings"][0]["changed_entity_ids"].extend(["R2", "D2"])
        self.assertValid(registry)
        self.assertInvalid(protocol)
        registry["findings"][0]["targets"][1]["evidence"] = {}
        self.assertInvalid(registry)

    def shared_fixture(self):
        registry = self.fixture()
        first = registry["protocol"]["items"][0]
        first["source"]["shared_graphic_ids"] = ["D"]
        second = deepcopy(first)
        second["source"].update(main_text_id="T2", number_id="N2", frame_id="F2", original_number=20,
                                entity_ids=["T2", "N2", "F2", "R2", "D", "E2"])
        second["response"]["entity_ids"] = ["C2"]
        second["consultant"]["entity_ids"] = ["P2"]
        second["transition"]["entity_ids"] = second["source"]["entity_ids"] + ["C2", "P2"]
        registry["protocol"]["items"].append(second)
        registry["protocol"]["scope_main_text_ids"].append("T2")
        for container in (registry["graphics_before"], registry["verified_graphics"]):
            next(entity for entity in container if entity["handle"] == "D")["type"] = "Dimension"
            for handle in ["T2", "N2", "F2", "R2", "E2"] + (["C2", "P2"] if container is registry["verified_graphics"] else []):
                container.append({"handle": handle, "layer": ACTIVE if container is registry["graphics_before"] else PROTOCOL_RESOLVED,
                                  "colorIndex": 256, "linetype": "ByLayer", "lineWeight": "ByLayer"})
        registry["changed_entity_ids"].extend(["T2", "N2", "F2", "R2", "E2"])
        registry["created_entity_ids"].extend(["C2", "P2"])
        registry["protocol"]["text_color_checks"].extend([
            {"entity_id": "C2", "role": "client_response", "color_index": 4, "inline_formatting_checked": True},
            {"entity_id": "P2", "role": "consultant_explanation", "color_index": 30, "inline_formatting_checked": True}])
        return registry

    def test_declared_shared_graphic_can_follow_same_destination_for_both_owners(self):
        self.assertValid(self.shared_fixture())

    def test_shared_graphic_cannot_receive_conflicting_destination_layers(self):
        registry = self.shared_fixture()
        second = registry["protocol"]["items"][1]
        second["response"]["text"] = "Не согласны"
        second["consultant"]["decision"] = "rejected"
        second["transition"].update(kind="rejected", evidence={"response_kind": "disagreement"})
        for entity in registry["verified_graphics"]:
            if entity["handle"] in second["transition"]["entity_ids"]:
                entity["layer"] = ACTIVE
        self.assertInvalid(registry)

    def test_shared_graphic_requires_all_owners_declaration_and_cannot_be_text_or_frame(self):
        for mutation in ("undeclared", "text", "frame"):
            registry = self.shared_fixture()
            if mutation == "undeclared":
                registry["protocol"]["items"][1]["source"]["shared_graphic_ids"] = []
            elif mutation == "text":
                next(entity for entity in registry["graphics_before"] if entity["handle"] == "D")["type"] = "MText"
            else:
                registry["protocol"]["items"][0]["source"]["frame_id"] = "D"
            self.assertInvalid(registry)

    def test_shared_transfer_does_not_exempt_another_findings_unresolved_target(self):
        registry = self.actualization_fixture()
        second = deepcopy(registry["findings"][0])
        second.update(original_number=20, changed_entity_ids=[], created_entity_ids=[])
        second["source"] = {"main_text_id": "T2", "number_id": "N2", "frame_id": "F2", "full_text": "Второе требование",
                            "ray_ids": ["R2"], "related_graphic_ids": ["D"], "original_layer": ACTIVE}
        second["targets"] = [{"ray_id": "R2", "graphic_ids": ["D"], "status": "unresolved",
                               "reason": "Другое требование к общей графике не выполнено"}]
        registry["findings"].append(second)
        registry["actualization_scope"]["issued_main_text_ids"].append("T2")
        for handle in ("T2", "N2", "F2", "R2"):
            registry["verified_graphics"].append({"handle": handle, "layer": ACTIVE, "colorIndex": 256,
                                                   "linetype": "ByLayer", "lineWeight": "ByLayer"})
        result = validate_registry(registry, True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("неустранённая цель осталась в слое устранения" in error for error in result["errors"]))

    def test_selected_special_method_with_remaining_violation_requires_traceable_notice(self):
        for method in ("stu", "risk", "firefighting_plan"):
            with self.subTest(method=method):
                registry = self.fixture()
                item = registry["protocol"]["items"][0]
                item["response"]["selected_methods"] = [method]
                self.assertInvalid(registry)
                item["manager_notice"] = {"status": "included_in_result", "reference": "Раздел уведомлений результата",
                                          "text": "Нарушение по замечанию сохраняется", "originating_remark_link": "T"}
                self.assertValid(registry)
                item["manager_notice"]["status"] = "sent"
                self.assertInvalid(registry)
                item["manager_notice"]["sent_reference"] = "Подтверждённое исходящее письмо"
                self.assertValid(registry)

    def word_fixture(self, mode="protocol"):
        registry = self.fixture(mode=mode)
        item = registry["protocol"]["items"][0]
        item["response"].update(entity_ids=[], planned_inside_original_block=True)
        item["consultant"].update(entity_ids=[], word_reference="Раздел протокола в Word",
                                  planned_inside_original_block=True)
        item["transition"]["entity_ids"] = list(item["source"]["entity_ids"])
        item["transition"]["planned_only"] = True
        registry["protocol"]["planned_text_colors"] = {"client_response": 4, "consultant_explanation": 30}
        registry["protocol"]["text_color_checks"] = []
        registry["changed_entity_ids"] = []
        registry["created_entity_ids"] = []
        registry["verified_graphics"] = []
        registry["dwg_actions"] = {"work_completed": False}
        registry["word_report"] = {"status": "complete"}
        return registry

    def test_word_protocol_validates_substance_without_claiming_cad_execution(self):
        for mode in ("protocol", "actualization_protocol"):
            registry = self.word_fixture(mode)
            result = validate_protocol_analysis_registry(registry, True)
            self.assertTrue(result["ok"], result["errors"])
            self.assertTrue(result["graphics_layer_check"]["protocol_analysis_verified"])
            self.assertNotIn("protocol_objects_verified", result["graphics_layer_check"])
            self.assertEqual(registry["changed_entity_ids"], [])
            self.assertEqual(registry["created_entity_ids"], [])
            registry["protocol"]["items"][0]["transition"]["evidence"]["covers_all_requirements"] = False
            self.assertFalse(validate_protocol_analysis_registry(registry, True)["ok"])

    def test_word_protocol_requires_planned_not_fictitious_completed_transition(self):
        for mutation in ("planned", "colors", "word_address", "claims_fixed", "physical_fix"):
            with self.subTest(mutation=mutation):
                registry = self.word_fixture()
                item = registry["protocol"]["items"][0]
                if mutation == "planned":
                    item["transition"]["planned_only"] = False
                elif mutation == "colors":
                    registry["protocol"]["planned_text_colors"]["consultant_explanation"] = 3
                elif mutation == "word_address":
                    item["consultant"]["word_reference"] = ""
                elif mutation == "claims_fixed":
                    item["response"]["claims_fixed"] = True
                else:
                    item["transition"]["kind"] = "plan_resolved"
                self.assertFalse(validate_protocol_analysis_registry(registry, True)["ok"])

    def test_word_only_protocol_cannot_report_cad_edits(self):
        registry = self.word_fixture()
        registry["changed_entity_ids"] = ["T"]
        self.assertFalse(validate_protocol_analysis_registry(registry, True)["ok"])

    def test_malformed_nested_record_returns_validation_error(self):
        registry = self.fixture()
        registry["protocol"]["items"][0]["source"] = None
        self.assertInvalid(registry)


if __name__ == "__main__":
    unittest.main()
