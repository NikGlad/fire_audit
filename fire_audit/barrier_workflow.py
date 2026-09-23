"""Barrier-specific review, copy construction, and independent CAD verification.

The assistant supplies reviewed normative decisions and room geometry. This
module checks their coverage and executes graphics; it does not invent norms.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

from .barriers import MODE, SOURCE_EXPLANATIONS_LAYER, preparation_errors
from .barrier_inputs import room_key
from .barrier_dxf import (library, point, build_dxf, model_snapshot, protected_changes, verify_entities,
                         supporting_snapshot, supporting_changes, equivalent)
from .cad_bridge import AutoCADBridge, assert_drawing
from .control import project_digest
from .control_geometry import digest
from .io_utils import read_json, write_json, atomic_write_text, utc_now
from .norm_lock import normative_update_lock


VENT_CHAMBER_QUERY = (
    "Просим уточнить данная венткамера относится к противодымной защите? "
    "Если да, то просим уточнить, данная венткамера обслуживает только пожарный "
    "отсек, в котором расположена? Пока рассматриваем, что да."
)
INTERFLOOR_BELT_RULE = "sp2_5_4_18_interfloor_belts"


def is_vent_chamber(name):
    value = " ".join(str(name or "").casefold().replace("ё", "е").split())
    compact = value.replace(" ", "").replace(".", "")
    return "венткам" in compact or ("вентиляц" in compact and "камер" in compact)


def refers_to_interfloor_belt(item):
    if item.get("rule_reference") == INTERFLOOR_BELT_RULE:
        return True
    if INTERFLOOR_BELT_RULE in (item.get("requirement_tags") or []):
        return True
    text = " ".join(str(item.get(key, "")) for key in ("text", "basis")).casefold().replace("ё", "е")
    return "5.4.18" in text and "междуэтаж" in text and "пояс" in text


def on_segment(p, a, b, tolerance=2.0):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length = math.hypot(dx, dy)
    if length <= tolerance:
        return math.dist(p, a) <= tolerance
    projection = ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / length
    cross = abs((p[0]-a[0])*dy - (p[1]-a[1])*dx) / length
    return cross <= tolerance and -tolerance <= projection <= length+tolerance


def covers_edge(items, a, b):
    length = math.dist(a, b)
    if length < 0.001:
        return False
    intervals = []
    for item in items:
        pts = [point(p) for p in item.get("points", [])]
        if item.get("closed") and pts:
            pts.append(pts[0])
        for p, q in zip(pts, pts[1:]):
            # A polyline can be longer than the room edge.
            if on_segment(a, p, q) and on_segment(b, p, q):
                return True
            if on_segment(p, a, b) and on_segment(q, a, b):
                project = lambda v: ((v[0]-a[0])*(b[0]-a[0]) + (v[1]-a[1])*(b[1]-a[1]))/length
                intervals.append(sorted((project(p), project(q))))
    covered = 0
    for start, end in sorted(intervals):
        if start > covered+2:
            return False
        covered = max(covered, end)
    return covered >= length-2


def inside_boundary(p, boundary):
    inside = False
    for a,b in zip(boundary, boundary[1:]+boundary[:1]):
        if on_segment(p,a,b):
            return True
        if (a[1]>p[1]) != (b[1]>p[1]) and p[0] < (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


def validate_plan(plan, project):
    errors = []
    review = plan.get("review", {})
    if (review.get("gpzu_date") != project.get("dates", {}).get("gpzu") or not review.get("edition_basis")
            or review.get("comments_and_letters_reviewed") is not True
            or review.get("special_requirements_reviewed") is not True):
        errors.append("Не завершена проверка редакции по ГПЗУ, пояснений, писем и специальных требований")
    if review.get("sp2_5_4_18_interfloor_belts_graphics_omitted") is not True:
        errors.append("Не подтверждено отсутствие графики по междуэтажным поясам п. 5.4.18 СП 2")
    if not plan.get("scope", {}).get("inventory_complete") or not plan.get("scope", {}).get("reviewer"):
        errors.append("Не подтверждены полнота перечня помещений и автор нормативной проверки")
    if not plan.get("scope", {}).get("description"):
        errors.append("Не описаны границы проверяемой части здания и этажи")
    if plan.get("scope", {}).get("open_mezzanines_and_platforms_reviewed") is not True:
        errors.append("Не завершена инвентаризация открытых антресолей и площадок без помещений")
    items = plan.get("items", [])
    if not items:
        errors.append("Нет подготовленных элементов схемы")
    ids = [i.get("id") for i in items]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        errors.append("Идентификаторы элементов должны быть заполнены и уникальны")
    by_id = {i.get("id"): i for i in items}
    legend = {i.get("key"): i for i in plan.get("legend", [])}
    colors = [i.get("color_index") for i in legend.values()]
    if len(legend) != len(plan.get("legend", [])) or len(colors) != len(set(colors)):
        errors.append("Легенда содержит повторяющиеся типы или одинаковые цвета разных типов")
    for entry in legend.values():
        if type(entry.get("color_index")) is not int or not 1 <= entry["color_index"] <= 255 or not entry.get("name"):
            errors.append("В легенде нужны название конструкции и явный цвет 1–255")
        if not entry.get("source_handles") and not any(i.get("kind")=="legend" and i.get("legend_key")==entry["key"] for i in items):
            errors.append(f"{entry.get('key')}: нет исходной строки легенды или новой строки для нанесения")
        if refers_to_interfloor_belt(entry):
            errors.append(f"{entry.get('key')}: обозначение междуэтажного пояса по п. 5.4.18 СП 2 не включать в схему ПП")
    sources = {s.get("id") for s in plan.get("sources", [])}
    if len(sources) != len(plan.get("sources", [])) or None in sources:
        errors.append("У источников должны быть уникальные идентификаторы")
    categories = {room_key(r): r for r in project.get(MODE, {}).get("rooms", [])}
    geometric_keys = {"barrier": ["points"], "category": ["position"], "callout": ["position", "leader_start"],
                      "dimension": ["p1", "p2", "base"], "legend": ["position", "start", "end"]}
    signatures = set()
    for item in items:
        kind = item.get("kind")
        if kind not in geometric_keys:
            errors.append(f"{item.get('id')}: неизвестный вид элемента")
            continue
        if refers_to_interfloor_belt(item):
            errors.append(f"{item.get('id')}: по требованию п. 5.4.18 СП 2 о междуэтажных поясах графику не наносить")
        try:
            for key in geometric_keys[kind]:
                if key == "points":
                    if len(item[key]) < 2:
                        raise ValueError("Нужны минимум две вершины")
                    for p in item[key]:
                        point(p)
                else:
                    point(item[key])
            if kind == "callout":
                if item.get("element") == "vent_chamber":
                    targets = item.get("targets", [])
                    if not targets:
                        raise ValueError("для запроса по венткамерам нужны цели лучей")
                    for target in targets:
                        point(target)
                else:
                    point(item["target"])
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{item.get('id')}: неполная геометрия: {exc}")
        if kind in {"barrier", "legend"} and item.get("legend_key") not in legend:
            errors.append(f"{item.get('id')}: отсутствует тип в легенде")
        if kind in {"barrier", "callout"}:
            user_request = kind == "callout" and item.get("element") == "vent_chamber"
            if (not item.get("basis") or (not user_request and not item.get("sources"))
                    or not set(item.get("sources", [])).issubset(sources)):
                errors.append(f"{item.get('id')}: нет нормативного основания и проверенных источников")
            if not item.get("source_handles"):
                errors.append(f"{item.get('id')}: нет исходных объектов AutoCAD")
        if kind == "category":
            room = categories.get(item.get("room_key"))
            if not room or item.get("category") != room["category"] or item.get("room_name") != room["name"]:
                errors.append(f"{item.get('id')}: подпись не соответствует утверждённой таблице")
        if kind == "callout":
            if item.get("element") not in {"door", "gate", "curtain", "window", "slab", "vent_chamber"} or not item.get("text"):
                errors.append(f"{item.get('id')}: не определён элемент/текст выноски")
            if item.get("element") in {"door", "gate", "curtain"} and item.get("independent_requirement") is not True:
                errors.append(f"{item.get('id')}: выноска дублирует обычное заполнение преграды")
            if item.get("element") == "slab" and item.get("exceeds_table21_baseline") is not True:
                errors.append(f"{item.get('id')}: не подтверждено превышение базового предела перекрытия")
            if item.get("element") == "vent_chamber":
                if item.get("text") != VENT_CHAMBER_QUERY:
                    errors.append(f"{item.get('id')}: изменён обязательный текст запроса по венткамерам")
                if item.get("assumption_until_response") is not True:
                    errors.append(f"{item.get('id')}: не зафиксировано временное принятие «да» до ответа")
                if (not isinstance(item.get("room_keys"), list)
                        or len(item["room_keys"]) != len(item.get("targets", []))):
                    errors.append(f"{item.get('id')}: каждой венткамере нужен отдельный луч и room_key")
                floors = {str(key).rsplit(":", 1)[0] for key in item.get("room_keys", []) if ":" in str(key)}
                if len(floors) > 1:
                    errors.append(f"{item.get('id')}: запрос и лучи по венткамерам должны относиться к одному этажу")
        shape = {k:v for k,v in item.items() if k in {"kind", "points", "position", "target", "targets", "p1", "p2", "base", "legend_key"}}
        token = digest(shape)
        if token in signatures:
            errors.append(f"{item.get('id')}: дублируется геометрия элемента")
        signatures.add(token)
    reviewed = plan.get("rooms", [])
    category_items = [i for i in items if i.get("kind")=="category"]
    for key in categories:
        matches = [i for i in category_items if i.get("room_key")==key]
        if len(matches) != 1:
            errors.append(f"{key}: нужна ровно одна подпись утверждённой категории (новая или существующая)")
    keys = [r.get("key") for r in reviewed]
    if not reviewed or len(keys) != len(set(keys)):
        errors.append("Перечень проверенных помещений пуст или содержит дубли")
    if not set(categories).issubset(set(keys)):
        errors.append("Не все помещения утверждённой таблицы вошли в проверку")
    components = plan.get("open_components", [])
    if not isinstance(components, list):
        errors.append("Перечень открытых антресолей и площадок должен быть списком")
        components = []
    component_keys = [component.get("key") for component in components if isinstance(component, dict)]
    if (len(component_keys) != len(components) or any(not key for key in component_keys)
            or len(component_keys) != len(set(component_keys))):
        errors.append("У открытых антресолей и площадок нужны уникальные идентификаторы")
    for component in components:
        if not isinstance(component, dict):
            continue
        key = component.get("key")
        if component.get("kind") not in {"open_mezzanine", "open_platform"}:
            errors.append(f"{key}: не установлен вид открытой антресоли или площадки")
        if (component.get("open_to_host_volume") is not True
                or component.get("contains_rooms") is not False
                or component.get("treated_as_part_of_host_room") is not True):
            errors.append(f"{key}: не подтверждено, что открытый элемент без помещений является частью основного помещения")
        if component.get("host_room_key") not in set(keys):
            errors.append(f"{key}: не найдено помещение, в объёме которого расположен открытый элемент")
        if not component.get("source_handles"):
            errors.append(f"{key}: не указаны исходные объекты открытой антресоли или площадки")
        if key in set(keys):
            errors.append(f"{key}: открытая антресоль или площадка без помещений не должна учитываться как отдельное помещение")
        if component.get("separating_barrier_item_ids") not in (None, []):
            errors.append(f"{key}: между открытым элементом и основным помещением не требуется самостоятельная преграда")
    project_names = {room_key(r): r.get("name", "") for r in project.get(MODE, {}).get("rooms", [])}
    vent_rooms = {}
    for room in reviewed:
        name = str(room.get("name") or project_names.get(room.get("key"), "")).strip()
        if not name:
            errors.append(f"{room.get('key')}: не указано наименование помещения")
        elif is_vent_chamber(name):
            vent_rooms[room.get("key")] = room
    vent_targets = {}
    for item in items:
        if item.get("kind") == "callout" and item.get("element") == "vent_chamber":
            for key, target in zip(item.get("room_keys", []), item.get("targets", [])):
                vent_targets.setdefault(key, []).append(target)
    for key, room in vent_rooms.items():
        targets = vent_targets.get(key, [])
        if len(targets) != 1:
            errors.append(f"{key}: для венткамеры нужен ровно один луч от обязательного запроса")
        elif not inside_boundary(point(targets[0]), [point(p) for p in room.get("boundary", [])]):
            errors.append(f"{key}: луч обязательного запроса не указывает внутрь венткамеры")
    for key in set(vent_targets) - set(vent_rooms):
        errors.append(f"{key}: цель запроса не относится к установленной венткамере")
    for room in reviewed:
        try:
            boundary = [point(p) for p in room["boundary"]]
            edges = room["edges"]
            if len(boundary) < 3 or len(edges) != len(boundary) or not room.get("source_handles"):
                raise ValueError("нужны контур, исходные объекты и решение по каждой стороне")
            for item in category_items:
                if item.get("room_key")==room["key"] and not inside_boundary(point(item["position"]),boundary):
                    raise ValueError("подпись категории находится за контуром помещения")
            for edge, a, b in zip(edges, boundary, boundary[1:]+boundary[:1]):
                if not edge.get("basis") or not edge.get("sources") or not set(edge["sources"]).issubset(sources):
                    raise ValueError("не обоснована одна из границ")
                drawing_excluded = edge.get("decision") == "excluded_from_drawing"
                if edge.get("adjacent_space_kind") == "stairwell" and edge.get("decision") == "barrier":
                    if (edge.get("explicit_remark_requires_barrier") is not True
                            or edge.get("explicit_remark_layer") != SOURCE_EXPLANATIONS_LAYER
                            or not str(edge.get("explicit_remark_fire_resistance", "")).strip()
                            or not edge.get("explicit_remark_source_handles")):
                        raise ValueError(
                            "линию на стене ЛК допускает только конкретное замечание слоя "
                            f"{SOURCE_EXPLANATIONS_LAYER} с определённым пределом огнестойкости"
                        )
                if drawing_excluded:
                    exclusions = {"exterior_wall": "outside", "stairwell_wall": "stairwell"}
                    reason = edge.get("graphic_exclusion")
                    if (reason not in exclusions
                            or edge.get("adjacent_space_kind") != exclusions[reason]
                            or edge.get("explicit_remark_requires_barrier") is not False
                            or not edge.get("normative_requirement")
                            or not edge.get("source_handles")
                            or edge.get("items")):
                        raise ValueError("исключение из схемы требует подтверждённой стены, нормативного вывода и отсутствия адресного требования замечания")
                if edge.get("decision") == "barrier":
                    candidates = [by_id[i] for i in edge.get("items", []) if i in by_id and by_id[i]["kind"]=="barrier"]
                    if not covers_edge(candidates, a, b):
                        raise ValueError("линия преграды не покрывает требуемую сторону помещения")
                elif edge.get("decision") != "not_required" and not drawing_excluded:
                    raise ValueError("не принято решение по границе")
                if room.get("key") in vent_rooms:
                    adjacent = edge.get("adjacent_space_kind")
                    internal_adjacencies = {"internal", "room", "corridor", "hall", "vestibule", "tambour",
                                            "production", "storage", "large_storage", "stairwell"}
                    if adjacent not in internal_adjacencies | {"outside"}:
                        raise ValueError("для границы венткамеры не установлено внутреннее или наружное примыкание")
                    if adjacent in internal_adjacencies and not drawing_excluded:
                        if edge.get("decision") != "barrier":
                            raise ValueError("при первой оценке внутренняя граница венткамеры должна быть противопожарной преградой")
                        candidates = [by_id[i] for i in edge.get("items", []) if i in by_id and by_id[i]["kind"] == "barrier"]
                        for candidate in candidates:
                            requirement = candidate.get("vent_chamber_initial_requirement", {})
                            if (requirement.get("minimum") != "fire_partition_type_1"
                                    or requirement.get("minimum_fire_resistance") != "EI45"
                                    or not requirement.get("selected")):
                                raise ValueError("для границы венткамеры не подтверждён минимум: перегородка 1-го типа EI 45")
                            higher = requirement.get("higher_requirement_applied")
                            if higher is True and not requirement.get("higher_requirement_basis"):
                                raise ValueError("для более строгой преграды венткамеры не указано отдельное основание")
                            if higher is not True and requirement.get("selected") != "fire_partition_type_1":
                                raise ValueError("выбранная преграда венткамеры должна быть перегородкой 1-го типа либо иметь подтверждённое более строгое требование")
                    if drawing_excluded and adjacent == "stairwell":
                        normalized_requirement = "".join(str(edge.get("normative_requirement", "")).upper().split())
                        if "EI45" not in normalized_requirement:
                            raise ValueError("для исключённой из графики стены ЛК у венткамеры нужно сохранить требование EI 45 в реестре")
            if room.get("openings_reviewed") is not True:
                raise ValueError("не завершена проверка дверей, окон и других проёмов")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{room.get('key')}: {exc}")
    for issue in plan.get("unresolved", []):
        errors.append("Не решено: " + str(issue))
    return errors


class BarrierWorkflow:
    def __init__(self, app, bridge_factory=None):
        self.app = app
        self.bridge_factory = bridge_factory or app.control._bridge

    def folder(self, slug, run_id):
        loaded = self.app.runs.load(slug, run_id)
        if loaded["run"]["mode"] != MODE:
            raise ValueError("Выберите запуск «Оценка Преград»")
        return Path(loaded["folder"]), loaded["run"]

    def freshness(self, slug, run):
        if run["knowledge_digest"] != self.app.knowledge.digest_for_mode(MODE):
            raise ValueError("Правила изменены. Создайте новый запуск ПП")
        if project_digest(run["project_snapshot"]) != project_digest(self.app.projects.load(slug)):
            raise ValueError("Паспорт изменён. Создайте новый запуск ПП")
        rules=self.app.runs.run_dir(slug,run["run_id"]) / "ПРАВИЛА_ЗАПУСКА.md"
        if not rules.is_file() or hashlib.sha256(rules.read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest()!=run["knowledge_digest"]:
            raise ValueError("Закреплённая версия правил отсутствует или изменена. Создайте новый запуск ПП")

    def prepare(self, slug, run_id, plan):
        folder, _ = self.folder(slug, run_id)
        with normative_update_lock(folder / "barrier.lock"):
            return self._prepare(slug, run_id, plan)

    def _prepare(self, slug, run_id, plan):
        folder, run = self.folder(slug, run_id)
        previous = read_json(folder / "ПЛАН_ПП.json", {})
        if previous.get("status") not in {None, "prepared"}:
            raise ValueError("План уже применялся. Для новых решений создайте новый запуск")
        self.freshness(slug, run)
        profile = read_json(self.app.paths.knowledge / "barrier_drawing_profile.json")
        errors = preparation_errors(run["project_snapshot"], profile) + validate_plan(plan, run["project_snapshot"])
        if errors:
            raise ValueError("\n".join(errors))
        plan = copy.deepcopy(plan)
        data = run["project_snapshot"][MODE]
        category_file = data["category_source"]
        input_files = [{"path":category_file.get("path") or category_file.get("snapshot_path"),
                        "sha256":category_file["sha256"], "role":"categories"}, *copy.deepcopy(data.get("attachments", []))]
        for record in input_files:
            path = Path(record.get("path") or "")
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=record.get("sha256"):
                raise ValueError("Не подтверждена сохранённая версия вводного документа: " + str(path))
        for source in plan["sources"]:
            if not source.get("quote") or not source.get("interpretation") or source.get("page", 0) < 1:
                raise ValueError("Для источника нужны страница, прочитанный фрагмент и его применимость")
            if source.get("kind", "norm")=="norm" and source.get("full_context_read") is not True:
                raise ValueError("Нужна отметка чтения полного относящегося контекста нормы")
            record = self.read_source(run["project_snapshot"], source)
            if record.get("source_matches_index") is False:
                raise ValueError(f"{source['id']}: источник изменён; обновите нормативный индекс и перечитайте документ")
            clean = lambda x: " ".join(str(x).split()).casefold()
            if clean(source["quote"]) not in clean(record.get("text", "")):
                raise ValueError(f"{source['id']}: цитата не найдена на указанной странице")
            source["sha256"] = record["source_sha256"]
            source["source_path"] = record["source_path"]
            if record.get("visual_review_required") and source.get("visual_reviewed") is not True:
                raise ValueError(f"{source['id']}: требуется сверка распознанной страницы с изображением")
        token = uuid4().hex[:12]
        source_dxf = folder / ("ПП_исходный_" + token + ".dxf")
        with self.bridge_factory() as cad:
            info = cad.call("get_drawing_info")
            expected = run["project_snapshot"]["dwg"]["path"]
            assert_drawing(info, expected)
            source_hash = hashlib.sha256(Path(expected).read_bytes()).hexdigest()
            cad.call_write("save_drawing", {"filePath": str(source_dxf), "version": "2013"})
            assert_drawing(cad.call("get_drawing_info"), expected)
        doc = library().readfile(source_dxf)
        handles = set()
        for record in [*plan["items"], *plan["rooms"], *plan["legend"]]:
            handles.update(str(h).upper() for h in record.get("source_handles", []))
            if record.get("existing_handle"):
                handles.add(record["existing_handle"].upper())
        missing = sorted(h for h in handles if doc.entitydb.get(h) is None)
        if missing:
            raise ValueError("В открытом DWG отсутствуют исходные объекты: " + ", ".join(missing[:20]))
        for entry in plan["legend"]:
            if entry.get("source_handles"):
                entities = [doc.entitydb[h.upper()] for h in entry["source_handles"]]
                colors = [e.dxf.color if e.dxf.color != 256 else doc.layers.get(e.dxf.layer).dxf.color for e in entities]
                texts = [e.plain_text() if e.dxftype()=="MTEXT" else e.dxf.text for e in entities if e.dxftype() in {"TEXT","MTEXT"}]
                if entry["color_index"] not in colors or not any(entry["name"].casefold() in t.casefold() for t in texts):
                    raise ValueError(f"{entry['key']}: цвет или название не подтверждены исходной строкой легенды; укажите handles линии и текста")
                if not any(e.dxftype() in {"LINE","LWPOLYLINE"} and e.dxf.lineweight==50 and color==entry["color_index"] for e,color in zip(entities,colors)):
                    raise ValueError(f"{entry['key']}: исходная линия легенды должна иметь соответствующий цвет и явный вес 50")
        state = {"schema_version": 1, "run_id": run_id, "status": "prepared", "plan": plan,
            "plan_digest": digest(plan), "prepared_at": utc_now(), "source_dxf": str(source_dxf),
            "source_dxf_sha256": hashlib.sha256(source_dxf.read_bytes()).hexdigest(),
            "source_path": expected, "source_disk_sha256": source_hash,
            "knowledge_digest": run["knowledge_digest"], "profile": profile, "input_files":input_files}
        write_json(folder / "ПЛАН_ПП.json", state)
        atomic_write_text(folder / "ПЛАН_ПП.md", self.summary(state))
        self.sync_registry(folder, state)
        return {"status": "prepared", "items": len(plan["items"]), "rooms": len(plan["rooms"]),
                "plan_file": str(folder / "ПЛАН_ПП.md"), "message": "Решения подготовлены. Построение отдельной копии — кнопкой приложения."}

    def read_source(self, project, source):
        if source.get("kind", "norm") == "norm":
            return self.app.norms.read_document(source["relative_path"], page=source["page"],
                                                offset=source.get("offset", 0), max_chars=40000)
        if source.get("kind") != "attachment":
            raise ValueError("Источник: допустимы norm или attachment")
        path = Path(source["path"]).resolve()
        registered = {Path(a["path"]).resolve():a for a in project.get(MODE, {}).get("attachments", [])}
        if path not in registered:
            raise ValueError("Документ должен быть приложен к паспорту ПП")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if sha != registered[path]["sha256"]:
            raise ValueError("Приложенный документ изменён после фиксации")
        from .document_text import DocumentTextExtractor
        extracted = DocumentTextExtractor(self.app.paths.data / "attachment_text").extract(path, sha)
        page = next((p for p in extracted.get("pages", []) if p["page"]==source["page"]), None)
        if not page or not page.get("text"):
            raise ValueError("В документе нет читаемого текста указанной страницы / части")
        return {"text":page["text"], "source_sha256":sha, "source_path":str(path),
                "visual_review_required":extracted.get("visual_review_required", True)}

    @staticmethod
    def integrity_errors(state):
        errors = []
        if digest(state.get("plan")) != state.get("plan_digest"):
            errors.append("Подготовленные решения изменены вне процедуры подготовки")
        if state.get("receipt") and digest(state["receipt"]) != state.get("receipt_digest"):
            errors.append("Протокол построения изменён")
        for source in state.get("plan", {}).get("sources", []):
            path = Path(source.get("source_path", ""))
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source.get("sha256"):
                errors.append("Изменён или недоступен прочитанный источник: " + source.get("id", ""))
        for record in state.get("input_files", []):
            path = Path(record["path"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=record["sha256"]:
                errors.append("Изменена сохранённая версия вводного документа: " + str(path))
        return errors

    @staticmethod
    def sync_registry(folder, state):
        path = folder / "РЕЕСТР_ПРОВЕРКИ.json"
        registry = read_json(path, {})
        registry.setdefault(MODE, {}).update({"status":state["status"], "plan_digest":state["plan_digest"],
            "requirements":state["plan"]["items"], "reviewed_rooms":state["plan"]["rooms"],
            "sources":state["plan"]["sources"], "created_entities":state.get("receipt", {}).get("created", []),
            "unresolved":state.get("verification", {}).get("errors", []),
            "verification":state.get("verification"), "result_dwg":state.get("result_dwg")})
        registry.setdefault("dwg_actions", {}).update({"work_completed":state["status"]=="verified",
            "source_dwg":state["source_path"], "result_dwg":state.get("result_dwg"),
            "executor":"barrier_workflow_copy", "source_not_overwritten":state.get("verification", {}).get("ok") is True})
        write_json(path, registry)
        run = read_json(folder / "run.json")
        run["status"] = "completed" if state["status"]=="verified" else "in_progress"
        run["updated_at"] = utc_now()
        write_json(folder / "run.json", run)

    @staticmethod
    def summary(state):
        plan = state["plan"]
        lines = ["# План схемы противопожарных преград", "", plan["scope"]["description"], "",
                 "Результат строится в отдельной копии; исходный DWG на диске не перезаписывается.", "",
                 "| Элемент | Вид | Основание |", "|---|---|---|"]
        for item in plan["items"]:
            lines.append(f"| {item['id']} | {item['kind']} | {item.get('basis', item.get('room_key', 'Оформление'))} |")
        return "\n".join(lines)+"\n"

    def status(self, slug, run_id):
        folder, run = self.folder(slug, run_id)
        state = read_json(folder / "ПЛАН_ПП.json", {})
        errors = []
        try:
            self.freshness(slug, run)
        except ValueError as exc:
            errors.append(str(exc))
        if state:
            errors += self.integrity_errors(state)
            result = Path(state.get("result_dwg") or "")
            if state.get("status")=="verified" and (not result.is_file() or hashlib.sha256(result.read_bytes()).hexdigest()!=state.get("result_sha256")):
                errors.append("Файл результата изменён после построения; требуется новая сверка")
        return {"status": state.get("status", "not_prepared"), "errors": errors,
                "items": len(state.get("plan", {}).get("items", [])),
                "result_dwg": state.get("result_dwg"), "verification": state.get("verification"),
                "report": str(folder / "ПЛАН_ПП.md")}

    def build_copy_from_ui(self, slug, run_id, *, expected_plan_digest=None):
        folder, run = self.folder(slug, run_id)
        with normative_update_lock(folder / "barrier.lock"):
            if expected_plan_digest is not None and read_json(folder / "ПЛАН_ПП.json", {}).get("plan_digest")!=expected_plan_digest:
                raise ValueError("План изменился после открытия просмотра. Откройте и подтвердите актуальный план")
            return self._build(folder, slug, run_id, run)

    def _build(self, folder, slug, run_id, run):
        self.freshness(slug, run)
        path = folder / "ПЛАН_ПП.json"
        state = read_json(path, {})
        if state.get("profile") != read_json(self.app.paths.knowledge / "barrier_drawing_profile.json"):
            raise ValueError("Профиль оформления плана отличается от сохранённых правил")
        if state.get("source_path") != run["project_snapshot"]["dwg"]["path"]:
            raise ValueError("Исходный путь плана отличается от паспорта запуска")
        if state.get("status") != "prepared" or digest(state.get("plan")) != state.get("plan_digest"):
            raise ValueError("Нужен неизменённый подготовленный план. Выполненный или прерванный план повторно не применяется")
        errors = self.integrity_errors(state)
        if errors:
            raise ValueError("\n".join(errors))
        for source in state["plan"]["sources"]:
            if hashlib.sha256(Path(source["source_path"]).read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError("Нормативный источник изменился после подготовки")
        source = Path(state["source_path"])
        build_source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        source_was_resaved = build_source_sha256 != state["source_disk_sha256"]
        if hashlib.sha256(Path(state["source_dxf"]).read_bytes()).hexdigest() != state["source_dxf_sha256"]:
            raise ValueError("Снимок исходного DWG изменён")
        result = source.with_name(source.stem + "__barrier_assessment__" + run_id + ".dwg")
        if result.exists() or result.resolve() == source.resolve():
            raise ValueError("Имя результата уже занято. Создайте новый запуск; перезапись запрещена")
        fresh = folder / "ПП_перед_построением.dxf"
        proposed = folder / "ПП_подготовленный_результат.dxf"
        baseline_doc = library().readfile(state["source_dxf"])
        baseline = model_snapshot(baseline_doc)
        support = supporting_snapshot(baseline_doc)
        with self.bridge_factory() as cad:
            assert_drawing(cad.call("get_drawing_info"), str(source))
            cad.call_write("save_drawing", {"filePath": str(fresh), "version": "2013"})
            assert_drawing(cad.call("get_drawing_info"), str(source))
            fresh_doc = library().readfile(fresh)
            if not equivalent(model_snapshot(fresh_doc),baseline) or supporting_changes(support, fresh_doc):
                raise ValueError("Открытый чертёж изменён после подготовки плана. Подготовьте решения заново")
            state["build_source_disk_sha256"] = build_source_sha256
            if source_was_resaved:
                state["source_resave_accepted"] = {
                    "prepared_sha256": state["source_disk_sha256"],
                    "build_sha256": build_source_sha256,
                    "accepted_at": utc_now(),
                    "validation": "fresh_dxf_equivalent_to_prepared_source_dxf",
                }
            state["status"] = "building"
            write_json(path, state)
            try:
                receipt = build_dxf(fresh, proposed, state["plan"], state["profile"])
                state["receipt"] = receipt
                state["receipt_digest"] = digest(receipt)
                state["result_dwg"] = str(result)
                write_json(path, state)
                cad.call_write("open_drawing", {"path": str(proposed)})
                assert_drawing(cad.call("get_drawing_info"), str(proposed))
                cad.call_write("save_drawing", {"filePath": str(result), "version": "2013"})
                assert_drawing(cad.call("get_drawing_info"), str(result))
                if hashlib.sha256(source.read_bytes()).hexdigest() != state["build_source_disk_sha256"]:
                    raise ValueError("Не подтверждена сохранность исходного файла")
                state["status"] = "built_needs_verification"
                state["built_at"] = utc_now()
                state["result_sha256"] = hashlib.sha256(result.read_bytes()).hexdigest()
                write_json(path, state)
            except Exception as exc:
                state["status"] = "interrupted_requires_review"
                state["error"] = str(exc)
                write_json(path, state)
                self.sync_registry(folder, state)
                raise
        return self._verify(slug, run_id)

    def verify(self, slug, run_id):
        folder, _ = self.folder(slug, run_id)
        with normative_update_lock(folder / "barrier.lock"):
            return self._verify(slug, run_id)

    def _verify(self, slug, run_id):
        folder, run = self.folder(slug, run_id)
        self.freshness(slug, run)
        path = folder / "ПЛАН_ПП.json"
        state = read_json(path, {})
        if state.get("status") not in {"built_needs_verification", "verified", "verification_failed"}:
            raise ValueError("Сначала постройте отдельную копию по подготовленному плану")
        integrity = self.integrity_errors(state)
        if integrity:
            raise ValueError("\n".join(integrity))
        export = folder / ("ПП_контроль_" + uuid4().hex[:10] + ".dxf")
        with self.bridge_factory() as cad:
            assert_drawing(cad.call("get_drawing_info"), state["result_dwg"])
            cad.call_write("save_drawing", {"filePath": str(export), "version": "2013"})
            assert_drawing(cad.call("get_drawing_info"), state["result_dwg"])
        doc = library().readfile(export)
        errors = verify_entities(doc, state["receipt"]["created"])
        expected_handles = set(state["receipt"]["source_entities"])
        expected_handles.update(h for r in state["receipt"]["created"] for h in r["handles"])
        extra = set(model_snapshot(doc)) - expected_handles
        if extra:
            errors.append("В модели появились объекты вне подготовленного плана: " + ", ".join(sorted(extra)[:20]))
        errors += ["Изменены блок / слой / стиль: " + x for x in supporting_changes(state["receipt"]["result_supporting"], doc)]
        changes = protected_changes(state["receipt"]["source_entities"], doc)
        if changes["missing"] or changes["changed"]:
            errors.append("Изменены исходные элементы: " + str(changes)[:700])
        accepted_source_sha256 = state.get("build_source_disk_sha256", state["source_disk_sha256"])
        if hashlib.sha256(Path(state["source_path"]).read_bytes()).hexdigest() != accepted_source_sha256:
            errors.append("Изменён исходный DWG на диске")
        errors += validate_plan(state["plan"], run["project_snapshot"])
        state["verification"] = {"ok": not errors, "errors": errors, "checked_at": utc_now(),
            "checked_export": str(export), "items": len(state["receipt"]["created"]),
            "coverage": "Контуры и решения по каждой стороне помещений, перечисленных в плане помощника",
            "normative_review": "Основания и применимость проверены автором решений; программа проверяет их наличие, источники и графическое выполнение"}
        state["status"] = "verified" if not errors else "verification_failed"
        state["result_sha256"] = hashlib.sha256(Path(state["result_dwg"]).read_bytes()).hexdigest()
        write_json(path, state)
        self.sync_registry(folder, state)
        report = ["# Проверка схемы ПП", "", "Графическая проверка пройдена." if not errors else "Проверка не пройдена.",
            "", "Файл: " + state["result_dwg"], "", state["verification"]["coverage"], "", state["verification"]["normative_review"]]
        report.extend("- " + e for e in errors)
        atomic_write_text(folder / "ПРОВЕРКА_СХЕМЫ_ПП.md", "\n".join(report)+"\n")
        return {**state["verification"], "status": state["status"], "result_dwg": state["result_dwg"],
                "report": str(folder / "ПРОВЕРКА_СХЕМЫ_ПП.md")}
