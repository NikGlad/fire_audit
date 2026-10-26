"""Narrow evidence contracts for explicitly approved instruction exceptions."""
from __future__ import annotations

from typing import Any


APPROVED_RULE_TEXT_IDS = {
    "n2_pressurization": "doc_11_024",
    "n2_smoke_compensation": "doc_11_025",
    "n2_fire_doors": "doc_11_026",
}


def approved_rule_text_errors(trace: dict[str, Any], eligible_sources: list[dict] | None = None) -> list[str]:
    proof = trace.get("approved_rule_text", {})
    if not isinstance(proof, dict):
        return ["Не заполнено доказательство approved_rule_text"]
    errors = []
    key = proof.get("exception_key")
    if key not in {*APPROVED_RULE_TEXT_IDS, "sp1_4_2_21"}:
        errors.append("approved_rule_text допустим только для п. 4.2.21 и трёх утверждённых требований Н2")
    if key in APPROVED_RULE_TEXT_IDS and proof.get("rule_id") != APPROVED_RULE_TEXT_IDS[key]:
        errors.append("Не указан точный ID утверждённого текста Н2")
    for field in ("rule_id", "full_text", "source_reference", "source_approval_reference"):
        if not isinstance(proof.get(field), str) or not proof[field].strip():
            errors.append(f"approved_rule_text: отсутствует {field}")
    eligible = [row for row in (eligible_sources or []) if isinstance(row, dict)
                and row.get("exception_key") == key and row.get("rule_id") == proof.get("rule_id")]
    if len(eligible) != 1 or any(proof.get(field) != eligible[0].get(field) or not proof.get(field)
                                for field in ("full_text", "source_reference", "source_sha256")):
        errors.append("Утверждённый текст и его источник не совпадают с допустимыми источниками снимка запуска")
    if proof.get("verified_against_source") is not True or proof.get("format_verified") is not True:
        errors.append("Не подтверждены полный утверждённый текст и образец оформления")
    ids = proof.get("format_sample_entity_ids", [])
    if not isinstance(ids, list) or len({x for x in ids if isinstance(x, str) and x}) < 3:
        errors.append("Не указан полный образец оформления текста, рамки и номера")
    # This is permission to use a separately documented approved text, not to
    # invent CAD source handles or claim that a missing source block was copied.
    if trace.get("source_group_fully_detected") is True or trace.get("source_entity_ids"):
        errors.append("approved_rule_text не должен имитировать копирование отсутствующего исходного блока")
    return errors


def cycle_marker_state(registry: dict[str, Any], *, strict: bool, planned_only: bool = False) -> tuple[dict[str, dict], list[str]]:
    """ACI 2 is permitted only for identified group/cycle novelty clouds."""
    errors = []
    rows = registry.get("new_finding_cycle_markers", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return {}, ["Неверный реестр облаков новизны"]
    verified = {r.get("handle"): r for r in registry.get("verified_graphics", []) if isinstance(r, dict)}
    before = {r.get("handle"): r for r in registry.get("graphics_before", []) if isinstance(r, dict)}
    created = set(registry.get("created_entity_ids", []))
    deleted = set(registry.get("deleted_entity_ids", []))
    markers = {}
    for row in rows:
        handle, main = row.get("entity_id"), row.get("main_text_id")
        if not handle or handle in markers or row.get("role") != "new_finding_cycle_marker" or not row.get("cycle_id") or not main:
            errors.append("Облако новизны должно иметь уникальный ID, группу, роль и цикл")
            continue
        markers[handle] = row
        if not strict:
            continue
        entity, group = verified.get(handle, {}), verified.get(main, {})
        if planned_only:
            if row.get("planned_only") is not True or row.get("planned_color_index") != 2 or not row.get("placement_reference"):
                errors.append(f"{handle}: не задано планируемое облако новизны ACI 2")
            continue
        if (entity.get("layer") != group.get("layer") or not group.get("layer")
                or entity.get("layer") == "экспертиза изменения" or entity.get("colorIndex") != 2
                or entity.get("linetype") != "ByLayer" or entity.get("lineWeight") != "ByLayer"
                or row.get("encloses_complete_group_verified") is not True):
            errors.append(f"{handle}: облако новизны должно охватывать группу в её слое, с ACI 2 и типом/весом ByLayer")
        if handle in deleted or (handle in before and handle in created):
            errors.append(f"{handle}: прежнее облако нельзя удалять или объявлять вновь созданным")
        if row["cycle_id"] != registry.get("cycle_id") and handle not in before:
            errors.append(f"{handle}: прежний маркер не подтверждён исходной графикой")
        if handle in created and row["cycle_id"] != registry.get("cycle_id"):
            errors.append(f"{handle}: новое облако должно относиться к текущему циклу")
        if handle in created and main not in created:
            errors.append(f"{handle}: облако новой выдачи нельзя создавать для прежнего основного текста")
    return markers, errors
