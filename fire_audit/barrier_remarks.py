"""Fail-closed completion gate for every object in the source explanations layer.

These pure functions do not read CAD or files. The capture/import boundary must
provide a complete, independently retained inventory; a checksum only detects
changes to that inventory, it does not establish where the evidence came from.
This gate supplements, and never replaces, the other barrier completion gates.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .barriers import SOURCE_EXPLANATIONS_LAYER, LAYER


def new_source_remarks() -> dict[str, Any]:
    """Return a fresh, deliberately incomplete register for a new assessment."""
    return {
        "inventory": {
            "status": "pending",
            "layer": SOURCE_EXPLANATIONS_LAYER,
            "source_path": "",
            "scope": "all",
            "captured_at": "",
            "digest": "",
            "entities": [],
            "matched": 0,
            "layer_exists": None,
        },
        "decisions": [],
        "none_found_reason": "",
    }


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _handle(value: Any) -> str:
    return value.strip().upper() if _text(value) else ""


def _choice(value: Any, choices: tuple[str, ...]) -> bool:
    return isinstance(value, str) and value in choices


def inventory_digest(inventory: dict[str, Any]) -> str:
    """Hash complete raw evidence, independent of ordering, handle case or time.

All raw entity properties (including text, attributes and geometry) participate
in the digest. Only each entity's top-level handle is normalized. Invalid or
non-JSON input raises ValueError; the completion validator converts this into
a blocking error rather than crashing.
    """
    try:
        if not isinstance(inventory, dict):
            raise ValueError("Инвентаризация должна быть объектом")
        entities = inventory.get("entities")
        if not isinstance(entities, list) or any(not isinstance(item, dict) for item in entities):
            raise ValueError("Объекты инвентаризации должны быть списком объектов")
        normalized = []
        for entity in entities:
            handle = _handle(entity.get("handle"))
            if not handle:
                raise ValueError("У исходного объекта отсутствует строковый handle")
            normalized.append({**entity, "handle": handle})
        normalized.sort(key=lambda entity: entity["handle"])
        payload = {key: inventory.get(key) for key in
                   ("layer", "source_path", "scope", "layer_exists")}
        payload["entities"] = normalized
        for key in ("tracked_dimensions", "dimension_layer_evidence", "dimension_layer_journal_digest"):
            if key in inventory:
                payload[key] = inventory[key]
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                               allow_nan=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError(f"Невозможно вычислить контрольную сумму инвентаризации: {exc}") from exc


def _has_text_content(entity: dict[str, Any]) -> bool:
    for container in (entity, entity.get("properties", {})):
        if isinstance(container, dict) and any(
            _text(container.get(key)) for key in ("text", "textString", "contents", "content")
        ):
            return True
    return False


def _is_text_entity(entity: dict[str, Any]) -> bool:
    entity_type = entity.get("type")
    return isinstance(entity_type, str) and entity_type.upper() in (
        "TEXT", "DBTEXT", "MTEXT", "ATTRIB", "ATTDEF", "ATTRIBUTE", "ATTRIBUTEDEFINITION",
        "MULTILEADER", "MLEADER", "ACDBTEXT", "ACDBMTEXT", "ACDBMLEADER",
        "ACDBATTRIBUTE", "ACDBATTRIBUTEDEFINITION", "ATTRIBUTEREFERENCE", "ACDBATTRIBUTEREFERENCE",
    )


def source_remarks_errors(registry: dict[str, Any],
                          task_variant: str = "full_execution") -> list[str]:
    """Return concrete blocking errors for the source-remark completion gate.

The same coverage requirement applies to drawing execution and Word analysis;
result_refs may therefore identify a CAD object, registry item or report section.
Empty or absent layers need a successful complete capture and an explanation.
    """
    errors: list[str] = []
    prefix = "Исходные замечания"
    if not _choice(task_variant, ("full_execution", "analysis_word")):
        errors.append(f"{prefix}: неизвестный вариант оценки преград")
    data = registry.get("barrier_assessment") if isinstance(registry, dict) else None
    source = data.get("source_remarks") if isinstance(data, dict) else None
    if not isinstance(source, dict):
        return errors + [f"{prefix}: отсутствует пообъектный реестр source_remarks"]
    inventory = source.get("inventory")
    if not isinstance(inventory, dict):
        return errors + [f"{prefix}: отсутствует полная инвентаризация слоя"]
    if inventory.get("status") != "complete":
        errors.append(f"{prefix}: инвентаризация слоя не завершена успешно")
    if inventory.get("layer") != SOURCE_EXPLANATIONS_LAYER:
        errors.append(f"{prefix}: требуется точный слой «{SOURCE_EXPLANATIONS_LAYER}»")
    if inventory.get("scope") != "all":
        errors.append(f"{prefix}: инвентаризация должна охватывать весь слой (scope=all)")
    source_path = inventory.get("source_path")
    if not _text(source_path) or not (
        PureWindowsPath(source_path).is_absolute() or PurePosixPath(source_path).is_absolute()
    ):
        errors.append(f"{prefix}: не указан абсолютный путь исходного чертежа")
    if not _text(inventory.get("captured_at")):
        errors.append(f"{prefix}: не указано время получения исходных объектов")
    layer_exists = inventory.get("layer_exists")
    if type(layer_exists) is not bool:
        errors.append(f"{prefix}: наличие слоя должно быть явно подтверждено логическим значением")
    for key in ("truncated", "partial", "isPartial", "has_more", "hasMore"):
        if key in inventory and inventory[key] is not False:
            errors.append(f"{prefix}: выгрузка неполная или не подтверждена полнота ({key})")
    for key in ("next_cursor", "nextCursor", "continuation_token"):
        if inventory.get(key) not in (None, ""):
            errors.append(f"{prefix}: не дочитана следующая часть слоя ({key})")

    entities = inventory.get("entities")
    if not isinstance(entities, list):
        errors.append(f"{prefix}: исходные объекты должны быть списком")
        entities = []
    matched = inventory.get("matched")
    if type(matched) is not int or matched < 0 or matched != len(entities):
        errors.append(f"{prefix}: число найденных объектов matched не совпадает с полной выгрузкой")
    if layer_exists is False and entities:
        errors.append(f"{prefix}: отсутствующий слой не может содержать исходные объекты")
    tracked = inventory.get("tracked_dimensions", [])
    if not isinstance(tracked, list):
        errors.append(f"{prefix}: tracked_dimensions должен быть отдельным списком исходных обмеров")
        tracked = []
    tracked_handles = set()
    for item in tracked:
        if not isinstance(item, dict):
            errors.append(f"{prefix}: перенесённый исходный размер должен быть объектом")
            continue
        kind = str(item.get("type", "")).casefold().removeprefix("acdb").replace("_", "")
        if not kind.endswith("dimension") or item.get("layer") != LAYER:
            errors.append(f"{prefix}: исключение слоя только для рабочего DIMENSION основного слоя ПП")
        tracked_handles.add(_handle(item.get("handle")))
    if tracked and (not inventory.get("dimension_layer_journal_digest") or not isinstance(inventory.get("dimension_layer_evidence"), dict)):
        errors.append(f"{prefix}: нет независимого журнала и свежей проверки перенесённых обмеров")
    entities = entities + tracked
    handles: set[str] = set()
    entities_by_handle: dict[str, dict[str, Any]] = {}
    for number, entity in enumerate(entities, 1):
        label = f"{prefix}, исходный объект № {number}"
        if not isinstance(entity, dict):
            errors.append(f"{label}: требуется объект с handle, type, layer и space")
            continue
        handle = _handle(entity.get("handle"))
        if not handle:
            errors.append(f"{label}: отсутствует непустой строковый handle")
        elif handle in handles:
            errors.append(f"{prefix}: повтор исходного handle {handle}")
        else:
            handles.add(handle)
            entities_by_handle[handle] = entity
        if handle:
            label = f"{prefix}, объект {handle}"
        for key in ("type", "space"):
            if not _text(entity.get(key)):
                errors.append(f"{label}: не заполнено строковое поле {key}")
        if (not isinstance(entity.get("layer"), str)
                or (entity["layer"].casefold() != SOURCE_EXPLANATIONS_LAYER.casefold() and handle not in tracked_handles)):
            errors.append(f"{label}: объект относится к другому или неуказанному слою")
        if _is_text_entity(entity) and not _has_text_content(entity):
            errors.append(f"{label}: не сохранён полный исходный текст")
    digest = inventory.get("digest")
    if not isinstance(digest, str) or len(digest) != 64 or any(
        character not in "0123456789abcdefABCDEF" for character in digest
    ):
        errors.append(f"{prefix}: отсутствует корректная контрольная сумма SHA-256")
    else:
        try:
            if inventory_digest(inventory) != digest.lower():
                errors.append(f"{prefix}: контрольная сумма не совпадает; исходная выгрузка изменена")
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")

    decisions = source.get("decisions")
    if not isinstance(decisions, list):
        errors.append(f"{prefix}: решения должны быть списком")
        decisions = []
    seen_ids: set[str] = set()
    covered: dict[str, str] = {}
    remark_count = 0
    for number, decision in enumerate(decisions, 1):
        label = f"{prefix}, решение № {number}"
        if not isinstance(decision, dict):
            errors.append(f"{label}: требуется объект решения")
            continue
        decision_id = decision.get("id")
        if not _text(decision_id):
            errors.append(f"{label}: отсутствует непустой строковый id")
        else:
            label = f"{prefix}, решение {decision_id.strip()}"
            normalized_id = decision_id.strip().casefold()
            if normalized_id in seen_ids:
                errors.append(f"{label}: повтор id решения")
            seen_ids.add(normalized_id)
        entity_ids = decision.get("source_entity_ids")
        if not isinstance(entity_ids, list) or not entity_ids:
            errors.append(f"{label}: не указаны исходные source_entity_ids")
        else:
            for entity_id in entity_ids:
                handle = _handle(entity_id)
                if not handle:
                    errors.append(f"{label}: исходный handle должен быть непустой строкой")
                    continue
                if handle not in handles:
                    errors.append(f"{label}: неизвестный исходный handle {handle}")
                if handle in covered:
                    errors.append(f"{label}: handle {handle} уже учтён в {covered[handle]}")
                else:
                    covered[handle] = label
        decision_digest = decision.get("inventory_digest")
        if not isinstance(decision_digest, str) or not isinstance(digest, str) or (
            len(decision_digest) != 64 or decision_digest.lower() != digest.lower()
        ):
            errors.append(f"{label}: решение не связано с актуальной инвентаризацией (inventory_digest)")
        kind = decision.get("kind")
        if not _choice(kind, ("remark", "non_remark")):
            errors.append(f"{label}: kind должен быть remark или non_remark")
        if decision.get("status") != "resolved":
            errors.append(f"{label}: замечание не решено (требуется status=resolved)")
        if not _text(decision.get("summary")):
            errors.append(f"{label}: отсутствует содержание и итог решения (summary)")
        disposition = decision.get("decision")
        refs = decision.get("result_refs")
        refs_valid = isinstance(refs, list) and all(_text(ref) for ref in refs)
        if not refs_valid:
            errors.append(f"{label}: result_refs должен быть списком непустых строк")
        elif len({ref.strip().casefold() for ref in refs}) != len(refs):
            errors.append(f"{label}: повтор ссылки в result_refs")
        if kind == "non_remark":
            if disposition != "non_remark":
                errors.append(f"{label}: служебный объект требует decision=non_remark")
            if not _text(decision.get("basis")):
                errors.append(f"{label}: нет обоснования, почему объект не является замечанием")
            if not isinstance(entity_ids, list) or len(entity_ids) != 1:
                errors.append(f"{label}: non_remark требует отдельного решения для одного исходного объекта")
            elif (entity := entities_by_handle.get(_handle(entity_ids[0]))) and (
                _is_text_entity(entity) or _has_text_content(entity)
            ):
                if not _choice(decision.get("non_remark_kind"),
                               ("legend", "heading", "measurement", "reference_note")):
                    errors.append(f"{label}: текстовый объект требует явной классификации non_remark_kind")
                if not _text(decision.get("context_review")):
                    errors.append(f"{label}: для служебного текста не рассмотрен контекст (context_review)")
        elif kind == "remark":
            remark_count += 1
            if not _choice(disposition, ("applied", "already_satisfied", "not_applicable", "superseded")):
                errors.append(f"{label}: не выбрано допустимое окончательное решение")
            if decision.get("all_requirements_reviewed") is not True:
                errors.append(f"{label}: не подтверждено рассмотрение всех требований замечания")
            if not _text(decision.get("context_review")):
                errors.append(f"{label}: не рассмотрены ответы заказчика, даты, варианты и цели линий")
            if not _text(decision.get("basis")):
                errors.append(f"{label}: у решения нет обоснования basis")
            if disposition in ("applied", "already_satisfied", "superseded") and not (
                refs_valid and refs
            ):
                errors.append(f"{label}: нет ссылки на результат или актуальную редакцию (result_refs)")
    missing = sorted(handles - set(covered))
    if missing:
        errors.append(f"{prefix}: отсутствует решение для исходных объектов: {', '.join(missing)}")
    if not remark_count and not _text(source.get("none_found_reason")):
        errors.append(f"{prefix}: отсутствие замечаний не обосновано (none_found_reason)")
    return errors
