"""Pure expansion of versioned shared rules, without filesystem side effects.

Source files and their byte hashes belong to KnowledgeBase. This module only
validates and expands the captured catalogue, so a single run cannot mix reads
from two revisions of the knowledge base.
"""
from __future__ import annotations

import copy
import re
from typing import Any


SUPPORTED_MODES = frozenset({
    "stage", "compliance", "article89", "actualization_protocol",
    "ar_assessment_transfer", "ar_plan_preparation", "ar_plan_comparison",
    "protocol", "barrier_assessment", "du_assessment",
})
ID_PATTERN = re.compile(r"^[\w][\w.-]*$", re.UNICODE)
REFERENCE_PATTERN = re.compile(r"\{\{(rule|checklist):([\w.-]+)\}\}", re.UNICODE)
REFERENCE_START = re.compile(r"\{\{(?:rule|checklist):")


class RuleRegistryError(ValueError):
    """A rule cannot be delivered completely and unambiguously."""


class RuleResolver:
    def __init__(self, catalogue: dict[str, Any], mode: str | None = None, *,
                 emit_once: bool = True):
        self.catalogue = copy.deepcopy(catalogue)
        self.mode = mode
        self.emit_once = emit_once
        self.emitted: set[str] = set()
        self.used_ids: set[str] = set()
        self._rules: dict[str, dict[str, Any]] = {}
        self._groups: dict[str, list[dict[str, Any]]] = {}
        self._validated = False

    @staticmethod
    def _error(message: str) -> None:
        raise RuleRegistryError("rule_blocks.json: " + message)

    @staticmethod
    def references(text: str) -> list[tuple[str, str]]:
        return [(match.group(1), match.group(2))
                for match in REFERENCE_PATTERN.finditer(text)]

    def validate(self) -> None:
        if self._validated:
            return
        if self.mode is not None and self.mode not in SUPPORTED_MODES:
            self._error(f"неизвестный вид работы {self.mode}")
        if (not isinstance(self.catalogue, dict) or
                self.catalogue.get("schema_version") != 1 or
                not isinstance(self.catalogue.get("rules"), list)):
            self._error("ожидается schema_version 1 и массив rules")
        rules: dict[str, dict[str, Any]] = {}
        groups: dict[str, list[dict[str, Any]]] = {}
        checklist_ids: set[str] = set()
        for entry in self.catalogue["rules"]:
            if not isinstance(entry, dict):
                self._error("каждое правило должно быть объектом")
            rule_id = entry.get("id")
            if not isinstance(rule_id, str) or not ID_PATTERN.fullmatch(rule_id):
                self._error("неверный или пустой устойчивый ID")
            if rule_id in rules:
                detail = ("расхождение включённых копий" if
                          rules[rule_id].get("text") != entry.get("text") else
                          "повторное определение единственного источника")
                self._error(f"{rule_id}: {detail}")
            if entry.get("status", "active") != "active":
                self._error(f"{rule_id}: прежние редакции должны храниться отдельно в истории")
            if not isinstance(entry.get("text"), str) or not entry["text"].strip():
                self._error(f"{rule_id}: text должен быть непустой строкой")
            for label in ("title", "checklist_title"):
                if label in entry and (not isinstance(entry[label], str) or not entry[label].strip()):
                    self._error(f"{rule_id}: {label} должен быть непустой строкой")
            modes = entry.get("modes")
            if (not isinstance(modes, list) or not modes or
                    any(not isinstance(mode, str) or mode not in SUPPORTED_MODES for mode in modes) or
                    len(set(modes)) != len(modes)):
                self._error(f"{rule_id}: нужны неповторяющиеся известные modes")
            requires = entry.get("requires", [])
            if (not isinstance(requires, list) or
                    any(not isinstance(item, str) or not ID_PATTERN.fullmatch(item) for item in requires) or
                    len(set(requires)) != len(requires)):
                self._error(f"{rule_id}: requires должен содержать неповторяющиеся ID")
            group = entry.get("checklist_group")
            if group is not None:
                checklist_id = entry.get("checklist_id", rule_id)
                if (not isinstance(group, str) or not ID_PATTERN.fullmatch(group) or
                        not isinstance(checklist_id, str) or not ID_PATTERN.fullmatch(checklist_id)):
                    self._error(f"{rule_id}: неверный идентификатор контрольного перечня")
                if checklist_id in checklist_ids:
                    self._error(f"повторный пункт контрольного перечня {checklist_id}")
                checklist_ids.add(checklist_id)
                groups.setdefault(group, []).append(entry)
            rules[rule_id] = entry
        self._rules, self._groups = rules, groups
        graph: dict[str, set[str]] = {}
        for rule_id, rule in rules.items():
            self._validate_text(rule["text"], rule_id)
            dependencies = set(rule.get("requires", []))
            dependencies.update(key for kind, key in self.references(rule["text"]) if kind == "rule")
            for dependency in dependencies:
                if dependency not in rules:
                    self._error(f"{rule_id}: отсутствующая зависимость {dependency}")
                missing_modes = set(rule["modes"]) - set(rules[dependency]["modes"])
                if missing_modes:
                    self._error(f"{rule_id}: зависимость {dependency} не действует в " +
                                ", ".join(sorted(missing_modes)))
            graph[rule_id] = dependencies
        self._validate_cycles(graph)
        self._validated = True

    def _validate_text(self, text: str, source: str) -> None:
        if not isinstance(text, str):
            self._error(f"{source}: ожидается текст")
        if len(REFERENCE_START.findall(text)) != len(REFERENCE_PATTERN.findall(text)):
            self._error(f"{source}: повреждённая ссылка на правило или контрольный перечень")
        for kind, key in self.references(text):
            if kind == "rule" and key not in self._rules:
                self._error(f"{source}: устаревшая ссылка или неизвестный ID {key}")
            if kind == "checklist" and key not in self._groups:
                self._error(f"{source}: отсутствует контрольный перечень {key}")

    def _validate_cycles(self, graph: dict[str, set[str]]) -> None:
        # Iterative DFS avoids a recursion failure even for malformed long chains.
        finished: set[str] = set()
        for root in graph:
            if root in finished:
                continue
            active: set[str] = {root}
            stack = [(root, iter(sorted(graph[root])))]
            while stack:
                node, children = stack[-1]
                child = next(children, None)
                if child is None:
                    stack.pop()
                    active.remove(node)
                    finished.add(node)
                elif child in active:
                    self._error("цикл зависимостей: " + " → ".join([n for n, _ in stack] + [child]))
                elif child not in finished:
                    active.add(child)
                    stack.append((child, iter(sorted(graph[child]))))

    def validate_text(self, text: str, source: str = "") -> None:
        self.validate()
        self._validate_text(text, source or "документ")
        if self.mode is not None:
            for kind, key in self.references(text):
                if kind == "rule" and self.mode not in self._rules[key]["modes"]:
                    self._error(f"{source or 'документ'}: правило {key} не действует в {self.mode}")

    def rows(self) -> list[dict[str, Any]]:
        self.validate()
        rows = []
        for entry in self._rules.values():
            if self.mode is not None and self.mode not in entry["modes"]:
                continue
            row = copy.deepcopy(entry)
            row.setdefault("title", row["id"])
            row.setdefault("requires", [])
            row.setdefault("status", "active")
            rows.append(row)
        return rows

    @staticmethod
    def _natural_key(value: str) -> tuple:
        return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                     for part in re.split(r"(\d+)", value))

    def checklist(self, group: str) -> str:
        self.validate()
        if group not in self._groups:
            self._error(f"отсутствует контрольный перечень {group}")
        entries = [rule for rule in self._groups[group]
                   if self.mode is None or self.mode in rule["modes"]]
        entries.sort(key=lambda rule: self._natural_key(rule.get("checklist_id", rule["id"])))
        if not entries:
            return f"Контрольный перечень {group}: для выбранного вида работ нет применимых пунктов."
        lines = []
        for rule in entries:
            label = rule.get("checklist_id", rule["id"])
            title = rule.get("checklist_title", rule.get("title", rule["id"]))
            title = re.sub(r"^" + re.escape(label) + r"(?:[.:\s—-]+)", "", title)
            lines.append(f"- {label}. {title} (правило {rule['id']}).")
        return "\n".join(lines)

    def render(self, text: str, source: str = "") -> str:
        self.validate_text(text, source)
        return self._render_text(text, source, depth=0)

    def _render_text(self, text: str, source: str, depth: int) -> str:
        if depth > 128:
            self._error(f"{source}: чрезмерная глубина включения правил")

        def substitute(match: re.Match) -> str:
            kind, key = match.groups()
            if kind == "checklist":
                return self.checklist(key)
            return self._render_rule(key, depth + 1)

        return REFERENCE_PATTERN.sub(substitute, text)

    def _render_rule(self, rule_id: str, depth: int) -> str:
        if depth > 128:
            self._error(f"{rule_id}: чрезмерная глубина включения правил")
        rule = self._rules[rule_id]
        if self.mode is not None and self.mode not in rule["modes"]:
            self._error(f"{rule_id}: правило не действует в {self.mode}")
        self.used_ids.add(rule_id)
        if self.emit_once and rule_id in self.emitted:
            return f"См. правило {rule_id} (полный текст приведён выше)."
        self.emitted.add(rule_id)
        dependencies = [self._render_rule(dependency, depth + 1)
                        for dependency in rule.get("requires", [])]
        body = self._render_text(rule["text"], rule_id, depth)
        return "\n".join([*dependencies, f"[Правило {rule_id}]", body])
