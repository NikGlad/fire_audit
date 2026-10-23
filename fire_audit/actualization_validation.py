"""Validate preserved remarks and evidenced additions in unified actualization."""
from __future__ import annotations

from collections import Counter
from pathlib import PureWindowsPath
from typing import Any

from .protocol_validation import protocol_validation_state


RESOLVED_LAYER = "экспертиза устранено"
RESOLVED_SOURCE_LAYERS = {RESOLVED_LAYER, "экспертиза устранено протокол"}
ACTIVE_LAYER = "экспертиза"
BLUE_STU_LAYERS = {"экспертиза СТУ", "экспертиза СТУ протокол"}
GROUP_STATUSES = {"resolved", "partially_resolved", "unresolved", "insufficient_data",
                  "not_reviewed", "excluded_fire_safety_frame", "information_request"}
TARGET_STATUSES = {"resolved", "unresolved", "insufficient_data", "not_reviewed"}


def validate_actualization_registry(registry: dict[str, Any], for_application: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    actions = registry.get("dwg_actions", {})
    complete = bool(actions.get("work_completed"))
    strict = for_application or complete
    protocol_state = protocol_validation_state(registry, strict=strict)
    errors.extend(protocol_state["errors"])
    warnings.extend(protocol_state["warnings"])
    protocol_all_owned = protocol_state["owned_entity_ids"]
    protocol_physical = protocol_state["physical_status_by_main_id"]
    findings = registry.get("findings", [])
    scope = registry.get("actualization_scope", {})
    additions = [item for item in findings if item.get("origin") == "new_group"]
    existing = [item for item in findings if item.get("origin") != "new_group"]
    ids = [item.get("source", {}).get("main_text_id") for item in findings]
    numbers = [item.get("original_number") for item in findings]
    if strict:
        if not findings or Counter(item.get("source", {}).get("main_text_id") for item in existing) != Counter(scope.get("issued_main_text_ids", [])):
            errors.append("Не подтверждён полный состав исходных замечаний актуализации")
        existing_numbers = [item.get("original_number") for item in existing]
        new_numbers = [item.get("original_number") for item in additions]
        if (len(ids) != len(set(ids)) or None in ids or None in numbers
                or len(new_numbers) != len(set(new_numbers))
                or set(new_numbers) & set(existing_numbers)):
            errors.append("Повторяются или отсутствуют идентификаторы / новые номера замечаний")
        repeated_source_numbers = [number for number, count in Counter(existing_numbers).items() if count > 1]
        if repeated_source_numbers:
            warnings.append("Сохранены повторяющиеся исходные номера замечаний: "
                            + ", ".join(map(str, repeated_source_numbers)))
        if registry.get("final_numbering", {}).get("status") != "preserved":
            errors.append("Актуализация должна сохранять исходную нумерацию")
        if additions:
            old_numeric_numbers = [n for n in existing_numbers if isinstance(n, int) and not isinstance(n, bool)]
            highest_old_number = max(old_numeric_numbers, default=0)
            if (not all(isinstance(n, int) and not isinstance(n, bool) for n in new_numbers)
                    or sorted(new_numbers) != list(range(highest_old_number + 1,
                                                        highest_old_number + len(additions) + 1))):
                errors.append("Новые номера должны последовательно продолжать сохранённую нумерацию")
        if scope.get("unified_review_required"):
            coverage = registry.get("new_plan_review", {})
            rows = coverage.get("items", [])
            required = scope.get("required_review_keys", [])
            allowed = {"resolved", "unresolved", "insufficient_data", "already_covered",
                       "added_target", "new_group", "not_applicable"}
            if (not required or Counter(row.get("key") for row in rows) != Counter(required)
                    or any(row.get("status") not in allowed or not row.get("reason")
                           or row.get("second_review_confirmed") is not True for row in rows)):
                errors.append("Не подтверждён полный индивидуальный охват новых планов и повторная проверка")

    verified = {item.get("handle"): item for item in registry.get("verified_graphics", [])}
    before = {item.get("handle"): item for item in registry.get("graphics_before", [])}
    changed = set(registry.get("changed_entity_ids", []))
    created = set(registry.get("created_entity_ids", []))
    expected_green: set[str] = set()
    expected_returned: set[str] = set()
    expected_active: set[str] = set()
    expected_explanations: set[str] = set()
    expected_blue: dict[str, str] = {}
    owned_changes: set[str] = protocol_all_owned & (changed | created)
    added_keys: set[tuple[str, ...]] = set()
    for finding in findings:
        number = finding.get("original_number", "?")
        label = f"Замечание №{number}"
        status = finding.get("status")
        source = finding.get("source", {})
        protocol_owned = protocol_state["owned_by_main_id"].get(source.get("main_text_id"), set())
        protocol_layers = protocol_state["layers_by_main_id"].get(source.get("main_text_id"), {})
        protocol_status = protocol_physical.get(source.get("main_text_id"))
        if (protocol_status is not None and status in {"resolved", "partially_resolved", "unresolved", "insufficient_data"}
                and protocol_status != status):
            errors.append(f"{label}: фактический статус в протоколе не совпадает с проверкой планов")
        group_ids = {source.get(key) for key in ("main_text_id", "number_id", "frame_id")}
        group_ids.discard(None)
        source_layer = (source.get("original_layer") or before.get(source.get("main_text_id"), {}).get("layer")
                        or verified.get(source.get("main_text_id"), {}).get("layer"))
        blue_group = source_layer in BLUE_STU_LAYERS
        if blue_group:
            for handle in group_ids | set(source.get("ray_ids", [])) | set(source.get("related_graphic_ids", [])):
                if handle and handle not in protocol_layers:
                    expected_blue[handle] = source_layer
        targets = finding.get("targets", [])
        new_group = finding.get("origin") == "new_group"
        added_targets = [t for t in targets if t.get("origin") == "added_target"]
        returned = set(finding.get("returned_entity_ids", []))
        reopened = set(finding.get("reopened_for_added_targets_ids", []))
        active_additions: set[str] = set()
        explanation_ids: set[str] = set()
        if status not in GROUP_STATUSES:
            errors.append(f"{label}: неизвестный статус")
        if strict and (len(group_ids) != 3 or not source.get("full_text") or not finding.get("reason")):
            errors.append(f"{label}: не заполнены исходная группа, текст или основание решения")
        if strict and status == "not_reviewed":
            errors.append(f"{label}: не проверено")
        if status == "excluded_fire_safety_frame":
            if (targets or (set(finding.get("changed_entity_ids", [])) | set(finding.get("created_entity_ids", []))
                            | (group_ids & changed)) - protocol_owned or returned or reopened or new_group):
                errors.append(f"{label}: исключённая группа не должна перепроверяться или изменяться")
            if protocol_status is not None and protocol_status != "not_rechecked":
                errors.append(f"{label}: протокол не должен выдавать исключённую рамку за перепроверенную")
            continue
        if status == "information_request":
            if ((set(finding.get("changed_entity_ids", [])) | set(finding.get("created_entity_ids", []))
                 | (group_ids & changed)) - protocol_owned or returned or reopened or new_group or added_targets):
                errors.append(f"{label}: информационный запрос нельзя закрывать зелёным как нарушение")
            continue
        if strict and not targets:
            errors.append(f"{label}: не зарегистрированы проверяемые цели")
        if strict and Counter(source.get("ray_ids", [])) != Counter(
                target.get("ray_id") for target in targets if target.get("ray_id") and target.get("origin") != "added_target"):
            errors.append(f"{label}: не охвачены все исходные лучи")
        if new_group:
            trace = finding.get("template_copy", {})
            for key in ("source_group_fully_detected", "left_explanation_checked", "left_explanation_copied_when_present",
                        "text_styles_preserved", "relative_positions_preserved", "repeated_floor_group_copied_from_actualized_group"):
                if trace.get(key) is not True:
                    errors.append(f"{label}: не подтверждено копирование шаблона: {key}")
            if (trace.get("source_kind") not in {"source_approved_block", "previous_actualized_compliance_group"}
                    or len(set(trace.get("source_entity_ids", []))) < 3 or not group_ids <= created
                    or len(added_targets) != len(targets) or source.get("ray_ids")):
                errors.append(f"{label}: новая группа должна быть полной копией с новыми целями")
            active_additions.update(group_ids)
        if added_targets:
            comment = finding.get("addition_comment", {})
            explanation_ids = set(comment.get("entity_ids", []))
            if (not blue_group and (not explanation_ids or not comment.get("text") or comment.get("mentions_new_places") is not True
                    or comment.get("history_preserved") is not True)):
                errors.append(f"{label}: нет пояснения консультанта о новых местах с сохранением истории")
            if not explanation_ids <= changed | created:
                errors.append(f"{label}: пояснение о новых местах не зарегистрировано в журнале")
        if reopened:
            if not added_targets or new_group or not reopened <= group_ids or reopened & returned:
                errors.append(f"{label}: неверно зарегистрировано открытие заголовка из-за новых целей")
            expected_returned.update(reopened)
        states = [target.get("status") for target in targets]
        if any(state not in TARGET_STATUSES for state in states):
            errors.append(f"{label}: неизвестный статус цели")
        if strict and "not_reviewed" in states:
            errors.append(f"{label}: остались непроверенные цели")
        all_resolved = bool(states) and all(state == "resolved" for state in states)
        some_resolved = "resolved" in states
        if (status == "resolved") != all_resolved:
            errors.append(f"{label}: вся группа закрывается только после всех целей")
        if (status == "partially_resolved") != (some_resolved and not all_resolved):
            errors.append(f"{label}: неверно отражено частичное устранение")
        returnable = group_ids | set(source.get("related_graphic_ids", []))
        shared_returned = set(source.get("shared_graphic_ids", [])) & returned
        for handle in shared_returned:
            owners = [t for t in targets if handle in t.get("graphic_ids", [])]
            if (handle in group_ids or handle in source.get("ray_ids", []) or len(owners) < 2
                    or not any(t.get("status") == "unresolved" for t in owners)):
                errors.append(f"{label}: не подтверждена общая графика возвращаемой цели")
        if returned:
            evidence = finding.get("return_evidence", {})
            if status not in {"unresolved", "partially_resolved"} or "unresolved" not in states:
                errors.append(f"{label}: возврат требует фактически неустранённой цели")
            if any(not evidence.get(key) for key in ("project_entity_ids", "method", "result", "normative_basis")):
                errors.append(f"{label}: нет проектного и нормативного обоснования возврата")
        for target in targets:
            target_ids = set(target.get("graphic_ids", []))
            ray = target.get("ray_id")
            if ray:
                target_ids.add(ray)
            if blue_group:
                for handle in target_ids:
                    if before.get(handle, {}).get("layer") in BLUE_STU_LAYERS and handle not in protocol_layers:
                        expected_blue[handle] = source_layer
            if target.get("origin") == "added_target":
                proof = target.get("evidence", {})
                dedup = target.get("deduplication", {})
                key = tuple(str(dedup.get(k, "")).strip().casefold() for k in ("floor", "project_target", "parameter", "requirement"))
                if (not ray or (not blue_group and not target.get("graphic_ids")) or not target_ids <= created
                        or target.get("status") != "unresolved" or target.get("returned_entity_ids")
                        or any(not proof.get(k) for k in ("project_entity_ids", "method", "result", "normative_basis"))):
                    errors.append(f"{label}: новая цель не подтверждена обмером, нормой или новой графикой")
                if (not all(key) or key in added_keys or dedup.get("same_target_already_covered") is not False
                        or Counter(dedup.get("checked_main_text_ids", [])) != Counter(scope.get("issued_main_text_ids", []))):
                    errors.append(f"{label}: не доказано отсутствие дубля новой цели")
                added_keys.add(key)
                if not finding.get("floor") or str(target.get("floor")) != str(finding.get("floor")) or str(dedup.get("floor")) != str(finding.get("floor")):
                    errors.append(f"{label}: новая цель и группа должны относиться к одному этажу")
                if blue_group:
                    expected_blue.update({handle: source_layer for handle in target_ids if handle not in protocol_layers})
                else:
                    active_additions.update(target_ids)
            if strict and not target.get("reason"):
                errors.append(f"{label}: у цели отсутствует конкретное основание")
            target_returned = set(target.get("returned_entity_ids", []))
            if not target_returned <= target_ids & returned:
                errors.append(f"{label}: возврат цели не совпадает с её графикой и журналом группы")
            if target.get("status") == "unresolved":
                returnable.update(target_ids)
                if target_ids & returned != target_returned:
                    errors.append(f"{label}: возврат графики не зарегистрирован у неустранённой цели")
                if strict and any(verified.get(h, {}).get("layer") in RESOLVED_SOURCE_LAYERS
                                  for h in target_ids - set(protocol_layers)):
                    errors.append(f"{label}: неустранённая цель осталась в слое устранения")
            elif target_returned:
                errors.append(f"{label}: нельзя возвращать цель без установленного неустранения")
            if target.get("status") == "resolved":
                evidence = target.get("evidence", {})
                if not evidence.get("project_entity_ids") or not evidence.get("method") or not evidence.get("result"):
                    errors.append(f"{label}: устранение не подтверждено проектными объектами и проверкой")
                if not blue_group:
                    expected_green.update(target_ids - shared_returned - set(protocol_layers))
            elif (target_ids & changed) - target_returned:
                # Restoring an old STU ray from an erroneously green layer to its
                # original blue group is a graphics correction, not a resolution
                # of the target. Keep the target's factual status unchanged.
                corrected_blue = {handle for handle in target_ids & changed
                                  if blue_group and verified.get(handle, {}).get("layer") == source_layer
                                  and before.get(handle, {}).get("layer") in RESOLVED_SOURCE_LAYERS}
                if ((target_ids & changed) - target_returned) - corrected_blue - protocol_owned:
                    errors.append(f"{label}: изменена графика неподтверждённой цели")
        finding_changes = set(finding.get("changed_entity_ids", []))
        finding_created = set(finding.get("created_entity_ids", []))
        owned_changes.update(finding_changes | finding_created)
        expected_active.update(active_additions - protocol_owned)
        expected_explanations.update(explanation_ids - protocol_owned)
        expected_green.update((finding_changes | finding_created) - returned - reopened - active_additions - explanation_ids
                              - set(expected_blue) - protocol_owned)
        expected_returned.update(returned)
        if not reopened <= finding_changes or not active_additions <= finding_created or not explanation_ids <= finding_changes | finding_created:
            errors.append(f"{label}: дополнения не совпадают с журналом группы")
        if not returned <= finding_changes & returnable:
            errors.append(f"{label}: возвращённые объекты не относятся к изменённой графике группы")
        if status == "resolved" and not blue_group:
            expected_green.update(group_ids - set(protocol_layers))
        elif not blue_group and (group_ids & changed) - returned - reopened - protocol_owned:
            errors.append(f"{label}: незакрытая группа целиком переведена в устранённые")
        elif (not blue_group and strict and status != "resolved"
              and not (source_layer in RESOLVED_SOURCE_LAYERS and not group_ids & changed)
              and any(verified.get(handle, {}).get("layer") in {None, *RESOLVED_SOURCE_LAYERS}
                      for handle in group_ids - set(protocol_layers))):
            errors.append(f"{label}: нет подтверждения действующего слоя незакрытой группы")
        if not finding_changes <= changed or not finding_created <= created:
            errors.append(f"{label}: изменения не совпадают с общим журналом")

    if strict and owned_changes != changed | created:
        errors.append("Не все изменённые объекты привязаны к проверенным замечаниям")
    if expected_green & (expected_active | expected_returned | expected_explanations) or expected_explanations & (expected_active | expected_returned):
        errors.append("Один объект получил несовместимые роли или слои")
    for handles, layer in ((expected_active, ACTIVE_LAYER), (expected_explanations, "экспертиза пояснения")):
        for handle in handles:
            entity = verified.get(handle, {})
            if (entity.get("layer") != layer or entity.get("colorIndex") != 256
                    or entity.get("linetype") != "ByLayer" or entity.get("lineWeight") != "ByLayer"):
                errors.append(f"{handle}: не подтверждён слой дополнения {layer} и ByLayer")
    for handle in expected_returned:
        entity = verified.get(handle, {})
        if before.get(handle, {}).get("layer") not in RESOLVED_SOURCE_LAYERS:
            errors.append(f"{handle}: не подтверждён исходный слой устранения перед возвратом")
        if (entity.get("layer") != ACTIVE_LAYER or entity.get("colorIndex") != 256
                or entity.get("linetype") != "ByLayer" or entity.get("lineWeight") != "ByLayer"):
            errors.append(f"{handle}: не подтверждён возврат в экспертиза со свойствами ByLayer")
    for handle in expected_green:
        entity = verified.get(handle, {})
        if (entity.get("layer") != RESOLVED_LAYER or entity.get("colorIndex") != 256
                or entity.get("linetype") != "ByLayer" or entity.get("lineWeight") != "ByLayer"):
            errors.append(f"{handle}: нет повторного подтверждения зелёного слоя и свойств ByLayer")
    for handle, layer in expected_blue.items():
        entity = verified.get(handle, {})
        if entity.get("layer") != layer:
            errors.append(f"{handle}: группа СТУ или её луч вышли из исходного синего слоя {layer}")
        if handle in created and (entity.get("colorIndex") != 256 or entity.get("linetype") != "ByLayer"
                                  or entity.get("lineWeight") != "ByLayer"):
            errors.append(f"{handle}: новый луч СТУ должен иметь свойства ByLayer")
    layer_check = registry.get("graphics_layer_check", {}).copy()
    if strict:
        if layer_check.get("resolved_layer_color") != 3:
            errors.append("Слой устранённых нарушений должен иметь цвет ACI 3")
        for key in ("project_geometry_unchanged", "original_texts_and_numbers_unchanged",
                    "excluded_frame_unchanged", "no_entities_deleted", "changes_re_read"):
            if layer_check.get(key) is not True:
                errors.append(f"Не подтверждена проверка: {key}")
    if any(actions.get(key) for key in ("save_requested", "source_save_requested", "source_overwritten", "delete_requested")):
        errors.append("Сохранение исходного DWG или удаление объектов запрещено")
    if complete:
        source_path = actions.get("source_path", "")
        result_path = actions.get("result_copy_path", "")
        if not source_path or not result_path or PureWindowsPath(source_path) == PureWindowsPath(result_path):
            errors.append("Исходный и итоговый DWG должны иметь разные полные пути")
        for key in ("result_copy_created", "source_document_kept_unchanged", "final_fresh_read_verified"):
            if actions.get(key) is not True:
                errors.append(f"Не подтверждено сохранение: {key}")
        if actions.get("source_document_contains_results") is not False:
            errors.append("Исходный документ не должен содержать результаты актуализации")
        # SaveAs can change the active document name without writing the source file.
        # Accept it only with explicit independent evidence of source preservation.
        if actions.get("copy_did_not_change_open_source_identity") is not True:
            hashes = actions.get("source_preservation", {})
            baseline = str(hashes.get("sha256_before_edits", ""))
            final = str(hashes.get("sha256_after_edits", ""))
            if (len(baseline) != 64 or baseline.lower() != final.lower()
                    or hashes.get("source_last_write_unchanged") is not True):
                errors.append("Нет независимого подтверждения сохранности исходника при смене активного имени")
            else:
                warnings.append("Копия создана SaveAs: исходный файл сохранён, активное имя документа изменено")
    layer_check["checked_entity_ids"] = sorted(verified)
    layer_check["all_resolved_graphics_verified"] = not errors
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "graphics_layer_check": layer_check}
