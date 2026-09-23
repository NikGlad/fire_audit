"""Persistent, fail-closed review workflow. Recorded claims are not CAD receipts."""
from __future__ import annotations

import copy
import hashlib
import math
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .cad_bridge import AutoCADBridge, assert_drawing, read_entities
from .control_geometry import bounds, contains, digest, geometry_anchor, compare_measurement
from .io_utils import read_json, write_json, atomic_write_text, utc_now, sha256_file
from .norm_lock import normative_update_lock, NormativeUpdateBusy


STATES = ["registered", "scope_checked", "evidence_checked", "decided", "reviewed", "graphics_verified", "verified"]
STATE_LABELS = {"registered": "Применимость нормы", "scope_checked": "Объекты и обмеры",
                "evidence_checked": "Решение", "decided": "Повторная проверка",
                "reviewed": "Графика и подтверждение", "graphics_verified": "Итоговый реестр", "verified": "Завершено",
                "awaiting_data": "Недостаточно данных"}
KINDS = {"room", "corridor", "door", "stair", "stairwell", "external_opening", "wall", "exit", "other"}


def project_digest(project):
    data = copy.deepcopy(project)
    data.pop("metadata", None)
    return digest(data)


def finding_analysis_digest(finding):
    fields = {k: finding.get(k) for k in ("source_text_id", "source_text", "norm", "applicability", "analysis", "decision", "output")}
    output = copy.deepcopy(fields.get("output") or {})
    if isinstance(output.get("non_issue_explanation"), dict):
        output["non_issue_explanation"].pop("entity_id", None)
    fields["output"] = output
    fields["measurement"] = {k: finding.get("measurement", {}).get(k) for k in
                             ("required", "method", "value", "unit", "required_value", "points", "autocad_entity_ids")}
    return digest(fields)


class ReviewControl:
    def __init__(self, app, bridge_factory=None):
        self.app = app
        self.bridge_factory = bridge_factory or self._bridge

    def _bridge(self):
        cfg = read_json(self.app.paths.root / "config/control.json", {})
        return AutoCADBridge(**{k: cfg[k] for k in ("command", "timeout") if k in cfg})

    def folder(self, slug, run_id):
        return self.app.runs.run_dir(slug, run_id)

    def load(self, slug, run_id):
        state = read_json(self.folder(slug, run_id) / "КОНТРОЛЬ.json")
        if state is None:
            raise ValueError("Это прежний запуск. Создайте новый для защищённой проверки; прежние результаты сохраняются.")
        return state

    @contextmanager
    def edit(self, slug, run_id):
        folder = self.folder(slug, run_id)
        try:
            with normative_update_lock(folder / "control.lock"):
                state = self.load(slug, run_id)
                yield state
                write_json(folder / "КОНТРОЛЬ.json", state)
        except NormativeUpdateBusy as exc:
            raise RuntimeError("Этот запуск уже обновляется в другом окне или через чат. Дождитесь завершения операции.") from exc

    @staticmethod
    def event(state, action, detail):
        state["events"].append({"at": utc_now(), "action": action, "detail": detail})

    def initialize(self, slug, run_id):
        loaded = self.app.runs.load(slug, run_id)
        run, folder = loaded["run"], Path(loaded["folder"])
        context = (folder / "ПРАВИЛА_ЗАПУСКА.md").read_text(encoding="utf-8-sig")
        if hashlib.sha256(context.encode("utf-8")).hexdigest() != run["knowledge_digest"]:
            raise ValueError("Сохранённые правила запуска не соответствуют его хешу")
        revision = {"mode": run["mode"], "digest": run["knowledge_digest"], "created_at": utc_now(),
                    "files": copy.deepcopy(run.get("knowledge_documents", [])), "context": context}
        # Immutable per-run content. Global history is content addressed, never rewritten.
        history = self.app.paths.data / "rule_versions" / (run["knowledge_digest"] + ".json")
        if not history.exists():
            write_json(history, revision)
        write_json(folder / "ВЕРСИЯ_ПРАВИЛ.json", revision)
        state = {"schema_version": 1, "project_slug": slug, "run_id": run_id,
                 "policy_digest": run["knowledge_digest"], "project_digest": project_digest(run["project_snapshot"]),
                 "inventory": [], "inventory_note": "", "snapshots": {}, "floors": [], "nodes": [],
                 "model_revision": 0, "read_receipts": [], "measurements": {}, "plans": {}, "events": [],
                 "source_disk_hash": "", "source_path": run["project_snapshot"].get("dwg", {}).get("path", ""),
                 "capabilities": {"cad_execution": "allowlist_with_local_user_approval",
                     "global_interception": False, "semantic_recognition": "requires_user_confirmation",
                     "normative_interpretation": "requires_expert_review"}}
        write_json(folder / "КОНТРОЛЬ.json", state)
        return state

    def freshness_errors(self, slug, run_id, state=None):
        state = state or self.load(slug, run_id)
        run = self.app.runs.load(slug, run_id)["run"]
        errors = []
        if not run.get("hard_constraints", {}).get("workflow_instructions_ready"):
            errors.append("Алгоритм данного вида работ ещё не утверждён")
        if self.app.knowledge.digest_for_mode(run["mode"]) != state["policy_digest"]:
            errors.append("Инструкции изменены после начала запуска. Создайте новый запуск с новой версией правил.")
        if project_digest(self.app.projects.load(slug)) != state["project_digest"]:
            errors.append("Паспорт объекта изменён после начала запуска. Создайте новый запуск с актуальными вводными.")
        return errors

    def require_fresh(self, slug, run_id, state):
        errors = self.freshness_errors(slug, run_id, state)
        if errors:
            raise ValueError("; ".join(errors))

    def capture(self, slug, run_id, handles):
        handles = [str(h).upper() for h in handles]
        with self.edit(slug, run_id) as state:
            self.require_fresh(slug, run_id, state)
            with self.bridge_factory() as client:
                info = client.call("get_drawing_info")
                assert_drawing(info, state["source_path"])
                entities = read_entities(client, handles)
                assert_drawing(client.call("get_drawing_info"), state["source_path"])
            snapshot_id = uuid4().hex
            snapshot = {"id": snapshot_id, "connector": "AUTOM8LABS", "captured_at": utc_now(),
                        "drawing": info, "entities": entities, "digest": digest(entities)}
            path = self.folder(slug, run_id) / "evidence" / (snapshot_id + ".json")
            write_json(path, snapshot)
            state["snapshots"][snapshot_id] = {"handles": handles, "digest": snapshot["digest"]}
            if not state["source_disk_hash"]:
                state["source_disk_hash"] = sha256_file(Path(state["source_path"]))
            self.event(state, "cad_capture", snapshot_id)
        return snapshot

    def snapshot(self, slug, run_id, snapshot_id, state=None):
        state = state or self.load(slug, run_id)
        if snapshot_id not in state["snapshots"]:
            raise ValueError("Нет снимка, полученного программой через AUTOM8LABS")
        snap = read_json(self.folder(slug, run_id) / "evidence" / (snapshot_id + ".json"))
        if digest(snap["entities"]) != state["snapshots"][snapshot_id]["digest"]:
            raise ValueError("Снимок геометрии изменён вручную")
        return snap

    @staticmethod
    def current(state):
        return next((i for i in state["inventory"] if i["state"] not in {"verified", "awaiting_data"}), None)

    def item(self, state, item_id, *, current=True):
        item = next((i for i in state["inventory"] if i["id"] == item_id), None)
        if not item:
            raise ValueError("Замечание не включено в перечень проверки")
        if current and self.current(state) != item:
            raise ValueError("Сначала завершите предыдущее замечание в установленном порядке")
        return item

    def register_inventory(self, slug, run_id, entries, coverage_note):
        with self.edit(slug, run_id) as state:
            self.require_fresh(slug, run_id, state)
            if state["inventory"]:
                raise ValueError("Перечень уже закреплён. Не заменяйте его: создайте отдельный запуск для нового перечня.")
            if not entries or not str(coverage_note).strip():
                raise ValueError("Нужны полный перечень и описание проверенных зон исходных замечаний")
            run = self.app.runs.load(slug, run_id)["run"]
            if run["mode"] == "compliance":
                blocks = run.get("semantic_blocks", [])
                if not blocks:
                    raise ValueError("Сначала зарегистрируйте исходные смысловые блоки через record_semantic_blocks")
                required_ids = set()
                for block in blocks:
                    for remark in block["remarks"]:
                        source_id = remark.get("source_text_id") or remark.get("entity_id")
                        if not source_id:
                            raise ValueError("У каждого замечания исходного блока нужен source_text_id объекта AutoCAD")
                        required_ids.add(source_id)
                included = {h for e in entries for h in e.get("source_ids", [])}
                if not required_ids or not required_ids.issubset(included):
                    raise ValueError("Перечень пропускает замечания из зарегистрированных смысловых блоков")
            seen, pairs, last_order = set(), set(), 0
            for entry in entries:
                for key in ("id", "title", "source_ids", "snapshot_id", "floor", "block_order"):
                    if not entry.get(key):
                        raise ValueError(f"В перечне отсутствует {key}")
                if entry["id"] in seen or not isinstance(entry["block_order"], int) or entry["block_order"] < last_order:
                    raise ValueError("Повторный id или нарушен порядок смысловых блоков")
                pair = (tuple(sorted(entry["source_ids"])), entry["floor"])
                if pair in pairs:
                    raise ValueError("Исходное замечание повторно зарегистрировано на одном этаже")
                snap = self.snapshot(slug, run_id, entry["snapshot_id"], state)
                if not set(entry["source_ids"]).issubset(snap["entities"]):
                    raise ValueError("Исходная группа не подтверждена снимком AutoCAD")
                seen.add(entry["id"]); pairs.add(pair); last_order = entry["block_order"]
                state["inventory"].append({**copy.deepcopy(entry), "state": "registered", "steps": {}, "finding_hash": ""})
            state["inventory_note"] = str(coverage_note)
            self.event(state, "inventory_registered", len(entries))
        return self.status(slug, run_id)

    def register_model(self, slug, run_id, floors, nodes):
        with self.edit(slug, run_id) as state:
            self.require_fresh(slug, run_id, state)
            if any(i["state"] not in {"registered", "scope_checked", "awaiting_data"} for i in state["inventory"]):
                raise ValueError("Модель уже использована в решениях. Для изменённой модели нужен новый запуск.")
            floor_map = {}
            for floor in floors:
                if not floor.get("id") or floor["id"] in floor_map:
                    raise ValueError("У этажей должны быть уникальные id")
                floor_map[floor["id"]] = bounds(floor["bounds"])
            from .control_geometry import overlaps
            boxes = list(floor_map.values())
            if any(overlaps(a, b) for i, a in enumerate(boxes) for b in boxes[i+1:]):
                raise ValueError("Границы планов этажей пересекаются; этаж нельзя определить однозначно")
            if not floor_map:
                raise ValueError("Не указаны границы проверяемых планов этажей")
            normalized, ids = [], set()
            for node in nodes:
                if not node.get("id") or node["id"] in ids or node.get("kind") not in KINDS:
                    raise ValueError("Повторный id или неизвестное назначение объекта")
                if node.get("floor") not in floor_map or not node.get("recognition_basis"):
                    raise ValueError("Не указаны этаж или основание распознавания объекта")
                snap = self.snapshot(slug, run_id, node["snapshot_id"], state)
                if not node.get("entity_ids"):
                    raise ValueError("Объект не привязан к геометрии")
                for h in node["entity_ids"]:
                    if h not in snap["entities"]:
                        raise ValueError("Нет фактической геометрии объекта")
                    box = bounds(snap["entities"][h]["bounds"])
                    if not contains(floor_map[node["floor"]], box[:2]) or not contains(floor_map[node["floor"]], box[2:]):
                        raise ValueError("Геометрия объекта выходит за границы указанного этажа")
                normalized.append({**copy.deepcopy(node), "confirmed_by_user": False})
                ids.add(node["id"])
            for node in normalized:
                if not set(node.get("connects_to", [])).issubset(ids):
                    raise ValueError("Связь ведёт к отсутствующему объекту модели")
            state["floors"], state["nodes"] = copy.deepcopy(floors), normalized
            state["model_revision"] += 1
            self.event(state, "model_proposed", state["model_revision"])
        return {"model_revision": state["model_revision"], "nodes": normalized}

    def confirm_model_from_ui(self, slug, run_id, revision):
        # Deliberately absent from MCP: a model cannot certify its own semantic recognition.
        with self.edit(slug, run_id) as state:
            self.require_fresh(slug, run_id, state)
            if state["model_revision"] != revision or not state["nodes"]:
                raise ValueError("Модель изменилась или не заполнена; откройте её заново")
            for node in state["nodes"]:
                node["confirmed_by_user"] = True
            self.event(state, "model_confirmed_in_gui", revision)

    def read_source(self, slug, run_id, item_id, relative_path, page, offset=0, max_chars=20000):
        with self.edit(slug, run_id) as state:
            self.require_fresh(slug, run_id, state)
            item = self.item(state, item_id)
            if not isinstance(page, int) or page < 1:
                raise ValueError("Укажите конкретную страницу/часть источника")
            result = self.read_document(relative_path, page, offset, max_chars)
            if result.get("source_matches_index") is not True or not result.get("full_text_complete"):
                raise ValueError("Источник изменился или прочитан не полностью; обновите индекс и проверьте оригинал")
            receipt_id = uuid4().hex
            receipt = {"id": receipt_id, "item_id": item_id, "relative_path": relative_path,
                "source_sha256": result["source_sha256"], "page": page, "offset": result["offset"],
                "end": result["offset"] + len(result["text"]), "total": result["total_chars"],
                "text_hash": digest(result["text"]), "at": utc_now(),
                "pass": 2 if item["state"] == "decided" else 1,
                "visual_review_required": result.get("visual_review_required", True)}
            state["read_receipts"].append(receipt)
            write_json(self.folder(slug, run_id) / "sources" / (receipt_id + ".json"), result)
        return {**result, "reading_receipt_id": receipt_id,
                "warning": "Журнал доказывает получение текста, но не правильность его толкования"}

    def read_document(self, relative_path, page, offset=0, max_chars=20000):
        if relative_path.startswith("knowledge/"):
            if page != 1 or offset < 0:
                raise ValueError("У файла инструкции одна текстовая часть; offset должен быть неотрицательным")
            doc = self.app.knowledge.load_document(relative_path[len("knowledge/"):])
            length = max(1000, min(int(max_chars), 40000))
            text = doc.text[offset:offset+length]
            return {"relative_path": relative_path, "source_sha256": doc.sha256, "source_matches_index": True,
                    "source_kind": "user_instruction_not_normative_document", "full_text_complete": True,
                    "visual_review_required": False, "text": text, "offset": offset, "total_chars": len(doc.text),
                    "next_offset": offset+len(text) if offset+len(text) < len(doc.text) else None}
        return self.app.norms.read_document(relative_path, page, offset, max_chars)

    def reading_errors(self, state, item, pass_number):
        errors = []
        required = item["steps"].get("scope_checked", {}).get("required_sources", [])
        if not required:
            return ["Не указан относящийся нормативный контекст со страницами"]
        for doc in required:
            if not doc.get("pages") or not doc.get("relative_path"):
                errors.append("Для обязательного источника не указан путь или страницы")
            for page in doc.get("pages", []):
                receipts = [r for r in state["read_receipts"] if r["item_id"] == item["id"] and
                            r["relative_path"] == doc["relative_path"] and r["page"] == page and r["pass"] == pass_number]
                end = 0
                for r in sorted(receipts, key=lambda x: x["offset"]):
                    if r["offset"] > end:
                        break
                    end = max(end, r["end"])
                if not receipts or end < receipts[0]["total"] or len({r["source_sha256"] for r in receipts}) != 1:
                    errors.append(f"Не получена вся страница {page}: {doc['relative_path']} (проход {pass_number})")
                elif pass_number == 2:
                    first = {r["source_sha256"] for r in state["read_receipts"] if r["item_id"] == item["id"]
                             and r["relative_path"] == doc["relative_path"] and r["pass"] == 1}
                    if receipts[0]["source_sha256"] not in first:
                        errors.append("Редакция документа изменилась между проходами проверки")
        return errors

    def measure(self, slug, run_id, item_id, snapshot_id, anchors, purpose):
        with self.edit(slug, run_id) as state:
            self.require_fresh(slug, run_id, state)
            item = self.item(state, item_id)
            if item["state"] != "scope_checked" or len(anchors) != 2 or not str(purpose).strip():
                raise ValueError("Обмер выполняется после проверки применимости; нужны две фактические границы и цель")
            snap = self.snapshot(slug, run_id, snapshot_id, state)
            points, node_ids = [], []
            for anchor in anchors:
                node = next((n for n in state["nodes"] if n["id"] == anchor["node_id"]), None)
                if not node or not node["confirmed_by_user"] or node["floor"] != item["floor"]:
                    raise ValueError("Не подтверждены назначение объекта или нужный этаж")
                if anchor["handle"] not in node["entity_ids"]:
                    raise ValueError("Граница не относится к выбранному объекту")
                points.append(geometry_anchor(snap["entities"][anchor["handle"]], anchor["point_path"]))
                node_ids.append(node["id"])
            if abs(points[0][2] - points[1][2]) > 1:
                raise ValueError("Точки не лежат в одной горизонтальной плоскости")
            value = math.dist(points[0], points[1])
            if value <= 0:
                raise ValueError("Нулевой обмер не подтверждает фактическую ширину/расстояние")
            measure_id = uuid4().hex
            measure = {"id": measure_id, "item_id": item_id, "floor": item["floor"], "snapshot_id": snapshot_id,
                       "anchors": anchors, "points": points, "node_ids": node_ids,
                       "value_mm": value, "purpose": purpose, "method": "actual_geometry_segment", "at": utc_now()}
            state["measurements"][measure_id] = measure
            self.event(state, "measured", measure_id)
        return measure

    def require_sources_current(self, state, item):
        for doc in item["steps"]["scope_checked"]["required_sources"]:
            result = self.read_document(doc["relative_path"], doc["pages"][0], 0, 1000)
            hashes = {r["source_sha256"] for r in state["read_receipts"]
                      if r["item_id"] == item["id"] and r["relative_path"] == doc["relative_path"]}
            if result.get("source_matches_index") is not True or hashes != {result["source_sha256"]}:
                raise ValueError("Нормативный источник изменился после анализа; требуется новая проверка редакции")

    def advance(self, slug, run_id, item_id, next_state, data):
        with self.edit(slug, run_id) as state:
            self.require_fresh(slug, run_id, state)
            item = self.item(state, item_id)
            if next_state not in {"scope_checked", "evidence_checked", "decided", "reviewed"} or item["state"] != STATES[STATES.index(next_state)-1]:
                raise ValueError("Пропуск этапа запрещён. Завершение графики подтверждается только независимой сверкой.")
            data = copy.deepcopy(data)
            if next_state == "scope_checked":
                for key in ("document", "point", "applicability_reason", "scope", "exceptions", "edition_basis", "required_sources"):
                    if not data.get(key):
                        raise ValueError(f"Не заполнен индивидуальный анализ: {key}")
                if data.get("applicability") not in {"applicable", "not_applicable", "uncertain"}:
                    raise ValueError("Не установлена применимость нормы")
                item["steps"][next_state] = data
                errors = self.reading_errors(state, item, 1)
                if errors:
                    raise ValueError("; ".join(errors))
            elif next_state == "evidence_checked":
                if not isinstance(data.get("measurement_required"), bool) or not data.get("basis"):
                    raise ValueError("Нужны основание доказательств и решение о необходимости обмера")
                if not data.get("node_ids") and not data.get("no_geometry_reason"):
                    raise ValueError("Не указаны объекты проверки или причина неприменимости обмера")
                for node_id in data.get("node_ids", []):
                    node = next((n for n in state["nodes"] if n["id"] == node_id), None)
                    if not node or not node["confirmed_by_user"] or node["floor"] != item["floor"]:
                        raise ValueError("Не подтверждены назначение и этаж каждого объекта проверки")
                    kinds = item["steps"]["scope_checked"].get("expected_node_kinds", [])
                    if kinds and node["kind"] not in kinds:
                        raise ValueError("Назначение объекта не соответствует предмету требования (например, дверь вместо лестницы)")
                if data["measurement_required"] and not data.get("measurement_ids"):
                    raise ValueError("Нет фактически выполненного программой обмера")
                for mid in data.get("measurement_ids", []):
                    if state["measurements"].get(mid, {}).get("item_id") != item_id:
                        raise ValueError("Обмер отсутствует или относится к другому замечанию")
                    if not set(state["measurements"][mid]["node_ids"]).issubset(data.get("node_ids", [])):
                        raise ValueError("Измеренные объекты не включены в доказательства замечания")
            elif next_state == "decided":
                if data.get("status") not in {"violation", "compliant", "not_applicable", "general_requirement", "query"} or not data.get("reason"):
                    raise ValueError("Не задано обоснованное решение")
                scope = item["steps"]["scope_checked"]["applicability"]
                if scope == "uncertain" or (scope == "not_applicable" and data["status"] != "not_applicable"):
                    raise ValueError("Нельзя выдавать требование при неопределённой/отсутствующей применимости")
                if data["status"] == "not_applicable" and scope != "not_applicable":
                    raise ValueError("Решение о неприменимости противоречит проверенной области действия")
                mids = item["steps"]["evidence_checked"].get("measurement_ids", [])
                if mids and data["status"] in {"violation", "compliant"}:
                    results = [compare_measurement(state["measurements"][m]["value_mm"], data.get("limit_mm"), data.get("violation_when")) for m in mids]
                    if (data["status"] == "violation") != any(results):
                        raise ValueError("Решение противоречит фактическому обмеру и указанному нормативному пределу")
                if data["status"] in {"general_requirement", "query"} and not data.get("explicit_rule_basis"):
                    raise ValueError("Общее требование/запрос нуждается в конкретном основании инструкции")
            elif next_state == "reviewed":
                errors = self.reading_errors(state, item, 2)
                if errors or not data.get("reason") or data.get("visual_tables_checked") is not True:
                    raise ValueError("; ".join(errors or ["Не завершена повторная проверка контекста, таблиц и исключений"]))
            item["steps"][next_state] = data
            item["state"] = next_state
            self.event(state, "advance", {"item": item_id, "state": next_state})
        return self.status(slug, run_id)

    def defer(self, slug, run_id, item_id, missing_data, request_text):
        with self.edit(slug, run_id) as state:
            self.require_fresh(slug, run_id, state)
            item = self.item(state, item_id)
            if not str(missing_data).strip() or not str(request_text).strip():
                raise ValueError("Укажите недостающие данные и конкретный запрос")
            item.update(state="awaiting_data", missing_data=missing_data, request_text=request_text)
            self.event(state, "deferred", item_id)
        return self.status(slug, run_id)

    def status(self, slug, run_id):
        state = self.load(slug, run_id)
        current = self.current(state)
        errors = self.freshness_errors(slug, run_id, state)
        if not state["inventory"]:
            errors.append("Не зарегистрирован полный перечень исходных замечаний")
        registry = self.app.runs.load(slug, run_id)["registry"]
        findings = {f["id"]: f for f in registry.get("findings", []) if f.get("id")}
        for item in state["inventory"]:
            if item["state"] in {"verified", "graphics_verified"}:
                finding = findings.get(item["id"])
                if not finding:
                    errors.append(f"{item['id']}: не заполнен полный реестр record_finding")
                elif item.get("finding_hash") and finding_analysis_digest(finding) != item["finding_hash"]:
                    errors.append(f"{item['id']}: результат в реестре изменён после сверки графики")
                elif finding:
                    scope = item["steps"]["scope_checked"]
                    if any(str(finding.get("norm", {}).get(k, "")) != str(scope.get(k, "")) for k in ("document", "point")):
                        errors.append(f"{item['id']}: в полном реестре указана другая норма")
                    expected = item["steps"]["decided"]["status"]
                    if expected in {"violation", "compliant", "not_applicable"} and finding.get("decision", {}).get("status") != expected:
                        errors.append(f"{item['id']}: решение реестра противоречит проверенным доказательствам")
                    plan = state["plans"].get(item.get("verified_plan"), {})
                    actual = plan.get("actual", {})
                    listed = finding.get("autocad", {}).get("created_objects", [])
                    if {r.get("entity_id") for r in listed} != set(actual):
                        errors.append(f"{item['id']}: созданные объекты реестра не совпадают с независимой сверкой AutoCAD")
                    for row in listed:
                        obj = actual.get(row.get("entity_id"), {})
                        if row.get("layer") != obj.get("layer") or row.get("entity_type") != obj.get("type"):
                            errors.append(f"{item['id']}: свойства объекта {row.get('entity_id')} не соответствуют фактическим")
        unfinished = [i["id"] for i in state["inventory"] if i["state"] != "verified"]
        return {"ok": not errors and not unfinished, "errors": errors, "unfinished": unfinished,
                "total": len(state["inventory"]), "verified": len(state["inventory"]) - len(unfinished),
                "next_item": current, "waiting_for_data": [i for i in state["inventory"] if i["state"] == "awaiting_data"],
                "policy_digest": state["policy_digest"], "model_revision": state["model_revision"],
                "capabilities": state["capabilities"],
                "instruction": "Работать только с next_item. Не менять DWG напрямую в обход плана действий. "
                               "Файлы правил и паспорт закреплены; нормы читать через read_requirement_source."}

    def item_details(self, slug, run_id, item_id):
        state = self.load(slug, run_id)
        item = self.item(state, item_id, current=False)
        plan = state["plans"].get(item.get("verified_plan"))
        return {"item": item, "measurements": [m for m in state["measurements"].values() if m["item_id"] == item_id],
                "cad_verification": plan, "reading_receipts": [r for r in state["read_receipts"] if r["item_id"] == item_id]}

    def finalize(self, slug, run_id, item_id):
        from .validation import validate_finding
        with self.edit(slug, run_id) as state:
            self.require_fresh(slug, run_id, state)
            item = self.item(state, item_id)
            if item["state"] != "graphics_verified":
                raise ValueError("Сначала требуется независимая сверка фактически созданной графики")
            self.require_sources_current(state, item)
            errors = self.status(slug, run_id)["errors"]
            finding = next((f for f in self.app.runs.load(slug, run_id)["registry"].get("findings", []) if f.get("id") == item_id), None)
            if not finding:
                errors.append("Не заполнен полный реестр")
            else:
                errors.extend(validate_finding(finding, for_application=True)[0])
            if errors:
                raise ValueError("; ".join(errors))
            item["state"] = "verified"
            self.event(state, "item_finalized", item_id)
        return self.status(slug, run_id)
