"""Narrow, read-only proof for layer changes of classified source dimensions.

The journal never authorizes text, geometry, style, new handles or nested objects.
Unknown native geometry blocks the exception instead of weakening the baseline.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
from pathlib import Path

from .barriers import LAYER, SOURCE_EXPLANATIONS_LAYER
from .barrier_remarks_capture import (_block_catalog, _assert_no_nested_source, _entity,
                                      _integer, _reject_incomplete, PAGE_SIZE, MAX_OBJECTS)
from .control_geometry import bounds, contains, digest, finite, point

JOURNAL_FILE = "ИЗМЕНЕНИЯ_СЛОЕВ_ОБМЕРОВ_ПП.jsonl"
ALLOWED_LAYERS = {LAYER, SOURCE_EXPLANATIONS_LAYER}


def dimension(entity):
    kind = str(entity.get("type", "")).casefold().removeprefix("acdb").replace("_", "")
    return kind == "dimension" or kind.endswith("dimension")


def layer_only(before, after):
    return (dimension(before) and dimension(after)
            and before.get("layer") in ALLOWED_LAYERS and after.get("layer") in ALLOWED_LAYERS
            and {k: v for k, v in before.items() if k != "layer"}
            == {k: v for k, v in after.items() if k != "layer"})


def journal(folder, baseline):
    path = Path(folder) / JOURNAL_FILE
    latest, previous_hash, count = {}, "", 0
    if not path.exists():
        return latest, previous_hash, count
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        recorded_hash = row.get("entry_digest")
        if (row.get("previous_digest") != previous_hash
                or recorded_hash != digest({k: v for k, v in row.items() if k != "entry_digest"})
                or row.get("baseline_digest") != baseline.get("digest")
                or row.get("run_id") != baseline.get("run_id")
                or row.get("source_path") != baseline.get("source_path")):
            raise ValueError("Журнал изменения слоёв повреждён или относится к другому исходному снимку")
        for change in row["changes"]:
            latest[change["handle"]] = copy.deepcopy(change)
        previous_hash, count = recorded_hash, count + 1
    return latest, previous_hash, count


def append_journal(folder, row):
    """A rollback is another entry. Existing bytes are never rewritten."""
    row = copy.deepcopy(row)
    row["entry_digest"] = digest(row)
    encoded = (json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    with (Path(folder) / JOURNAL_FILE).open("ab") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return row["entry_digest"]


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Нужно содержательное обоснование: " + label)
    return value.strip()


def prepare_changes(changes, baseline, inventory, decisions, previous):
    if not isinstance(changes, list) or not changes or len(changes) > 100:
        raise ValueError("changes должен содержать от 1 до 100 изменений рабочих размеров")
    if baseline.get("nested_sources_checked") is not True:
        raise ValueError("Не подтверждено отсутствие вложенных исходных размеров; исключение только для самостоятельных DIMENSION")
    originals = {row["handle"]: row for row in baseline["entities"]}
    by_id = {row["id"]: row for row in decisions}
    prepared, handles = [], set()
    for item in changes:
        if not isinstance(item, dict):
            raise ValueError("Каждое изменение должно быть объектом")
        handle = str(item.get("handle", "")).strip().upper()
        if handle in handles or not re.fullmatch(r"[0-9A-F]+", handle):
            raise ValueError("Нужны уникальные исходные handles")
        handles.add(handle)
        original = originals.get(handle)
        if not original or not dimension(original):
            raise ValueError("Исключение только для исходного самостоятельного DIMENSION: " + handle)
        if original.get("layer") != SOURCE_EXPLANATIONS_LAYER:
            raise ValueError("Первичный размер должен принадлежать исходному слою пояснений")
        for container in (original, original.get("properties", {})):
            if not isinstance(container, dict):
                continue
            for key in ("dimensionText", "textOverride", "text", "textString", "contents", "prefix", "suffix"):
                value = container.get(key)
                if value not in (None, "", "<>") and (not isinstance(value, str) or not re.fullmatch(r"\s*[+-]?\d+(?:[.,]\d+)?\s*", value)):
                    raise ValueError("DIMENSION содержит дополнительный текст; узкое исключение его не разрешает")
        if item.get("baseline_digest") != baseline.get("digest") or item.get("inventory_digest") != inventory.get("digest"):
            raise ValueError("Устаревшая контрольная сумма baseline/inventory; перечитайте данные")
        decision = by_id.get(item.get("classification_decision_id"), {})
        if (decision.get("kind") != "non_remark" or decision.get("decision") != "non_remark"
                or decision.get("non_remark_kind") != "measurement" or decision.get("status") != "resolved"
                or [str(h).upper() for h in decision.get("source_entity_ids", [])] != [handle]
                or decision.get("inventory_digest") != inventory.get("digest")):
            raise ValueError("Нужна предварительная отдельная классификация measurement: " + handle)
        _text(decision.get("basis"), "основание классификации")
        _text(decision.get("context_review"), "контекст исходного измерения")
        if item.get("target_layer") not in ALLOWED_LAYERS:
            raise ValueError("Разрешён перенос только между двумя рабочими слоями ПП")
        before_layer = previous.get(handle, {}).get("target_layer", original["layer"])
        if item["target_layer"] == before_layer and handle not in previous:
            raise ValueError("Слой не изменяется; журнал требует действительный переход")
        _text(item.get("parameter_review"), "измеряемый параметр")
        if not isinstance(item.get("ray_reviews"), list):
            raise ValueError("Нужен полный список проверенных связей ray_reviews")
        finite(item.get("measurement_value"))
        if not isinstance(item.get("measurement_points"), list) or len(item["measurement_points"]) != 2:
            raise ValueError("Нужны обе измеряемые точки")
        for value in item["measurement_points"]:
            point(value)
        for review in item["ray_reviews"]:
            if not isinstance(review, dict):
                raise ValueError("Нужен объект проверки связи луча")
            for key in ("ray_handle", "note_handle"):
                if not isinstance(review.get(key), str) or not re.fullmatch(r"[0-9A-Fa-f]+", review[key]):
                    raise ValueError("Нужен корректный handle: " + key)
            for key in ("note_excerpt", "parameter_review", "geometry_review"):
                _text(review.get(key), key)
        prepared.append({**copy.deepcopy(item), "handle": handle, "from_layer": before_layer,
                         "kind": "evidence_refresh" if item["target_layer"] == before_layer else "layer_change",
                         "classification": copy.deepcopy(decision)})
    return prepared


def _page_objects(client, filters):
    items, handles, expected = [], set(), None
    while True:
        result = client.call("get_entities", {**filters, "space": "all", "format": "objects",
                            "includeProperties": True, "limit": PAGE_SIZE, "offset": len(items)})
        _reject_incomplete(result, "полного поиска объектов DWG")
        page = result.get("entities")
        count, matched = _integer(result.get("count"), "count"), _integer(result.get("matched"), "matched")
        if (not isinstance(page, list) or len(page) != count or count > PAGE_SIZE or matched > MAX_OBJECTS
                or type(result.get("hasMore")) is not bool or (expected is not None and expected != matched)
                or len(items) + count > matched or result["hasMore"] != (len(items) + count < matched)
                or (result["hasMore"] and not count)):
            raise ValueError("Неполный поиск объектов DWG: некорректная пагинация")
        expected = matched
        for raw in page:
            row = _entity(raw, expected_layer=None)
            if row["handle"] in handles:
                raise ValueError("Повтор handle в полном поиске DWG")
            handles.add(row["handle"])
            items.append(row)
        if not result["hasMore"]:
            return sorted(items, key=lambda row: row["handle"])


def _box(client, handle):
    result = client.call("get_bounding_box", {"handles": [handle]})
    _reject_incomplete(result, "габарита объекта " + handle)
    if result.get("entitiesMeasured") != 1 or result.get("excludedCount", 0) != 0 or result.get("excluded"):
        raise ValueError("Нет полного фактического габарита объекта " + handle)
    return bounds(result)


def _properties(client, handles):
    if not handles:
        return {}
    result = client.call("get_entity_properties", {"handles": sorted(handles)})
    _reject_incomplete(result, "текстов адресующих замечаний")
    if result.get("success") is not True or result.get("count") != len(handles):
        raise ValueError("Не прочитан полный текст адресующего замечания")
    values = [_entity(row, expected_layer=None) for row in result.get("entities", [])]
    if len(values) != len(handles) or {row["handle"] for row in values} != set(handles):
        raise ValueError("Прочитаны другие или повторные тексты замечаний")
    return {row["handle"]: row for row in values}


def read_move_evidence(client, baseline, latest):
    """One full native pass; capture_source_layer invokes it twice and compares."""
    blocks = _block_catalog(client)
    _assert_no_nested_source(client, blocks, checked_layers=ALLOWED_LAYERS)
    all_dimensions = _page_objects(client, {"entityType": "Dimension"})
    if any(not dimension(row) for row in all_dimensions):
        raise ValueError("Полный поиск DIMENSION вернул объект другого типа")
    dimensions = {row["handle"]: row for row in all_dimensions if row["handle"] in latest}
    if set(dimensions) != set(latest):
        raise ValueError("Исходный DIMENSION отсутствует в полном DWG: " + ", ".join(sorted(set(latest) - set(dimensions))))
    rays = _page_objects(client, {"layer": LAYER, "entityTypes": ["Line", "Leader", "MLeader"]})
    for ray in rays:
        if str(ray["type"]).casefold().removeprefix("acdb") != "line" or ray["layer"] != LAYER:
            raise ValueError("Неполная/неподдержанная геометрия луча: допускаются проверенные самостоятельные LINE")
        point(ray.get("startPoint")), point(ray.get("endPoint"))
    note_handles = {str(review.get("note_handle", "")).upper()
                    for item in latest.values() for review in item["ray_reviews"]}
    notes = _properties(client, note_handles)
    dim_boxes = {handle: _box(client, handle) for handle in sorted(dimensions)}
    note_boxes = {handle: _box(client, handle) for handle in sorted(notes)}
    evidence = {"dimensions": dimensions, "dimension_bounds": dim_boxes,
                "all_working_rays": rays, "notes": notes, "note_bounds": note_boxes,
                "dimension_search_scope": "all_spaces_all_layers", "nested_sources_checked": True}
    validate_move_evidence(baseline, latest, evidence)
    return {"tracked_dimensions": [dimensions[h] for h in sorted(dimensions)
                                    if dimensions[h]["layer"] != SOURCE_EXPLANATIONS_LAYER],
            "dimension_layer_evidence": evidence}


def validate_move_evidence(baseline, latest, evidence):
    originals = {row["handle"]: row for row in baseline["entities"]}
    if evidence.get("dimension_search_scope") != "all_spaces_all_layers" or evidence.get("nested_sources_checked") is not True:
        raise ValueError("Нет полного поиска самостоятельных размеров и вложенных источников")
    if set(evidence.get("dimensions", {})) != set(latest):
        raise ValueError("Полный поиск не подтверждает все размеры журнала")
    for handle, request in latest.items():
        actual = evidence["dimensions"][handle]
        if actual.get("layer") != request["target_layer"] or not layer_only(originals[handle], actual):
            raise ValueError("Разрешено только изменение слоя того же исходного DIMENSION: " + handle)
        if abs(finite(actual.get("measurement")) - finite(request.get("measurement_value"))) > 0.000001:
            raise ValueError("Измеряемый параметр не совпадает с фактическим числом DIMENSION")
        actual_points = [point(actual.get("xLine1Point", actual.get("extensionLine1Point"))),
                         point(actual.get("xLine2Point", actual.get("extensionLine2Point")))]
        requested_points = request.get("measurement_points")
        if (not isinstance(requested_points, list) or len(requested_points) != 2
                or any(math.dist(a, point(b)) > 0.01 for a, b in zip(actual_points, requested_points))):
            raise ValueError("Измеряемые грани не подтверждены свежей геометрией DIMENSION")
        point(actual.get("textPosition"))
        height = finite(actual.get("textHeight"))
        if height <= 0:
            raise ValueError("Не подтверждён размер отрисованного размерного текста")
        dim_box = evidence["dimension_bounds"][handle]
        candidates = {ray["handle"]: ray for ray in evidence["all_working_rays"]
                      if ray.get("space") == actual.get("space") and any(
                          contains(dim_box, ray.get(key), tolerance=height) for key in ("startPoint", "endPoint"))}
        reviews = request["ray_reviews"]
        review_ids = [str(row.get("ray_handle", "")).upper() for row in reviews]
        if len(set(review_ids)) != len(review_ids) or set(review_ids) != set(candidates):
            raise ValueError("Не проверены все возможные адресующие лучи либо указан ложный луч; требуется адресное уточнение")
        if request["target_layer"] == SOURCE_EXPLANATIONS_LAYER:
            _text(request.get("absence_review"), "отсутствие адресующего луча после полного просмотра")
            if candidates:
                raise ValueError("Отсутствие адресующих лучей не доказано; перенос в пояснения заблокирован")
        elif not candidates:
            raise ValueError("Для основного слоя нужен хотя бы один проверенный адресующий луч")
        for review in reviews:
            ray = candidates[str(review["ray_handle"]).upper()]
            note_handle = str(review.get("note_handle", "")).upper()
            note = evidence["notes"].get(note_handle, {})
            kind = str(note.get("type", "")).casefold().removeprefix("acdb")
            if kind not in {"text", "dbtext", "mtext"} or note.get("space") != actual.get("space"):
                raise ValueError("Не подтверждён исходный текст адресующего требования в том же пространстве")
            contents = next((note[key] for key in ("text", "textString", "contents") if isinstance(note.get(key), str)), "")
            if _text(review.get("note_excerpt"), "выдержка из требования") not in contents:
                raise ValueError("Выдержка не совпадает со свежим текстом адресующего требования")
            _text(review.get("parameter_review"), "связь требования с измеряемым параметром")
            _text(review.get("geometry_review"), "связь луча с фактической областью размерного числа")
            start, end = point(ray["startPoint"]), point(ray["endPoint"])
            note_box = evidence["note_bounds"][note_handle]
            if math.dist(start, end) <= 0.01 or not any(
                contains(dim_box, target, tolerance=height) and contains(note_box, origin, tolerance=height)
                for origin, target in ((start, end), (end, start))
            ):
                raise ValueError("Свежая геометрия луча не связывает текст требования с указанным размером")
            if (ray.get("colorIndex", ray.get("color")) not in (256, "ByLayer")
                    or str(ray.get("linetype", "")).casefold() != "bylayer"
                    or ray.get("lineweight") not in (-1, "ByLayer")):
                raise ValueError("Адресующий луч не соответствует обязательным свойствам ByLayer")
