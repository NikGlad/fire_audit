"""Validate protocol decisions separately from physical compliance with plans.

The protocol namespace is opt-in for historical actualization registries.  New
standalone protocols always require it.  Only fully validated protocol records
can authorize exceptions to the ordinary actualization layer rules.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import PureWindowsPath
from typing import Any


ACTIVE = "экспертиза"
RESOLVED = "экспертиза устранено"
PROTOCOL_RESOLVED = "экспертиза устранено протокол"
STU = "экспертиза СТУ"
STU_PROTOCOL = "экспертиза СТУ протокол"
EXPLANATIONS = "экспертиза пояснения"
CLIENT_EXPLANATIONS = "экспертиза пояснения для заказчика"
TRANSITION_LAYERS = {
    "accepted_commitment": PROTOCOL_RESOLVED,
    "plan_resolved": RESOLVED,
    "rejected": ACTIVE,
    "no_answer": ACTIVE,
    "documentary_carry_forward": RESOLVED,
    "stu_carry_forward": STU,
    "unanswered_explanation": EXPLANATIONS,
}
PHYSICAL_STATUSES = {"resolved", "partially_resolved", "unresolved", "insufficient_data", "not_rechecked"}
DECISIONS = {"accepted", "partially_accepted", "rejected", "not_confirmed", "no_answer"}


def _ids(value: Any) -> set[str]:
    return {item for item in value if isinstance(item, str) and item} if isinstance(value, list) else set()


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def protocol_validation_state(registry: dict[str, Any], *, strict: bool,
                              standalone: bool = False, planned_only: bool = False) -> dict[str, Any]:
    """Return errors and narrowly authorized object changes for actualization."""
    errors: list[str] = []
    warnings: list[str] = []
    state: dict[str, Any] = {"errors": errors, "warnings": warnings,
                            "owned_entity_ids": set(), "transition_layers": {},
                            "physical_status_by_main_id": {}, "source_main_text_ids": set(),
                            "owned_by_main_id": {}, "layers_by_main_id": {}}
    protocol = registry.get("protocol")
    if protocol is None:
        if standalone and strict:
            errors.append("Не заполнен отдельный реестр протокола")
        return state
    if not isinstance(protocol, dict):
        errors.append("Реестр protocol должен быть объектом")
        return state
    items = protocol.get("items", [])
    scope = protocol.get("scope_main_text_ids", [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        errors.append("protocol.items должен содержать записи выданных групп")
        return state
    required = standalone or protocol.get("required") is True
    if not isinstance(scope, list) or len(_ids(scope)) != len(scope):
        errors.append("Не подтверждён уникальный охват выданных групп протокола")
        scope = []
    main_ids = [_mapping(item.get("source")).get("main_text_id") for item in items]
    main_ids = [value if isinstance(value, str) else None for value in main_ids]
    if strict and (Counter(main_ids) != Counter(scope) or (required and not scope)):
        errors.append("Не подтверждён полный охват ответов и безответных выданных групп протокола")
    if len(main_ids) != len(set(main_ids)) or (items and any(not isinstance(h, str) or not h for h in main_ids)):
        errors.append("Повторяются или отсутствуют идентификаторы исходных групп протокола")
    verified = {item.get("handle"): item for item in registry.get("verified_graphics", [])}
    before = {item.get("handle"): item for item in registry.get("graphics_before", [])}
    changed, created = _ids(registry.get("changed_entity_ids", [])), _ids(registry.get("created_entity_ids", []))
    color_checks = protocol.get("text_color_checks", [])
    if not isinstance(color_checks, list):
        errors.append("Не заполнены проверки цветов текстов протокола")
        color_checks = []
    owned: set[str] = set()
    expected: dict[str, str] = {}
    source_owners: dict[str, list[tuple[str, bool]]] = {}
    layer_overrides: dict[str, str] = {}
    checked_roles: set[tuple[str, str]] = set()
    role_colors: dict[str, set[int]] = {}
    finding_sources = {_mapping(finding.get("source")).get("main_text_id"): _mapping(finding.get("source"))
                       for finding in registry.get("findings", []) if isinstance(finding, dict)}
    physical_targets_by_main: dict[str, set[str]] = {}
    if registry.get("mode") == "actualization_protocol":
        for finding in registry.get("findings", []):
            if not isinstance(finding, dict):
                continue
            main = _mapping(finding.get("source")).get("main_text_id")
            if not isinstance(main, str):
                continue
            target_ids: set[str] = set()
            for target in finding.get("targets", []):
                if isinstance(target, dict):
                    target_ids.update(_ids(target.get("graphic_ids", [])))
                    ray = target.get("ray_id")
                    if isinstance(ray, str) and ray:
                        target_ids.add(ray)
            physical_targets_by_main[main] = target_ids
    headers_by_main: dict[str, set[str]] = {}
    for item in items:
        source = _mapping(item.get("source"))
        main = source.get("main_text_id")
        if isinstance(main, str):
            fallback = finding_sources.get(main, {})
            headers_by_main[main] = {h for h in (main, source.get("number_id", fallback.get("number_id")),
                                                      source.get("frame_id", fallback.get("frame_id")))
                                     if isinstance(h, str) and h}
    protected_headers = set().union(*headers_by_main.values()) if headers_by_main else set()

    def require(condition: bool, message: str) -> None:
        if strict and not condition:
            errors.append(message)

    if planned_only and items:
        require(protocol.get("planned_text_colors") == {"client_response": 4, "consultant_explanation": 30},
                "В Word-задании не зафиксированы цвета ответа ACI 4 и консультанта ACI 30")
    for item in items:
        source, response = _mapping(item.get("source")), _mapping(item.get("response"))
        consultant, plan = _mapping(item.get("consultant")), _mapping(item.get("plan"))
        transition = _mapping(item.get("transition"))
        main_id = source.get("main_text_id")
        if not isinstance(main_id, str):
            main_id = None
        label = f"Протокол {source.get('original_number', main_id or '?')}"
        source_ids = _ids(source.get("entity_ids", []))
        shared_ids = _ids(source.get("shared_graphic_ids", []))
        item_owned: set[str] = set()
        item_overrides: dict[str, str] = {}
        response_ids = _ids(response.get("entity_ids", []))
        consultant_ids = _ids(consultant.get("entity_ids", []))
        all_ids = source_ids | response_ids | consultant_ids
        response_status, decision = response.get("status"), consultant.get("decision")
        physical = plan.get("physical_status")
        checkability = plan.get("checkability")
        kind = transition.get("kind")
        transition_ids = _ids(transition.get("entity_ids", []))
        evidence = _mapping(transition.get("evidence"))
        comment_date = _date(consultant.get("date"))
        require(not response.get("kind") or not evidence.get("response_kind")
                or response.get("kind") == evidence.get("response_kind"),
                f"{label}: классификация ответа противоречит основанию протокольного действия")
        require(source.get("issued_group") is True and source.get("inventory_complete") is True,
                f"{label}: не подтверждён полный состав именно выданной группы, а не исходного шаблона")
        require(bool(source.get("full_text")) and main_id in source_ids,
                f"{label}: отсутствуют исходное требование или основной текст в составе группы")
        require(source.get("kind", "remark") in {"remark", "explanation"},
                f"{label}: неизвестный вид исходной группы")
        require(source.get("kind", "remark") == "explanation" or source.get("original_number") is not None,
                f"{label}: не сохранён исходный номер замечания")
        require(shared_ids <= source_ids and not shared_ids & protected_headers,
                f"{label}: общими могут быть только связанные графические объекты, не текст, номер или рамка")
        if shared_ids:
            require(source.get("kind") == "explanation" or len(headers_by_main.get(main_id, set())) == 3,
                    f"{label}: для общей графики необходимо явно определить основной текст, номер и рамку")
        for handle in shared_ids:
            graphic = before.get(handle, verified.get(handle, {}))
            graphic_type = str(graphic.get("type", graphic.get("entity_type", ""))).casefold().removeprefix("acdb")
            require(graphic_type in {"line", "polyline", "lwpolyline", "polyline2d", "polyline3d", "circle", "arc",
                                     "hatch", "ellipse", "spline"} or graphic_type.endswith("dimension"),
                    f"{label}: общий объект {handle} не подтверждён как графика/обмер")
        for handle in source_ids:
            previous = source_owners.get(handle, [])
            if previous and (handle not in shared_ids or any(not is_shared for _, is_shared in previous)):
                errors.append(f"{label}: общий объект {handle} не объявлен общей графикой каждым владельцем")
            source_owners.setdefault(handle, []).append((main_id, handle in shared_ids))
        require(response_status in {"received", "missing"}, f"{label}: не определено наличие ответа")
        require(decision in DECISIONS and bool(consultant.get("text")) and comment_date is not None,
                f"{label}: нет датированного решения пожарного консультанта")
        require(bool(consultant.get("remaining_actions")) and bool(consultant.get("basis")),
                f"{label}: не указаны оставшиеся действия и основание решения")
        if planned_only:
            require(bool(consultant.get("word_reference")) and consultant.get("planned_inside_original_block") is True,
                    f"{label}: Word-задание не содержит адреса пояснения и его размещения внутри исходного блока")
        else:
            require(bool(consultant_ids) and consultant.get("inside_original_block") is True,
                    f"{label}: пояснение консультанта не подтверждено внутри исходного блока")
            require(consultant_ids <= changed | created,
                    f"{label}: датированное пояснение не зарегистрировано среди изменённых или созданных текстов")
        if response_status == "received":
            require(bool(response.get("text")) and bool(response.get("reference"))
                    and ((response.get("planned_inside_original_block") is True) if planned_only else
                         (bool(response_ids) and response.get("inside_original_block") is True)),
                    f"{label}: ответ не связан с источником и текстом внутри исходного блока")
            require(decision != "no_answer", f"{label}: поступивший ответ ошибочно отмечен как отсутствующий")
            require(isinstance(response.get("claims_fixed"), bool),
                    f"{label}: не классифицировано заявление заказчика об устранении")
            if response.get("claims_fixed") is True and physical in {"unresolved", "partially_resolved"}:
                require(decision in {"rejected", "partially_accepted"} and kind != "accepted_commitment",
                        f"{label}: ответ «исправлено» нельзя принять при доказанном оставшемся нарушении")
        else:
            require(not response_ids and not response.get("text"),
                    f"{label}: отсутствующему ответу приписаны текст или объекты")
        require(checkability in {"checkable", "not_checkable", "excluded"} and bool(plan.get("reference"))
                and physical in PHYSICAL_STATUSES,
                f"{label}: не записаны план, проверяемость и отдельный фактический статус")
        if checkability != "checkable":
            require(physical in {"insufficient_data", "not_rechecked"},
                    f"{label}: непроверяемое или исключённое требование выдано за фактическое устранение/нарушение")
        if physical in {"resolved", "partially_resolved", "unresolved"}:
            proof = _mapping(plan.get("evidence"))
            require(all(proof.get(key) for key in ("project_entity_ids", "method", "result")),
                    f"{label}: фактический статус не подтверждён проверкой проектных объектов")
        if kind not in {*TRANSITION_LAYERS, "none"}:
            errors.append(f"{label}: неизвестное протокольное действие")
        elif kind != "none":
            require(bool(all_ids) and transition_ids == all_ids,
                    f"{label}: переход должен охватывать всю исходную группу, геометрию, ответы и пояснения")
            require(main_id in before and source_ids <= set(before) | created,
                    f"{label}: не сохранены свойства исходных объектов до перехода и состав созданных дополнений")
            if planned_only:
                require(transition.get("planned_only") is True,
                        f"{label}: Word-задание должно отличать планируемый переход от выполненного")
            else:
                # Keeping an unanswered group on its existing active layer is
                # a reviewed status, not a CAD mutation of every old object.
                journal_required = transition_ids
                if kind == "no_answer":
                    journal_required = {h for h in transition_ids
                                        if before.get(h, {}).get("layer") != TRANSITION_LAYERS[kind]}
                require(journal_required <= changed | created,
                        f"{label}: переход всей группы не совпадает с журналом изменений")
            for handle in transition_ids:
                layer = TRANSITION_LAYERS[kind]
                if handle in expected and expected[handle] != layer:
                    errors.append(f"{label}: объекту {handle} назначены противоречивые слои")
                expected[handle] = layer
                layer_overrides[handle] = layer
                item_overrides[handle] = layer
            owned.update(transition_ids)
            item_owned.update(transition_ids)
        else:
            require(not transition_ids and bool(evidence.get("reason")),
                    f"{label}: сохранение слоя должно иметь основание и не содержать переходов")
            response_kind = response.get("kind", evidence.get("response_kind"))
            if response_kind is None and response.get("claims_fixed") is True:
                response_kind = "completed_correction"
            if response_status == "received":
                require(response_kind in {"information", "disagreement", "completed_correction", "commitment", "corrective_scheme"},
                        f"{label}: сохранение слоя требует явной классификации ответа, включая обещания и схемы")
            # A no-transition record must not silently recolour source graphics,
            # nor exempt unchanged targets from ordinary physical validation.
            require(source_ids <= set(before), f"{label}: не сохранены исходные слои группы без перехода")
            physical_target_ids = (physical_targets_by_main.get(main_id, set())
                                   - protected_headers - response_ids - consultant_ids)
            for handle in source_ids:
                # A target may have been physically corrected during this same
                # actualization cycle. Its layer and evidence remain solely the
                # responsibility of the ordinary per-target validator; do not
                # freeze the old layer or grant a protocol override for it.
                if handle in physical_target_ids:
                    continue
                layer = before.get(handle, {}).get("layer")
                if handle in expected and expected[handle] != layer:
                    errors.append(f"{label}: общей графике {handle} назначены противоречивые слои")
                if layer:
                    expected[handle] = layer
            # Appending different coloured spans to an existing MText is valid.
            for handle in response_ids | consultant_ids:
                layer = before.get(handle, before.get(main_id, {})).get("layer")
                require(bool(layer), f"{label}: не подтверждён исходный слой текста без перехода")
                if layer:
                    if handle in expected and expected[handle] != layer:
                        errors.append(f"{label}: объекту {handle} назначены противоречивые слои")
                    expected[handle] = layer
                    layer_overrides[handle] = layer
                    item_overrides[handle] = layer
        owned.update(response_ids | consultant_ids)
        item_owned.update(response_ids | consultant_ids)

        source_is_blue = before.get(main_id, {}).get("layer") in {STU, STU_PROTOCOL}
        response_kind = response.get("kind", evidence.get("response_kind"))
        if response_status == "received" and not source_is_blue and decision == "accepted":
            if response_kind in {"commitment", "corrective_scheme"}:
                require(kind in {"accepted_commitment", "plan_resolved"},
                        f"{label}: принятое обещание или схема требуют установленного переноса, а не сохранения слоя")
        ordinary_remark = source.get("kind", "remark") == "remark" and not source_is_blue
        active_header_required = ordinary_remark and (
            decision == "rejected" or (kind == "none" and physical in {"unresolved", "partially_resolved"})
            or (response.get("claims_fixed") is True and decision in {"partially_accepted", "not_confirmed"}))
        if active_header_required:
            headers = headers_by_main.get(main_id, set())
            require(kind != "none" or (len(headers) == 3 and headers <= source_ids),
                    f"{label}: для отказа без переноса необходимо определить основной текст, номер и рамку")
            for handle in headers:
                final_layer = expected.get(handle, before.get(handle, {}).get("layer")) if planned_only else verified.get(handle, {}).get("layer")
                require(final_layer == ACTIVE, f"{label}: отклонённое или неустранённое замечание должно иметь действующий заголовок")
        if ordinary_remark and kind == "none" and physical == "unresolved":
            require(all((expected.get(h, before.get(h, {}).get("layer")) if planned_only else
                         verified.get(h, {}).get("layer")) not in {RESOLVED, PROTOCOL_RESOLVED} for h in source_ids),
                    f"{label}: полностью неустранённая группа не может сохраняться в слое устранения")

        if kind == "accepted_commitment":
            require(response_status == "received" and decision == "accepted"
                    and evidence.get("response_kind") in {"commitment", "corrective_scheme"}
                    and bool(evidence.get("accepted_solution")) and evidence.get("covers_all_requirements") is True,
                    f"{label}: протокольное принятие всей группы не подтверждено принятым обещанием или схемой")
            require(evidence.get("not_physical_resolution") is True,
                    f"{label}: принятие обещания нельзя объявлять фактическим устранением")
        elif kind == "plan_resolved":
            require(response_status == "received" and physical == "resolved" and checkability == "checkable"
                    and decision == "accepted",
                    f"{label}: фактическое устранение не подтверждено полным исправлением по плану и принятым ответом")
        elif kind == "rejected":
            require(response_status == "received" and decision in {"rejected", "partially_accepted", "not_confirmed"},
                    f"{label}: возврат/сохранение действующего замечания не согласуется с решением по ответу")
        elif kind in {"no_answer", "unanswered_explanation"}:
            require(response_status == "missing" and decision == "no_answer"
                    and "замечание осталось без внимания" in str(consultant.get("text", "")).casefold(),
                    f"{label}: безответная группа требует пояснения «замечание осталось без внимания»")
            if kind == "unanswered_explanation":
                require(source.get("kind") == "explanation"
                        and before.get(main_id, {}).get("layer") == CLIENT_EXPLANATIONS,
                        f"{label}: перенос безответного пояснения не подтверждён исходным слоем для заказчика")
        elif kind == "documentary_carry_forward":
            prior_date = _date(evidence.get("previous_acceptance_date"))
            require(checkability == "not_checkable" and physical in {"not_rechecked", "insufficient_data"}
                    and decision == "accepted" and bool(evidence.get("previous_protocol_reference"))
                    and bool(evidence.get("previous_acceptance_text")) and prior_date is not None
                    and comment_date is not None and prior_date <= comment_date
                    and evidence.get("requirement_unchanged") is True
                    and evidence.get("no_conflicting_information") is True
                    and evidence.get("documentary_only") is True,
                    f"{label}: документарный перенос не подтверждён прежним принятием непроверяемого требования")
            require(all(before.get(h, {}).get("layer") == PROTOCOL_RESOLVED for h in source_ids),
                    f"{label}: документарный перенос допустим только из «экспертиза устранено протокол»")
        elif kind == "stu_carry_forward":
            prior_date = _date(evidence.get("previous_notice_date"))
            require(bool(evidence.get("previous_notice_reference")) and prior_date is not None
                    and comment_date is not None and prior_date <= comment_date
                    and evidence.get("previous_notice_sent") is True
                    and evidence.get("no_new_reply_about_requirement") is True
                    and evidence.get("requirement_unchanged") is True and response_status == "missing",
                    f"{label}: перенос СТУ не подтверждён прежним уведомлением и отсутствием нового ответа")
            require(all(before.get(h, {}).get("layer") == STU_PROTOCOL for h in source_ids),
                    f"{label}: повторный перенос СТУ допустим только из «экспертиза СТУ протокол»")
        if kind in {"documentary_carry_forward", "stu_carry_forward"}:
            require(evidence.get("subsequent_cycle") is True and evidence.get("new_materials_received") is True
                    and bool(evidence.get("previous_protocol_reference")) and bool(evidence.get("new_materials_reference"))
                    and evidence.get("previous_protocol_reference") != evidence.get("new_materials_reference"),
                    f"{label}: повторный перенос требует подтверждённого нового поступления после предыдущего протокола")
        if response_status == "missing" and kind not in {"no_answer", "unanswered_explanation",
                                                           "documentary_carry_forward", "stu_carry_forward"}:
            require(kind == "none" and before.get(main_id, {}).get("layer") in {STU, STU_PROTOCOL}
                    and decision == "no_answer"
                    and "замечание осталось без внимания" in str(consultant.get("text", "")).casefold(),
                    f"{label}: отсутствие ответа не обработано установленным правилом или специальным повторным циклом")
        if before.get(main_id, {}).get("layer") in {STU, STU_PROTOCOL}:
            require(kind in {"none", "stu_carry_forward"},
                    f"{label}: синяя группа СТУ не должна переноситься по обычному правилу устранения")
        if response_status == "missing" and before.get(main_id, {}).get("layer") == CLIENT_EXPLANATIONS:
            require(kind == "unanswered_explanation",
                    f"{label}: безответное пояснение для заказчика должно перейти в «экспертиза пояснения»")

        methods = response.get("selected_methods", [])
        require(isinstance(methods, list) and all(isinstance(method, str) and method in {"stu", "risk", "firefighting_plan"} for method in methods),
                f"{label}: не распознаны выбранные заказчиком варианты решения")
        if methods and physical in {"unresolved", "partially_resolved"}:
            notice = _mapping(item.get("manager_notice"))
            require(notice.get("status") in {"included_in_result", "sent"} and bool(notice.get("reference"))
                    and bool(notice.get("text")) and notice.get("originating_remark_link") == main_id,
                    f"{label}: сохраняющееся нарушение с запросом СТУ/риска/плана тушения не отражено в уведомлении руководителю")
            if notice.get("status") == "sent":
                require(bool(notice.get("sent_reference")),
                        f"{label}: отправка уведомления руководителю не подтверждена")

        for role, handles, index in (("client_response", response_ids, 4),
                                      ("consultant_explanation", consultant_ids, 30)):
            for handle in handles:
                checks = [check for check in color_checks if isinstance(check, dict)
                          and check.get("entity_id") == handle and check.get("role") == role]
                require(planned_only or (bool(checks) and all(check.get("color_index") == index
                        and check.get("inline_formatting_checked") is True for check in checks)),
                        f"{label}: не подтверждён фактический цвет {index} для {role} в {handle}")
                checked_roles.add((handle, role))
                role_colors.setdefault(handle, set()).add(index)
        derived_objects = item.get("derived_objects", [])
        if not isinstance(derived_objects, list) or any(not isinstance(obj, dict) for obj in derived_objects):
            errors.append(f"{label}: неверный перечень графики и характеристик по ответу")
            derived_objects = []
        for derived in derived_objects:
            derived_ids = _ids(derived.get("entity_ids", []))
            derived_kind = derived.get("kind")
            require(response_status == "received" and bool(derived.get("source_reference"))
                    and ((derived.get("planned_only") is True and bool(derived.get("placement_reference")))
                         if planned_only else (bool(derived_ids) and derived.get("placement_verified") is True)),
                    f"{label}: добавленная графика/характеристика не связана с ответом и проверенным местом")
            require(planned_only or derived_ids <= changed | created,
                    f"{label}: добавленные сведения не отражены в журнале")
            require(not derived_ids & all_ids,
                    f"{label}: добавленная графика/характеристика не должна подменять объекты исходного замечания")
            require(derived_kind in {"fire_lift_circle", "fire_post_label", "pump_station_label", "object_characteristics"},
                    f"{label}: неизвестный вид графики по ответу заказчика")
            if derived_kind == "fire_lift_circle":
                if planned_only:
                    require(derived.get("planned_layer") == "экспертиза лифты для пожарных"
                            and derived.get("planned_color_index") == 4 and derived.get("planned_type") == "Circle",
                            f"{label}: в Word не заданы голубая окружность и слой лифта для пожарных")
                for handle in derived_ids:
                    entity = verified.get(handle, {})
                    require(planned_only or (str(entity.get("type", entity.get("entity_type", ""))).casefold() in {"circle", "acdbcircle"}
                            and entity.get("layer") == "экспертиза лифты для пожарных"
                            and (entity.get("colorIndex") == 4 or (entity.get("colorIndex") == 256
                            and registry.get("graphics_layer_check", {}).get("fire_lift_layer_color") == 4))),
                            f"{label}: лифт для пожарных не обозначен голубой окружностью на требуемом слое")
            elif derived_kind in {"fire_post_label", "pump_station_label"}:
                require(derived.get("leader_planned" if planned_only else "leader_verified") is True,
                        f"{label}: подпись не связана с помещением выноской")
            else:
                require(derived.get("originating_remark_link") == main_id,
                        f"{label}: характеристика объекта не связана с исходным замечанием")
            owned.update(derived_ids)
            item_owned.update(derived_ids)
        require(planned_only or all_ids <= set(verified), f"{label}: состав группы не перечитан после оформления")
        state["physical_status_by_main_id"][main_id] = physical
        state["owned_by_main_id"][main_id] = set() if planned_only else item_owned
        state["layers_by_main_id"][main_id] = {} if planned_only else item_overrides

    for check in color_checks:
        if not isinstance(check, dict) or (check.get("entity_id"), check.get("role")) not in checked_roles:
            errors.append("Проверка цвета протокола не связана с текущим ответом или пояснением")
    for handle, layer in ([] if planned_only else expected.items()):
        entity = verified.get(handle, {})
        require(entity.get("layer") == layer, f"{handle}: не подтверждён протокольный слой {layer}")
        if handle not in layer_overrides:
            for key in ("colorIndex", "linetype", "lineWeight"):
                if key in before.get(handle, {}):
                    require(entity.get(key) == before[handle][key],
                            f"{handle}: изменены свойства исходного объекта без протокольного перехода")
            continue
        # An existing answer embedded in an unchanged source MText must keep
        # the source object's formatting. Author colours are checked above
        # against the actual inline spans, independently of these properties.
        baseline = before.get(handle, {})
        if (handle not in changed | created and baseline.get("layer") == layer
                and all(key in baseline and entity.get(key) == baseline[key]
                        for key in ("colorIndex", "linetype", "lineWeight"))):
            continue
        require(entity.get("linetype") == "ByLayer" and entity.get("lineWeight") == "ByLayer",
                f"{handle}: протокольный перенос должен сохранять тип и вес линии ByLayer")
        require(entity.get("colorIndex") in {256, *role_colors.get(handle, set())},
                f"{handle}: не подтверждён цвет ByLayer или допустимый авторский цвет текста")
    require(planned_only or owned <= set(verified), "Не все объекты протокола перечитаны из DWG")
    if not errors:
        state["owned_entity_ids"] = set() if planned_only else owned
        state["transition_layers"] = {} if planned_only else layer_overrides
        state["source_main_text_ids"] = set(main_ids)
    else:
        # Never let a malformed protocol row bypass the ordinary CAD checks.
        state["physical_status_by_main_id"] = {}
        state["owned_by_main_id"] = {}
        state["layers_by_main_id"] = {}
    return state


def validate_protocol_analysis_registry(registry: dict[str, Any], for_application: bool = False) -> dict[str, Any]:
    """Check substantive Word instructions without pretending CAD was changed."""
    strict = for_application or registry.get("word_report", {}).get("status") == "complete"
    state = protocol_validation_state(registry, strict=strict,
                                      standalone=registry.get("mode") == "protocol", planned_only=True)
    errors, warnings = state["errors"], state["warnings"]
    if strict:
        if registry.get("mode") == "protocol" and (registry.get("changed_entity_ids") or registry.get("created_entity_ids")):
            errors.append("Word-протокол не должен выполнять запланированные изменения DWG")
        if registry.get("final_numbering", {}).get("status") != "preserved":
            errors.append("Word-протокол должен сохранять исходную нумерацию")
    layer_check = registry.get("graphics_layer_check", {}).copy()
    layer_check["protocol_analysis_verified"] = not errors
    return {"ok": not errors, "errors": errors, "warnings": warnings, "graphics_layer_check": layer_check}


def validate_protocol_registry(registry: dict[str, Any], for_application: bool = False) -> dict[str, Any]:
    actions = registry.get("dwg_actions", {})
    complete = bool(actions.get("work_completed"))
    strict = for_application or complete
    state = protocol_validation_state(registry, strict=strict, standalone=True)
    errors, warnings = state["errors"], state["warnings"]
    changed = _ids(registry.get("changed_entity_ids", [])) | _ids(registry.get("created_entity_ids", []))
    layer_check = registry.get("graphics_layer_check", {}).copy()
    if strict:
        if not changed <= state["owned_entity_ids"]:
            errors.append("Не все изменения связаны с подтверждёнными решениями протокола")
        if registry.get("final_numbering", {}).get("status") != "preserved":
            errors.append("Протокол должен сохранять исходную нумерацию")
        for key in ("project_geometry_unchanged", "original_texts_and_numbers_unchanged",
                    "no_entities_deleted", "changes_re_read"):
            if layer_check.get(key) is not True:
                errors.append(f"Не подтверждена проверка протокола: {key}")
    if any(actions.get(key) for key in ("save_requested", "source_save_requested", "source_overwritten", "delete_requested")):
        errors.append("Сохранение исходного DWG или удаление объектов запрещено")
    if complete:
        source_path, result_path = actions.get("source_path", ""), actions.get("result_copy_path", "")
        if not source_path or not result_path or PureWindowsPath(source_path) == PureWindowsPath(result_path):
            errors.append("Исходный и итоговый DWG протокола должны иметь разные полные пути")
        for key in ("result_copy_created", "source_document_kept_unchanged", "final_fresh_read_verified"):
            if actions.get(key) is not True:
                errors.append(f"Не подтверждено сохранение протокола: {key}")
        if actions.get("source_document_contains_results") is not False:
            errors.append("Исходный документ не должен содержать результаты протокола")
        if actions.get("copy_did_not_change_open_source_identity") is not True:
            hashes = actions.get("source_preservation", {})
            initial, final = str(hashes.get("sha256_before_edits", "")), str(hashes.get("sha256_after_edits", ""))
            if len(initial) != 64 or initial.casefold() != final.casefold() or hashes.get("source_last_write_unchanged") is not True:
                errors.append("Нет независимого подтверждения сохранности исходника протокола")
    layer_check["protocol_objects_verified"] = not errors
    layer_check["checked_entity_ids"] = sorted(state["owned_entity_ids"])
    return {"ok": not errors, "errors": errors, "warnings": warnings, "graphics_layer_check": layer_check}
