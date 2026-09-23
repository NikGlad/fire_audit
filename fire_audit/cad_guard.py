"""Small allowlist executor. No arbitrary commands, deletes, saves or global text replacement."""
from __future__ import annotations

import copy
import math
from pathlib import Path
from uuid import uuid4

from .cad_bridge import assert_drawing, read_entities
from .control_geometry import bounds, contains, digest, finite, overlaps, point, properties_ok, ray_origin, union
from .io_utils import sha256_file, utc_now, write_json
from .control import finding_analysis_digest
from .validation import _validate_individual_analysis, validate_source_reading


TOOLS = {
    "copy_entities": {"handles", "deltaX", "deltaY", "deltaZ", "count"},
    "create_line": {"startX", "startY", "startZ", "endX", "endY", "endZ", "layer", "linetype"},
    "create_dimension_aligned": {"x1", "y1", "x2", "y2", "dimLineX", "dimLineY", "style", "layer"},
    "create_polyline": {"vertices", "closed", "layer", "linetype"},
    "create_mtext": {"text", "x", "y", "z", "height", "width", "style", "layer", "attachment"},
}
GROUP_ROLES = {"remark_text", "number", "frame", "purple_explanation"}
ROLES = GROUP_ROLES | {"dimension", "ray", "measurement_line", "cloud", "non_issue_explanation"}


def handles_from_result(result):
    if not isinstance(result, dict):
        raise ValueError("Не получены handles созданных объектов")
    handles = result.get("newHandles", result.get("handles"))
    if handles is None and result.get("handle"):
        handles = [result["handle"]]
    if not isinstance(handles, list) or not handles or any(not isinstance(h, str) or not h for h in handles):
        raise ValueError("Неизвестный формат handles после создания. Повторять операцию запрещено.")
    return [h.upper() for h in handles]


def verify_created_geometry(entity, tool, args):
    """Require connector geometry, not the arguments we previously sent."""
    def same(a, b):
        return math.dist(point(a), point(b)) <= 0.01
    if tool == "create_line":
        if not same(entity.get("startPoint"), [args["startX"], args["startY"], args.get("startZ", 0)]) or not same(entity.get("endPoint"), [args["endX"], args["endY"], args.get("endZ", 0)]):
            raise ValueError("Фактические начало/конец луча отличаются от рассчитанных")
    elif tool == "create_dimension_aligned":
        expected = math.hypot(args["x2"]-args["x1"], args["y2"]-args["y1"])
        if abs(finite(entity.get("measurement")) - expected) > 0.01:
            raise ValueError("Фактический размер не совпадает с проверенным обмером")
        a = entity.get("xLine1Point", entity.get("extensionLine1Point"))
        b = entity.get("xLine2Point", entity.get("extensionLine2Point"))
        if not same(a, [args["x1"], args["y1"], 0]) or not same(b, [args["x2"], args["y2"], 0]):
            raise ValueError("Коннектор не подтвердил фактические границы размерных точек")
        if entity.get("dimensionText", "") not in {"", "<>"}:
            raise ValueError("На размере обнаружена подмена размерного числа")
    elif tool == "create_polyline":
        actual = entity.get("vertices", [])
        if len(actual) != len(args["vertices"]) or any(not same(a, b) for a, b in zip(actual, args["vertices"])):
            raise ValueError("Фактический контур не совпал с утверждённым")


class GuardedCAD:
    def __init__(self, control):
        self.control = control

    def check_full_analysis(self, slug, run_id, item):
        finding = next((f for f in self.control.app.runs.load(slug, run_id)["registry"].get("findings", []) if f.get("id") == item["id"]), None)
        if not finding:
            raise ValueError("До подготовки графики заполните индивидуальный record_finding для текущего замечания")
        status = item["steps"]["decided"]["status"]
        if status in {"violation", "compliant", "not_applicable"}:
            errors = _validate_individual_analysis(finding) + validate_source_reading(finding)
            if finding.get("decision", {}).get("status") != status:
                errors.append("Решения контролируемой проверки и полного реестра различаются")
            if finding.get("source_text_id") not in item["source_ids"]:
                errors.append("Полный реестр ссылается на другое исходное замечание")
            scope = item["steps"]["scope_checked"]
            if any(finding.get("norm", {}).get(k) != scope[k] for k in ("document", "point")):
                errors.append("Норма в реестре отличается от проверенной")
            state = self.control.load(slug, run_id)
            for doc in finding.get("analysis", {}).get("source_reading", {}).get("required_documents", []):
                read_pages = {r["page"] for r in state["read_receipts"] if r["item_id"] == item["id"]
                              and r["relative_path"] == doc.get("relative_path") and r["source_sha256"] == doc.get("source_sha256")}
                if not set(doc.get("pages_read", [])).issubset(read_pages):
                    errors.append("Журнал реального чтения не подтверждает страницы, заявленные в полном реестре")
            evidence_handles = {h for node in state["nodes"] if node["id"] in item["steps"]["evidence_checked"].get("node_ids", []) for h in node["entity_ids"]}
            if not set(finding.get("analysis", {}).get("checked_autocad_entity_ids", [])).issubset(evidence_handles | set(item["source_ids"])):
                errors.append("Полный реестр заявляет объекты, не включённые в подтверждённые доказательства")
            if errors:
                raise ValueError("; ".join(errors))
        return finding_analysis_digest(finding)

    def validate_actions(self, state, item, actions):
        if state.get("mode") == "barrier_assessment":
            raise ValueError("Специальный исполнитель схем ПП ещё не подключён. Исполнитель замечаний АР меняет слои и свойства и не подходит для этой схемы.")
        if not actions or len(actions) > 40:
            raise ValueError("План должен содержать 1–40 адресных операций одного замечания")
        decision = item["steps"]["decided"]["status"]
        non_issue = decision in {"compliant", "not_applicable"}
        seen, sources, copied = set(), set(item["source_ids"]), set()
        for op in actions:
            if not op.get("id") or op["id"] in seen:
                raise ValueError("Операции должны иметь уникальные id")
            seen.add(op["id"])
            tool, role, args = op.get("tool"), op.get("role"), op.get("args", {})
            if tool not in TOOLS or role not in ROLES or set(args) - TOOLS[tool]:
                raise ValueError("Неподдерживаемая операция/параметры. Удаление, сохранение и массовая замена запрещены.")
            role_tool = {**{r: "copy_entities" for r in GROUP_ROLES}, "dimension": "create_dimension_aligned",
                         "ray": "create_line", "cloud": "create_polyline", "non_issue_explanation": "create_mtext"}
            if role_tool.get(role) != tool:
                raise ValueError("Инструмент не соответствует назначению создаваемого объекта")
            if non_issue and (role != "non_issue_explanation" or tool != "create_mtext"):
                raise ValueError("При отсутствии нарушения допустимо только зелёное пояснение о невыдаче, без луча")
            if tool == "create_mtext":
                if role != "non_issue_explanation" or "\\C3;" not in args.get("text", ""):
                    raise ValueError("Новый MText разрешён здесь только для зелёного объяснения невыдачи. Текст замечания копируется.")
                if not args.get("style") or finite(args.get("height")) <= 0 or finite(args.get("width")) <= 0:
                    raise ValueError("Пояснение должно иметь указанные стиль, высоту и ширину по образцу")
            expected = "экспертиза пояснения" if role == "purple_explanation" else "экспертиза"
            if tool != "copy_entities" and args.get("layer") != expected:
                raise ValueError("Слой должен быть указан явно и соответствовать назначению объекта")
            if args.get("linetype", "ByLayer") != "ByLayer":
                raise ValueError("Тип линии должен быть ByLayer")
            for key, value in args.items():
                if key.lower().endswith(("x", "y", "z")) or key in {"height", "width"}:
                    finite(value)
            if tool == "copy_entities":
                hs = args.get("handles", [])
                if role not in GROUP_ROLES or not hs or not set(hs).issubset(sources) or copied.intersection(hs):
                    raise ValueError("Копировать можно только однократно перечисленные части утверждённой исходной группы")
                if args.get("count", 1) != 1:
                    raise ValueError("Разрешена одна копия для конкретного этажа")
                finite(args.get("deltaX")); finite(args.get("deltaY"))
                copied.update(hs)
            if role == "dimension":
                if tool != "create_dimension_aligned" or not args.get("style"):
                    raise ValueError("Нужен размер фактического обмера со стилем по образцу")
                measurement = state["measurements"].get(op.get("measurement_id"), {})
                if measurement.get("item_id") != item["id"]:
                    raise ValueError("Размер не связан с фактическим обмером этого замечания")
                a, b = measurement["points"]
                if any(abs(finite(args.get(k)) - v) > 0.01 for k, v in zip(("x1", "y1", "x2", "y2"), (a[0], a[1], b[0], b[1]))):
                    raise ValueError("Размерные точки не совпадают с фактическим обмером")
            if role == "ray" and (tool != "create_line" or not op.get("target")):
                raise ValueError("Луч должен иметь конкретную подтверждённую цель")
            if role == "measurement_line":
                raise ValueError("Трассы путей эвакуации пока требуют отдельного проверяемого алгоритма; произвольная линия не является обмером")
            if tool == "create_line" and role != "ray":
                raise ValueError("Линия без проверяемого назначения не разрешена")
            if tool == "create_polyline":
                if role != "cloud" or not args.get("closed") or len(args.get("vertices", [])) < 3:
                    raise ValueError("Разрешён только замкнутый контур места нарушения")
                for vertex in args["vertices"]:
                    point(vertex)
                    if set(vertex) - {"x", "y", "z", "bulge"}:
                        raise ValueError("Неизвестные параметры вершины")
                    finite(vertex.get("bulge", 0))
        if not non_issue and state.get("mode") == "compliance" and copied != sources:
            raise ValueError("Для оценки соответствия требуется копия полного состава исходного замечания")
        if copied and not {"remark_text", "frame", "number"}.issubset({o["role"] for o in actions}):
            raise ValueError("Не включены текст, рамка и номер замечания")
        dimensions = {o["id"] for o in actions if o["role"] == "dimension"}
        if dimensions and not dimensions.issubset({o.get("target") for o in actions if o["role"] == "ray"}):
            raise ValueError("Для каждого подтверждающего размера нужен отдельный луч именно к нему")
        if (decision == "violation" or (not non_issue and item.get("relative_to_stage_frame") == "right")) and not any(o["role"] == "ray" for o in actions):
            raise ValueError("Для выдаваемого нарушения/актуального элемента справа от рамки обязателен луч")

    def preflight(self, state, item, actions, before, floor):
        group_boxes, purple_boxes, text_boxes, predicted = [], [], [], {}
        for op in actions:
            args = op["args"]
            if op["tool"] == "copy_entities":
                dx, dy = args["deltaX"], args["deltaY"]
                boxes = []
                for h in args["handles"]:
                    a, b, c, d = bounds(before[h]["bounds"])
                    boxes.append([a+dx, b+dy, c+dx, d+dy])
                box = union(boxes)
                predicted[op["id"]] = box
                group_boxes.append(box)
                if op["role"] == "purple_explanation":
                    purple_boxes.extend(boxes)
                if op["role"] == "remark_text":
                    text_boxes.extend(boxes)
            elif op["role"] == "dimension":
                points = [[args[kx], args[ky]] for kx, ky in (("x1", "y1"), ("x2", "y2"), ("dimLineX", "dimLineY"))]
                if any(not contains(floor["bounds"], p) for p in points):
                    raise ValueError("Размер находится вне своего этажа")
                predicted[op["id"]] = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
            elif op["role"] == "cloud":
                points = [point(v) for v in args["vertices"]]
                if any(not contains(floor["bounds"], p) for p in points):
                    raise ValueError("Облако выходит за границы нужного этажа")
                predicted[op["id"]] = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
        if any(overlaps(a, b) for a in text_boxes for b in purple_boxes):
            raise ValueError("Пояснение накладывается на текст замечания")
        group_box = union(group_boxes or [e["bounds"] for h, e in before.items() if h in item["source_ids"]])
        if group_boxes:
            if overlaps(group_box, floor["bounds"]):
                raise ValueError("Группа замечания накладывается на проверяемый план")
            for p in state["plans"].values():
                if p.get("status") == "verified" and p.get("group_bounds") and overlaps(group_box, p["group_bounds"]):
                    raise ValueError("Группа накладывается на ранее выданное замечание/пояснение")
        targets = []
        for op in actions:
            if op["role"] != "ray":
                continue
            target_op = next((o for o in actions if o["id"] == op["target"]), None)
            if target_op and target_op["role"] == "dimension":
                end = [target_op["args"]["dimLineX"], target_op["args"]["dimLineY"], 0]
            else:
                end = point(op.get("target_point"))
            box = predicted.get(op["target"]) if target_op else before[op["target"]]["bounds"]
            if not contains(box, end) or not contains(floor["bounds"], end):
                raise ValueError("Цель луча не принадлежит подтверждённому объекту на нужном этаже")
            targets.append(end)
        origin = ray_origin(group_box, targets) if targets else None
        for end in targets:
            if contains(group_box, [origin[0]+(end[0]-origin[0])*0.000001, origin[1]+(end[1]-origin[1])*0.000001], tolerance=0):
                raise ValueError("Луч пересечёт габарит замечания")
        return {"group_bounds": group_box, "ray_origin": origin, "targets": targets}

    def check_existing_text_collisions(self, client, actions, preflight):
        if not any(o["tool"] == "copy_entities" for o in actions):
            return
        a, b, c, d = preflight["group_bounds"]
        candidates = []
        for kind in ("MText", "DBText"):
            result = client.call("get_entities", {"entityType": kind, "boundingBox": {
                "minX": a, "minY": b, "maxX": c, "maxY": d}, "limit": 500})
            if result.get("hasMore"):
                raise ValueError("Слишком много объектов в зоне размещения: проверка наложений не завершена")
            candidates.extend(e["handle"] for e in result.get("entities", []))
        if len(set(candidates)) > 500:
            raise ValueError("Зона размещения требует отдельной проверки наложений")
        if candidates:
            existing = read_entities(client, sorted(set(candidates)))
            if any(overlaps(preflight["group_bounds"], e["bounds"]) for e in existing.values()):
                raise ValueError("Планируемая группа накладывается на существующий текст/пояснение в AutoCAD")

    def prepare(self, slug, run_id, item_id, actions, explanation):
        with self.control.edit(slug, run_id) as state:
            self.control.require_fresh(slug, run_id, state)
            item = self.control.item(state, item_id)
            if item["state"] != "reviewed" or not str(explanation).strip():
                raise ValueError("Сначала завершите индивидуальную и повторную проверку и опишите предлагаемые изменения")
            self.control.require_sources_current(state, item)
            full_analysis_hash = self.check_full_analysis(slug, run_id, item)
            if any(p["status"] in {"executing", "needs_review"} for p in state["plans"].values()):
                raise ValueError("Есть незавершённая/неопределённая операция AutoCAD. Нужна ручная сверка, повтор запрещён.")
            state["mode"] = self.control.app.runs.load(slug, run_id)["run"]["mode"]
            self.validate_actions(state, item, actions)
            floor = next((f for f in state["floors"] if f["id"] == item["floor"]), None)
            if not floor:
                raise ValueError("Не подтверждены границы плана соответствующего этажа")
            refs = set(item["source_ids"])
            for node in state["nodes"]:
                if node["id"] in item["steps"]["evidence_checked"].get("node_ids", []):
                    refs.update(node["entity_ids"])
            op_ids = {o["id"] for o in actions}
            for op in actions:
                if op.get("target") and op["target"] not in op_ids and op["target"] not in refs:
                    raise ValueError("Цель не является подтверждённым объектом замечания или создаваемым размером/контуром")
                if op.get("target") in op_ids and next(o for o in actions if o["id"] == op["target"])["role"] not in {"dimension", "cloud"}:
                    raise ValueError("Луч нельзя направлять к произвольной создаваемой графике")
            with self.control.bridge_factory() as client:
                info = client.call("get_drawing_info")
                assert_drawing(info, state["source_path"])
                before = read_entities(client, sorted(refs))
                assert_drawing(client.call("get_drawing_info"), state["source_path"])
            # Every earlier evidence capture used by this item must still match live geometry.
            snapshots = {item["snapshot_id"]}
            for n in state["nodes"]:
                if n["id"] in item["steps"]["evidence_checked"].get("node_ids", []):
                    snapshots.add(n["snapshot_id"])
            for mid in item["steps"]["evidence_checked"].get("measurement_ids", []):
                snapshots.add(state["measurements"][mid]["snapshot_id"])
            for sid in snapshots:
                snap = self.control.snapshot(slug, run_id, sid, state)
                for h in refs.intersection(snap["entities"]):
                    if digest(before[h]) != digest(snap["entities"][h]):
                        raise ValueError("Исходная геометрия изменилась после обмера/распознавания; требуется новая проверка")
            preflight = self.preflight(state, item, actions, before, floor)
            with self.control.bridge_factory() as client:
                assert_drawing(client.call("get_drawing_info"), state["source_path"])
                self.check_existing_text_collisions(client, actions, preflight)
                assert_drawing(client.call("get_drawing_info"), state["source_path"])
            plan_id = uuid4().hex
            payload = {"item_id": item_id, "actions": copy.deepcopy(actions), "explanation": explanation,
                       "before": before, "drawing": info, "floor": floor,
                       "policy_digest": state["policy_digest"], "model_revision": state["model_revision"],
                       "preflight": preflight, "analysis_hash": digest(item["steps"]),
                       "full_analysis_hash": full_analysis_hash}
            plan = {"id": plan_id, "payload": payload, "hash": digest(payload), "status": "awaiting_user",
                    "created_at": utc_now(), "created": [], "executed_operations": [], "errors": []}
            for previous in state["plans"].values():
                if previous["payload"]["item_id"] == item_id and previous["status"] == "awaiting_user":
                    previous["status"] = "superseded"
            state["plans"][plan_id] = plan
            self.control.event(state, "cad_plan_prepared", plan_id)
        return {"id": plan_id, "hash": plan["hash"], "status": plan["status"],
                "instruction": "План не выполнен. Просмотрите его в программе: Контроль → План изменений → Подтвердить и выполнить."}

    def execute_from_ui(self, slug, run_id, plan_id, viewed_hash):
        # This method has no MCP/CLI dispatch. Only a local GUI confirmation invokes it.
        with self.control.edit(slug, run_id) as state:
            self.control.require_fresh(slug, run_id, state)
            plan = state["plans"].get(plan_id)
            if not plan or plan["status"] != "awaiting_user" or plan["hash"] != viewed_hash or digest(plan["payload"]) != viewed_hash:
                raise ValueError("План изменён, уже выполнен или исход операции неопределён. Откройте актуальный план.")
            payload = plan["payload"]
            item = self.control.item(state, payload["item_id"])
            if item["state"] != "reviewed" or payload["model_revision"] != state["model_revision"]:
                raise ValueError("Результат анализа или модель изменены")
            if payload["analysis_hash"] != digest(item["steps"]):
                raise ValueError("Анализ замечания изменён после подготовки плана")
            self.control.require_sources_current(state, item)
            if self.check_full_analysis(slug, run_id, item) != payload["full_analysis_hash"]:
                raise ValueError("Полный нормативный анализ изменён после просмотра плана")
            self.validate_actions(state, item, payload["actions"])
            control_file = self.control.folder(slug, run_id) / "КОНТРОЛЬ.json"
            with self.control.bridge_factory() as client:
                assert_drawing(client.call("get_drawing_info"), state["source_path"])
                if sha256_file(Path(state["source_path"])) != state["source_disk_hash"]:
                    raise ValueError("Исходный DWG на диске изменился; нанесение остановлено")
                before = read_entities(client, sorted(payload["before"]))
                if digest(before) != digest(payload["before"]):
                    raise ValueError("Геометрия изменилась после подготовки плана; требуется новый план")
                preflight = self.preflight(state, item, payload["actions"], before, payload["floor"])
                self.check_existing_text_collisions(client, payload["actions"], preflight)
                count_before = client.call("get_drawing_info").get("entityCount")
                plan["status"] = "executing"
                plan["approved_at"] = utc_now()
                write_json(control_file, state)
                try:
                    created_by_op = {}
                    ops = payload["actions"]
                    for op in [o for o in ops if o["role"] != "ray"]:
                        self._execute_one(client, state, plan, op, op["args"], control_file, created_by_op)
                    group_handles = [h for op in ops if op["role"] in GROUP_ROLES for h in created_by_op.get(op["id"], [])]
                    if not group_handles:
                        group_handles = item["source_ids"]
                    group_box = union(e["bounds"] for e in read_entities(client, group_handles).values())
                    if any(abs(a-b) > 0.1 for a,b in zip(group_box,payload["preflight"]["group_bounds"])):
                        raise ValueError("Фактический габарит группы отличается от предварительно согласованного")
                    rays, targets = [o for o in ops if o["role"] == "ray"], []
                    for op in rays:
                        target = op["target"]
                        hs = created_by_op.get(target, [target])
                        if len(hs) != 1:
                            raise ValueError("Один луч должен иметь одну конкретную цель")
                        entity = read_entities(client, hs)[hs[0]]
                        target_op = next((o for o in ops if o["id"] == target), None)
                        if target_op and target_op["role"] == "dimension":
                            end = [target_op["args"]["dimLineX"], target_op["args"]["dimLineY"], 0]
                        else:
                            end = point(op.get("target_point"))
                        if not contains(entity["bounds"], end) or not contains(payload["floor"]["bounds"], end):
                            raise ValueError("Цель луча не принадлежит объекту/этажу")
                        targets.append(end)
                    origin = ray_origin(group_box, targets) if targets else None
                    for op, target in zip(rays, targets):
                        if contains(group_box, [origin[0]+(target[0]-origin[0])*0.000001,
                                               origin[1]+(target[1]-origin[1])*0.000001], tolerance=0):
                            raise ValueError("Луч пересекает полный габарит замечания")
                        args = {"startX": origin[0], "startY": origin[1], "endX": target[0], "endY": target[1],
                                "layer": "экспертиза", "linetype": "ByLayer"}
                        self._execute_one(client, state, plan, op, args, control_file, created_by_op)
                    after = read_entities(client, sorted(payload["before"]))
                    if digest(after) != digest(before):
                        raise ValueError("Изменились ранее существовавшие объекты; требуется ручная сверка")
                    created_handles = [i["handle"] for i in plan["created"]]
                    actual = read_entities(client, created_handles)
                    for row in plan["created"]:
                        if not properties_ok(actual[row["handle"]], row["role"]):
                            raise ValueError("Проверка фактического слоя/ByLayer не пройдена")
                    for executed in plan["executed_operations"]:
                        tool = next(o["tool"] for o in ops if o["id"] == executed["id"])
                        for h in executed["handles"]:
                            verify_created_geometry(actual[h], tool, executed["args"])
                    for earlier in state["plans"].values():
                        if earlier.get("status") == "verified" and earlier.get("group_bounds") and group_handles != item["source_ids"]:
                            if overlaps(group_box, earlier["group_bounds"]):
                                raise ValueError("Группа замечания накладывается на ранее выданную группу")
                    info_after = client.call("get_drawing_info")
                    assert_drawing(info_after, state["source_path"])
                    if not isinstance(count_before, int) or info_after.get("entityCount") != count_before + len(created_handles):
                        raise ValueError("Общее количество объектов изменилось не только по утверждённому плану; нужна сверка")
                    if sha256_file(Path(state["source_path"])) != state["source_disk_hash"]:
                        raise ValueError("Исходный файл был перезаписан во время работы")
                    self.control.require_fresh(slug, run_id, state)
                    self.control.require_sources_current(state, item)
                    plan.update(status="verified", actual=actual, group_bounds=group_box, ray_origin=origin,
                                origin_rule="outermost_group_point_toward_plan", verified_at=utc_now())
                    item["state"] = "graphics_verified"
                    item["verified_plan"] = plan_id
                    finding = next((f for f in self.control.app.runs.load(slug, run_id)["registry"].get("findings", []) if f.get("id") == item["id"]), None)
                    item["finding_hash"] = finding_analysis_digest(finding) if finding else ""
                    self.control.event(state, "cad_verified", plan_id)
                except Exception as exc:
                    plan["status"] = "needs_review"
                    plan["errors"].append(str(exc))
                    self.control.event(state, "cad_stopped_without_deletion", str(exc))
                    # Preserve partial output. Never erase or automatically repeat an uncertain write.
                    write_json(control_file, state)
                    return {"ok": False, "plan_id": plan_id, "errors": plan["errors"], "created": plan["created"]}
        return {"ok": True, "plan_id": plan_id, "created": plan["created"], "saved_dwg": False}

    def _execute_one(self, client, state, plan, op, args, control_file, mapping):
        self.control.require_fresh(state["project_slug"], state["run_id"], state)
        assert_drawing(client.call("get_drawing_info"), state["source_path"])
        plan["pending_operation"] = {"id": op["id"], "tool": op["tool"], "args": args}
        write_json(control_file, state)
        result = client.call_write(op["tool"], args)
        handles = handles_from_result(result)
        if any(h in plan["payload"]["before"] or h in {r["handle"] for r in plan["created"]} for h in handles):
            raise ValueError("Коннектор вернул прежний/повторный handle вместо нового объекта")
        expected_count = len(args["handles"]) if op["tool"] == "copy_entities" else 1
        plan["created"].extend({"handle": h, "role": op["role"], "operation": op["id"]} for h in handles)
        write_json(control_file, state)
        if len(handles) != expected_count:
            raise ValueError("Количество созданных объектов не совпало с планом")
        assert_drawing(client.call("get_drawing_info"), state["source_path"])
        layer = "экспертиза пояснения" if op["role"] == "purple_explanation" else "экспертиза"
        client.call_write("change_entity_properties", {"handles": handles, "layer": layer, "color": 256,
                                                       "linetype": "ByLayer", "lineweight": -1})
        mapping[op["id"]] = handles
        plan["executed_operations"].append({"id": op["id"], "args": args, "handles": handles})
        plan["pending_operation"] = None
        write_json(control_file, state)
