"""Rules and preparation checks for the separate fire-barrier assessment workflow.

Room suffixes express the user's drawing convention. They never calculate a
fire category or prove the applicability of a regulatory requirement.
"""
from __future__ import annotations

import math
import re
from typing import Any


MODE = "barrier_assessment"
LAYER = "экспертиза противопожарные преграды"
CATEGORY_LAYER = "экспертиза категории"
SOURCE_EXPLANATIONS_LAYER = LAYER + " пояснения"
CATEGORIES = {"А", "Б", "В1", "В2", "В3", "В4", "Г", "Д"}


def room_purpose_letter(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Не заполнено наименование помещения")
    words = re.findall(r"[а-я]+", name.casefold().replace("ё", "е"))
    return "С" if any(root in word for word in words for root in ("склад", "клад", "хран")) else "П"


def category_label(category: str, name: str) -> str:
    value = str(category).strip().upper()
    if value not in CATEGORIES:
        raise ValueError("Категория должна быть взята из утверждённого источника: А, Б, В1–В4, Г или Д")
    return value + room_purpose_letter(name)


def prepare_rooms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result, seen = [], set()
    for row in rows:
        floor, number = str(row.get("floor", "")).strip(), str(row.get("number", "")).strip()
        if not floor or not number:
            raise ValueError("Для сопоставления нужны этаж и номер помещения")
        key = (floor, number)
        if key in seen:
            raise ValueError(f"Повтор помещения: этаж {floor}, номер {number}")
        seen.add(key)
        result.append({**row, "floor": floor, "number": number,
                       "purpose_letter": room_purpose_letter(row.get("name", "")),
                       "label": category_label(row.get("category", ""), row.get("name", ""))})
    return result


def preparation_errors(project: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    data = project.get("barrier_assessment", {})
    errors = []
    if data.get("categories_approved") is not True or not data.get("category_approval_source"):
        errors.append("Не подтверждено утверждение категорий помещений и источник подтверждения")
    if not data.get("category_source", {}).get("sha256"):
        errors.append("Не зафиксирована утверждаемая версия таблицы категорий")
    if data.get("stu_status") not in {"absent", "provided"}:
        errors.append("Не установлено наличие СТУ")
    if data.get("stu_status") == "provided":
        if not data.get("stu_documents"):
            errors.append("Не представлены документы СТУ")
        if data.get("refrigeration_present") is True and data.get("refrigeration_equipment_rooms_confirmed") is not True:
            errors.append("Для СТУ нужно уточнить помещения с холодильными камерами, являющимися оборудованием")
    if data.get("additional_documents_reviewed") is not True:
        errors.append("Не завершён учёт дополнительных документов и ответов заказчика")
    if data.get("preparation_completed") is not True:
        errors.append("Не завершена подготовка планов к оценке ПП")
    if profile.get("status") != "verified" or profile.get("unresolved_fields"):
        errors.append("Не полностью подтверждены шрифты, размеры и оформление по образцу")
    try:
        prepared = prepare_rooms(data.get("rooms", []))
        if not prepared:
            errors.append("Таблица помещений не заполнена")
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def validate_created_entity(entity: dict[str, Any], role: str, legend: list[dict[str, Any]], barrier_key: str = "") -> list[str]:
    """Validate values read back from AutoCAD; caller must retain read evidence."""
    errors = []
    expected_layer = CATEGORY_LAYER if role == "category" else LAYER
    if entity.get("layer") != expected_layer:
        errors.append(f"Для объекта роли {role} требуется слой {expected_layer}")
    if role in {"barrier", "legend_line"}:
        if entity.get("lineWeight", entity.get("lineweight")) not in (50, "LineWeight050"):
            errors.append("Вес линии противопожарной преграды должен быть 50 (0,50 мм)")
        entry = next((item for item in legend if item.get("key") == barrier_key), None)
        if entry is None:
            errors.append("Тип преграды отсутствует в условных обозначениях")
        elif entity.get("colorIndex") != entry.get("color_index"):
            errors.append("Цвет преграды не соответствует условным обозначениям")
    if role in {"ray", "leader", "callout_ray"}:
        color = entity.get("colorIndex", entity.get("color"))
        linetype = entity.get("linetype")
        lineweight = entity.get("lineWeight", entity.get("lineweight"))
        if color not in (256, "ByLayer", "По слою"):
            errors.append("Цвет луча должен быть строго ByLayer (По слою)")
        if linetype not in ("ByLayer", "По слою"):
            errors.append("Тип линии луча должен быть строго ByLayer (По слою)")
        if lineweight not in (-1, "ByLayer", "По слою"):
            errors.append("Вес линии луча должен быть строго ByLayer (По слою); вес 50 запрещён")
    return errors


def covers_fire_resistance(candidate: dict[str, Any], required: dict[str, Any]) -> bool:
    """Compare every required criterion, never just the largest minute value."""
    def valid(values):
        return bool(values) and all(k in {"R", "E", "I", "W", "S"} and
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
            for k, v in values.items())
    if not valid(candidate) or not valid(required):
        raise ValueError("Нужны положительные пределы огнестойкости по отдельным признакам")
    return all(candidate.get(criterion, 0) >= minutes for criterion, minutes in required.items())


def barrier_completion_errors(registry: dict[str, Any]) -> list[str]:
    """Validate itemized completion gates for a finished barrier-assessment run."""
    data = registry.get("barrier_assessment", {})
    registers = data.get("completion_registers", {})
    errors: list[str] = []
    required_registers = {
        "large_volume_boundaries": "границы с большими складскими/производственными объёмами",
        "fire_openings": "противопожарные заполнения проёмов",
        "special_slabs": "сравнение специальных перекрытий с таблицей 21",
        "independent_normative_scan": "независимая проверка дверей и перекрытий на всех этажах",
        "callout_layout": "фактическое размещение выносок выше своего плана",
        "abk_public_parts_f4_3": "общественные части АБК Ф4.3",
        "escape_routes_by_gpzu_date": "общие пути эвакуации по дате ГПЗУ",
        "legend_actualization": "актуализация легенды",
    }
    for key, label in required_registers.items():
        item = registers.get(key, {})
        if item.get("status") != "complete":
            errors.append(f"Не закрыт реестр: {label}")
            continue
        if key != "legend_actualization" and not item.get("items") and not str(item.get("none_found_reason", "")).strip():
            errors.append(f"Реестр «{label}» пуст без обоснования")

    for item in registers.get("large_volume_boundaries", {}).get("items", []):
        if item.get("separation_required") is True and not item.get("entity_ids"):
            errors.append("Требуемая граница с большим объёмом не связана с AutoCAD-id")

    for item in registers.get("fire_openings", {}).get("items", []):
        if item.get("fire_rating_required") is True:
            if item.get("address_callout_required") is True:
                if not str(item.get("target_entity_id", "")).strip():
                    errors.append("Для самостоятельного требования к противопожарному заполнению не указана фактическая цель")
                if not item.get("leader_entity_ids"):
                    errors.append("Для самостоятельного требования к противопожарному заполнению нет адресной выноски")
                if item.get("callout_separate_from_explanation_graphics") is not True:
                    errors.append("Рабочая дверная выноска ошибочно связана с текстом или лучами слоя пояснений")
            else:
                if not item.get("legend_entity_ids"):
                    errors.append("Общее требование к заполнению проёма не связано со строкой легенды")
                if item.get("leader_entity_ids"):
                    errors.append("Общее требование по типу преграды ошибочно продублировано адресной выноской")

    for item in registers.get("special_slabs", {}).get("items", []):
        baseline = item.get("table21_baseline_minutes")
        special = item.get("special_required_minutes")
        if not isinstance(baseline, (int, float)) or not isinstance(special, (int, float)):
            errors.append("Для перекрытия не записано числовое сравнение с таблицей 21")
        elif special > baseline and not item.get("callout_entity_ids"):
            errors.append("Специальный предел перекрытия выше таблицы 21, но выноска отсутствует")
        try:
            covered = covers_fire_resistance(item.get("table21_criteria", {}), item.get("special_criteria", {}))
            if not covered and not item.get("callout_entity_ids"):
                errors.append("Базовое перекрытие не покрывает специальные признаки R/E/I, но выноска отсутствует")
        except (ValueError, AttributeError):
            errors.append("Для перекрытия не проверены пределы по отдельным признакам R/E/I")
        if not item.get("basis") or not item.get("scope") or item.get("independent_purpose_review") is not True:
            errors.append("Перекрытие не проверено независимо по назначению и точной области")

    scan = registers.get("independent_normative_scan", {})
    if scan.get("all_floors_reviewed") is not True or scan.get("independent_of_source_remarks") is not True:
        errors.append("Не подтверждён самостоятельный нормативный обход всех этажей вне исходных замечаний")
    for item in scan.get("items", []):
        if not item.get("floor") or not item.get("source_entity_ids") or not item.get("sp1_4_2_25_result") or not item.get("slab_review_result"):
            errors.append("Независимый обход этажа не содержит целей и результатов по дверям/перекрытиям")

    for item in registers.get("callout_layout", {}).get("items", []):
        if not item.get("text_entity_id") or not item.get("leader_entity_ids") or not item.get("floor"):
            errors.append("Выноска не связана с текстом, лучами и этажом")
        for flag in ("text_above_actual_plan_bounds", "no_text_overlaps", "single_start_adjacent_to_own_text", "targets_verified", "same_floor", "visual_verified"):
            if item.get(flag) is not True:
                errors.append(f"Не подтверждён контроль размещения выноски: {flag}")
        if item.get("rays_all_properties_by_layer") is not True:
            errors.append("Не подтверждено, что цвет, тип линии и вес всех лучей выноски установлены ByLayer")

    for item in registers.get("abk_public_parts_f4_3", {}).get("items", []):
        if item.get("functional_class") != "Ф4.3" or item.get("boundary_review_complete") is not True:
            errors.append("Вставка/встройка/пристройка АБК не закрыта как общественная часть Ф4.3")

    for item in registers.get("escape_routes_by_gpzu_date", {}).get("items", []):
        if not str(item.get("gpzu_branch", "")).strip() or item.get("full_contour_review_complete") is not True:
            errors.append("Общий путь эвакуации не проверен по выбранной ветви даты ГПЗУ")
        if str(item.get("space_type", "")).strip().casefold() not in {
            "общий коридор", "холл", "фойе", "вестибюль", "галерея"
        }:
            errors.append("Для общего пути эвакуации не указан проверяемый тип пространства")
        if item.get("toilet_and_sanitary_boundaries_reviewed") is not True:
            errors.append("В полном контуре пути эвакуации не подтверждены границы у туалетов и санитарных узлов")

    legend = registers.get("legend_actualization", {})
    used_keys = legend.get("used_keys", [])
    legend_keys = legend.get("legend_keys", [])
    if legend.get("status") == "complete":
        if sorted(set(used_keys)) != sorted(set(legend_keys)):
            errors.append("Состав легенды не равен фактически использованным типам преград")
        if legend.get("all_frames_match") is not True:
            errors.append("Повторные рамки условных обозначений не синхронизированы")
    return errors
