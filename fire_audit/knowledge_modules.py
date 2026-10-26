"""Conservative, explicit module selection; never infer scope from words or room data."""
from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from typing import Any

from .rule_registry import RuleResolver


POLICY_VERSION = "2026-10-26.1"
PENDING_MODES = frozenset({"ar_assessment_transfer", "ar_plan_preparation", "ar_plan_comparison", "du_assessment"})
CORE_DOCUMENTS = {
    "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md": ("core.safety", "Приоритеты и сохранность"),
    "22_ПРЯМАЯ_РАБОТА_ПОМОЩНИКА.md": ("core.execution", "Рабочая дисциплина и доказательства"),
    "29_ПРЕЕМСТВЕННОСТЬ_ПРОВЕРОК.md": ("core.continuity", "Преемственность и сохранение истории"),
}
# Reviewed exact IDs. These preserve user inputs, passport priority, the single
# result file, the registry, asynchronous answers and the prohibition on invention.
CORE_USER_RULE_IDS = frozenset(f"doc_25_{number:03d}" for number in (
    1, 2, 10, 11, 23, 24, 25, 26, 27, 29, 30, 34, 35, 36, 37,
    42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58,
    93, 94, 95, 96, 104, 106, 107, 108, 109, 110, 111, 112, 114, 115,
))
# Each of these texts expressly restricts itself to the barrier workflow.
# General fire requirements and object input/questionnaire rules are not here.
BARRIER_ONLY_RULE_IDS = frozenset({"doc_25_028", "doc_25_113", "doc_27_003", "doc_27_013", "doc_27_014"})


def rule_module(rule: dict[str, Any]) -> tuple[str, str, str]:
    rule_id, origin = rule["id"], rule.get("origin_document", "")
    if rule_id in BARRIER_ONLY_RULE_IDS:
        return "special.barrier_workflow", "Специальные действия оценки преград", "barrier_only"
    if rule_id in CORE_USER_RULE_IDS:
        return "core.user_inputs", "Общие условия пользователя и данные объекта", "core"
    if origin in CORE_DOCUMENTS:
        module_id, title = CORE_DOCUMENTS[origin]
        return module_id, title, "core"
    if origin == "rule_overrides.json":
        return "user.overrides", "Действующие прямые уточнения пользователя", "conditional"
    # Origin is an address, not a restriction of scope: a shared block first
    # written in a PP document may legitimately be used by another workflow.
    module_id = "source." + (origin.removesuffix(".md").removesuffix(".json") or "unclassified")
    return module_id, origin or "Положения без тематической классификации", "conditional"


def rule_scope(rule: dict[str, Any], mode: str | None) -> dict[str, Any]:
    module_id, title, kind = rule_module(rule)
    excluded = mode is not None and kind == "barrier_only" and mode != "barrier_assessment"
    return {"module_id": module_id, "module_title": title,
            "applicability_status": "not_applicable" if excluded else "required" if kind == "core" else "conditional",
            "applicability_reason": ("Текст прямо ограничен режимом оценки преград" if excluded else
                "Обязательное ядро" if kind == "core" else
                "Полный текст сохранён; применимость устанавливается по утверждённому алгоритму и данным объекта")}


class ModulePlan:
    def __init__(self, catalogue: dict[str, Any], sources: dict[str, str], mode: str,
                 *, pending: bool, algorithm_documents: list[str], override_ids: list[str]):
        self.mode, self.pending = mode, pending
        self.rules = {row["id"]: row for row in catalogue["rules"]}
        self.sources = sources
        self.forced = set(override_ids)
        roots: list[str] = []
        self.source_roots: dict[str, list[str]] = {}
        for name, text in sources.items():
            if not name.endswith(".md"):
                continue
            keys = [key for kind, key in RuleResolver.references(text) if kind == "rule"]
            self.source_roots[name] = keys
            roots.extend(keys)
        roots.extend(override_ids)
        self.roots = list(dict.fromkeys(roots))
        self.included: set[str] = set()
        self.reasons: dict[str, str] = {}
        algorithm_ids = {key for name in algorithm_documents for key in self.source_roots.get(name, [])}
        for key in self.roots:
            rule = self.rules[key]
            _, _, kind = rule_module(rule)
            if key in self.forced:
                reason = "Прямое действующее уточнение выбранного вида работы"
            elif kind == "barrier_only" and mode != "barrier_assessment":
                self.reasons[key] = "Текст прямо ограничен другим видом работы: оценкой преград"
                continue
            elif pending and kind != "core" and key not in algorithm_ids:
                self.reasons[key] = "Тематическая проверка отложена до утверждения алгоритма; выполнение режима заблокировано"
                continue
            else:
                reason = "Обязательное ядро" if kind == "core" else "Алгоритм выбранного вида" if key in algorithm_ids else "Условный модуль: применимость неизвестна, полный текст сохранён"
            self.included.add(key)
            self.reasons[key] = reason
        # Every actual include/requires dependency is retained in full. An
        # explicit dependency is stronger than a provisional module exclusion.
        pending_ids = list(self.included)
        while pending_ids:
            key = pending_ids.pop()
            row = self.rules[key]
            dependencies = list(row.get("requires", []))
            dependencies += [ref for kind, ref in RuleResolver.references(row["text"]) if kind == "rule"]
            for dependency in dependencies:
                if dependency not in self.included:
                    self.included.add(dependency)
                    self.reasons[dependency] = "Полная зависимость включённого правила " + key
                    pending_ids.append(dependency)
        self.modules = self._manifest()

    def _manifest(self) -> tuple[dict[str, Any], ...]:
        groups: dict[str, dict[str, Any]] = OrderedDict()
        for key in dict.fromkeys([*self.roots, *sorted(self.included)]):
            row = self.rules[key]
            module_id, title, kind = rule_module(row)
            group = groups.setdefault(module_id, {"id": module_id, "title": title, "policy_version": POLICY_VERSION,
                "modes": set(), "dependencies": set(), "rule_ids": [], "included_rule_ids": [],
                "excluded_rules": [], "conditions": "В пределах утверждённого алгоритма; неизвестная применимость не является основанием исключения"})
            group["modes"].update(["barrier_assessment"] if kind == "barrier_only" else row["modes"])
            if key in self.forced:
                group["modes"].add(self.mode)
            group["rule_ids"].append(key)
            if key in self.included:
                group["included_rule_ids"].append(key)
            else:
                group["excluded_rules"].append({"id": key, "reason": self.reasons[key]})
            dependencies = [*row.get("requires", []), *[ref for label, ref in RuleResolver.references(row["text"]) if label == "rule"]]
            for dependency in dependencies:
                dependency_module = rule_module(self.rules[dependency])[0]
                if dependency_module != module_id:
                    group["dependencies"].add(dependency_module)
        result = []
        for group in groups.values():
            group["modes"] = sorted(group["modes"])
            group["dependencies"] = sorted(group["dependencies"])
            body = {"policy_version": POLICY_VERSION, "modes": group["modes"],
                    "conditions": group["conditions"], "dependencies": group["dependencies"],
                    "rules": [{"id": key, "text": self.rules[key]["text"], "modes": self.rules[key]["modes"],
                               "requires": self.rules[key].get("requires", [])} for key in group["rule_ids"]]}
            group["version"] = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            group["status"] = "included" if group["included_rule_ids"] and not group["excluded_rules"] else "partial" if group["included_rule_ids"] else "deferred" if self.pending else "not_applicable"
            result.append(group)
        return tuple(result)

    def filter_template(self, name: str, text: str) -> str:
        """Filter exact include lines, retaining full included paragraphs and headings."""
        lines: list[str] = []
        headings: list[tuple[int, str]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            heading = re.match(r"^(#{1,6})\s", stripped)
            if heading:
                level = len(heading.group(1))
                headings = [(depth, value) for depth, value in headings if depth < level]
                headings.append((level, line))
                continue
            references = RuleResolver.references(stripped)
            if references and all(kind == "rule" and key not in self.included for kind, key in references):
                continue
            if references and references[0][0] == "checklist" and not any(key in self.included for key in self.source_roots.get(name, [])):
                continue
            lines.extend(value for _, value in headings)
            headings = []
            lines.append(line)
        return "\n\n".join(lines) + ("\n" if lines else "")

    def preamble(self) -> str:
        lines = ["# Комплект модулей задания", f"Политика сборки: {POLICY_VERSION}. Вид работы: {self.mode}.",
                 "Полные включённые правила приведены далее один раз. Условный модуль не расширяет поручение; применимость устанавливается по данным объекта."]
        if self.pending:
            lines.append("Алгоритм не утверждён: доступны только подготовка исходных данных и обязательное ядро. Проверку и изменения AutoCAD не выполнять.")
        lines.append("## Оглавление модулей")
        for group in self.modules:
            lines.append(f"- {group['id']} — {group['title']}; {group['status']}; версия {group['version'][:16]}; "
                         f"виды: {', '.join(group['modes'])}; зависимости: {', '.join(group['dependencies']) or 'нет'}. "
                         f"Включено правил: {len(group['included_rule_ids'])}.")
        lines.append("## Исключённые или отложенные положения")
        excluded = [entry for group in self.modules for entry in group["excluded_rules"]]
        for group in self.modules:
            by_reason: dict[str, list[str]] = {}
            for entry in group["excluded_rules"]:
                by_reason.setdefault(entry["reason"], []).append(entry["id"])
            for reason, keys in by_reason.items():
                lines.append(f"- {group['id']}: {', '.join(keys)}. {reason}. Полные тексты остаются в общей базе.")
        if not excluded:
            lines.append("- Нет. Неизвестная применимость сохранена как условная.")
        return "\n".join(lines) + "\n"
