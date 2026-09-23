from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fire_audit.core import FireAuditApp


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Подготовка задания на пожарную проверку")
    result.add_argument("--root", default=str(Path(__file__).resolve().parent))
    sub = result.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("projects")

    create_project = sub.add_parser("create-project")
    create_project.add_argument("name")
    create_project.add_argument("--slug", default="")

    validate_project = sub.add_parser("validate-project")
    validate_project.add_argument("slug")
    validate_project.add_argument("--ready", action="store_true")

    create_run = sub.add_parser("create-run")
    create_run.add_argument("slug")
    create_run.add_argument(
        "mode",
        choices=[
            "stage",
            "compliance",
            "article89",
            "actualization_protocol",
            "ar_assessment_transfer",
            "ar_plan_preparation",
            "ar_plan_comparison",
            "protocol",
            "barrier_assessment",
            "du_assessment",
        ],
    )
    create_run.add_argument(
        "--task-variant",
        choices=["full_execution", "analysis_word"],
        default="full_execution",
        help="Вид результата: итоговый DWG или аналитическое задание DOCX",
    )

    list_runs = sub.add_parser("runs")
    list_runs.add_argument("slug")

    validate_run = sub.add_parser("validate-run")
    validate_run.add_argument("slug")
    validate_run.add_argument("run_id")
    validate_run.add_argument("--for-application", action="store_true")

    search_knowledge = sub.add_parser("search-knowledge")
    search_knowledge.add_argument("query")
    search_knowledge.add_argument("--limit", type=int, default=20)

    sync_norms = sub.add_parser("sync-norms")
    sync_norms.add_argument("--index", action="store_true")
    sync_norms.add_argument("--full-text", action="store_true")
    sync_norms.set_defaults(full_text=True)

    index_norms = sub.add_parser("index-norms")
    index_norms.add_argument("--full-text", action="store_true")
    index_norms.add_argument("--full-text-letters", action="store_true")
    index_norms.set_defaults(full_text=True, full_text_letters=True)
    index_norms.add_argument("--without-letters", action="store_true")

    search_norms = sub.add_parser("search-norms")
    search_norms.add_argument("query")
    search_norms.add_argument("--limit", type=int, default=20)
    search_norms.add_argument("--folder", default="")
    return result


def main() -> int:
    args = parser().parse_args()
    app = FireAuditApp(args.root)
    try:
        if args.command == "status":
            value = app.status()
        elif args.command == "projects":
            value = app.projects.list_projects()
        elif args.command == "create-project":
            value = app.projects.create(args.name, args.slug or None)
        elif args.command == "validate-project":
            project = app.projects.load(args.slug)
            errors, warnings = app.projects.validate(project, require_ready=args.ready)
            value = {"ok": not errors, "errors": errors, "warnings": warnings}
        elif args.command == "create-run":
            value = app.runs.create(args.slug, args.mode, args.task_variant)
        elif args.command == "runs":
            value = app.runs.list_runs(args.slug)
        elif args.command == "validate-run":
            value = app.runs.validate(args.slug, args.run_id, args.for_application)
        elif args.command == "search-knowledge":
            value = app.knowledge.search(args.query, args.limit)
        elif args.command == "sync-norms":
            value = app.norms.sync_repository(rebuild_index=args.index, full_text=args.full_text)
            if "index" in value:
                value["index_result"] = value.pop("index")
        elif args.command == "index-norms":
            value = app.norms.rebuild(
                full_text=args.full_text,
                include_letters=not args.without_letters,
                full_text_letters=args.full_text_letters,
            )
        elif args.command == "search-norms":
            value = app.norms.search(args.query, args.limit, args.folder)
        else:
            raise AssertionError(args.command)
        print_json(value)
        return 0
    except Exception as exc:
        print_json({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
