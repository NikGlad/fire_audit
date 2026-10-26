from __future__ import annotations

from typing import Any
from .instruction_contracts import approved_rule_text_errors


NON_VIOLATION = {"compliant", "not_applicable", "insufficient_data", "not_reviewed"}
NON_ISSUED_REVIEWED = {"compliant", "not_applicable", "insufficient_data"}
FINAL_DECISIONS = {"violation", "compliant", "not_applicable", "insufficient_data"}
VIOLATION_GRAPHICS_LAYER = "экспертиза"
PURPLE_EXPLANATIONS_LAYER = "экспертиза пояснения"
PURPLE_OBJECT_ROLES = {"purple_explanation"}
TEXT_OBJECT_ROLES = {"main_text", "number", "purple_explanation", "solution_variant", "measurement_text"}
CREATED_OBJECT_ROLES = {
    "main_text",
    "number",
    "frame",
    "purple_explanation",
    "solution_variant",
    "ray",
    "dimension",
    "measurement_line",
    "measurement_polyline",
    "cloud",
    "contour",
    "circle",
    "marker",
    "arrow",
    "leader",
    "hatch",
    "symbol",
    "auxiliary_line",
    "measurement_text",
    "other_violation_graphic",
}


def _normalize_floor(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    for token in ("этаж", "эт.", "эт"):
        text = text.replace(token, "")
    text = " ".join(text.split())
    words = {
        "первый": "1",
        "второй": "2",
        "третий": "3",
        "четвертый": "4",
        "пятый": "5",
    }
    return words.get(text, text)


def _validate_ray_origin(finding_id: str, autocad: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    origin = autocad.get("ray_origin", {})
    if origin.get("rule") != "outermost_group_point_toward_plan":
        errors.append(f"{finding_id}: неверно или не указано правило исходной точки луча outermost_group_point_toward_plan")

    coordinates = origin.get("coordinates", [])
    if not isinstance(coordinates, list) or len(coordinates) not in {2, 3} or not all(
        isinstance(value, (int, float)) for value in coordinates
    ):
        errors.append(f"{finding_id}: не указаны числовые координаты исходной точки луча")

    bounds = origin.get("full_group_bounds", [])
    bounds_valid = (
        isinstance(bounds, list)
        and len(bounds) == 4
        and all(isinstance(value, (int, float)) for value in bounds)
        and bounds[0] <= bounds[2]
        and bounds[1] <= bounds[3]
    )
    if not bounds_valid:
        errors.append(f"{finding_id}: не зафиксирован полный внешний габарит группы замечания")

    plan_position = origin.get("plan_position_relative_to_group")
    expected_side = {
        "below": "bottom",
        "above": "top",
        "right": "right",
        "left": "left",
    }.get(plan_position)
    if plan_position not in {"below", "above", "right", "left", "diagonal"}:
        errors.append(f"{finding_id}: не указано положение плана относительно группы замечания")
    side = origin.get("side_toward_plan")
    if expected_side and side != expected_side:
        errors.append(f"{finding_id}: исходная точка выбрана не на стороне габарита, обращённой к плану")
    if plan_position == "diagonal" and side not in {"bottom", "top", "right", "left"}:
        errors.append(f"{finding_id}: для диагонального направления не указана пересечённая граница габарита")

    if bounds_valid and isinstance(coordinates, list) and len(coordinates) in {2, 3} and all(
        isinstance(value, (int, float)) for value in coordinates[:2]
    ):
        x, y = float(coordinates[0]), float(coordinates[1])
        min_x, min_y, max_x, max_y = map(float, bounds)
        tolerance = max(1e-6, max(max_x - min_x, max_y - min_y, 1.0) * 1e-6)
        on_sides = {
            "left": abs(x - min_x) <= tolerance and min_y - tolerance <= y <= max_y + tolerance,
            "right": abs(x - max_x) <= tolerance and min_y - tolerance <= y <= max_y + tolerance,
            "bottom": abs(y - min_y) <= tolerance and min_x - tolerance <= x <= max_x + tolerance,
            "top": abs(y - max_y) <= tolerance and min_x - tolerance <= x <= max_x + tolerance,
        }
        if side in on_sides and not on_sides[side]:
            errors.append(f"{finding_id}: координаты исходной точки не лежат на указанной внешней границе габарита")

    if origin.get("bounds_checked") is not True or origin.get("bounds_check_result") != "verified":
        errors.append(f"{finding_id}: отсутствует положительный результат проверки габарита исходной точки")
    if origin.get("path_clear_of_group") is not True:
        errors.append(f"{finding_id}: не подтверждено, что луч не пересекает элементы замечания")
    if autocad.get("common_ray_origin") is not True:
        errors.append(f"{finding_id}: все лучи одного замечания должны иметь общую исходную точку")
    return errors


def _validate_individual_analysis(finding: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    finding_id = finding.get("id") or "без id"
    norm = finding.get("norm", {})
    applicability = finding.get("applicability", {})
    measurement = finding.get("measurement", {})
    decision = finding.get("decision", {})
    analysis = finding.get("analysis", {})

    def require_text(container: dict[str, Any], key: str, label: str) -> None:
        if not str(container.get(key, "")).strip():
            errors.append(f"{finding_id}: не заполнено обязательное поле — {label}")

    require_text(finding, "source_text_id", "идентификатор исходного текста")
    require_text(finding, "source_text", "полный текст исходного замечания")
    for key, label in (
        ("document", "нормативный документ"),
        ("edition", "применённая редакция"),
        ("point", "пункт"),
        ("subpoint", "подпункт либо отметка `не применимо`"),
        ("table", "таблица либо отметка `не применимо`"),
        ("notes", "примечания либо отметка `отсутствуют`"),
        ("requirement_summary", "краткое содержание требования"),
    ):
        require_text(norm, key, label)
    if not isinstance(norm.get("pages_read"), list) or not norm.get("pages_read"):
        errors.append(f"{finding_id}: не указаны прочитанные страницы нормативного документа")
    if not isinstance(norm.get("related_provisions_read"), list) or not norm.get("related_provisions_read"):
        errors.append(f"{finding_id}: не перечислены прочитанные связанные положения")

    for key, label in (
        ("regulated_subject", "предмет регулирования"),
        ("established_scope", "установленная область применения"),
    ):
        require_text(applicability, key, label)
    if applicability.get("mgn_dependency") not in {"depends_directly", "does_not_depend"}:
        errors.append(f"{finding_id}: не зафиксирована непосредственная зависимость требования от МГН/М4")
    if not isinstance(applicability.get("exceptions_checked"), list) or not applicability.get("exceptions_checked"):
        errors.append(f"{finding_id}: не перечислены проверенные исключения")
    if not isinstance(applicability.get("alternative_solutions_checked"), list) or not applicability.get("alternative_solutions_checked"):
        errors.append(f"{finding_id}: не перечислены проверенные допустимые альтернативы")

    if analysis.get("decision_method") != "individual_normative_analysis":
        errors.append(f"{finding_id}: решение принято без индивидуального нормативного анализа")
    if analysis.get("automatic_decision_used") is not False:
        errors.append(f"{finding_id}: не подтверждено, что автоматический поиск не принимал итоговое решение")
    if analysis.get("individual_analysis_complete") is not True:
        errors.append(f"{finding_id}: первичный индивидуальный анализ не завершён")
    if not isinstance(analysis.get("checked_project_materials"), list) or not analysis.get("checked_project_materials"):
        errors.append(f"{finding_id}: не перечислены проверенные материалы проекта")
    if not isinstance(analysis.get("checked_autocad_entity_ids"), list) or not analysis.get("checked_autocad_entity_ids"):
        errors.append(f"{finding_id}: отсутствуют идентификаторы проверенных объектов AutoCAD")
    facts = analysis.get("object_facts", [])
    if not isinstance(facts, list) or not facts:
        errors.append(f"{finding_id}: не записаны фактические данные объекта")
    else:
        for index, fact in enumerate(facts, start=1):
            if not isinstance(fact, dict) or not str(fact.get("fact", "")).strip() or not str(fact.get("source_type", "")).strip() or not str(fact.get("source_reference", "")).strip():
                errors.append(f"{finding_id}: факт {index} не содержит факт, тип источника и точную ссылку на источник")
    require_text(measurement, "result_summary", "результат измерений либо обоснование отсутствия обмера")
    require_text(analysis, "comparison_result", "результат сопоставления факта с требованием")
    require_text(analysis, "individual_reasoning", "индивидуальное обоснование решения")

    status = decision.get("status")
    if status not in FINAL_DECISIONS:
        errors.append(f"{finding_id}: отсутствует допустимый итоговый статус")
    insufficient = analysis.get("insufficient_characteristics", [])
    facts_complete = analysis.get("facts_complete") is True
    if (insufficient or not facts_complete) and status in {"compliant", "not_applicable"}:
        errors.append(f"{finding_id}: при недостатке характеристик запрещён статус {status}")
    if status == "insufficient_data":
        request = analysis.get("missing_data_request", {})
        if not isinstance(insufficient, list) or not insufficient:
            errors.append(f"{finding_id}: не перечислены характеристики, которых недостаточно для проверки")
        for key, label in (
            ("missing_data", "каких данных не хватает"),
            ("required_for_requirement", "для какого требования нужны данные"),
            ("expected_document_or_section", "где должны быть представлены данные"),
            ("unverifiable_decision", "какое решение невозможно проверить"),
        ):
            require_text(request, key, label)

    combined_reasoning = " ".join(
        str(value).lower()
        for value in (
            analysis.get("individual_reasoning", ""),
            analysis.get("comparison_result", ""),
            finding.get("output", {}).get("explanation", ""),
            finding.get("output", {}).get("non_issue_explanation", {}).get("text", ""),
        )
    )
    mgn_absence_used = ("мгн" in combined_reasoning or "м4" in combined_reasoning) and (
        "отсутств" in combined_reasoning or "нет мгн" in combined_reasoning or "нет м4" in combined_reasoning
    )
    if mgn_absence_used and applicability.get("mgn_dependency") != "depends_directly":
        errors.append(f"{finding_id}: отсутствие МГН/М4 использовано для требования, которое непосредственно от них не зависит")

    second_pass = analysis.get("second_pass", {})
    second_pass_checks = (
        "completed",
        "norm_reopened",
        "number_and_text_verified",
        "scope_and_exceptions_verified",
        "project_facts_verified",
        "reasoning_specific_verified",
        "no_copied_explanation_verified",
    )
    if any(second_pass.get(key) is not True for key in second_pass_checks):
        errors.append(f"{finding_id}: обязательная повторная проверка выполнена не полностью")
    return errors


def _valid_point(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) in {2, 3}
        and all(isinstance(coordinate, (int, float)) for coordinate in value)
    )


def _validate_stairwell_facade_opening_clearance(
    finding_id: str,
    finding: dict[str, Any],
    measurement: dict[str, Any],
    autocad: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    checks = measurement.get("opening_clearance_checks", [])
    if not isinstance(checks, list) or not checks:
        return [f"{finding_id}: отсутствуют отдельные обмеры сочетаний проёмов"]

    finding_floor = _normalize_floor(autocad.get("floor"))
    dimension_ids: list[str] = []
    ray_ids: list[str] = []
    for index, check in enumerate(checks, start=1):
        prefix = f"{finding_id}: сочетание проёмов {index}"
        if not isinstance(check, dict):
            errors.append(f"{prefix}: запись обмера имеет неверный формат")
            continue
        check_floor = _normalize_floor(check.get("floor"))
        if not check_floor:
            errors.append(f"{prefix}: не указан этаж")
        elif finding_floor and check_floor != finding_floor:
            errors.append(f"{prefix}: размер расположен не на этаже соответствующего замечания")
        if not str(check.get("stairwell_opening_entity_id", "")).strip():
            errors.append(f"{prefix}: не указан AutoCAD-id проёма лестничной клетки")
        if not str(check.get("facade_opening_entity_id", "")).strip():
            errors.append(f"{prefix}: не указан AutoCAD-id соседнего проёма наружной стены")
        points = check.get("nearest_edge_points", [])
        if not isinstance(points, list) or len(points) != 2 or not all(_valid_point(point) for point in points):
            errors.append(f"{prefix}: не указаны координаты двух ближайших фактических граней")
        if check.get("nearest_edges_verified") is not True:
            errors.append(f"{prefix}: не подтверждён выбор ближайших геометрических границ проёмов")
        if check.get("measured_along_exterior_wall_plane") is not True:
            errors.append(f"{prefix}: не подтверждён обмер вдоль плоскости наружной стены")
        if check.get("dimension_type") not in {"linear", "aligned_parallel_to_wall"}:
            errors.append(f"{prefix}: размер должен быть линейным или параллельным наружной стене")

        actual = check.get("actual_distance_mm")
        required = check.get("required_distance_mm")
        if not isinstance(actual, (int, float)) or not isinstance(required, (int, float)):
            errors.append(f"{prefix}: не указаны фактическое и нормативное расстояния в миллиметрах")
        elif actual >= required:
            errors.append(f"{prefix}: размер не подтверждает нарушение — расстояние не меньше нормативного")

        dimension_id = str(check.get("dimension_entity_id", "")).strip()
        ray_id = str(check.get("ray_entity_id", "")).strip()
        if not dimension_id:
            errors.append(f"{prefix}: отсутствует AutoCAD-id созданного размера")
        else:
            dimension_ids.append(dimension_id)
        if not ray_id:
            errors.append(f"{prefix}: отсутствует AutoCAD-id отдельного луча")
        else:
            ray_ids.append(ray_id)
        if str(check.get("dimension_layer", "")).strip().lower() != "экспертиза":
            errors.append(f"{prefix}: размер должен находиться в слое экспертиза")
        if check.get("dimension_by_layer") is not True:
            errors.append(f"{prefix}: свойства размера должны быть ByLayer")
        if check.get("ray_rule") != "ray_to_violation_dimension":
            errors.append(f"{prefix}: неверно правило цели луча")
        if str(check.get("ray_target_entity_id", "")).strip() != dimension_id:
            errors.append(f"{prefix}: луч приходит не к объекту соответствующего размера")
        if check.get("ray_endpoint_near_dimension_text") is not True:
            errors.append(f"{prefix}: не подтверждено окончание луча на размерной линии рядом с числом")

    if autocad.get("ray_rule") != "ray_to_violation_dimension":
        errors.append(f"{finding_id}: для замечания не установлено правило ray_to_violation_dimension")
    target_ids = {str(value) for value in autocad.get("target_entity_ids", [])}
    if dimension_ids and not set(dimension_ids).issubset(target_ids):
        errors.append(f"{finding_id}: не все созданные размеры зарегистрированы целями лучей")
    registered_ray_ids = {str(value) for value in autocad.get("ray_entity_ids", [])}
    if ray_ids and not set(ray_ids).issubset(registered_ray_ids):
        errors.append(f"{finding_id}: не все отдельные лучи зарегистрированы у замечания")
    return errors


def _issued_graphics_required(finding: dict[str, Any]) -> bool:
    status = finding.get("decision", {}).get("status", "not_reviewed")
    if status in NON_ISSUED_REVIEWED:
        return False
    output = finding.get("output", {})
    return (
        status == "violation"
        or bool(output.get("is_general_requirement"))
        or (finding.get("mode") == "stage" and output.get("stage_is_issued") is True)
    )


def _result_graphics_rows(autocad: dict[str, Any]) -> list | None:
    created, reused = autocad.get("created_objects", []), autocad.get("reused_objects", [])
    return created + reused if isinstance(created, list) and isinstance(reused, list) else None


def _reused_object_valid(item: dict[str, Any]) -> bool:
    action = item.get("action", "reused")
    before = item.get("properties_before")
    required = {"layer", "color_by_layer", "linetype_by_layer", "lineweight_by_layer"}
    if (action not in {"reused", "modified", "unchanged"} or not item.get("original_layer")
            or item.get("existing_object_verified") is not True or item.get("properties_re_read") is not True
            or not isinstance(before, dict) or not required.issubset(before)
            or before["layer"] != item["original_layer"] or not item.get("layer")
            or any(type(before[key]) is not bool or type(item.get(key)) is not bool
                   for key in required - {"layer"})):
        return False
    changes = item.get("changes", [])
    if action != "modified":
        return not changes and all(key in item and item[key] == value for key, value in before.items())
    if not isinstance(changes, list) or not changes:
        return False
    recorded = set()
    for change in changes:
        if not isinstance(change, dict) or not {"property", "before", "after"}.issubset(change):
            return False
        key = change["property"]
        if (not isinstance(key, str) or key in recorded or key not in before or key not in item
                or before[key] != change["before"] or item[key] != change["after"]
                or change["before"] == change["after"]):
            return False
        recorded.add(key)
    return recorded == {key for key, value in before.items() if key not in item or item[key] != value}


def _preserved_analysis_graphic(item: dict[str, Any], finding: dict[str, Any]) -> bool:
    """Original project evidence does not replace issued expert graphics."""
    return (item.get("usage") == "analysis_only" and item.get("source_kind") == "project_geometry"
            and _preserved_existing_graphic(item, finding))


def _preserved_existing_graphic(item: dict[str, Any], finding: dict[str, Any]) -> bool:
    """Unaddressed measurements stay in their original layer; targets inherit their source."""
    if (item.get("action", "reused") == "modified"
            or item.get("role") not in {"dimension", "measurement_line", "measurement_polyline", "cloud", "contour"}):
        return False
    return ((item.get("source_kind") == "project_geometry" and item.get("usage") == "analysis_only")
            or (item.get("source_kind") == "existing_measurement"
                and item.get("layer") == "экспертиза обмеры"
                and not item.get("source_entity_id")
                and str(item.get("entity_id")) not in {
                    str(value) for value in finding.get("autocad", {}).get("target_entity_ids", [])} ))


def _source_layer_for_graphic(item: dict[str, Any], finding: dict[str, Any]) -> str | None:
    """Resolve an exact layer from the reread source record, never an unbacked override."""
    autocad = finding.get("autocad", {})
    reused = autocad.get("reused_objects", [])
    if item in reused and item.get("role") in {"main_text", "purple_explanation", "solution_variant"}:
        return item.get("original_layer") if _reused_object_valid(item) else None
    source_id = item.get("source_entity_id")
    if source_id:
        sources = [row for row in (_result_graphics_rows(autocad) or []) if isinstance(row, dict)
                   and str(row.get("entity_id")) == str(source_id)
                   and row.get("role") in {"main_text", "purple_explanation"}]
        if len(sources) != 1 or str(source_id) == str(item.get("entity_id")):
            return None
        source = sources[0]
        if source in reused:
            if not _reused_object_valid(source) or source.get("layer") != source.get("original_layer"):
                return None
        elif source.get("layer") != (PURPLE_EXPLANATIONS_LAYER if source["role"] in PURPLE_OBJECT_ROLES else VIOLATION_GRAPHICS_LAYER):
            return None
        return source["layer"] if item.get("source_layer") == source.get("layer") else None
    if item.get("source_layer"):
        return None
    if item.get("role") in PURPLE_OBJECT_ROLES:
        return PURPLE_EXPLANATIONS_LAYER
    main_sources = [row for row in reused if isinstance(row, dict) and row.get("role") == "main_text"
                    and _reused_object_valid(row) and row.get("layer") == row.get("original_layer")]
    if len(main_sources) == 1:
        return main_sources[0]["layer"]
    return VIOLATION_GRAPHICS_LAYER


def _created_graphics_state(finding: dict[str, Any]) -> dict[str, Any]:
    autocad = finding.get("autocad", {})
    objects = _result_graphics_rows(autocad)
    state = {
        "valid_list": isinstance(objects, list),
        "violation_ok": True,
        "purple_ok": True,
        "checked_entity_ids": [],
        "roles_by_id": {},
    }
    if not isinstance(objects, list):
        state["violation_ok"] = False
        state["purple_ok"] = False
        return state
    for item in objects:
        if not isinstance(item, dict):
            state["violation_ok"] = False
            state["purple_ok"] = False
            continue
        entity_id = str(item.get("entity_id", "")).strip()
        role = str(item.get("role", "")).strip()
        if entity_id and not _preserved_analysis_graphic(item, finding):
            state["checked_entity_ids"].append(entity_id)
            state["roles_by_id"].setdefault(entity_id, []).append(role)
        if item in autocad.get("reused_objects", []):
            if not _reused_object_valid(item):
                state["violation_ok"] = state["purple_ok"] = False
            if _preserved_existing_graphic(item, finding):
                continue
        correct_properties = all(
            item.get(key) is True
            for key in ("color_by_layer", "linetype_by_layer", "lineweight_by_layer")
        )
        expected_layer = _source_layer_for_graphic(item, finding)
        if role in PURPLE_OBJECT_ROLES:
            if not expected_layer or item.get("layer") != expected_layer or not correct_properties:
                state["purple_ok"] = False
        else:
            if not expected_layer or item.get("layer") != expected_layer or not correct_properties:
                state["violation_ok"] = False
    return state


def _validate_created_graphics(finding: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    finding_id = str(finding.get("id") or "без id")
    autocad = finding.get("autocad", {})
    objects = _result_graphics_rows(autocad)
    state = _created_graphics_state(finding)
    if not state["valid_list"]:
        return [f"{finding_id}: реестр созданных объектов должен быть списком"]

    seen: set[str] = set()
    for index, item in enumerate(objects, start=1):
        prefix = f"{finding_id}: объект результата {index}"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: запись имеет неверный формат")
            continue
        entity_id = str(item.get("entity_id", "")).strip()
        role = str(item.get("role", "")).strip()
        if not entity_id:
            errors.append(f"{prefix}: отсутствует AutoCAD-id")
        elif entity_id in seen:
            errors.append(f"{prefix}: AutoCAD-id {entity_id} зарегистрирован повторно")
        else:
            seen.add(entity_id)
        if role not in CREATED_OBJECT_ROLES:
            errors.append(f"{prefix}: не указана допустимая роль объекта")
        if str(item.get("related_finding_id", "")).strip() != finding_id:
            errors.append(f"{prefix}: объект не связан с текущим замечанием")
        if not str(item.get("entity_type", "")).strip():
            errors.append(f"{prefix}: не указан тип объекта AutoCAD")
        if not str(item.get("floor", "")).strip():
            errors.append(f"{prefix}: не указан этаж")
        elif autocad.get("floor") and _normalize_floor(item.get("floor")) != _normalize_floor(autocad.get("floor")):
            errors.append(f"{prefix}: объект зарегистрирован на другом этаже")
        if not str(item.get("purpose", "")).strip():
            errors.append(f"{prefix}: не указано назначение объекта")
        if role in TEXT_OBJECT_ROLES and not str(item.get("text_style", "")).strip():
            errors.append(f"{prefix}: для текстового объекта не записан сохранённый текстовый стиль")

        if item in autocad.get("reused_objects", []):
            if not _reused_object_valid(item):
                errors.append(f"{prefix}: не подтверждены происхождение, снимок свойств и точные изменения до/после существующего объекта")
            if item.get("source_kind") == "project_geometry" and item.get("action") == "modified":
                errors.append(f"{prefix}: исходную проектную графику изменять нельзя")
            if _preserved_existing_graphic(item, finding):
                continue
        elif item.get("action", "created") != "created":
            errors.append(f"{prefix}: существующий объект нельзя записывать в created_objects")

        expected_layer = _source_layer_for_graphic(item, finding)
        if not expected_layer:
            errors.append(f"{prefix}: не подтверждены AutoCAD-id и точный слой исходного объекта графики")
        if item.get("layer") != expected_layer:
            errors.append(f"{prefix}: требуется точный слой {expected_layer}")
        for key, label in (
            ("color_by_layer", "цвет"),
            ("linetype_by_layer", "тип линии"),
            ("lineweight_by_layer", "вес линии"),
        ):
            if item.get(key) is not True:
                errors.append(f"{prefix}: {label} должен быть ByLayer")

    if _issued_graphics_required(finding) and autocad.get("status") in {"applied", "post_checked"}:
        objects = [item for item in objects if not isinstance(item, dict) or not _preserved_analysis_graphic(item, finding)]
        verification = autocad.get("layer_verification", {})
        if not objects:
            errors.append(f"{finding_id}: после нанесения не зарегистрированы объекты результата")
        if verification.get("all_created_objects_collected") is not True:
            errors.append(f"{finding_id}: не подтверждено, что собраны все созданные объекты")
        if verification.get("checked_against_autocad") is not True:
            errors.append(f"{finding_id}: слои и свойства объектов не сверены с AutoCAD")
        if verification.get("all_violation_graphics_on_expertise_layer") is not True or not state["violation_ok"]:
            errors.append(f"{finding_id}: условие all_violation_graphics_on_expertise_layer не подтверждено")
        if verification.get("all_purple_explanations_on_expertise_explanations_layer") is not True or not state["purple_ok"]:
            errors.append(
                f"{finding_id}: условие all_purple_explanations_on_expertise_explanations_layer не подтверждено"
            )

        roles = {str(item.get("role", "")) for item in objects if isinstance(item, dict)}
        for required_role, label in (
            ("main_text", "основной текст"),
            ("number", "номер"),
            ("frame", "рамка"),
        ):
            if required_role not in roles:
                errors.append(f"{finding_id}: в пообъектном реестре отсутствует {label} замечания")

        ray_ids = {str(value) for value in autocad.get("ray_entity_ids", []) if str(value).strip()}
        registered_ray_ids = {
            str(item.get("entity_id"))
            for item in objects
            if isinstance(item, dict) and item.get("role") == "ray"
        }
        if not ray_ids.issubset(registered_ray_ids):
            errors.append(f"{finding_id}: не все лучи включены в пообъектную проверку слоёв")

        dimension_ids = {
            str(value) for value in finding.get("measurement", {}).get("dimension_entity_ids", []) if str(value).strip()
        }
        registered_dimension_ids = {
            str(item.get("entity_id"))
            for item in objects
            if isinstance(item, dict) and item.get("role") == "dimension"
        }
        if not dimension_ids.issubset(registered_dimension_ids):
            errors.append(f"{finding_id}: не все размеры включены в пообъектную проверку слоёв")

        source_group = autocad.get("source_group", {})
        purple_present = source_group.get("purple_explanation_present") is True
        if purple_present:
            if not roles.intersection(PURPLE_OBJECT_ROLES):
                errors.append(f"{finding_id}: фиолетовое пояснение не включено в пообъектный реестр")
            if verification.get("text_parts_separated_by_layer") is not True:
                errors.append(f"{finding_id}: не подтверждено разделение основного текста и фиолетового пояснения")
            main_ids = {
                str(item.get("entity_id"))
                for item in objects
                if isinstance(item, dict) and item.get("role") == "main_text"
            }
            purple_ids = {
                str(item.get("entity_id"))
                for item in objects
                if isinstance(item, dict) and item.get("role") in PURPLE_OBJECT_ROLES
            }
            if main_ids.intersection(purple_ids):
                errors.append(f"{finding_id}: основной текст и фиолетовое пояснение должны быть разными объектами")
    return errors


def _validate_compliance_source_copy(finding: dict[str, Any], eligible_sources: list[dict] | None = None) -> list[str]:
    errors: list[str] = []
    if finding.get("mode") != "compliance" or not _issued_graphics_required(finding):
        return errors

    finding_id = str(finding.get("id") or "без id")
    autocad = finding.get("autocad", {})
    source_group = autocad.get("source_group", {})
    prefix = f"{finding_id}: исходная группа замечания"

    if source_group.get("copy_source") == "approved_rule_text":
        errors.extend(f"{prefix}: {error}" for error in approved_rule_text_errors(source_group, eligible_sources))
        if finding.get("output", {}).get("remark_text") != source_group.get("approved_rule_text", {}).get("full_text"):
            errors.append(f"{prefix}: выдаваемый текст не совпадает с полным утверждённым источником")
        if source_group.get("copy_method") != "approved_text_with_verified_format_sample":
            errors.append(f"{prefix}: требуется отдельный метод approved_text_with_verified_format_sample")
        if source_group.get("approved_block_header_entity_id") or source_group.get("remark_text_entity_ids"):
            errors.append(f"{prefix}: нельзя приписывать утверждённому тексту отсутствующий исходный блок DWG")
        if not source_group.get("new_floor") or _normalize_floor(source_group.get("new_floor")) != _normalize_floor(autocad.get("floor")):
            errors.append(f"{prefix}: не подтверждён этаж новой группы")
        if source_group.get("text_styles_preserved") is not True or source_group.get("relative_positions_preserved") is not True:
            errors.append(f"{prefix}: не подтверждено сохранение оформления полного образца")
        return errors

    if not str(source_group.get("source_template_key", "")).strip():
        errors.append(f"{prefix}: не указан устойчивый идентификатор шаблона замечания")
    if source_group.get("source_group_fully_detected") is not True:
        errors.append(f"{prefix}: признак source_group_fully_detected должен быть true")
    if source_group.get("left_explanation_checked") is not True:
        errors.append(f"{prefix}: признак left_explanation_checked должен быть true")
    if source_group.get("left_explanation_position_verified") is not True:
        errors.append(f"{prefix}: не подтверждено, что пояснение искалось только непосредственно слева")
    left_present = source_group.get("left_explanation_present")
    if left_present not in {True, False}:
        errors.append(f"{prefix}: не зафиксировано наличие или отсутствие пояснения слева")
    elif left_present and not source_group.get("left_explanation_entity_ids"):
        errors.append(f"{prefix}: пояснение слева найдено, но его AutoCAD-id не записан")
    purple_present = source_group.get("purple_explanation_present")
    if purple_present is True and left_present is not True:
        errors.append(f"{prefix}: пояснение замечания не может находиться не слева")
    if left_present is True and purple_present is not True:
        errors.append(f"{prefix}: найденное слева пояснение не включено в состав пояснения замечания")
    if left_present is True:
        left_ids = {str(value) for value in source_group.get("left_explanation_entity_ids", [])}
        purple_ids = {str(value) for value in source_group.get("purple_explanation_entity_ids", [])}
        if left_ids != purple_ids:
            errors.append(f"{prefix}: идентификаторы пояснения должны совпадать с объектами слева")

    for key, label in (
        ("remark_text_entity_ids", "основной исходный текст"),
        ("frame_entity_ids", "исходная рамка"),
        ("number_entity_ids", "исходный текст `Замечание №`"),
    ):
        if not source_group.get(key):
            errors.append(f"{prefix}: отсутствует {label}")

    copy_case = source_group.get("copy_case")
    copy_source = source_group.get("copy_source")
    if copy_case not in {"first_occurrence", "repeated_floor"}:
        errors.append(f"{prefix}: не указан случай first_occurrence или repeated_floor")
    if copy_case == "first_occurrence":
        if copy_source != "source_approved_block":
            errors.append(f"{prefix}: первое применение должно копироваться из source_approved_block")
        if not str(source_group.get("approved_block_header_entity_id", "")).strip():
            errors.append(f"{prefix}: не указан AutoCAD-id заголовка утверждённого блока")
    if copy_case == "repeated_floor":
        if copy_source != "previous_actualized_compliance_group":
            errors.append(
                f"{prefix}: повтор другого этажа должен копироваться из previous_actualized_compliance_group"
            )
        if not str(source_group.get("previous_actualized_group_finding_id", "")).strip():
            errors.append(f"{prefix}: не указана ранее актуализированная группа-источник")
        if autocad.get("instruction_policy_version", 1) >= 2:
            reconciliation = source_group.get("content_reconciliation", {})
            if (not isinstance(reconciliation, dict) or not reconciliation.get("full_template_reference")
                    or not reconciliation.get("full_template_text") or reconciliation.get("checked_for_current_floor") is not True
                    or not isinstance(reconciliation.get("restored_branches"), list)):
                errors.append(f"{prefix}: повторный этаж требует сверки содержания с полным неизменённым шаблоном")
            else:
                for restored in reconciliation["restored_branches"]:
                    if (not isinstance(restored, dict) or not restored.get("source_fragment") or not restored.get("reason")
                            or restored.get("result_fragment") != restored.get("source_fragment")
                            or restored["source_fragment"] not in reconciliation["full_template_text"]):
                        errors.append(f"{prefix}: восстановленная ветка должна быть точным фрагментом полного шаблона с основанием")

    if source_group.get("copy_method") != "copy_existing_complete_group":
        errors.append(f"{prefix}: требуется копирование существующей полной группы")
    if source_group.get("manual_text_creation_used") is not False:
        errors.append(f"{prefix}: запрещено создавать или набирать новый MText замечания")
    if not str(source_group.get("source_floor", "")).strip():
        errors.append(f"{prefix}: не указан этаж или зона исходной группы")
    new_floor = source_group.get("new_floor")
    if not str(new_floor or "").strip():
        errors.append(f"{prefix}: не указан этаж новой группы")
    elif autocad.get("floor") and _normalize_floor(new_floor) != _normalize_floor(autocad.get("floor")):
        errors.append(f"{prefix}: этаж новой группы не совпадает с этажом замечания")

    for key in (
        "left_explanation_copied_when_present",
        "text_styles_preserved",
        "relative_positions_preserved",
        "repeated_floor_group_copied_from_actualized_group",
    ):
        if source_group.get(key) is not True:
            errors.append(f"{prefix}: признак {key} должен быть true")

    text_ids = {
        str(value)
        for key in ("remark_text_entity_ids", "number_entity_ids", "left_explanation_entity_ids")
        for value in source_group.get(key, [])
        if str(value).strip()
    }
    style_rows = source_group.get("source_text_styles", [])
    style_ids: set[str] = set()
    if not isinstance(style_rows, list) or not style_rows:
        errors.append(f"{prefix}: не записаны исходные текстовые стили")
    else:
        for index, row in enumerate(style_rows, start=1):
            if not isinstance(row, dict):
                errors.append(f"{prefix}: запись текстового стиля {index} имеет неверный формат")
                continue
            entity_id = str(row.get("entity_id", "")).strip()
            text_style = str(row.get("text_style", "")).strip()
            if not entity_id or not text_style:
                errors.append(f"{prefix}: запись текстового стиля {index} неполна")
            else:
                style_ids.add(entity_id)
    if text_ids and not text_ids.issubset(style_ids):
        errors.append(f"{prefix}: текстовые стили записаны не для всех исходных текстовых объектов")

    if autocad.get("status") in {"applied", "post_checked"}:
        copied_ids = {
            str(value) for value in source_group.get("created_copy_entity_ids", []) if str(value).strip()
        }
        if not copied_ids:
            errors.append(f"{prefix}: не записаны идентификаторы созданных копий группы")
        created_group_ids = {
            str(item.get("entity_id"))
            for item in autocad.get("created_objects", [])
            if isinstance(item, dict)
            and item.get("role") in {"main_text", "number", "frame", "purple_explanation", "solution_variant"}
        }
        if created_group_ids and not created_group_ids.issubset(copied_ids):
            errors.append(f"{prefix}: не все созданные элементы группы записаны как копии")
        if left_present and not any(
            isinstance(item, dict) and item.get("role") == "purple_explanation"
            for item in autocad.get("created_objects", [])
        ):
            errors.append(f"{prefix}: найденное пояснение слева отсутствует в размещённой группе")
    return errors


def validate_finding(finding: dict[str, Any], for_application: bool = False, *, eligible_rule_text_sources: list[dict] | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    finding_id = finding.get("id") or "без id"
    norm = finding.get("norm", {})
    applicability = finding.get("applicability", {})
    measurement = finding.get("measurement", {})
    decision = finding.get("decision", {})
    output = finding.get("output", {})
    autocad = finding.get("autocad", {})

    status = decision.get("status", "not_reviewed")
    if for_application and status in {"violation", "compliant", "not_applicable"}:
        errors.extend(validate_source_reading(finding))
    is_general = bool(output.get("is_general_requirement"))
    target_graphics = bool(autocad.get("target_entity_ids"))
    drawing_progress = autocad.get("status") in {
        "verified",
        "applied",
        "post_checked",
    }
    non_issue = output.get("non_issue_explanation", {})
    non_issue_required = status in NON_ISSUED_REVIEWED
    non_issue_planned = bool(str(non_issue.get("text", "")).strip()) and non_issue.get("green") is True
    # A reviewed non-issue needs evidence in the registry, not a drawing note.
    non_issue_note_present = bool(str(non_issue.get("text", "")).strip() or non_issue.get("entity_id"))
    source_group = autocad.get("source_group", {})
    placement = autocad.get("placement", {})
    issued_output = status == "violation" or (is_general and not non_issue_required)
    is_stage = finding.get("mode") == "stage"
    stage_is_issued = output.get("stage_is_issued") is True or issued_output
    relative_to_frame = placement.get("relative_to_stage_frame")
    if is_stage and stage_is_issued:
        if output.get("stage_element_kind") not in {"remark", "query", "explanation", "requirement"}:
            errors.append(f"{finding_id}: не указан вид выдаваемого элемента этапизации")
        if relative_to_frame not in {"right", "left", "inside_frame", "above", "below", "other"}:
            errors.append(f"{finding_id}: не указано положение элемента относительно рамки этапизации")
    stage_right = relative_to_frame == "right"
    mandatory_stage_ray = is_stage and stage_is_issued and stage_right and not non_issue_required
    mandatory_compliance_ray = (
        finding.get("mode") == "compliance" and issued_output and not non_issue_required
    )
    mandatory_registry_ray = mandatory_stage_ray or mandatory_compliance_ray
    any_ray = (
        autocad.get("ray_required") is True
        or bool(autocad.get("ray_planned"))
        or bool(autocad.get("ray_entity_ids"))
    )

    if any_ray and not non_issue_required:
        errors.extend(_validate_ray_origin(finding_id, autocad))

    if mandatory_registry_ray:
        if autocad.get("ray_required") is not True:
            errors.append(f"{finding_id}: для актуального выдаваемого элемента не указана обязательность луча")
        if not autocad.get("ray_planned") and not autocad.get("ray_entity_ids"):
            errors.append(f"{finding_id}: обязательный луч не запланирован и не зарегистрирован")
        if not str(autocad.get("ray_requirement_reason", "")).strip():
            errors.append(f"{finding_id}: не указано основание обязательности луча")
        if not str(autocad.get("ray_rule", "")).strip():
            errors.append(f"{finding_id}: не указано правило выбора цели обязательного луча")
        if not str(autocad.get("target_semantics", "")).strip():
            errors.append(f"{finding_id}: не указана конкретная смысловая цель обязательного луча")
        if not autocad.get("target_entity_ids"):
            errors.append(f"{finding_id}: отсутствует AutoCAD-id подтверждённой цели обязательного луча")
        if autocad.get("target_confirmed") is not True:
            errors.append(f"{finding_id}: цель обязательного луча не подтверждена")
        if not str(autocad.get("target_floor", "")).strip():
            errors.append(f"{finding_id}: не указан этаж цели обязательного луча")
        target_scope = autocad.get("target_scope")
        if target_scope not in {"whole_building", "specific_object"}:
            errors.append(f"{finding_id}: не указано, относится цель к зданию или конкретному объекту")
        if target_scope == "whole_building":
            if autocad.get("ray_rule") != "stage_whole_building_first_floor_lower_left_corner":
                errors.append(f"{finding_id}: общий элемент должен вести к нижнему левому углу здания первого этажа")
            if _normalize_floor(autocad.get("target_floor")) != "1":
                errors.append(f"{finding_id}: общий элемент без адресата должен иметь цель на первом этаже")
        if target_scope == "specific_object":
            finding_floor = _normalize_floor(autocad.get("floor"))
            target_floor = _normalize_floor(autocad.get("target_floor"))
            if finding_floor and target_floor and finding_floor != target_floor:
                errors.append(f"{finding_id}: межэтажный луч этапизации запрещён")

    combined_norm = " ".join(
        str(value) for value in (norm.get("point", ""), finding.get("source_text", ""), output.get("remark_text", ""))
    )
    direct_minimum_exit_rule = (
        finding.get("mode") in {"stage", "compliance"}
        and (stage_is_issued if is_stage else issued_output)
        and "4.2.19" in combined_norm
        and "9.3.3" in combined_norm
    )
    if direct_minimum_exit_rule:
        if autocad.get("ray_required") is not True:
            errors.append(f"{finding_id}: для п. 4.2.19 и п. 9.3.3 обязательный луч не зарегистрирован")
        if autocad.get("ray_rule") != "minimum_first_floor_room_exit_under_1200":
            errors.append(f"{finding_id}: для п. 4.2.19 и п. 9.3.3 не подтверждён выбор наименьшего выхода менее 1200 мм")
        if not autocad.get("ray_planned") and not autocad.get("ray_entity_ids"):
            errors.append(f"{finding_id}: луч для п. 4.2.19 и п. 9.3.3 не запланирован и не зарегистрирован")
        if not str(autocad.get("target_semantics", "")).strip():
            errors.append(f"{finding_id}: не указано, что целью является наименьший выход из помещения")
        if autocad.get("target_scope") != "specific_object":
            errors.append(f"{finding_id}: цель п. 4.2.19 и п. 9.3.3 должна быть конкретным выходом")
        if _normalize_floor(autocad.get("target_floor")) != "1":
            errors.append(f"{finding_id}: цель требования п. 4.2.19 и п. 9.3.3 должна быть на первом этаже")
        if autocad.get("target_confirmed") is not True or not autocad.get("target_entity_ids"):
            errors.append(f"{finding_id}: не подтверждён AutoCAD-объект наименьшего выхода менее 1200 мм")
        if measurement.get("selection_rule") != "minimum_first_floor_room_exit_under_1200":
            errors.append(f"{finding_id}: в обмере не указано правило выбора наименьшего выхода")
        if measurement.get("selection_verified") is not True:
            errors.append(f"{finding_id}: не подтверждено сравнение ширины всех выходов-кандидатов")
        width = measurement.get("value")
        if not isinstance(width, (int, float)) or width >= 1200 or str(measurement.get("unit", "")).lower() not in {"мм", "mm"}:
            errors.append(f"{finding_id}: выбранный выход должен иметь подтверждённую ширину менее 1200 мм")
        if measurement.get("in_clear") is not True:
            errors.append(f"{finding_id}: ширина выбранной двери не подтверждена обмером в свету")
        if str(measurement.get("dimension_layer", "")).strip().lower() != "экспертиза":
            errors.append(f"{finding_id}: размер ширины выбранной двери должен быть в слое экспертиза")
        if measurement.get("dimension_by_layer") is not True:
            errors.append(f"{finding_id}: размер ширины выбранной двери должен быть оформлен ByLayer")
        if not measurement.get("dimension_entity_ids"):
            errors.append(f"{finding_id}: в реестре отсутствует AutoCAD-id размера ширины выбранной двери")

    combined_text = " ".join(
        str(value).lower() for value in (finding.get("source_text", ""), output.get("remark_text", ""))
    )
    stairwell_facade_opening_clearance = (
        measurement.get("measurement_type") == "stairwell_to_adjacent_facade_opening_horizontal_clearance"
        or (
            "расстояни" in combined_text
            and "проем" in combined_text.replace("ё", "е")
            and "лестнич" in combined_text
            and "наружн" in combined_text
            and "стен" in combined_text
        )
    )
    if stairwell_facade_opening_clearance and status == "violation":
        errors.extend(
            _validate_stairwell_facade_opening_clearance(finding_id, finding, measurement, autocad)
        )
    if stairwell_facade_opening_clearance and status in {"compliant", "not_applicable", "insufficient_data"}:
        if measurement.get("dimension_entity_ids") or measurement.get("opening_clearance_checks") or autocad.get("ray_entity_ids"):
            errors.append(f"{finding_id}: при отсутствии подтверждённого нарушения запрещены постоянный размер и луч")
    ambiguous_room_query = (
        is_stage
        and stage_is_issued
        and "уточн" in combined_text
        and "назначен" in combined_text
        and "выделенн" in combined_text
        and "помещен" in combined_text
    )
    if ambiguous_room_query:
        if autocad.get("ray_required") is not True:
            errors.append(f"{finding_id}: запрос об уточнении назначения помещений должен иметь обязательные лучи")
        if autocad.get("ray_rule") != "ambiguous_rooms_by_explication":
            errors.append(f"{finding_id}: не указано правило выбора помещений с неясным назначением по экспликации")
        if autocad.get("target_scope") != "specific_object":
            errors.append(f"{finding_id}: запрос об уточнении назначения должен вести к конкретным помещениям")
        if not autocad.get("target_entity_ids") or autocad.get("target_confirmed") is not True:
            errors.append(f"{finding_id}: не подтверждены помещения с неясным назначением")
        if autocad.get("target_selection_verified") is not True:
            errors.append(f"{finding_id}: не подтверждена проверка всех помещений этажа по экспликации")

    if not finding.get("source_text"):
        errors.append(f"{finding_id}: отсутствует исходный пользовательский текст")
    if not norm.get("document") or not norm.get("point"):
        errors.append(f"{finding_id}: не заполнены документ и пункт нормы")
    if status == "violation":
        if applicability.get("status") != "applicable":
            errors.append(f"{finding_id}: нарушение нельзя подтвердить без применимости нормы")
        if not output.get("remark_text"):
            errors.append(f"{finding_id}: для нарушения отсутствует текст замечания")
        if not autocad.get("floor"):
            errors.append(f"{finding_id}: не указан этаж замечания")
        if not autocad.get("target_semantics"):
            errors.append(f"{finding_id}: не указано смысловое назначение цели луча")
        if measurement.get("required") and measurement.get("value") is None:
            errors.append(f"{finding_id}: обязательный обмер не выполнен")
    elif status in NON_VIOLATION and not stage_is_issued:
        ray_graphics = (
            autocad.get("ray_required") is True
            or bool(autocad.get("ray_planned"))
            or bool(autocad.get("ray_entity_ids"))
        )
        if target_graphics or ray_graphics:
            errors.append(f"{finding_id}: графика запрещена при результате {status}; допускается только зелёное пояснение рядом с исходным элементом")
        if drawing_progress and not non_issue_planned and not is_general and not non_issue_required:
            errors.append(f"{finding_id}: при результате {status} допускается только зелёное пояснение рядом с исходным элементом")

    if non_issue_required:
        if autocad.get("ray_required") is True or autocad.get("ray_planned") or autocad.get("ray_entity_ids"):
            errors.append(f"{finding_id}: от зелёного пояснения о невыдаче луч строить запрещено")

    if non_issue_required:
        if not any(str(value or "").strip() for value in (
            decision.get("reason"), finding.get("analysis", {}).get("individual_reasoning"),
            non_issue.get("text"),
        )):
            errors.append(f"{finding_id}: не указана причина, почему исходный элемент не выдан")
    if non_issue_required and non_issue_note_present:
        if non_issue.get("green") is not True:
            errors.append(f"{finding_id}: пояснение об отказе должно быть зелёным")
        if non_issue.get("placement") != "next_to_source":
            errors.append(f"{finding_id}: пояснение об отказе должно располагаться рядом с исходным элементом")

    if (
        is_general
        and not non_issue_required
        and not mandatory_stage_ray
        and not direct_minimum_exit_rule
        and autocad.get("ray_required_by_sample") is None
    ):
        warnings.append(f"{finding_id}: не определено по образцу, требуется ли луч от общего требования")
    if autocad.get("by_layer") is not True:
        errors.append(f"{finding_id}: оформление должно быть ByLayer")
    expected_main_layer = (
        PURPLE_EXPLANATIONS_LAYER
        if is_stage and output.get("stage_element_kind") == "explanation"
        else VIOLATION_GRAPHICS_LAYER
    )
    existing_main_role = "purple_explanation" if is_stage and output.get("stage_element_kind") == "explanation" else "main_text"
    existing_main = [row for row in autocad.get("reused_objects", [])
                     if isinstance(row, dict) and row.get("role") == existing_main_role and _reused_object_valid(row)
                     and row.get("layer") == row.get("original_layer")]
    if len(existing_main) == 1:
        expected_main_layer = existing_main[0]["original_layer"]
    if (issued_output or stage_is_issued) and autocad.get("layer") != expected_main_layer:
        errors.append(f"{finding_id}: для основной группы требуется точный слой {expected_main_layer}")
    errors.extend(_validate_created_graphics(finding))
    if autocad.get("common_ray_origin") is not True and not any_ray:
        errors.append(f"{finding_id}: лучи замечания должны иметь одну начальную точку")
    if for_application:
        errors.extend(_validate_individual_analysis(finding))
        errors.extend(_validate_compliance_source_copy(finding, eligible_rule_text_sources))
        if status != "violation" and not is_general and not non_issue_required and not stage_is_issued:
            errors.append(f"{finding_id}: отсутствует результат, разрешённый для нанесения")
        if non_issue_required and non_issue_note_present:
            if not autocad.get("source_entity_ids"):
                errors.append(f"{finding_id}: не указан исходный элемент, рядом с которым размещается зелёное пояснение")
            if not non_issue_planned:
                errors.append(f"{finding_id}: зелёное пояснение об отказе не подготовлено")
        if issued_output:
            if not source_group.get("remark_text_entity_ids"):
                errors.append(f"{finding_id}: не определён текстовый объект исходного замечания")
            if not source_group.get("frame_entity_ids"):
                errors.append(f"{finding_id}: не определена рамка над исходным замечанием")
            if not source_group.get("number_entity_ids"):
                errors.append(f"{finding_id}: не определён номер в рамке исходного замечания")
            purple_present = source_group.get("purple_explanation_present")
            if purple_present not in {True, False}:
                errors.append(f"{finding_id}: не проверено наличие фиолетового пояснения у замечания")
            elif purple_present and not source_group.get("purple_explanation_entity_ids"):
                errors.append(f"{finding_id}: фиолетовое пояснение есть, но его объект не включён в группу")

            side = placement.get("side")
            if side not in {"left", "top", "right", "bottom"}:
                errors.append(f"{finding_id}: не указана сторона размещения замечания относительно этажа")
            sequence_index = placement.get("sequence_index")
            if not isinstance(sequence_index, int) or sequence_index < 1:
                errors.append(f"{finding_id}: не указан последовательный номер размещения замечания у этажа")
            offset = placement.get("exterior_wall_offset_mm")
            if not isinstance(offset, (int, float)) or offset <= 0:
                errors.append(f"{finding_id}: не указано расстояние замечания от наружной стены")
            elif not 25000 <= float(offset) <= 35000:
                warnings.append(f"{finding_id}: расстояние от наружной стены отличается от ориентировочных 30000 мм")

            if source_group.get("purple_explanation_present") is True:
                expected_side = {"left": "left", "top": "top", "right": "right", "bottom": "left"}.get(side)
                if placement.get("explanation_side") != expected_side:
                    errors.append(f"{finding_id}: фиолетовое пояснение расположено не с требуемой стороны")
        if autocad.get("ray_required_by_sample") and not autocad.get("target_entity_ids"):
            errors.append(f"{finding_id}: отсутствует подтверждённая цель луча")
        if autocad.get("overlap_checked") is not True:
            errors.append(f"{finding_id}: не выполнена проверка наложений")
        if autocad.get("status") not in {"verified", "applied", "post_checked"}:
            errors.append(f"{finding_id}: операция не прошла предварительную проверку")
        if autocad.get("status") in {"applied", "post_checked"} and non_issue_required and non_issue_note_present and not non_issue.get("entity_id"):
            errors.append(f"{finding_id}: не записан AutoCAD id созданного зелёного пояснения")
        if mandatory_registry_ray and autocad.get("status") in {"applied", "post_checked"} and not autocad.get("ray_entity_ids"):
            errors.append(f"{finding_id}: нанесённый обязательный луч не имеет AutoCAD-id в реестре")
    return errors, warnings


def validate_source_reading(finding: dict[str, Any]) -> list[str]:
    errors = []
    prefix = str(finding.get("id") or "без id")
    reading = finding.get("analysis", {}).get("source_reading", {})
    if not isinstance(reading, dict):
        return [f"{prefix}: неверный формат журнала чтения источников"]
    documents = reading.get("required_documents", [])
    if not isinstance(documents, list) or not documents:
        return [f"{prefix}: не зафиксировано полное чтение необходимых нормативных источников"]
    for document in documents:
        if not isinstance(document, dict):
            errors.append(f"{prefix}: неверная запись нормативного источника")
            continue
        name = document.get("relative_path", "")
        if not name or not document.get("source_sha256") or not document.get("pages_read"):
            errors.append(f"{prefix}: у источника отсутствуют имя, SHA256 или прочитанные страницы")
        if document.get("full_context_read") is not True:
            errors.append(f"{prefix}: не прочитан полный относящийся контекст {name}")
        if document.get("unresolved_pages"):
            errors.append(f"{prefix}: в необходимом источнике {name} остались непрочитанные страницы")
        if not isinstance(document.get("ocr_used"), bool) or not isinstance(document.get("visual_review_required"), bool):
            errors.append(f"{prefix}: для источника {name} не указан способ чтения и необходимость визуальной сверки")
        if (document.get("ocr_used") is True or document.get("visual_review_required") is True) and document.get("visual_verified") is not True:
            errors.append(f"{prefix}: OCR/графика источника {name} не сверены с изображением")
    if reading.get("norm_comments_letters_separated") is not True:
        errors.append(f"{prefix}: норма, авторский комментарий и письмо не разделены")
    letter_checks = reading.get("letter_checks", [])
    if not isinstance(letter_checks, list) or any(not isinstance(c, dict) for c in letter_checks):
        return errors + [f"{prefix}: неверный формат проверок частей писем"]
    checks = {str(c.get("reference")): c for c in letter_checks}
    for reference in reading.get("detected_letter_references", []):
        check = checks.get(str(reference), {})
        parts = check.get("all_parts", [])
        if not parts or not set(parts).issubset(set(check.get("read_parts", []))):
            errors.append(f"{prefix}: прочитаны не все части письма [{reference}]")
        if check.get("date_number_and_current_norm_checked") is not True:
            errors.append(f"{prefix}: реквизиты и применимость письма [{reference}] не проверены")
    order = reading.get("order_2594_check", {})
    if order.get("status") == "included":
        if not order.get("source_file") or not order.get("exact_item"):
            errors.append(f"{prefix}: не указан файл перечня № 2594 и конкретная включённая часть СП")
    elif order.get("status") == "not_applicable":
        if not order.get("reason"):
            errors.append(f"{prefix}: не обоснована неприменимость проверки перечня № 2594")
    elif order.get("status") == "excluded" and finding.get("decision", {}).get("status") == "not_applicable":
        if not order.get("source_file") or not order.get("exact_item"):
            errors.append(f"{prefix}: отсутствие пункта в перечне № 2594 не подтверждено")
    else:
        errors.append(f"{prefix}: применимость точного пункта по перечню № 2594 не установлена")
    return errors


def validate_registry(registry: dict[str, Any], for_application: bool = False) -> dict[str, Any]:
    if registry.get("mode") == "protocol":
        from .protocol_validation import validate_protocol_registry
        return validate_protocol_registry(registry, for_application=for_application)
    if registry.get("mode") == "actualization_protocol":
        from .actualization_validation import validate_actualization_registry
        return validate_actualization_registry(registry, for_application=for_application)
    errors: list[str] = []
    warnings: list[str] = []
    placement_sequences: dict[str, list[int]] = {}
    explanation_owners: dict[str, tuple[str, str]] = {}
    earlier_unfinished_id: str | None = None
    all_violation_graphics_on_expertise_layer = True
    all_purple_explanations_on_expertise_explanations_layer = True
    checked_entity_ids: list[str] = []
    seen_findings: dict[str, dict[str, Any]] = {}
    for finding in registry.get("findings", []):
        item_errors, item_warnings = validate_finding(finding, for_application=for_application,
                                                     eligible_rule_text_sources=registry.get("eligible_rule_text_sources"))
        errors.extend(item_errors)
        warnings.extend(item_warnings)
        graphics_state = _created_graphics_state(finding)
        checked_entity_ids.extend(graphics_state["checked_entity_ids"])
        if not graphics_state["valid_list"] or not graphics_state["violation_ok"]:
            all_violation_graphics_on_expertise_layer = False
        if not graphics_state["valid_list"] or not graphics_state["purple_ok"]:
            all_purple_explanations_on_expertise_explanations_layer = False
        autocad = finding.get("autocad", {})
        finding_id = str(finding.get("id") or "без id")
        source_group = autocad.get("source_group", {})
        if (
            for_application
            and finding.get("mode") == "compliance"
            and _issued_graphics_required(finding)
            and source_group.get("copy_case") == "repeated_floor"
        ):
            previous_id = str(source_group.get("previous_actualized_group_finding_id", "")).strip()
            previous = seen_findings.get(previous_id)
            if previous is None:
                errors.append(
                    f"{finding_id}: повторная группа ссылается не на предыдущее замечание текущего реестра"
                )
            else:
                previous_group = previous.get("autocad", {}).get("source_group", {})
                if previous_group.get("source_template_key") != source_group.get("source_template_key"):
                    errors.append(f"{finding_id}: предыдущая группа относится к другому шаблону замечания")
                if previous.get("autocad", {}).get("status") not in {"applied", "post_checked"}:
                    errors.append(f"{finding_id}: предыдущая группа ещё не размещена и не актуализирована")
        seen_findings[finding_id] = finding
        if _issued_graphics_required(finding) and autocad.get("status") in {"applied", "post_checked"}:
            verification = autocad.get("layer_verification", {})
            if (
                not _result_graphics_rows(autocad)
                or verification.get("all_created_objects_collected") is not True
                or verification.get("checked_against_autocad") is not True
                or verification.get("all_violation_graphics_on_expertise_layer") is not True
            ):
                all_violation_graphics_on_expertise_layer = False
            if (
                verification.get("all_created_objects_collected") is not True
                or verification.get("checked_against_autocad") is not True
                or verification.get("all_purple_explanations_on_expertise_explanations_layer") is not True
            ):
                all_purple_explanations_on_expertise_explanations_layer = False
        analysis = finding.get("analysis", {})
        analysis_finished = (
            analysis.get("individual_analysis_complete") is True
            and analysis.get("second_pass", {}).get("completed") is True
        )
        if earlier_unfinished_id and analysis_finished:
            errors.append(
                f"{finding.get('id') or 'без id'}: анализ завершён раньше предыдущего незавершённого замечания {earlier_unfinished_id}"
            )
        if not analysis_finished and earlier_unfinished_id is None:
            earlier_unfinished_id = str(finding.get("id") or "без id")
        status = finding.get("decision", {}).get("status")
        output = finding.get("output", {})
        is_general = bool(output.get("is_general_requirement"))
        issued_output = status == "violation" or (is_general and status not in NON_ISSUED_REVIEWED)
        if for_application and issued_output:
            autocad = finding.get("autocad", {})
            floor = str(autocad.get("floor", "")).strip()
            sequence_index = autocad.get("placement", {}).get("sequence_index")
            if floor and isinstance(sequence_index, int):
                placement_sequences.setdefault(floor, []).append(sequence_index)
        if for_application:
            finding_id = str(finding.get("id") or "без id")
            analysis = finding.get("analysis", {})
            output = finding.get("output", {})
            explanations = (
                ("индивидуальное обоснование", analysis.get("individual_reasoning", "")),
                ("пояснение", output.get("explanation", "")),
                ("пояснение о невыдаче", output.get("non_issue_explanation", {}).get("text", "")),
            )
            for label, value in explanations:
                normalized = " ".join(str(value).lower().split())
                if not normalized:
                    continue
                previous = explanation_owners.get(normalized)
                if previous and previous[0] != finding_id:
                    errors.append(
                        f"{finding_id}: {label} дословно повторяет объяснение замечания {previous[0]}; шаблонные объяснения блокируют применение"
                    )
                else:
                    explanation_owners[normalized] = (finding_id, label)
    for floor, indexes in placement_sequences.items():
        if len(indexes) != len(set(indexes)):
            errors.append(f"этаж {floor}: повторяются номера последовательности размещения замечаний")
        expected = list(range(1, len(indexes) + 1))
        if sorted(set(indexes)) != expected:
            warnings.append(f"этаж {floor}: последовательность размещения замечаний имеет пропуски")
    dwg_actions = registry.get("dwg_actions", {})
    if dwg_actions.get("save_requested") or dwg_actions.get("source_save_requested"):
        errors.append("Сохранение или перезапись исходного DWG запрещены; требуется отдельный новый файл результата")
    if dwg_actions.get("source_overwritten"):
        errors.append("Исходный DWG был перезаписан")
    if dwg_actions.get("delete_requested"):
        errors.append("Удаление объектов не разрешено постоянными правилами")
    if dwg_actions.get("work_completed"):
        if dwg_actions.get("result_copy_created") is not True:
            errors.append("Для завершённой работы не создан отдельный новый DWG с результатом")
        if not str(dwg_actions.get("result_copy_path", "")).strip():
            errors.append("Для завершённой работы не записан полный путь нового DWG")
        if dwg_actions.get("source_document_contains_results") is not False:
            errors.append("Исходный документ содержит результаты запуска; работать требуется только в отдельной копии")
        if dwg_actions.get("source_document_kept_unchanged") is not True:
            errors.append("Не подтверждена сохранность исходного DWG без изменений")
        if dwg_actions.get("copy_did_not_change_open_source_identity") is not True:
            errors.append("Не подтверждена сохранность пути и идентичности исходного открытого документа")
    graphics_layer_check = {
        "all_violation_graphics_on_expertise_layer": all_violation_graphics_on_expertise_layer,
        "all_purple_explanations_on_expertise_explanations_layer": (
            all_purple_explanations_on_expertise_explanations_layer
        ),
        "checked_entity_ids": list(dict.fromkeys(checked_entity_ids)),
    }
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "all_violation_graphics_on_expertise_layer": all_violation_graphics_on_expertise_layer,
        "all_purple_explanations_on_expertise_explanations_layer": (
            all_purple_explanations_on_expertise_explanations_layer
        ),
        "graphics_layer_check": graphics_layer_check,
    }


def validate_final_numbering(items: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(items, list):
        return {"ok": False, "errors": ["Финальная нумерация должна быть списком"], "warnings": []}
    if not items:
        return {"ok": True, "errors": [], "warnings": ["Нет выданных замечаний для нумерации"]}

    categories = {"lowest_floor", "stage_frame", "upper_floor"}
    numbers: list[int] = []
    floor_orders: list[float] = []
    for index, item in enumerate(items, start=1):
        label = item.get("id") or f"строка {index}"
        number = item.get("final_number")
        if not isinstance(number, int) or number < 1:
            errors.append(f"{label}: итоговый номер должен быть положительным целым числом")
        else:
            numbers.append(number)
        category = item.get("category")
        if category not in categories:
            errors.append(f"{label}: неизвестная группа финальной нумерации")
        sequence = item.get("placement_sequence")
        if not isinstance(sequence, int) or sequence < 1:
            errors.append(f"{label}: не указан порядок замечания внутри этажа или рамки")
        if not item.get("number_entity_ids"):
            errors.append(f"{label}: не указан объект номера в рамке замечания")
        if not item.get("remark_entity_ids"):
            errors.append(f"{label}: не указана группа соответствующего замечания")
        if category in {"lowest_floor", "upper_floor"}:
            floor_order = item.get("floor_order")
            if not isinstance(floor_order, (int, float)):
                errors.append(f"{label}: не задан числовой порядок этажа")
            else:
                floor_orders.append(float(floor_order))

    expected_numbers = list(range(1, len(items) + 1))
    if sorted(numbers) != expected_numbers:
        errors.append("Итоговые номера должны начинаться с 1 и идти без повторов и пропусков")

    floor_min = min(floor_orders) if floor_orders else None
    if floor_min is not None:
        for item in items:
            category = item.get("category")
            floor_order = item.get("floor_order")
            if not isinstance(floor_order, (int, float)):
                continue
            if category == "lowest_floor" and float(floor_order) != floor_min:
                errors.append(f"{item.get('id') or 'замечание'}: неверно отнесено к нижнему этажу")
            if category == "upper_floor" and float(floor_order) <= floor_min:
                errors.append(f"{item.get('id') or 'замечание'}: верхний этаж должен следовать после нижнего")

    rank = {"lowest_floor": 0, "stage_frame": 1, "upper_floor": 2}

    def expected_key(item: dict[str, Any]) -> tuple[float, float, float]:
        category = item.get("category")
        group_rank = float(rank.get(category, 99))
        floor_order = float(item.get("floor_order", 0) or 0) if category == "upper_floor" else 0.0
        sequence = float(item.get("placement_sequence", 0) or 0)
        return group_rank, floor_order, sequence

    by_number = sorted(items, key=lambda row: row.get("final_number", 10**9))
    expected_order = sorted(items, key=expected_key)
    actual_ids = [item.get("id") or id(item) for item in by_number]
    expected_ids = [item.get("id") or id(item) for item in expected_order]
    if actual_ids != expected_ids:
        errors.append("Нарушен порядок: нижний этаж, рамка этапизации, затем верхние этажи по возрастанию")

    return {"ok": not errors, "errors": errors, "warnings": warnings}
