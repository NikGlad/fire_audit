"""Opt-in evidence for annotation edits that do not resolve a requirement.

Legacy registries keep the original validation path. This contract does not
authorize changing project geometry, source requirements, or resolution layers.
"""
from __future__ import annotations

from collections import Counter


def graphic_operation_state(registry):
    state = {"errors": [], "neutral": set(), "comments": set(), "owned": set(),
             "reused": set(), "new_main": set(), "conditional": set(), "corrected": set(), "active": set()}
    contract = registry.get("graphic_operations")
    if contract is None:
        return state
    if not isinstance(contract, dict) or contract.get("version") != 1:
        state["errors"].append("Неизвестный контракт графических операций")
        return state
    before = {e.get("handle"): e for e in registry.get("graphics_before", [])}
    after = {e.get("handle"): e for e in registry.get("verified_graphics", [])}
    changed = set(registry.get("changed_entity_ids", []))
    created = set(registry.get("created_entity_ids", []))
    findings = registry.get("findings", [])
    source_texts = {f.get("source", {}).get(k) for f in findings for k in ("main_text_id", "number_id")}
    rows = contract.get("items", [])
    if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
        state["errors"].append("Неверный список графических операций")
        return state
    counts = Counter(x.get("entity_id") for x in rows)
    for row in rows:
        h, kind = row.get("entity_id"), row.get("kind")
        a, b = after.get(h, {}), before.get(h, {})
        valid = bool(h and counts[h] == 1 and h in changed | created and a
                     and row.get("rule_reference") and row.get("evidence_reference")
                     and row.get("reason") and row.get("second_review_confirmed") is True)
        if kind in {"layout", "measurement", "revision_marker"}:
            valid &= bool(h in changed and b and b.get("type") == a.get("type")
                          and str(b.get("layer", "")).startswith("экспертиза"))
            if kind == "layout":
                # Moving, stretching a ray or normalizing style does not change
                # its status. Text, dimensions and layer must be preserved.
                valid &= all(b.get(k) == a.get(k) for k in
                             ("layer", "contents", "textString", "dimensionText", "measurement"))
            elif kind == "revision_marker":
                valid &= (a.get("layer") == b.get("layer") == "экспертиза изменения"
                          and all(a.get(k) == b.get(k) for k in ("contents", "textString")))
            else:
                proof = row.get("measurement_evidence", {})
                valid &= bool(h not in source_texts and
                              ("Dimension" in a.get("type", "") or a.get("type") == "MText")
                              and a.get("layer") in {b.get("layer"), "экспертиза", "экспертиза обмеры"}
                              and proof.get("project_entity_ids") and proof.get("method") and proof.get("result"))
                if row.get("reused_for_added_target") is True:
                    valid &= "Dimension" in a.get("type", "")
            valid &= a.get("linetype") == a.get("lineWeight") == "ByLayer"
            if valid:
                state["neutral"].add(h)
                if row.get("reused_for_added_target") is True:
                    state["reused"].add(h)
        elif kind == "target_measurement":
            owners = [t for f in findings for t in f.get("targets", []) if h in t.get("graphic_ids", [])]
            valid &= bool(h in created and "Dimension" in a.get("type", "")
                          and a.get("layer") == "экспертиза" and a.get("colorIndex") == 256
                          and a.get("linetype") == a.get("lineWeight") == "ByLayer"
                          and owners and all(t.get("status") == "unresolved" and
                            all(t.get("evidence", {}).get(k) for k in ("project_entity_ids", "method", "result", "normative_basis"))
                            for t in owners))
            if valid:
                state["active"].add(h)
        elif kind == "author_comment":
            text = a.get("contents", "")
            role = row.get("author_role")
            phrase = "пояснения пожарного консультанта:" if role == "consultant" else "ответ заказчика:"
            valid &= bool(h in created and h not in source_texts and a.get("type") == "MText"
                          and a.get("layer") == "экспертиза" and role in {"consultant", "client"}
                          and a.get("colorIndex") == (30 if role == "consultant" else 4)
                          and phrase in text and text == row.get("text")
                          and a.get("linetype") == a.get("lineWeight") == "ByLayer")
            if valid:
                state["comments"].add(h)
        else:
            valid = False
        if valid:
            state["owned"].add(h)
        else:
            state["errors"].append(f"{h}: не подтверждена графическая операция {kind}")
    if contract.get("new_main_text_rule") == "doc_05_019":
        for f in findings:
            h = f.get("source", {}).get("main_text_id")
            a = after.get(h, {})
            if (f.get("origin") == "new_group" and h in created and a.get("type") == "MText"
                    and a.get("contents") == f.get("source", {}).get("full_text")):
                state["new_main"].add(h)
    for f in findings:
        for t in f.get("targets", []):
            correction = t.get("review_correction", {})
            if isinstance(correction, dict):
                measured, required = correction.get("measured_mm"), correction.get("required_mm")
                if (t.get("origin") == "added_target" and t.get("status") == "resolved"
                        and correction.get("rule_reference") == "error_ro_06"
                        and correction.get("previous_registry_reference")
                        and correction.get("previous_status") == "unresolved" and correction.get("method")
                        and type(measured) in (int, float) and type(required) in (int, float)
                        and measured >= required > 0):
                    state["corrected"].add((f.get("source", {}).get("main_text_id"), t.get("ray_id")))
            proof = t.get("general_requirement", {})
            if (t.get("status") == "insufficient_data" and t.get("origin") == "added_target"
                    and proof.get("rule_reference") == "doc_03_029"
                    and proof.get("applicable_object_fact") and proof.get("missing_design_detail")
                    and proof.get("not_claimed_as_measured_violation") is True
                    and proof.get("source_reference")):
                state["conditional"].add((f.get("source", {}).get("main_text_id"), t.get("ray_id")))
    return state
