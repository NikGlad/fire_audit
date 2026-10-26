"""A source-linked reading index, never an automatic certification of old conclusions."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


SUMMARY_NAME = "СВОД_ПРЕЕМСТВЕННОСТИ.json"


def summary_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_continuity_summary(history: dict[str, Any], kind: str, *, archive_path: str,
                             passport_changes: dict[str, Any], plan_revision: dict[str, Any]) -> dict[str, Any]:
    """Keep the latest record per stable key and every link to earlier evidence.

    Records in different work kinds and different categories never overwrite one
    another. A latest record is a candidate to verify, not an accepted fact.
    """
    by_kind = history.get("by_work_kind", {})
    own_history = by_kind.get(kind, [])
    candidates: dict[tuple[str, str, str], dict[str, Any]] = {}
    for work_kind, entries in by_kind.items():
        for entry in entries:
            for collection, records in (("continuity", entry.get("continuity_items", [])),
                                        ("finding", entry.get("finding_summaries", []))):
                for index, record in enumerate(records):
                    if not isinstance(record, dict):
                        continue
                    item_id = str(record.get("id") or f"unidentified-{entry['run_id']}-{index}")
                    category = str(record.get("kind", collection))
                    key = (work_kind, collection + ":" + category, item_id)
                    source = {"run_id": entry["run_id"], "registry_file": entry.get("registry_file", ""),
                              "collection": collection, "item_id": item_id, "source_reference": record.get("source", "")}
                    if key not in candidates:
                        candidates[key] = {"key": list(key), "work_kind": work_kind,
                            "verification_status": "verification_required", "record": copy.deepcopy(record),
                            "latest_source": source, "earlier_sources": []}
                    else:
                        candidates[key]["earlier_sources"].append(source)
    values = list(candidates.values())
    result = {
        "schema_version": 1, "policy_version": "2026-10-26.1", "work_kind": kind,
        "verification_status": "verification_required" if history.get("runs") else "no_prior_history",
        "warning": "Автоматический свод — указатель на источники. Ни актуальность фактов, ни нормативные выводы не подтверждены автоматически.",
        "nearest_iteration": copy.deepcopy(own_history[0]) if own_history else None,
        "full_archive": {"path": archive_path, "run_count": len(history.get("runs", [])),
                         "digest": summary_digest(history), "complete": True},
        "state_candidates": values,
        "question_candidate_keys": [row["key"] for row in values if row["record"].get("kind") == "question"],
        "impact_review": {"required": bool(history.get("runs")), "status": "not_recorded",
                          "passport_changes": copy.deepcopy(passport_changes), "plan_revision": copy.deepcopy(plan_revision),
                          "instruction": "Сопоставить изменения с кандидатами свода и затронутыми решениями всех видов; проверить источники адресно, сохранить матрицу влияния в новом реестре."},
        "verification_record_instruction": "Сверку свода и основания повторного использования записать через record_continuity с точной ссылкой на свод и проверенные источники; этот замороженный кандидат не переписывать.",
    }
    result["summary_digest"] = summary_digest(result)
    return result


def eligible_rule_text_sources(snapshot, mode: str) -> list[dict[str, Any]]:
    """Only approved full N2 texts from the exact captured rules revision."""
    if mode != "actualization_protocol":
        return []
    document = next((item for item in snapshot.documents if item.name == "rule_blocks.json"), None)
    if document is None:
        return []
    records = {row["id"]: row for row in json.loads(document.text)["rules"]}
    keys = {"n2_pressurization": "doc_11_024", "n2_smoke_compensation": "doc_11_025", "n2_fire_doors": "doc_11_026"}
    return [{"exception_key": exception, "rule_id": key, "full_text": records[key]["text"],
             "source_reference": "knowledge/rule_blocks.json#" + key, "source_sha256": document.sha256}
            for exception, key in keys.items() if key in records and mode in records[key]["modes"]]
