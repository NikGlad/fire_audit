"""Project-scoped, evidence-linked continuity between independent audit runs."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .io_utils import read_json, utc_now, write_json
from .barrier_remarks_service import stored_errors as source_remarks_errors


HISTORY_NAME = "ИСТОРИЯ_ОЦЕНОК.json"
PRIOR_SNAPSHOT_NAME = "ПРЕДЫДУЩИЕ_ПРОВЕРКИ.json"
PLAN_CHANGE_STATUSES = {"initial", "same", "new", "unconfirmed"}
CONTEXT_KINDS = {"architecture", "normative_decision", "finding", "plan_change", "impact", "question"}


def work_kind(mode: str, task_variant: str) -> str:
    """Norm collection is its own work kind even though it reuses compliance rules."""
    return "object_norms" if task_variant == "object_norms" else mode


def file_stamp(path_text: str) -> dict[str, Any]:
    """Cheap change hint, deliberately not proof that CAD geometry is identical."""
    path_text = str(path_text or "").strip()
    result: dict[str, Any] = {"path": path_text, "exists": False, "size_bytes": None, "modified_ns": None}
    if not path_text:
        return result
    try:
        path = Path(path_text)
        if path.is_file():
            stat = path.stat()
            result.update({"exists": True, "size_bytes": stat.st_size, "modified_ns": stat.st_mtime_ns})
    except (OSError, ValueError):
        pass
    return result


def _finding_summary(finding: dict[str, Any]) -> dict[str, Any]:
    decision = finding.get("decision", {})
    norm = finding.get("norm", {})
    applicability = finding.get("applicability", {})
    if not isinstance(decision, dict):
        decision = {}
    if not isinstance(norm, dict):
        norm = {}
    if not isinstance(applicability, dict):
        applicability = {}
    return {
        "id": str(finding.get("id", "")),
        "decision": str(decision.get("status", "")),
        "floor": str(applicability.get("floor", "")),
        "norm_document": str(norm.get("document", "")),
        "norm_point": str(norm.get("point", "")),
    }


def project_history(project_dir: Path) -> dict[str, Any]:
    """Rebuild from immutable run folders, including legacy runs without continuity data."""
    entries: list[dict[str, Any]] = []
    for run_file in (project_dir / "runs").glob("*/run.json"):
        try:
            run = read_json(run_file, {})
            registry = read_json(run_file.parent / "РЕЕСТР_ПРОВЕРКИ.json", {})
        except (OSError, ValueError):
            continue
        if not isinstance(run, dict) or run.get("project_slug") != project_dir.name:
            continue
        if not isinstance(registry, dict):
            registry = {}
        findings = registry.get("findings", [])
        if not isinstance(findings, list):
            findings = []
        context = registry.get("continuity", {})
        if not isinstance(context, dict):
            context = {}
        items = context.get("items", [])
        if not isinstance(items, list):
            items = []
        actions = registry.get("dwg_actions", {})
        report = registry.get("word_report", {})
        if not isinstance(actions, dict):
            actions = {}
        if not isinstance(report, dict):
            report = {}
        snapshot = run.get("project_snapshot", {})
        dwg = snapshot.get("dwg", {}) if isinstance(snapshot, dict) else {}
        plan = run.get("plan_revision", {})
        remark_errors = source_remarks_errors(run, registry, run_file.parent)
        entries.append({
            "run_id": run.get("run_id", run_file.parent.name),
            "created_at": run.get("created_at", ""),
            "mode": run.get("mode", ""),
            "task_variant": run.get("task_variant", "full_execution"),
            "work_kind": work_kind(run.get("mode", ""), run.get("task_variant", "full_execution")),
            "status": run.get("status", ""),
            "knowledge_digest": run.get("knowledge_digest", ""),
            "gpzu": snapshot.get("dates", {}).get("gpzu", "") if isinstance(snapshot, dict) else "",
            "source_dwg": dwg.get("path", "") if isinstance(dwg, dict) else "",
            "source_file_stamp": run.get("source_file_stamp", {}),
            "plan_revision": plan if isinstance(plan, dict) else {},
            "result_dwg": actions.get("result_copy_path", ""),
            "word_report": report.get("path", ""),
            "result_complete": bool(actions.get("work_completed") or report.get("status") == "complete") and not remark_errors,
            "source_remarks_complete": not remark_errors if run.get("mode") == "barrier_assessment" else None,
            "source_remarks_errors": remark_errors,
            "registry_file": str(run_file.parent / "РЕЕСТР_ПРОВЕРКИ.json"),
            "run_file": str(run_file),
            "task_file": str(run_file.parent / "ЗАДАНИЕ_ДЛЯ_ЧАТА.md"),
            "findings_count": len(findings),
            "finding_summaries": [_finding_summary(item) for item in findings if isinstance(item, dict)],
            "continuity_items": [item for item in items if isinstance(item, dict)],
        })
    entries.sort(key=lambda item: (item["created_at"], item["run_id"]), reverse=True)
    by_work_kind: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_work_kind.setdefault(entry["work_kind"], []).append(entry)
    for group in by_work_kind.values():
        def order_key(entry: dict[str, Any]) -> tuple[str, int]:
            # Older runs have second-precision timestamps. Their numeric folder
            # suffix preserves order when 10+ runs were made in one second.
            match = re.search(rf"-{re.escape(str(entry['mode']))}(?:-(\d+))?$", str(entry["run_id"]))
            ordinal = int(match.group(1)) if match and match.group(1) else 1
            return str(entry["created_at"]), ordinal

        group.sort(key=order_key, reverse=True)
        for number, entry in enumerate(reversed(group), start=1):
            entry["work_iteration"] = number
    # Newest first within each work kind; the flat list remains for legacy readers.
    return {"schema_version": 2, "project_slug": project_dir.name, "runs": entries,
            "by_work_kind": by_work_kind}


def comparison_baseline(history: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """The immediate preceding iteration of this work kind, completed or not."""
    group = history.get("by_work_kind", {}).get(kind)
    if isinstance(group, list):
        return group[0] if group else None
    return next((entry for entry in history.get("runs", []) if entry.get("work_kind") == kind), None)


def passport_changes(previous: dict[str, Any], current: dict[str, Any], *, limit: int = 200) -> dict[str, Any]:
    """List changed passport fields; a change list is not a CAD or normative conclusion."""
    changes: list[dict[str, str]] = []
    total = 0

    def preview(value: Any) -> str:
        value_text = str(value)
        return value_text[:300] + ("…" if len(value_text) > 300 else "")

    def visit(left: Any, right: Any, prefix: str) -> None:
        nonlocal total
        if prefix == "metadata" or prefix.startswith("metadata."):
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                visit(left.get(key), right.get(key), f"{prefix}.{key}" if prefix else key)
        elif left != right:
            total += 1
            if len(changes) < limit:
                changes.append({"field": prefix, "previous": preview(left), "current": preview(right)})

    visit(previous, current, "")
    return {"count": total, "items": changes, "truncated": total > limit}


def write_project_history(project_dir: Path) -> dict[str, Any]:
    history = project_history(project_dir)
    history["updated_at"] = utc_now()
    write_json(project_dir / HISTORY_NAME, history)
    return history
