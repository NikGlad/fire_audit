"""Read-only, fail-closed inventory of the mandatory source explanations layer.

This module deliberately does not open, save, switch or modify drawings.  A block
or proxy cannot be counted as inspected merely because its insertion was read.
"""
from __future__ import annotations

import copy
import ntpath
import os
import re
from pathlib import Path
from typing import Any

from .barrier_remarks import SOURCE_EXPLANATIONS_LAYER, inventory_digest
from .io_utils import utc_now


PAGE_SIZE = 500
MAX_OBJECTS = 100_000
_SUPPORTED_TYPES = {
    "line", "polyline", "lwpolyline", "polyline2d", "polyline3d", "2dpolyline",
    "3dpolyline", "arc", "circle", "ellipse", "hatch", "text", "mtext", "dbtext",
    "dimension", "leader", "mleader", "multileader", "spline", "solid", "solid3d",
    "3dsolid", "region", "point", "ray", "xline", "trace", "face", "face3d",
    "3dface", "wipeout", "viewport", "attribute", "attributedefinition",
    "attributereference", "attdef", "mline",
}
_DIAGNOSTIC_STATE_KEYS = {"captured_at", "timestamp", "elapsedMs", "durationMs"}


def _path_key(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Коннектор не подтвердил полный путь открытого DWG")
    value = value.strip()
    if ntpath.isabs(value) and (ntpath.splitdrive(value)[0] or value.startswith("\\\\")):
        return ntpath.normcase(ntpath.normpath(value))
    if not Path(value).is_absolute():
        raise ValueError("Для контроля замечаний нужен полный путь DWG")
    return os.path.normcase(os.path.normpath(value))


def _integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"Неполный ответ коннектора: некорректное поле {name}")
    return value


def _reject_incomplete(value: Any, context: str) -> None:
    """Read error flags without interpreting the contents of annotation text."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in {"error", "errors", "propertieserror", "iserror",
                              "truncated", "istruncated", "propertiestruncated",
                              "partial", "ispartial", "unknownfields"} and item:
                raise ValueError(f"Неполное чтение {context}: {key}={item}")
            if normalized == "success" and item is False:
                raise ValueError(f"Ошибка чтения {context}")
            _reject_incomplete(item, context)
    elif isinstance(value, list):
        for item in value:
            _reject_incomplete(item, context)


def _drawing_state(value: Any, allowed: set[str]) -> tuple[str, dict]:
    if not isinstance(value, dict):
        raise ValueError("Коннектор не вернул состояние открытого DWG")
    _reject_incomplete(value, "состояния DWG")
    path = value.get("path")
    if _path_key(path) not in allowed:
        raise ValueError("Открыт другой DWG. Откройте исходник или разрешённую копию выбранного запуска")
    if type(value.get("modified")) is not bool or not value.get("lastSavedDate"):
        raise ValueError("Коннектор не подтвердил modified и lastSavedDate; полноту чтения установить нельзя")
    state = {key: copy.deepcopy(item) for key, item in value.items()
             if key not in _DIAGNOSTIC_STATE_KEYS}
    state["path"] = _path_key(path)
    return path, state


def _source_layer_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        raise ValueError("Коннектор не вернул полный список слоёв")
    _reject_incomplete(value, "списка слоёв")
    layers = value.get("layers")
    if not isinstance(layers, list) or _integer(value.get("count"), "layers.count") != len(layers):
        raise ValueError("Список слоёв неполон: count не соответствует layers")
    if value.get("hasMore", False) is not False:
        raise ValueError("Список слоёв усечён: есть непрочитанные слои")
    if "matched" in value and _integer(value["matched"], "layers.matched") != len(layers):
        raise ValueError("Список слоёв усечён: matched не соответствует layers")
    names = []
    for layer in layers:
        if not isinstance(layer, dict) or not isinstance(layer.get("name"), str) or not layer["name"]:
            raise ValueError("Некорректная запись списка слоёв")
        names.append(layer["name"])
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Список слоёв содержит повторные имена")
    return next((name for name in names if name.casefold() == SOURCE_EXPLANATIONS_LAYER.casefold()), None)


def _block_catalog(client) -> list[dict]:
    response = client.call("list_blocks", {"includeAnonymous": True, "includeXrefDependent": True})
    if not isinstance(response, dict):
        raise ValueError("Коннектор не вернул полный каталог определений блоков")
    _reject_incomplete(response, "каталога блоков")
    blocks = response.get("blocks")
    if not isinstance(blocks, list) or _integer(response.get("count"), "blocks.count") != len(blocks):
        raise ValueError("Каталог блоков неполон: count не соответствует blocks")
    if response.get("hasMore", False) is not False:
        raise ValueError("Каталог блоков усечён")
    if "matched" in response and _integer(response["matched"], "blocks.matched") != len(blocks):
        raise ValueError("Каталог блоков усечён: matched не соответствует blocks")
    for key in ("xrefDependentExcluded", "anonymousExcluded", "excludedCount"):
        if key in response and _integer(response[key], key) != 0:
            raise ValueError("Каталог блоков исключил вложенные определения: " + key)
    names = set()
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("name"), str) or not block["name"]:
            raise ValueError("Некорректное имя определения блока")
        name = block["name"].casefold()
        if name in names:
            raise ValueError("Каталог блоков содержит повторные определения")
        names.add(name)
        _integer(block.get("entityCount"), "block.entityCount")
        if "isLayout" in block and type(block["isLayout"]) is not bool:
            raise ValueError("Не установлен признак пространства определения блока")
        for flag in ("isXref", "xrefDependent", "isXrefDependent"):
            if flag in block and type(block[flag]) is not bool:
                raise ValueError("Не установлен признак внешней ссылки определения блока: " + flag)
        if (any(block.get(flag) is True for flag in ("isXref", "xrefDependent", "isXrefDependent"))
                or "|" in block["name"]):
            raise ValueError(
                f"Определение «{block['name']}» относится к внешней ссылке XREF. "
                "Коннектор не подтверждает полноту её вложенных данных, включая выгруженные или неразрешённые ссылки. "
                "Такую ссылку нельзя считать пустой; требуется отдельная проверка всех вложенных источников. "
                "Чертёж автоматически не изменялся."
            )
    return sorted(copy.deepcopy(blocks), key=lambda item: item["name"].casefold())


def _layer_counts(value: Any) -> dict[str, int]:
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, list):
        pairs = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Не распознан полный подсчёт объектов блока по слоям")
            layer = item.get("layer", item.get("name"))
            pairs.append((layer, item.get("count")))
    else:
        raise ValueError("Не распознан полный подсчёт объектов блока по слоям")
    counts = {}
    for layer, count in pairs:
        if not isinstance(layer, str) or not layer or layer.casefold() in counts:
            raise ValueError("Неверные или повторные слои в подсчёте определения блока")
        counts[layer.casefold()] = _integer(count, "block.byLayer.count")
    return counts


def _assert_no_nested_source(client, blocks: list[dict]) -> None:
    """Use whole-definition summaries, never the limited entity sample.

    Every definition is inspected, including unreferenced, anonymous and dependent
    definitions; a source-layer child on a layer-0 INSERT must not disappear from
    the inventory. Layout records alone are covered by the direct all-space read.
    """
    for block in blocks:
        if (block.get("isLayout") is True
                or re.fullmatch(r"\*(?:model_space|paper_space\d*)", block["name"].casefold())):
            continue
        response = client.call("get_block_definition", {
            "name": block["name"], "includeProperties": False, "limit": 1, "offset": 0,
        })
        if not isinstance(response, dict):
            raise ValueError("Не прочитано определение блока " + block["name"])
        _reject_incomplete(response, "определения блока " + block["name"])
        if "name" in response and response["name"] != block["name"]:
            raise ValueError("Коннектор вернул другое определение блока")
        summaries = [response[key] for key in ("byLayer", "countsByLayer", "entitiesByLayer") if key in response]
        nested_counts = response.get("counts")
        if isinstance(nested_counts, dict) and "byLayer" in nested_counts:
            summaries.append(nested_counts["byLayer"])
        if not summaries:
            raise ValueError("Неизвестная схема полного подсчёта слоёв блока " + block["name"])
        normalized = [_layer_counts(summary) for summary in summaries]
        if any(summary != normalized[0] for summary in normalized[1:]):
            raise ValueError("Противоречивые подсчёты слоёв блока " + block["name"])
        counts = normalized[0]
        if sum(counts.values()) != block["entityCount"]:
            raise ValueError("Неполный подсчёт слоёв или изменённое определение блока " + block["name"])
        if "entityCount" in response and _integer(response["entityCount"], "definition.entityCount") != block["entityCount"]:
            raise ValueError("Определение блока изменилось во время чтения")
        target = SOURCE_EXPLANATIONS_LAYER.casefold()
        if any(count and (layer == target or layer.rsplit("|", 1)[-1] == target)
               for layer, count in counts.items()):
            raise ValueError(
                f"В определении блока «{block['name']}» есть объекты слоя замечаний. "
                "Нужно раскрыть и проверить вложенные источники; прямое чтение слоя недостаточно. "
                "Чертёж автоматически не изменялся."
            )


def _entity(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Объекты слоя должны быть возвращены в формате objects")
    _reject_incomplete(value, "объекта слоя замечаний")
    handle = value.get("handle")
    if not isinstance(handle, str) or not re.fullmatch(r"[0-9A-Fa-f]+", handle):
        raise ValueError("Объект слоя замечаний не имеет корректного handle")
    if not isinstance(value.get("layer"), str) or value["layer"].casefold() != SOURCE_EXPLANATIONS_LAYER.casefold():
        raise ValueError(f"Коннектор вернул объект {handle} из другого слоя")
    entity_type = value.get("type")
    if not isinstance(entity_type, str) or not entity_type.strip():
        raise ValueError(f"Не установлен тип объекта {handle}")
    normalized = entity_type.casefold().removeprefix("acdb").replace("_", "")
    proxy = "proxy" in normalized or any(value.get(key) for key in ("isProxy", "proxy", "proxyInfo"))
    nested = normalized in {"blockreference", "insert", "minsertblock", "mtextattribute"}
    if proxy or nested:
        raise ValueError(
            f"Объект {handle} ({entity_type}) содержит неподдержанный блок, XREF или proxy. "
            "Нужно раскрыть и проверить вложенные источники; такой объект нельзя считать пустым или прочитанным. "
            "Чертёж автоматически не изменялся."
        )
    if normalized not in _SUPPORTED_TYPES and not normalized.endswith("dimension"):
        raise ValueError(f"Неподдержанный тип объекта {handle}: {entity_type}; полнота чтения не подтверждена")
    if normalized in {"mleader", "multileader"} and (
        value.get("blockContent") or value.get("blockId") or value.get("blockName")
        or str(value.get("contentType", "")).casefold() in {"block", "blockcontent", "1"}
    ):
        raise ValueError(f"Мультивыноска {handle} содержит блок: нужно проверить вложенный источник")
    result = copy.deepcopy(value)
    result["handle"] = handle.upper()
    return result


def _read_entities(client, layer_name: str | None) -> list[dict]:
    entities: list[dict] = []
    handles: set[str] = set()
    matched: int | None = None
    if layer_name is not None:
        while True:
            response = client.call("get_entities", {
                "layer": layer_name, "space": "all", "format": "objects",
                "includeProperties": True, "limit": PAGE_SIZE, "offset": len(entities),
            })
            if not isinstance(response, dict):
                raise ValueError("Коннектор не вернул страницу объектов слоя")
            _reject_incomplete(response, "страницы объектов слоя")
            page = response.get("entities")
            count = _integer(response.get("count"), "count")
            total = _integer(response.get("matched"), "matched")
            more = response.get("hasMore")
            if not isinstance(page, list) or count != len(page) or count > PAGE_SIZE or type(more) is not bool:
                raise ValueError("Некорректные count/entities/hasMore страницы объектов слоя")
            if total > MAX_OBJECTS:
                raise ValueError(f"Слой содержит более {MAX_OBJECTS} объектов: требуется отдельное полное чтение")
            if matched is None:
                matched = total
            elif matched != total:
                raise ValueError("Число объектов слоя изменилось между страницами")
            if len(entities) + count > total or more != (len(entities) + count < total):
                raise ValueError("Пагинация слоя неполна: matched и hasMore противоречат странице")
            if more and not count:
                raise ValueError("Получена пустая промежуточная страница слоя")
            for raw in page:
                entity = _entity(raw)
                if entity["handle"] in handles:
                    raise ValueError(f"Повтор объекта/страницы: handle {entity['handle']}")
                handles.add(entity["handle"])
                entities.append(entity)
            if not more:
                break
    return sorted(entities, key=lambda item: item["handle"])


def capture_source_layer(client, source_path: str, allowed_document_paths: list[str]) -> dict:
    """Capture every supported object, from model space and every paper layout.

    The digest and completeness flags are generated here, never supplied by the
    assistant.  Any doubtful page raises before a complete inventory is returned.
    A second full read catches edits with unchanged handles/counts in an already
    modified drawing, which get_drawing_info alone cannot detect.
    """
    _path_key(source_path)
    if not isinstance(allowed_document_paths, list) or not allowed_document_paths:
        raise ValueError("Не заданы разрешённые документы выбранного запуска")
    allowed = {_path_key(path) for path in allowed_document_paths}
    actual_path, before = _drawing_state(client.call("get_drawing_info", {}), allowed)
    layer_name = _source_layer_name(client.call("list_layers", {}))
    blocks_before = _block_catalog(client)
    _assert_no_nested_source(client, blocks_before)
    entities = _read_entities(client, layer_name)
    inventory = {
        "status": "complete", "layer": SOURCE_EXPLANATIONS_LAYER,
        "source_path": source_path, "captured_document_path": actual_path,
        "scope": "all", "captured_at": utc_now(), "entities": entities,
        "matched": len(entities), "layer_exists": layer_name is not None, "drawing_state": before,
        "actual_layer_name": layer_name, "nested_sources_checked": True,
    }
    digest = inventory_digest(inventory)
    second_layer_name = _source_layer_name(client.call("list_layers", {}))
    second_entities = _read_entities(client, second_layer_name)
    second = {**inventory, "entities": second_entities, "matched": len(second_entities),
              "layer_exists": second_layer_name is not None}
    if digest != inventory_digest(second) or second_layer_name != layer_name:
        raise ValueError("Содержимое слоя изменилось между двумя полными чтениями; снимок не принят")
    blocks_after = _block_catalog(client)
    if blocks_after != blocks_before:
        raise ValueError("Каталог блоков изменился во время чтения замечаний; снимок не принят")
    _assert_no_nested_source(client, blocks_after)
    _, after = _drawing_state(client.call("get_drawing_info", {}), allowed)
    if before != after:
        raise ValueError("Документ или его состояние изменились во время чтения замечаний; снимок не принят")
    inventory["captured_at"] = utc_now()
    inventory["digest"] = digest
    return inventory
