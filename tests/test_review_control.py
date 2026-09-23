"""Synthetic fixtures only; no live AutoCAD operations and no normative certification."""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fire_audit.core import FireAuditApp
from fire_audit.cad_bridge import AutoCADBridge, unwrap
from fire_audit.control_geometry import bounds, compare_measurement, geometry_anchor, ray_origin, digest
from fire_audit.cad_guard import verify_created_geometry
from fire_audit.io_utils import read_json, write_json
from fire_audit.quality import compare_case
from mcp_server import MCPServer, TOOLS

ROOT = Path(__file__).resolve().parent.parent


def entity(handle, x=0, y=0, kind="Line"):
    return {"handle": handle, "type": kind, "layer": "project", "colorIndex": 7,
            "linetype": "ByLayer", "lineWeight": "ByLayer", "startPoint": {"x": x, "y": y, "z": 0},
            "endPoint": {"x": x, "y": y+10, "z": 0}}


class FakeCAD:
    def __init__(self, path):
        self.path = str(path)
        self.entities = {"A": entity("A", -100, 0, "MText"), "B": entity("B", 0, 0), "C": entity("C", 1200, 0)}
        self.writes = []
        self.fail_write = False

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def call(self, name, args=None):
        args = args or {}
        if name == "get_drawing_info":
            return {"path": self.path, "units": "Millimeters", "entityCount": len(self.entities)}
        if name == "get_entities":
            return {"entities": [], "hasMore": False}
        hs = args.get("handles", [])
        if name == "get_entity_properties":
            return {"entities": [copy.deepcopy(self.entities[h]) for h in hs if h in self.entities]}
        if name == "get_bounding_box":
            e = self.entities[hs[0]]
            a, b = e["startPoint"], e["endPoint"]
            return {"minX": min(a["x"], b["x"]), "maxX": max(a["x"], b["x"])+10,
                    "minY": min(a["y"], b["y"]), "maxY": max(a["y"], b["y"])}
        raise ValueError(name)

    def call_write(self, name, args):
        self.writes.append((name, args))
        if self.fail_write:
            raise TimeoutError("synthetic uncertain result")
        if name == "change_entity_properties":
            for h in args["handles"]:
                self.entities[h].update(layer=args["layer"], colorIndex=args["color"], linetype=args["linetype"], lineWeight="ByLayer")
            return {"success": True}
        if name == "copy_entities":
            created = []
            for old in args["handles"]:
                h = format(100+len(self.entities), "X")
                e = copy.deepcopy(self.entities[old]); e["handle"] = h
                for p in ("startPoint", "endPoint"):
                    e[p]["x"] += args["deltaX"]; e[p]["y"] += args["deltaY"]
                self.entities[h] = e; created.append(h)
            return {"newHandles": created}
        h = format(100 + len(self.entities), "X")
        self.entities[h] = entity(h, args.get("x", 0), args.get("y", 0), "MText")
        if name == "create_line":
            self.entities[h] = entity(h, args["startX"], args["startY"])
            self.entities[h]["endPoint"] = {"x": args["endX"], "y": args["endY"], "z": 0}
        if name == "create_dimension_aligned":
            import math
            self.entities[h] = entity(h, min(args["x1"],args["x2"]), min(args["y1"],args["y2"]), "AlignedDimension")
            self.entities[h]["endPoint"] = {"x": max(args["x1"],args["x2"]), "y": args["dimLineY"], "z": 0}
            self.entities[h].update(measurement=math.hypot(args["x2"]-args["x1"],args["y2"]-args["y1"]),
                xLine1Point={"x":args["x1"],"y":args["y1"],"z":0}, xLine2Point={"x":args["x2"],"y":args["y2"],"z":0})
        return {"handle": h}


class FakeNorms:
    def __init__(self): self.changed = False
    def read_document(self, path, page, offset, max_chars):
        text = "Synthetic context. Not an actual legal source."
        return {"relative_path": path, "source_sha256": "changed" if self.changed else "sample-hash",
                "source_matches_index": True, "full_text_complete": True, "visual_review_required": False,
                "offset": offset, "text": text[offset:offset+max_chars], "total_chars": len(text),
                "next_offset": offset+max_chars if offset+max_chars < len(text) else None}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("knowledge", "config", "templates"):
            shutil.copytree(ROOT/name, self.root/name)
        self.app = FireAuditApp(self.root)
        p = self.app.projects.create("Синтетический тест", "test")
        p["dates"]["gpzu"] = "2026-04-23"
        self.dwg = self.root / "input.dwg"
        self.dwg.write_bytes(b"synthetic fixture, not DWG")
        p["dwg"]["path"] = str(self.dwg)
        self.app.projects.save("test", p)
        self.run = self.app.runs.create("test", "compliance")["run"]["run_id"]
        self.cad = FakeCAD(self.dwg)
        self.app.control.bridge_factory = lambda: self.cad
        self.app.norms = FakeNorms()
        self.c = self.app.control

    def tearDown(self): self.temp.cleanup()

    def setup_inventory(self, two=False):
        self.snap = self.c.capture("test", self.run, ["A", "B", "C"])["id"]
        entries = [{"id": "r1", "title": "Synthetic width", "source_ids": ["A"], "floor": "1", "snapshot_id": self.snap, "block_order": 1}]
        if two:
            entries.append({**entries[0], "id": "r2", "floor": "2", "block_order": 2})
        self.app.runs.record_semantic_blocks("test", self.run, [{"title": "Synthetic block", "remarks": [{"source_text_id": "A"}]}])
        self.c.register_inventory("test", self.run, entries, "Synthetic complete inventory")

    def read(self):
        return self.c.read_source("test", self.run, "r1", "synthetic.txt", 1)

    def scope(self):
        self.read()
        self.c.advance("test", self.run, "r1", "scope_checked", {"document": "SYNTHETIC", "point": "test",
            "applicability": "applicable", "applicability_reason": "Synthetic case", "scope": "door",
            "exceptions": "None in synthetic case", "edition_basis": "test-only",
            "expected_node_kinds": ["door"], "required_sources": [{"relative_path": "synthetic.txt", "pages": [1]}]})

    def model(self, kind="door", confirm=True):
        nodes = [{"id": "door", "kind": kind, "floor": "1", "entity_ids": ["B", "C"], "snapshot_id": self.snap,
                  "recognition_basis": "Synthetic labelled geometry"}]
        self.c.register_model("test", self.run, [{"id": "1", "bounds": [-10, -10, 1300, 100]}], nodes)
        if confirm:
            self.c.confirm_model_from_ui("test", self.run, 1)

    def measured(self):
        return self.c.measure("test", self.run, "r1", self.snap, [
            {"node_id": "door", "handle": h, "point_path": "startPoint"} for h in ("B", "C")], "clear distance")

    def evidence(self, measured=True):
        mids = [self.measured()["id"]] if measured else []
        return self.c.advance("test", self.run, "r1", "evidence_checked", {
            "node_ids": ["door"], "measurement_required": measured, "measurement_ids": mids, "basis": "synthetic boundaries"})

    def reviewed(self):
        self.setup_inventory(); self.scope(); self.model(); self.evidence()
        self.c.advance("test", self.run, "r1", "decided", {"status": "compliant", "reason": "equal to synthetic minimum",
            "limit_mm": 1200, "violation_when": "below"})
        self.read()
        self.c.advance("test", self.run, "r1", "reviewed", {"reason": "independent synthetic recheck", "visual_tables_checked": True})
        self.full_finding("compliant")

    def full_finding(self, status):
        from test_fire_audit import FireAuditTestCase
        f = read_json(self.root/"templates/finding_template.json")
        f.update(id="r1", source_text="Synthetic example, not normative text")
        f["norm"].update(document="SYNTHETIC", point="test")
        f["decision"]["status"] = status
        FireAuditTestCase.set_complete_individual_analysis(self, f)
        f["source_text_id"] = "A"
        f["analysis"]["checked_autocad_entity_ids"] = ["B", "C"]
        f["analysis"]["source_reading"]["required_documents"][0].update(relative_path="synthetic.txt", source_sha256="sample-hash")
        if status == "compliant":
            f["output"]["non_issue_explanation"].update(text="No violation: synthetic case", green=True, placement="next_to_source")
        self.app.runs.add_finding("test",self.run,f)

    def non_issue_plan(self):
        return self.app.cad_guard.prepare("test", self.run, "r1", [{"id": "note", "tool": "create_mtext", "role": "non_issue_explanation",
              "args": {"text": "{\\C3;No violation: synthetic case}", "x": -200, "y": 0,
                       "height": 2, "width": 50, "style": "Standard", "layer": "экспертиза"}}], "Synthetic no-issue explanation")

    def test_rules_are_frozen_and_changed_rules_block(self):
        frozen = (self.c.folder("test", self.run)/"ПРАВИЛА_ЗАПУСКА.md").read_text(encoding="utf-8")
        with (self.root/"knowledge/05_ОФОРМЛЕНИЕ_AUTOCAD.md").open("a", encoding="utf-8") as f: f.write("\nchange\n")
        self.assertTrue(self.c.freshness_errors("test", self.run))
        self.assertEqual(frozen, (self.c.folder("test", self.run)/"ПРАВИЛА_ЗАПУСКА.md").read_text(encoding="utf-8"))

    def test_rule_replacement_keeps_history_and_excludes_old_rule(self):
        rules = [{"id":"old","status":"active","modes":["compliance"],"replaces":[],"text":"OLD_ONLY_RULE"},
                 {"id":"new","status":"active","modes":["compliance"],"replaces":["old"],"text":"NEW_ONLY_RULE"}]
        write_json(self.root/"knowledge/rule_overrides.json", {"rules":rules})
        context = self.app.knowledge.context_for_mode("compliance")
        self.assertNotIn("OLD_ONLY_RULE", context)
        self.assertIn("NEW_ONLY_RULE", context)
        self.assertNotIn("NEW_ONLY_RULE", self.app.knowledge.context_for_mode("stage"))
        rules[0]["replaces"] = ["new"]
        write_json(self.root/"knowledge/rule_overrides.json", {"rules":rules})
        with self.assertRaises(ValueError): self.app.knowledge.context_for_mode("compliance")

    def test_metadata_only_does_not_invalidate_passport(self):
        p = self.app.projects.load("test"); p["metadata"]["updated_at"] = "new"
        write_json(self.app.projects.project_dir("test")/"project.json", p)
        self.assertEqual(self.c.freshness_errors("test", self.run), [])

    def test_changed_passport_blocks(self):
        p = self.app.projects.load("test"); p["name"] = "Changed input"
        self.app.projects.save("test", p)
        with self.assertRaises(ValueError): self.c.capture("test", self.run, ["A"])

    def test_empty_inventory_not_complete(self):
        self.assertFalse(self.c.status("test", self.run)["ok"])
        self.assertFalse(self.app.runs.validate("test", self.run, True)["ok"])

    def test_snapshot_must_be_real_receipt_and_correct_document(self):
        self.cad.path = str(self.root/"other.dwg")
        with self.assertRaises(ValueError): self.setup_inventory()
        self.assertFalse(self.cad.writes)

    def test_missing_handle_fails(self):
        with self.assertRaises(ValueError): self.c.capture("test", self.run, ["MISSING"])

    def test_no_skipping_steps_or_remarks(self):
        self.setup_inventory(two=True)
        with self.assertRaises(ValueError): self.c.advance("test", self.run, "r1", "decided", {})
        with self.assertRaises(ValueError): self.c.read_source("test", self.run, "r2", "synthetic.txt", 1)

    def test_missing_source_remark_cannot_be_hidden_by_inventory(self):
        sid = self.c.capture("test", self.run, ["A"])["id"]
        self.app.runs.record_semantic_blocks("test", self.run, [{"title": "B", "remarks": [{"source_text_id": "A"}, {"source_text_id": "omitted"}]}])
        with self.assertRaises(ValueError):
            self.c.register_inventory("test", self.run, [{"id": "r1", "title": "missing", "source_ids": ["A"], "floor": "1", "snapshot_id": sid, "block_order": 1}], "Declared complete")

    def test_defer_does_not_claim_completion(self):
        self.setup_inventory(two=True)
        status = self.c.defer("test", self.run, "r1", "Missing occupancy", "Please confirm occupancy")
        self.assertEqual(status["next_item"]["id"], "r2")
        self.assertFalse(status["ok"])

    def test_partial_norm_read_fails(self):
        self.setup_inventory()
        self.c.read_source("test", self.run, "r1", "synthetic.txt", 1, offset=10)
        item = self.c.load("test", self.run)["inventory"][0]
        item["steps"]["scope_checked"] = {"required_sources": [{"relative_path": "synthetic.txt", "pages": [1]}]}
        self.assertTrue(self.c.reading_errors(self.c.load("test", self.run), item, 1))

    def test_measurement_requires_confirmed_semantics(self):
        self.setup_inventory(); self.scope(); self.model(confirm=False)
        with self.assertRaises(ValueError): self.measured()

    def test_stair_requirement_cannot_use_door(self):
        self.setup_inventory(); self.scope(); self.model(kind="stair")
        with self.assertRaises(ValueError): self.evidence(measured=False)

    def test_real_geometry_value_and_equality(self):
        self.setup_inventory(); self.scope(); self.model()
        self.assertEqual(self.measured()["value_mm"], 1200)
        self.evidence()
        with self.assertRaises(ValueError):
            self.c.advance("test", self.run, "r1", "decided", {"status": "violation", "reason": "wrong",
                "limit_mm": 1200, "violation_when": "below"})

    def test_second_pass_requires_actual_new_read(self):
        self.setup_inventory(); self.scope(); self.model(); self.evidence()
        self.c.advance("test", self.run, "r1", "decided", {"status": "compliant", "reason": "equal", "limit_mm": 1200, "violation_when": "below"})
        with self.assertRaises(ValueError):
            self.c.advance("test", self.run, "r1", "reviewed", {"reason": "claim only", "visual_tables_checked": True})

    def test_stale_geometry_blocks_plan(self):
        self.reviewed(); self.cad.entities["B"]["startPoint"]["x"] = 100
        with self.assertRaises(ValueError): self.non_issue_plan()
        self.assertFalse(self.cad.writes)

    def test_stale_norm_blocks_plan(self):
        self.reviewed(); self.app.norms.changed = True
        with self.assertRaises(ValueError): self.non_issue_plan()

    def test_plan_does_not_execute_until_gui_confirmation(self):
        self.reviewed(); plan = self.non_issue_plan()
        self.assertFalse(self.cad.writes)
        result = self.app.cad_guard.execute_from_ui("test", self.run, plan["id"], plan["hash"])
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.cad.writes)
        self.assertFalse(self.app.runs.validate("test", self.run, True)["ok"], "Full finding registry is still mandatory")
        self.assertEqual(self.c.status("test",self.run)["next_item"]["state"], "graphics_verified")
        with self.assertRaises(ValueError): self.c.finalize("test",self.run,"r1")

    def test_finalize_requires_and_accepts_full_independent_graphics_registry(self):
        self.reviewed(); plan = self.non_issue_plan()
        result = self.app.cad_guard.execute_from_ui("test", self.run, plan["id"], plan["hash"])
        self.assertTrue(result["ok"],result)
        f=self.app.runs.load("test",self.run)["registry"]["findings"][0]
        h=result["created"][0]["handle"]
        f["autocad"].update(floor="1",status="post_checked",overlap_checked=True,source_entity_ids=["A"],
            created_objects=[{"entity_id":h,"entity_type":"MText","role":"main_text","floor":"1",
                "related_finding_id":"r1","purpose":"green non-issue explanation","layer":"экспертиза",
                "color_by_layer":True,"linetype_by_layer":True,"lineweight_by_layer":True,"text_style":"Standard"}])
        f["output"]["non_issue_explanation"]["entity_id"]=h
        self.app.runs.add_finding("test",self.run,f)
        result=self.c.finalize("test",self.run,"r1")
        self.assertTrue(result["ok"],result)
        self.assertIsNone(result["next_item"])

    def test_preflight_rejects_wrong_floor_before_writes(self):
        self.reviewed()
        state=self.c.load("test",self.run)
        item=state["inventory"][0]
        with self.assertRaises(ValueError):
            self.app.cad_guard.preflight(state,item,[{"id":"ray","tool":"create_line","role":"ray","target":"B","target_point":[99999,99999],"args":{"layer":"экспертиза"}}],
                 self.c.snapshot("test",self.run,self.snap)["entities"],state["floors"][0])
        self.assertFalse(self.cad.writes)

    def test_violation_copy_dimension_and_ray_end_to_end(self):
        self.cad.entities["C"]["startPoint"]["x"] = 1190
        self.cad.entities["C"]["endPoint"]["x"] = 1190
        self.cad.entities.update(D=entity("D", -100, 20), E=entity("E", -90, 20, "MText"))
        self.snap = self.c.capture("test", self.run, ["A", "B", "C", "D", "E"])["id"]
        self.app.runs.record_semantic_blocks("test", self.run, [{"title": "Synthetic block", "remarks": [{"source_text_id": "A"}]}])
        self.c.register_inventory("test", self.run, [{"id":"r1","title":"Synthetic","source_ids":["A","D","E"],"floor":"1","snapshot_id":self.snap,"block_order":1}], "test")
        self.scope(); self.model(); self.evidence()
        self.c.advance("test",self.run,"r1","decided", {"status":"violation","reason":"synthetic minimum","limit_mm":1200,"violation_when":"below"})
        self.read(); self.c.advance("test",self.run,"r1","reviewed",{"reason":"synthetic check","visual_tables_checked":True})
        self.full_finding("violation")
        mid = self.c.load("test",self.run)["inventory"][0]["steps"]["evidence_checked"]["measurement_ids"][0]
        actions = [{"id":h,"tool":"copy_entities","role":r,"args":{"handles":[h],"deltaX":-100,"deltaY":0}}
                   for h,r in (("A","remark_text"),("D","frame"),("E","number"))]
        actions += [{"id":"dim","tool":"create_dimension_aligned","role":"dimension","measurement_id":mid,
                     "args":{"x1":0,"y1":0,"x2":1190,"y2":0,"dimLineX":595,"dimLineY":50,"style":"Standard","layer":"экспертиза"}},
                    {"id":"ray","tool":"create_line","role":"ray","target":"dim","args":{"layer":"экспертиза"}}]
        plan=self.app.cad_guard.prepare("test",self.run,"r1",actions,"synthetic violation")
        result=self.app.cad_guard.execute_from_ui("test",self.run,plan["id"],plan["hash"])
        self.assertTrue(result["ok"],result)
        self.assertEqual(len(result["created"]),5)
        ray = next(args for name,args in self.cad.writes if name=="create_line")
        self.assertEqual([ray["endX"],ray["endY"]],[595,50])
        self.assertEqual(ray["startX"],-180)

    def test_actual_ray_geometry_is_checked_not_claimed(self):
        with self.assertRaises(ValueError):
            verify_created_geometry(entity("X", 999,999), "create_line", {"startX":0,"startY":0,"endX":10,"endY":0})

    def test_no_mcp_approval_or_execution_tool(self):
        names = {t["name"] for t in TOOLS}
        self.assertNotIn("prepare_cad_plan", names)
        self.assertNotIn("prepare_barrier_plan", names)
        self.assertIn("record_quality_control", names)
        self.assertNotIn("execute_from_ui", names)
        self.assertNotIn("confirm_model_from_ui", names)
        with self.assertRaises(ValueError): MCPServer(self.root).dispatch_tool("execute_from_ui", {})

    def test_deletion_and_global_replacement_rejected(self):
        self.reviewed()
        for tool in ("erase_entities", "replace_text", "save_drawing", "execute_lisp"):
            with self.assertRaises(ValueError):
                self.app.cad_guard.prepare("test", self.run, "r1", [{"id": "x", "tool": tool, "role": "ray", "args": {}}], "bad")

    def test_non_violation_ray_forbidden(self):
        self.reviewed()
        with self.assertRaises(ValueError):
            self.app.cad_guard.prepare("test", self.run, "r1", [{"id": "x", "tool": "create_line", "role": "ray", "target": "B", "args": {"layer": "экспертиза"}}], "wrong ray")

    def test_unknown_outcome_is_not_retried(self):
        self.reviewed(); plan = self.non_issue_plan(); self.cad.fail_write = True
        result = self.app.cad_guard.execute_from_ui("test", self.run, plan["id"], plan["hash"])
        self.assertFalse(result["ok"])
        with self.assertRaises(ValueError): self.app.cad_guard.execute_from_ui("test", self.run, plan["id"], plan["hash"])
        self.assertEqual(len(self.cad.writes), 1)

    def test_changed_plan_hash_is_rejected(self):
        self.reviewed(); plan = self.non_issue_plan()
        with self.assertRaises(ValueError): self.app.cad_guard.execute_from_ui("test", self.run, plan["id"], "wrong-hash")
        self.assertFalse(self.cad.writes)

    def test_geometry_math_rejects_invalid_inputs(self):
        for number in (float("nan"), float("inf"), True):
            with self.assertRaises(ValueError): compare_measurement(number, 1200, "below")
        with self.assertRaises(ValueError): geometry_anchor({"center": {"x": 0, "y": 0}}, "center")
        self.assertEqual(bounds({"minX": 1, "minY": 2, "maxX": 3, "maxY": 4}), [1, 2, 3, 4])
        self.assertEqual(ray_origin([0, 0, 10, 10], [[20, 5], [30, 5]]), [10, 5, 0])

    def test_connector_nested_error_is_not_success(self):
        with self.assertRaises(RuntimeError): unwrap({"structuredContent": {"result": {"error": "No valid entities"}}})

    def test_expert_tests_not_fabricated(self):
        report = self.app.quality.run()
        self.assertTrue(report["software_ok"])
        self.assertFalse(report["expert_coverage_available"])
        self.assertFalse(report["expert_cases_ok"])
        case = {"id": "x", "expected": {"decision": "compliant", "floor": "1", "target_ids": []}}
        self.assertFalse(compare_case(case, {"decision": "violation", "floor": "2", "target_ids": ["wrong"]})["ok"])


if __name__ == "__main__": unittest.main()
