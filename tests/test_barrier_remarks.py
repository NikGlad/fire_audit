import copy
import unittest

from fire_audit.barrier_remarks import inventory_digest, new_source_remarks, source_remarks_errors
from fire_audit.barriers import SOURCE_EXPLANATIONS_LAYER


class SourceRemarksGateTests(unittest.TestCase):
    def registry(self):
        source = new_source_remarks()
        source["inventory"].update({
            "status": "complete", "source_path": "C:/project/source.dwg",
            "captured_at": "2026-10-15T12:00:00+03:00", "matched": 2, "layer_exists": True,
            "entities": [
                {"handle": "ab1", "type": "MTEXT", "layer": SOURCE_EXPLANATIONS_LAYER,
                 "space": "Model", "text": "Заказчик: дверь EI 30. Ответ от 14.10.2026."},
                {"handle": "ab2", "type": "LEADER", "layer": SOURCE_EXPLANATIONS_LAYER,
                 "space": "Model", "vertices": [[1, 2], [3, 4]]},
            ],
        })
        source["inventory"]["digest"] = inventory_digest(source["inventory"])
        source["decisions"] = [{
            "id": "remark-1", "source_entity_ids": ["AB1", "AB2"], "kind": "remark",
            "inventory_digest": source["inventory"]["digest"],
            "status": "resolved", "decision": "applied", "summary": "Дверь EI 30 обозначена",
            "context_review": "Учтён ответ заказчика от 14.10.2026, луч указывает на дверь 101.",
            "basis": "Согласованное решение", "result_refs": ["CAD:CD1", "report:section-3"],
            "all_requirements_reviewed": True,
        }]
        return {"barrier_assessment": {"source_remarks": source}}

    def source(self, registry):
        return registry["barrier_assessment"]["source_remarks"]

    def assertBlocked(self, registry, message):
        errors = source_remarks_errors(registry)
        self.assertTrue(any(message in error for error in errors), errors)

    def test_initial_state_and_missing_inventory_cannot_finish(self):
        self.assertTrue(source_remarks_errors({}))
        self.assertBlocked({"barrier_assessment": {"source_remarks": {}}}, "инвентаризация")
        first, second = new_source_remarks(), new_source_remarks()
        first["decisions"].append({"id": "changed"})
        self.assertEqual(second["decisions"], [])
        self.assertTrue(source_remarks_errors({"barrier_assessment": {"source_remarks": second}}))

    def test_resolved_complete_register_passes_both_task_variants(self):
        for variant in ("full_execution", "analysis_word"):
            self.assertEqual(source_remarks_errors(self.registry(), variant), [])
        self.assertTrue(source_remarks_errors(self.registry(), "unknown"))

    def test_pending_partial_and_truncated_inventory_block(self):
        for patch in ({"status": "pending"}, {"scope": "visible"}, {"matched": 3},
                      {"truncated": True}, {"has_more": "false"}, {"nextCursor": "page2"}):
            with self.subTest(patch=patch):
                registry = self.registry()
                self.source(registry)["inventory"].update(patch)
                self.assertTrue(source_remarks_errors(registry))

    def test_each_handle_must_have_exactly_one_decision(self):
        registry = self.registry()
        decision = self.source(registry)["decisions"][0]
        decision["source_entity_ids"] = ["AB1"]
        self.assertBlocked(registry, "AB2")
        decision["source_entity_ids"] = ["AB1", "AB2", "ab2"]
        self.assertBlocked(registry, "уже учтён")
        decision["source_entity_ids"] = ["AB1", "AB2", "FFFF"]
        self.assertBlocked(registry, "неизвестный")

    def test_duplicate_inventory_handles_or_decision_ids_block(self):
        registry = self.registry()
        inventory = self.source(registry)["inventory"]
        inventory["entities"][1]["handle"] = "AB1"
        inventory["digest"] = inventory_digest(inventory)
        self.assertBlocked(registry, "повтор исходного")
        registry = self.registry()
        self.source(registry)["decisions"].append(copy.deepcopy(self.source(registry)["decisions"][0]))
        self.assertBlocked(registry, "повтор id")

    def test_exact_layer_in_inventory_and_each_raw_entity_is_required(self):
        for entity_patch in (False, True):
            registry = self.registry()
            inventory = self.source(registry)["inventory"]
            target = inventory["entities"][0] if entity_patch else inventory
            target["layer"] = "экспертиза противопожарные преграды"
            inventory["digest"] = inventory_digest(inventory)
            self.assertBlocked(registry, "сло")

    def test_digest_covers_text_geometry_and_source_but_not_capture_time_or_handle_case(self):
        inventory = self.source(self.registry())["inventory"]
        before = inventory_digest(inventory)
        equivalent = copy.deepcopy(inventory)
        equivalent["captured_at"] = "later"
        equivalent["entities"].reverse()
        for entity in equivalent["entities"]:
            entity["handle"] = entity["handle"].upper()
        self.assertEqual(inventory_digest(equivalent), before)
        for key in ("text", "geometry", "source"):
            with self.subTest(key=key):
                registry = self.registry()
                changed = self.source(registry)["inventory"]
                if key == "text":
                    changed["entities"][0]["text"] = "Другой текст"
                elif key == "geometry":
                    changed["entities"][1]["vertices"][0][0] = 999
                else:
                    changed["source_path"] = "C:/another/source.dwg"
                self.assertBlocked(registry, "выгрузка изменена")

    def test_unresolved_status_false_boolean_empty_context_and_missing_result_block(self):
        for patch in ({"status": "pending"}, {"status": "needs_clarification"},
                      {"decision": "pending"}, {"all_requirements_reviewed": "true"},
                      {"all_requirements_reviewed": 1}, {"context_review": " "},
                      {"summary": True}, {"basis": " "}, {"basis": True},
                      {"result_refs": []}, {"result_refs": [""]}):
            with self.subTest(patch=patch):
                registry = self.registry()
                self.source(registry)["decisions"][0].update(patch)
                self.assertTrue(source_remarks_errors(registry))

    def test_non_applicable_and_superseded_require_reasoned_decisions(self):
        for disposition in ("not_applicable", "superseded"):
            registry = self.registry()
            decision = self.source(registry)["decisions"][0]
            decision.update({"decision": disposition, "basis": "Ответ заказчика изменяет исходное решение"})
            if disposition == "not_applicable":
                decision["result_refs"] = []
            self.assertEqual(source_remarks_errors(registry), [])
            decision["basis"] = " "
            self.assertBlocked(registry, "обоснования basis")
        registry = self.registry()
        self.source(registry)["decisions"][0].update({"decision": "superseded", "result_refs": []})
        self.assertBlocked(registry, "актуальную редакцию")

    def test_empty_or_absent_layer_needs_successful_inventory_and_explanation(self):
        for exists in (True, False):
            registry = self.registry()
            source = self.source(registry)
            source["inventory"].update({"entities": [], "matched": 0, "layer_exists": exists})
            source["inventory"]["digest"] = inventory_digest(source["inventory"])
            source["decisions"] = []
            self.assertBlocked(registry, "none_found_reason")
            source["none_found_reason"] = "Полная выгрузка: слой пуст." if exists else "Слой отсутствует в исходном DWG."
            self.assertEqual(source_remarks_errors(registry), [])
            source["inventory"]["status"] = "pending"
            self.assertBlocked(registry, "не завершена")

    def test_non_remark_classification_is_possible_but_must_explain_every_object(self):
        registry = self.registry()
        source = self.source(registry)
        source["decisions"][0].update({
            "kind": "non_remark", "decision": "non_remark", "summary": "Образец в легенде",
            "basis": "Текст и луч входят в рамку образца, не адресованы конструкции здания",
            "result_refs": [], "source_entity_ids": ["AB1"], "non_remark_kind": "legend",
        })
        source["decisions"].append({
            "id": "aux-1", "source_entity_ids": ["AB2"], "kind": "non_remark",
            "status": "resolved", "decision": "non_remark", "summary": "Луч образца",
            "basis": "Служебная линия соединена с текстом образца AB1 внутри легенды",
            "inventory_digest": source["inventory"]["digest"], "result_refs": [],
        })
        self.assertBlocked(registry, "none_found_reason")
        source["none_found_reason"] = "Весь слой содержит только образец оформления."
        self.assertEqual(source_remarks_errors(registry), [])
        source["decisions"][0]["basis"] = " "
        self.assertBlocked(registry, "не является замечанием")

    def test_non_remark_text_cannot_be_dismissed_as_a_group_or_without_classification(self):
        registry = self.registry()
        source = self.source(registry)
        decision = source["decisions"][0]
        source["none_found_reason"] = "Слой содержит служебные объекты."
        decision.update({"kind": "non_remark", "decision": "non_remark"})
        self.assertBlocked(registry, "одного исходного объекта")
        decision["source_entity_ids"] = ["AB1"]
        self.assertBlocked(registry, "non_remark_kind")
        decision["non_remark_kind"] = "legend"
        decision["context_review"] = " "
        self.assertBlocked(registry, "context_review")

    def test_changed_inventory_invalidates_old_decisions_even_after_recomputing_digest(self):
        registry = self.registry()
        inventory = self.source(registry)["inventory"]
        inventory["entities"][0]["text"] += " Новое дополнительное требование."
        inventory["digest"] = inventory_digest(inventory)
        self.assertBlocked(registry, "inventory_digest")
        self.source(registry)["decisions"][0]["inventory_digest"] = inventory["digest"]
        self.assertEqual(source_remarks_errors(registry), [])

    def test_raw_text_and_identity_properties_cannot_be_omitted(self):
        for field in ("handle", "type", "layer", "space", "text"):
            registry = self.registry()
            inventory = self.source(registry)["inventory"]
            inventory["entities"][0].pop(field)
            if field != "handle":
                inventory["digest"] = inventory_digest(inventory)
            self.assertTrue(source_remarks_errors(registry), field)

    def test_text_entity_aliases_require_complete_text(self):
        for entity_type in ("DBTEXT", "MULTILEADER", "MLEADER", "ACDBMLEADER",
                            "ATTRIBUTE", "ATTRIBUTEDEFINITION"):
            with self.subTest(entity_type=entity_type):
                registry = self.registry()
                source = self.source(registry)
                inventory = source["inventory"]
                inventory["entities"][0]["type"] = entity_type
                inventory["digest"] = inventory_digest(inventory)
                source["decisions"][0]["inventory_digest"] = inventory["digest"]
                self.assertEqual(source_remarks_errors(registry), [])
                inventory["entities"][0].pop("text")
                inventory["digest"] = inventory_digest(inventory)
                source["decisions"][0]["inventory_digest"] = inventory["digest"]
                self.assertBlocked(registry, "не сохранён полный исходный текст")

    def test_malformed_payloads_fail_closed_without_crashing(self):
        for malformed in (None, [], "true", 1, False):
            with self.subTest(malformed=malformed):
                self.assertTrue(source_remarks_errors(malformed))
                self.assertTrue(source_remarks_errors({"barrier_assessment": malformed}))
                registry = self.registry()
                registry["barrier_assessment"]["source_remarks"] = malformed
                self.assertTrue(source_remarks_errors(registry))
        for key, malformed in (("entities", None), ("entities", [False]), ("matched", True),
                               ("layer_exists", "true"), ("source_path", []), ("digest", {})):
            registry = self.registry()
            self.source(registry)["inventory"][key] = malformed
            self.assertTrue(source_remarks_errors(registry), key)
        for key, malformed in (("kind", []), ("decision", {}), ("source_entity_ids", [False]),
                               ("result_refs", [True]), ("id", {}), ("status", [])):
            registry = self.registry()
            self.source(registry)["decisions"][0][key] = malformed
            self.assertTrue(source_remarks_errors(registry), key)


if __name__ == "__main__":
    unittest.main()
