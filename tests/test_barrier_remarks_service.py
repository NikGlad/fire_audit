"""Integration tests of the mandatory source gate; no live CAD is accessed."""
import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from fire_audit.barrier_remarks import SOURCE_EXPLANATIONS_LAYER, inventory_digest
from fire_audit.barrier_remarks_service import BASELINE_FILE, INVENTORY_FILE
from fire_audit.core import FireAuditApp
from fire_audit.io_utils import read_json, write_json
from fire_audit.runs import FULL_QUALITY_CHECKS, WORD_QUALITY_CHECKS


ROOT = Path(__file__).resolve().parents[1]


class SourceRemarksBridge:
    """Only source-inventory read tools are implemented; writes fail the test."""
    def __init__(self, path):
        self.current_path = str(path)
        self.entities = [
            {"handle": "A1", "type": "MText", "layer": SOURCE_EXPLANATIONS_LAYER,
             "space": "Model", "text": "Первое исходное замечание; ответ заказчика от 15.10.2026"},
            {"handle": "A2", "type": "MText", "layer": SOURCE_EXPLANATIONS_LAYER,
             "space": "Лист 1", "text": "Второе исходное замечание; вариант решения 2"},
        ]
        self.exists = True
        self.failure = None
        self.calls = []
        self.info_paths = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def call(self, name, args=None):
        self.calls.append((name, copy.deepcopy(args)))
        if name == "get_drawing_info":
            path = self.info_paths.pop(0) if self.info_paths else self.current_path
            return {"path": path, "modified": False,
                    "lastSavedDate": "2026-10-15T09:00:00", "entityCount": len(self.entities)}
        if name == "list_layers":
            layers = [{"name": "0"}]
            if self.exists:
                layers.append({"name": SOURCE_EXPLANATIONS_LAYER})
            return {"layers": layers, "count": len(layers)}
        if name == "list_blocks":
            return {"blocks": [], "count": 0}
        if name == "get_entities":
            if self.failure:
                raise RuntimeError(self.failure)
            if args.get("space") != "all" or args.get("layer") != SOURCE_EXPLANATIONS_LAYER:
                raise AssertionError("The complete source layer in all spaces must be requested")
            offset, limit = args["offset"], args["limit"]
            entities = self.entities[offset:offset + limit]
            return {"entities": copy.deepcopy(entities), "count": len(entities),
                    "matched": len(self.entities), "hasMore": offset + len(entities) < len(self.entities)}
        raise AssertionError("Unexpected CAD tool (no real CAD or mutations allowed): " + name)


class SourceRemarksServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("config", "knowledge", "templates"):
            shutil.copytree(ROOT / folder, self.root / folder)
        self.app = FireAuditApp(self.root)
        self.source = self.root / "source.dwg"
        self.source.write_bytes(b"synthetic fixture; not an actual DWG")
        project = self.app.projects.create("Синтетическая проверка реестра", "remarks")
        project["dates"]["gpzu"] = "2026-04-23"
        project["dwg"]["path"] = str(self.source)
        self.app.projects.save("remarks", project)
        self.bridge = SourceRemarksBridge(self.source)
        self.app.runs.source_remarks_bridge_factory = lambda: self.bridge
        self.run_id = self.create_run()

    def create_run(self, mode="barrier_assessment", variant="full_execution"):
        return self.app.runs.create("remarks", mode, variant)["run"]["run_id"]

    def load(self, run_id=None):
        return self.app.runs.load("remarks", run_id or self.run_id)

    def save_registry(self, loaded):
        write_json(Path(loaded["folder"]) / "РЕЕСТР_ПРОВЕРКИ.json", loaded["registry"])

    def capture(self, run_id=None):
        return self.app.runs.capture_barrier_source_remarks("remarks", run_id or self.run_id)

    def decision(self, handle, run_id=None, status="resolved"):
        inventory = self.load(run_id)["registry"]["barrier_assessment"]["source_remarks"]["inventory"]
        return {
            "id": "remark-" + handle, "source_entity_ids": [handle], "kind": "remark",
            "status": status, "decision": "applied", "summary": "Требование принято и отражено в результате",
            "context_review": "Прочитаны ответ заказчика, дата, варианты и адресная цель замечания",
            "basis": "Обоснование по проверенному источнику синтетического теста",
            "result_refs": ["report:section-" + handle], "all_requirements_reviewed": True,
            "inventory_digest": inventory["digest"],
        }

    def resolve(self, run_id=None):
        run_id = run_id or self.run_id
        self.capture(run_id)
        return self.app.runs.record_barrier_source_decisions(
            "remarks", run_id, [self.decision("A1", run_id), self.decision("A2", run_id)])

    def quality(self, run_id=None):
        checks = FULL_QUALITY_CHECKS if self.load(run_id)["run"]["task_variant"] == "full_execution" else WORD_QUALITY_CHECKS
        return {"checks": {name: True for name in checks}, "unresolved": [],
                "created_or_changed_entity_ids": []}

    def result_file(self, run_id=None):
        run_id = run_id or self.run_id
        run = self.load(run_id)["run"]
        path = self.source.with_name(f"{self.source.stem}__{run['mode']}__{run_id}.dwg")
        path.write_bytes(b"synthetic output fixture; not an actual DWG")
        return path

    def report_file(self, run_id):
        loaded = self.load(run_id)
        path = Path(loaded["folder"]) / loaded["run"]["hard_constraints"]["word_report_filename"]
        path.write_bytes(b"synthetic report fixture; content validation is outside this gate")
        return path

    def record_result(self, run_id=None, path=None):
        run_id = run_id or self.run_id
        path = path or self.result_file(run_id)
        self.bridge.current_path = str(path)
        return self.app.runs.record_result_dwg(
            "remarks", run_id, str(path),
            source_document_contains_results=False, copy_did_not_change_open_source_identity=True)

    def record_report(self, run_id):
        return self.app.runs.record_word_report(
            "remarks", run_id, str(self.report_file(run_id)), rendered_page_count=1,
            visual_review_complete=True, source_dwg_unchanged=True)

    def history_entry(self, run_id=None):
        run_id = run_id or self.run_id
        return next(entry for entry in self.app.runs.project_history("remarks")["runs"]
                    if entry["run_id"] == run_id)

    def test_capture_partial_decisions_then_all_resolved_never_finishes_drawing_implicitly(self):
        captured = self.capture()
        self.assertEqual(captured["inventory"]["matched"], 2)
        self.assertEqual({item["space"] for item in captured["inventory"]["entities"]}, {"Model", "Лист 1"})
        self.assertFalse(captured["complete"])
        partial = self.app.runs.record_barrier_source_decisions("remarks", self.run_id, [self.decision("A1")])
        self.assertFalse(partial["complete"])
        self.assertTrue(any("A2" in error for error in partial["errors"]))
        complete = self.app.runs.record_barrier_source_decisions("remarks", self.run_id, [self.decision("A2")])
        self.assertTrue(complete["complete"], complete)
        self.assertEqual(complete["decision_count"], 2)
        self.assertFalse(self.load()["registry"]["dwg_actions"].get("work_completed", False))
        self.assertFalse(self.history_entry()["result_complete"])

    def test_missing_inventory_and_missing_decisions_block_all_true_quality_and_dwg(self):
        for captured in (False, True):
            with self.subTest(captured=captured):
                if captured:
                    self.bridge.current_path = str(self.source)
                    self.capture()
                with self.assertRaisesRegex(ValueError, "контроль замечаний"):
                    self.app.runs.record_quality_control("remarks", self.run_id, self.quality())
                with self.assertRaisesRegex(ValueError, "контроль замечаний"):
                    self.record_result()
                self.assertFalse(self.load()["registry"]["dwg_actions"].get("work_completed", False))
                self.assertNotEqual(self.load()["registry"].get("quality_control", {}).get("status"), "complete")

    def test_word_report_has_the_same_gate_before_and_after_capture(self):
        run_id = self.create_run(variant="analysis_word")
        for captured in (False, True):
            with self.subTest(captured=captured):
                if captured:
                    self.capture(run_id)
                with self.assertRaisesRegex(ValueError, "контроль замечаний"):
                    self.record_report(run_id)
                with self.assertRaisesRegex(ValueError, "контроль замечаний"):
                    self.app.runs.record_quality_control("remarks", run_id, self.quality(run_id))
        self.assertTrue(self.resolve(run_id)["complete"])
        self.assertEqual(self.record_report(run_id)["status"], "complete")

    def test_application_validation_blocks_while_work_completed_is_false(self):
        self.assertFalse(self.load()["registry"]["dwg_actions"].get("work_completed", False))
        validation = self.app.runs.validate("remarks", self.run_id, for_application=True)
        self.assertFalse(validation["ok"])
        self.assertTrue(any("контроль замечаний" in error for error in validation["errors"]), validation)
        self.assertEqual(self.bridge.calls, [])

    def test_resolved_sources_allow_result_registration_after_fresh_read_from_allowed_copy(self):
        self.assertTrue(self.resolve()["complete"])
        self.app.runs.record_quality_control("remarks", self.run_id, self.quality())
        result = self.result_file()
        self.bridge.current_path = str(result)
        calls_before = len(self.bridge.calls)
        recorded = self.record_result(path=result)
        self.assertTrue(recorded["work_completed"])
        self.assertGreater(len(self.bridge.calls), calls_before)
        inventory = self.load()["registry"]["barrier_assessment"]["source_remarks"]["inventory"]
        self.assertEqual(inventory["source_path"], str(self.source.resolve()))
        self.assertEqual(inventory["captured_document_path"], str(result))
        self.assertTrue(self.history_entry()["source_remarks_complete"])
        self.assertTrue(self.history_entry()["result_complete"])

    def test_changed_text_on_same_handle_invalidates_decisions_quality_and_history(self):
        self.resolve()
        self.app.runs.record_quality_control("remarks", self.run_id, self.quality())
        self.record_result()
        previous = copy.deepcopy(self.load()["registry"]["barrier_assessment"]["source_remarks"])
        self.bridge.current_path = str(self.source)
        self.bridge.entities[0]["text"] += "; дополнительное требование заказчика"
        changed = self.capture()
        self.assertTrue(changed["changed"])
        self.assertFalse(changed["complete"])
        registry = self.load()["registry"]
        self.assertEqual(registry["barrier_assessment"]["source_remarks"]["decisions"], [])
        self.assertFalse(registry["dwg_actions"]["work_completed"])
        self.assertEqual(registry["quality_control"]["status"], "needs_attention")
        self.assertTrue(any(item["record"] == previous for item in registry["barrier_assessment"]["source_remarks_history"]))
        baseline = read_json(Path(self.load()["folder"]) / BASELINE_FILE)
        self.assertIn("дополнительное требование", baseline["entities"][0]["text"])
        self.assertTrue(registry["barrier_assessment"]["source_baseline_history"])
        self.assertFalse(self.history_entry()["result_complete"])

    def test_fresh_final_read_detects_source_change_and_blocks_completion(self):
        self.resolve()
        self.bridge.entities[1]["text"] += "; новая редакция"
        with self.assertRaisesRegex(ValueError, "изменены исходные"):
            self.record_result()
        self.assertFalse(self.load()["registry"]["dwg_actions"]["work_completed"])
        self.assertFalse(self.history_entry()["result_complete"])

    def test_self_rehashed_registry_inventory_does_not_replace_server_snapshot(self):
        self.resolve()
        loaded = self.load()
        source = loaded["registry"]["barrier_assessment"]["source_remarks"]
        source["inventory"]["entities"][0]["text"] = "Подменённое исходное замечание"
        source["inventory"]["digest"] = inventory_digest(source["inventory"])
        for decision in source["decisions"]:
            decision["inventory_digest"] = source["inventory"]["digest"]
        self.save_registry(loaded)
        with self.assertRaisesRegex(ValueError, "сохранённому снимку"):
            self.record_result()
        self.assertFalse(self.history_entry()["source_remarks_complete"])

    def test_foreign_run_inventory_and_foreign_digest_are_rejected(self):
        self.capture()
        decision = self.decision("A1")
        decision["inventory_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "другой версии"):
            self.app.runs.record_barrier_source_decisions("remarks", self.run_id, [decision])
        self.resolve()
        loaded = self.load()
        inventory = loaded["registry"]["barrier_assessment"]["source_remarks"]["inventory"]
        inventory["run_id"] = "another-run"
        self.save_registry(loaded)
        # Even a copied server-side inventory cannot waive the run binding.
        write_json(Path(loaded["folder"]) / INVENTORY_FILE, inventory)
        with self.assertRaisesRegex(ValueError, "другому запуску"):
            self.app.runs.record_quality_control("remarks", self.run_id, self.quality())

    def test_pending_decision_is_saved_as_work_in_progress_and_blocks_completion(self):
        self.capture()
        response = self.app.runs.record_barrier_source_decisions(
            "remarks", self.run_id, [self.decision("A1", status="needs_clarification"), self.decision("A2")])
        self.assertFalse(response["complete"])
        self.assertTrue(any("не решено" in error for error in response["errors"]))
        with self.assertRaisesRegex(ValueError, "не решено"):
            self.record_result()

    def test_failed_capture_retains_nonempty_inventory_but_invalidates_completion(self):
        self.resolve()
        self.app.runs.record_quality_control("remarks", self.run_id, self.quality())
        self.record_result()
        before = copy.deepcopy(self.load()["registry"]["barrier_assessment"]["source_remarks"]["inventory"])
        self.bridge.failure = "Synthetic connector read failure"
        with self.assertRaisesRegex(RuntimeError, "Synthetic connector"):
            self.capture()
        registry = self.load()["registry"]
        record = registry["barrier_assessment"]["source_remarks"]
        self.assertEqual(record["inventory"], before)
        self.assertEqual(len(record["decisions"]), 2)
        self.assertIn("Synthetic connector", record["refresh_error"])
        self.assertFalse(registry["dwg_actions"]["work_completed"])
        self.assertEqual(registry["quality_control"]["status"], "needs_attention")
        self.assertFalse(self.history_entry()["result_complete"])

    def test_hand_written_completion_flags_cannot_bypass_history_gate(self):
        loaded = self.load()
        loaded["registry"]["dwg_actions"]["work_completed"] = True
        loaded["registry"]["word_report"] = {"status": "complete", "path": "pretend.docx"}
        self.save_registry(loaded)
        entry = self.history_entry()
        self.assertFalse(entry["result_complete"])
        self.assertFalse(entry["source_remarks_complete"])
        self.assertTrue(entry["source_remarks_errors"])

    def test_other_modes_and_object_norms_do_not_acquire_the_barrier_gate(self):
        self.bridge.failure = "The bridge must not be accessed by another mode"
        other = self.create_run(mode="article89")
        self.assertEqual(self.app.runs.record_quality_control("remarks", other, self.quality(other))["status"], "complete")
        self.assertTrue(self.record_result(other)["work_completed"])
        norms = self.create_run(mode="compliance", variant="object_norms")
        self.assertEqual(self.app.runs.record_quality_control("remarks", norms, self.quality(norms))["status"], "complete")
        self.assertEqual(self.record_report(norms)["status"], "complete")
        self.assertTrue(self.history_entry(norms)["result_complete"])
        self.assertEqual(self.bridge.calls, [])
        with self.assertRaisesRegex(ValueError, "только для оценки преград"):
            self.capture(norms)

    def test_first_capture_from_an_empty_result_copy_is_rejected(self):
        self.bridge.current_path = str(self.result_file())
        self.bridge.entities = []
        with self.assertRaisesRegex(ValueError, "Первый снимок"):
            self.capture()
        loaded = self.load()
        self.assertFalse((Path(loaded["folder"]) / BASELINE_FILE).exists())
        self.assertFalse((Path(loaded["folder"]) / INVENTORY_FILE).exists())
        self.assertFalse(loaded["registry"]["dwg_actions"].get("work_completed", False))
        self.assertFalse(self.history_entry()["result_complete"])

    def test_deleting_original_remarks_in_copy_cannot_be_reclassified_as_none_found(self):
        self.resolve()
        baseline_path = Path(self.load()["folder"]) / BASELINE_FILE
        original = read_json(baseline_path)
        self.bridge.current_path = str(self.result_file())
        self.bridge.entities = []
        with self.assertRaisesRegex(ValueError, "отсутствуют"):
            self.capture()
        response = self.app.runs.record_barrier_source_decisions(
            "remarks", self.run_id, [], none_found_reason="В копии теперь пустой слой",
            delete_decision_ids=["remark-A1", "remark-A2"])
        self.assertFalse(response["complete"])
        self.assertTrue(any("A1" in error for error in response["errors"]), response)
        self.assertEqual(read_json(baseline_path), original)
        self.assertEqual(len(self.load()["registry"]["barrier_assessment"]["source_remarks"]["inventory"]["entities"]), 2)
        with self.assertRaises(ValueError):
            self.record_result()
        self.assertFalse(self.history_entry()["result_complete"])

    def test_modified_original_text_in_copy_is_rejected_without_replacing_original_snapshot(self):
        self.resolve()
        baseline_path = Path(self.load()["folder"]) / BASELINE_FILE
        original = read_json(baseline_path)
        self.bridge.current_path = str(self.result_file())
        self.bridge.entities[0]["text"] = "Текст замечания удалён и заменён"
        with self.assertRaisesRegex(ValueError, "изменены исходные"):
            self.capture()
        self.assertEqual(read_json(baseline_path), original)
        self.assertEqual(self.load()["registry"]["barrier_assessment"]["source_remarks"]["inventory"]["entities"],
                         original["entities"])
        self.assertFalse(self.history_entry()["source_remarks_complete"])

    def test_result_registration_requires_the_registered_dwg_to_be_active(self):
        self.resolve()
        result = self.result_file()
        self.assertEqual(self.bridge.current_path, str(self.source))
        with self.assertRaisesRegex(ValueError, "именно сохраняемый DWG результата"):
            self.app.runs.record_result_dwg(
                "remarks", self.run_id, str(result), source_document_contains_results=False,
                copy_did_not_change_open_source_identity=True)
        self.assertFalse(self.load()["registry"]["dwg_actions"]["work_completed"])

    def test_copy_may_add_sources_but_every_new_source_requires_a_new_decision(self):
        self.resolve()
        baseline_path = Path(self.load()["folder"]) / BASELINE_FILE
        original = read_json(baseline_path)
        self.bridge.current_path = str(self.result_file())
        self.bridge.entities.append({
            "handle": "A3", "type": "MText", "layer": SOURCE_EXPLANATIONS_LAYER,
            "space": "Model", "text": "Дополнительное замечание в копии",
        })
        capture = self.capture()
        self.assertTrue(capture["changed"])
        self.assertFalse(capture["complete"])
        self.assertEqual(capture["inventory"]["matched"], 3)
        self.assertEqual(read_json(baseline_path), original)
        partial = self.app.runs.record_barrier_source_decisions(
            "remarks", self.run_id, [self.decision("A1"), self.decision("A2")])
        self.assertFalse(partial["complete"])
        complete = self.app.runs.record_barrier_source_decisions(
            "remarks", self.run_id, [self.decision("A3")])
        self.assertTrue(complete["complete"], complete)
        self.assertTrue(self.record_result()["work_completed"])

    def test_casefold_upsert_and_deleting_old_decisions_allow_regrouping_with_history(self):
        self.resolve()
        original_decisions = copy.deepcopy(
            self.load()["registry"]["barrier_assessment"]["source_remarks"]["decisions"])
        grouped = self.decision("A1")
        grouped.update({"id": "group", "source_entity_ids": ["A1", "A2"],
                        "summary": "Связанные требования рассмотрены вместе"})
        duplicate = self.app.runs.record_barrier_source_decisions("remarks", self.run_id, [grouped])
        self.assertFalse(duplicate["complete"])
        grouped["id"] = "GROUP"
        complete = self.app.runs.record_barrier_source_decisions(
            "remarks", self.run_id, [grouped],
            delete_decision_ids=[" REMARK-A1 ", "remark-a2"])
        self.assertTrue(complete["complete"], complete)
        self.assertEqual(complete["decision_count"], 1)
        data = self.load()["registry"]["barrier_assessment"]
        self.assertEqual(data["source_remarks"]["decisions"][0]["id"], "GROUP")
        self.assertTrue(any(entry["record"]["decisions"] == original_decisions
                            for entry in data["source_remarks_history"]))
        self.assertFalse(self.load()["registry"]["dwg_actions"]["work_completed"])

    def test_document_switch_between_service_check_and_capture_is_rejected(self):
        self.resolve()
        result = self.result_file()
        # The service sees the requested result, but the inventory reader would
        # otherwise accept the original if it retained both allowed documents.
        self.bridge.info_paths = [str(result), str(self.source)]
        with self.assertRaisesRegex(ValueError, "другой DWG|сменился"):
            self.record_result(path=result)
        loaded = self.load()
        record = loaded["registry"]["barrier_assessment"]["source_remarks"]
        self.assertEqual(record["inventory"]["captured_document_path"], str(self.source))
        self.assertTrue(record.get("refresh_error"))
        self.assertFalse(loaded["registry"]["dwg_actions"]["work_completed"])
        self.assertFalse(self.history_entry()["result_complete"])

    def test_quality_completion_after_result_is_pinned_requires_that_result_open(self):
        self.resolve()
        result = self.result_file()
        self.record_result(path=result)
        self.bridge.current_path = str(self.source)
        with self.assertRaises(ValueError):
            self.app.runs.record_quality_control("remarks", self.run_id, self.quality())
        self.assertNotEqual(self.load()["registry"].get("quality_control", {}).get("status"), "complete")
        self.assertFalse(self.history_entry()["result_complete"])
        self.bridge.current_path = str(result)
        self.capture()
        complete = self.app.runs.record_quality_control("remarks", self.run_id, self.quality())
        self.assertEqual(complete["status"], "complete")

    def test_source_file_changed_on_disk_blocks_copy_until_original_is_recaptured(self):
        self.resolve()
        baseline_path = Path(self.load()["folder"]) / BASELINE_FILE
        original_baseline = read_json(baseline_path)
        result = self.result_file()
        self.source.write_bytes(self.source.read_bytes() + b"; source was changed on disk")
        with self.assertRaises(ValueError):
            self.record_result(path=result)
        self.assertFalse(self.history_entry()["result_complete"])
        self.assertEqual(read_json(baseline_path), original_baseline)
        self.bridge.current_path = str(self.source)
        refreshed = self.capture()
        self.assertEqual(refreshed["inventory"]["captured_document_path"], str(self.source))
        self.assertNotEqual(read_json(baseline_path), original_baseline)
        repaired = self.app.runs.record_barrier_source_decisions(
            "remarks", self.run_id, [self.decision("A1"), self.decision("A2")])
        self.assertTrue(repaired["complete"], repaired)
        self.assertTrue(self.record_result(path=result)["work_completed"])


if __name__ == "__main__":
    unittest.main()
