from __future__ import annotations

import hashlib
import json
import re
import copy
from datetime import datetime, timezone
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io_utils import sha256_file, atomic_write_text
from .paths import AppPaths
from .rule_registry import RuleResolver
from .norm_lock import normative_update_lock
from .knowledge_modules import ModulePlan, PENDING_MODES, rule_scope


MODE_FILES = {
    "stage": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "01_ОБЩИЙ_ПРОЦЕСС.md",
        "02_НОРМАТИВНЫЙ_ПОИСК.md",
        "04_ОБМЕРЫ.md",
        "05_ОФОРМЛЕНИЕ_AUTOCAD.md",
        "06_ЭТАПИЗАЦИЯ.md",
        "10_ЭТАПИЗАЦИЯ_ДЕТАЛЬНЫЕ_УСЛОВИЯ.md",
        "08_САМОПРОВЕРКА.md",
    ],
    "compliance": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "01_ОБЩИЙ_ПРОЦЕСС.md",
        "02_НОРМАТИВНЫЙ_ПОИСК.md",
        "03_ОЦЕНКА_СООТВЕТСТВИЯ.md",
        "04_ОБМЕРЫ.md",
        "05_ОФОРМЛЕНИЕ_AUTOCAD.md",
        "08_САМОПРОВЕРКА.md",
        "28_НОРМЫ_ДЛЯ_ОБЪЕКТА.md",
    ],
    "article89": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "01_ОБЩИЙ_ПРОЦЕСС.md",
        "02_НОРМАТИВНЫЙ_ПОИСК.md",
        "04_ОБМЕРЫ.md",
        "05_ОФОРМЛЕНИЕ_AUTOCAD.md",
        "07_СТАТЬЯ_89.md",
        "08_САМОПРОВЕРКА.md",
    ],
    "actualization_protocol": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "03_ОЦЕНКА_СООТВЕТСТВИЯ.md",
        "04_ОБМЕРЫ.md",
        "05_ОФОРМЛЕНИЕ_AUTOCAD.md",
        "07_СТАТЬЯ_89.md",
        "08_САМОПРОВЕРКА.md",
        "11_АКТУАЛИЗАЦИЯ.md",
    ],
    "ar_assessment_transfer": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "12_ПЕРЕНОС_ОЦЕНКИ_АР.md",
    ],
    "ar_plan_preparation": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "13_ПОДГОТОВКА_ПЛАНОВ_АР.md",
    ],
    "ar_plan_comparison": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "15_СРАВНЕНИЕ_ПЛАНОВ_АР.md",
    ],
    "protocol": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "04_ОБМЕРЫ.md",
        "05_ОФОРМЛЕНИЕ_AUTOCAD.md",
        "08_САМОПРОВЕРКА.md",
        "16_ПРОТОКОЛ.md",
    ],
    "barrier_assessment": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "32_ПОСЛЕДОВАТЕЛЬНОСТЬ_ОЦЕНКИ_ПРЕГРАД.md",
        "23_НОРМАТИВНАЯ_МАТРИЦА_ПП.md",
        "18_ОЦЕНКА_ПРЕГРАД.md",
        "21_ПОСТРОЕНИЕ_И_ПРОВЕРКА_ПП.md",
        "26_СОСТАВ_СХЕМЫ_ПП.md",
        "29_КОНТРОЛЬ_ЗАМЕЧАНИЙ_ПП.md",
        "barrier_drawing_profile.json",
    ],
    "du_assessment": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "19_ОЦЕНКА_ДУ.md",
    ],
}


@dataclass(frozen=True)
class KnowledgeDocument:
    name: str
    path: Path
    text: str
    sha256: str


@dataclass(frozen=True)
class KnowledgeSnapshot:
    mode: str
    context: str
    digest: str
    documents: tuple[KnowledgeDocument, ...]
    instructions_pending: bool
    rendered_documents: tuple[tuple[str, str], ...] = ()
    module_manifest_json: str = ""


class KnowledgeBase:
    def __init__(self, paths: AppPaths):
        self.paths = paths

    def inventory(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for path in sorted(self.paths.knowledge.glob("*")):
            if not path.is_file():
                continue
            result.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        return result

    def load_document(self, name: str) -> KnowledgeDocument:
        path = (self.paths.knowledge / name).resolve()
        if path.parent != self.paths.knowledge.resolve() or not path.is_file():
            raise FileNotFoundError(name)
        raw = path.read_bytes()
        # Keep read_text's universal-newline semantics, but hash the same read.
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        return KnowledgeDocument(name=name, path=path, text=text, sha256=hashlib.sha256(raw).hexdigest())

    def document_names_for_mode(self, mode: str) -> list[str]:
        if mode not in MODE_FILES:
            raise ValueError(f"Неизвестный режим: {mode}")
        return list(dict.fromkeys([
            "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md", "02_НОРМАТИВНЫЙ_ПОИСК.md",
            "14_РЕГЛАМЕНТ_ПОИСКА_ДОКУМЕНТОВ.md", *MODE_FILES[mode],
            "20_КЛАССИФИКАЦИЯ_ЧАСТЕЙ_ЗДАНИЯ.md",
            "22_ПРЯМАЯ_РАБОТА_ПОМОЩНИКА.md",
            "24_ДВА_ВИДА_ЗАДАНИЯ.md",
            "25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md",
            "27_НЕЗАВИСИМЫЕ_ТРЕБОВАНИЯ_ДВЕРИ_ПЕРЕКРЫТИЯ.md",
            "29_ПРЕЕМСТВЕННОСТЬ_ПРОВЕРОК.md",
            "30_КОНТРОЛЬ_ОШИБОК_И_ПРОПУСКОВ.md",
            "31_РЕЕСТР_ОШИБОК_И_МЕТОДИКА.md",
        ]))

    def scope_issues(self, name: str, text: str) -> list[str]:
        """Catch explicit project facts; this is a guard, not semantic proof."""
        checks = (
            (r"(?i)\bпомещени(?:е|я|й|ю|и|ем|ях|ям|ями)[ \t]*(?:№|N)?[ \t]*"
             r"\d+(?:[./-]\d+)*(?:[а-яa-z])?(?![\w/-]|\.\d)(?!\s*(?:м²|м2|м\b|кв\.))",
             "номер конкретного помещения"),
            (r"(?i)(?<![\w.])\d{1,3}(?:[./-]\d{1,4})+\s+"
             r"(?:принять|принимаем|присвоить|принято)\s+(?:категори\w*\s+)?[АБВГД]\d?\b",
             "категория присвоена конкретному номеру помещения"),
            (r"(?i)\bзамечани(?:е|я|й|ю|и|ем|ям|ями|ях)[ \t]*(?:№|N)?[ \t]*"
             r"\d+(?:[./-]\d+)*(?![\w/-]|\.\d)", "номер замечания конкретного объекта"),
            (r"(?i)\bдата\s+гпзу\s*[:=]?\s*(?:\d{4}-\d{2}-\d{2}|\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})\b",
             "конкретная дата ГПЗУ"),
            (r"(?i)(?:projects|runs)[\\/][^\s\"']+", "путь к папке конкретного объекта или запуска"),
            (r'(?i)"(?:gpzu|fire_resistance_degree|constructive_fire_hazard_class|height_m)"\s*:\s*'
             r'(?:"(?:\d{4}-\d{2}-\d{2}|I{1,3}|IV|V|С[0-3]|\d+(?:[.,]\d+)?)"|\d+(?:\.\d+)?)',
             "значение параметра паспорта конкретного объекта"),
        )
        issues = [f"{name}: обнаружен {label}" for pattern, label in checks if re.search(pattern, text)]

        # Absolute document locations may identify shared regulations/instructions.
        # Do not mistake those or repository URLs for a particular object's files.
        path_pattern = (r"(?i)(?:(?<![a-z])[a-z]:[\\/]|(?<![\\/])\\\\|(?<![\w:./\\])/)(?:[^\r\n\"'<>]+?)"
                        r"\.(?:dwg|dxf|xlsx|xls|pdf|docx?|rtf)\b")
        shared_document = re.compile(
            r"(?i)(?:[\\/](?:knowledge|norms|normative_cache|templates|шаблоны)[\\/]|"
            r"регламент|инструкци|норматив|письма[ _]вниипо|[\\/](?:СП|ГОСТ|СНиП|ФЗ)[ _-]?\d)"
        )
        for match in re.finditer(path_pattern, text):
            path_text = match.group()
            if re.search(r"(?i)\.(?:pdf|docx?|rtf)$", path_text) and shared_document.search(path_text):
                continue
            issues.append(f"{name}: обнаружен абсолютный путь к файлу конкретного объекта")
            break

        assignments = (
            (r"\bкатегории\s+утверждены\b", "подтверждение категорий конкретного объекта"),
            (r"\bсту\s+(?:нет|отсутству(?:ет|ют))\b", "статус СТУ конкретного объекта"),
            (r"\bдля\s+(?:(?:этого|данного|текущего)\s+)?объекта\s+"
             r"(?:принять|принято|принимаем|установить)\s+(?:[IV]+\s+степен\w*\s+огнестойкости|"
             r"(?:категори\w*\s+)?[АБВГД]\d?\b|(?:класс\s+)?[СФ]\d(?:\.\d)?)",
             "присвоение параметра конкретному объекту"),
            (r"\b(?:степень\s+огнестойкости(?:\s+(?:здания|объекта))?\s*[:=—-]\s*[IV]+\b|"
             r"высота\s+(?:здания|объекта)\s*[:=—-]\s*\d+(?:[.,]\d+)?\s*м\b|"
             r"категория\s+помещения\s*[:=—-]\s*[АБВГД]\d?\b|"
             r"класс\s+(?:конструктивной|функциональной)\s+пожарной\s+опасности\s*[:=—-]\s*[СФ]\d(?:\.\d)?)",
             "конкретное значение параметра здания или помещения"),
        )
        for pattern, label in assignments:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                before = re.split(r"[\n;!?]|\.\s+(?=[А-ЯA-Z])", text[:match.start()])[-1]
                if re.search(r"(?i)\b(?:если|при условии|в случае)\b", before):
                    continue
                issues.append(f"{name}: обнаружен {label}")
                break
        return issues

    def validate_shared_scope(self, name: str, text: str) -> None:
        issues = self.scope_issues(name, text)
        if issues:
            raise ValueError(
                "Постоянные инструкции действуют для всех объектов, поэтому в них нельзя сохранять факты конкретного объекта. "
                "Перенесите их в паспорт/вводные объекта и сформулируйте правило условно. " + "; ".join(issues)
            )

    def validate_document(self, name: str, text: str) -> None:
        """Validate an editor candidate without writing it or changing the rules."""
        self._validate_document_syntax(name, text)
        registry_path = self.paths.knowledge / "rule_blocks.json"
        if name != "rule_blocks.json" and not registry_path.is_file():
            if name == "rule_overrides.json" and any(row.get("text_ref") for row in json.loads(text)["rules"]):
                raise ValueError("Для text_ref требуется rule_blocks.json")
            return
        # Candidate overlay: never persist half of an invalid include graph.
        sources = {path.name: (text if path.name == name else self.load_document(path.name).text)
                   for path in sorted(self.paths.knowledge.iterdir())
                   if path.is_file() and path.suffix.lower() in {".md", ".json"}}
        sources[name] = text
        self._validate_resolver_sources(sources)

    def _validate_document_syntax(self, name: str, text: str) -> None:
        if Path(name).suffix.lower() == ".json":
            try:
                value = json.loads(text)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{name}: некорректный JSON: {exc}") from exc
            if name == "rule_overrides.json":
                self._validate_rule_catalogue(value)
            # Decode escaped JSON strings before checking their meaning.
            self.validate_shared_scope(name, json.dumps(value, ensure_ascii=False))
        else:
            self.validate_shared_scope(name, text)

    def _validate_resolver_sources(self, sources: dict[str, str], mode: str | None = None) -> RuleResolver | None:
        for name, text in sources.items():
            self._validate_document_syntax(name, text)
        if "rule_blocks.json" not in sources:
            if any(row.get("text_ref") for row in json.loads(sources.get("rule_overrides.json", '{"rules": []}'))["rules"]):
                raise ValueError("Для text_ref требуется rule_blocks.json")
            return None
        resolver = RuleResolver(json.loads(sources["rule_blocks.json"]), mode)
        resolver.validate()
        templates_only = resolver.catalogue.get("template_sources_only") is True
        rule_modes = {row["id"]: set(row["modes"]) for row in resolver.catalogue["rules"]}

        def validate_modes(text: str, applicable_modes: list[str], source: str) -> None:
            for kind, key in resolver.references(text):
                if kind == "rule":
                    missing = set(applicable_modes) - rule_modes[key]
                    if missing:
                        raise ValueError(f"{source}: правило {key} не действует в {', '.join(sorted(missing))}")

        for name, text in sources.items():
            if name.endswith(".md"):
                resolver.validate_text(text, source=name)
                if templates_only:
                    for line_number, line in enumerate(text.splitlines(), 1):
                        line = line.strip()
                        if not line or re.fullmatch(r"#{1,6}\s+\S.*", line) or re.fullmatch(r"\{\{(?:rule|checklist):[\w.-]+\}\}", line):
                            continue
                        raise ValueError(f"{name}:{line_number}: шаблон инструкции может содержать только заголовки и ссылки "
                                         "{{rule:ID}} / {{checklist:ID}}. Измените единый текст в каталоге правил.")
                if mode is None:
                    applicable_modes = [key for key in MODE_FILES if name in self.document_names_for_mode(key)]
                    validate_modes(text, applicable_modes, name)
        catalogue = json.loads(sources.get("rule_overrides.json", '{"rules": []}'))
        states = self._override_states(catalogue["rules"], mode)
        for rule in catalogue["rules"]:
            if not states[rule["id"]]["effective_modes"]:
                continue
            if templates_only and not rule.get("text_ref"):
                raise ValueError("rule_overrides.json:" + rule["id"] +
                                 ": действующее уточнение должно ссылаться на единый текст через text_ref")
            body = self._override_text(rule)
            resolver.validate_text(body, source="rule_overrides.json:" + rule["id"])
            validate_modes(body, states[rule["id"]]["effective_modes"], "rule_overrides.json:" + rule["id"])
        return resolver

    @staticmethod
    def _override_text(rule: dict[str, Any]) -> str:
        if rule.get("text_ref"):
            return "{{rule:" + rule["text_ref"] + "}}"
        return rule.get("text", "")

    @staticmethod
    def _validate_rule_catalogue(catalogue: Any) -> None:
        prefix = "rule_overrides.json: "
        if not isinstance(catalogue, dict) or not isinstance(catalogue.get("rules"), list):
            raise ValueError(prefix + "ожидается объект с массивом rules")
        records = catalogue["rules"]
        mapping: dict[str, dict[str, Any]] = {}
        for rule in records:
            if not isinstance(rule, dict):
                raise ValueError(prefix + "каждое правило должно быть объектом")
            rule_id = rule.get("id")
            if not isinstance(rule_id, str) or not rule_id.strip() or rule_id in mapping:
                raise ValueError(prefix + "нужны уникальные непустые id правил")
            if rule.get("status") not in ("active", "superseded"):
                raise ValueError(prefix + "неверный status правила")
            text_value, text_ref = rule.get("text"), rule.get("text_ref")
            has_text = isinstance(text_value, str) and bool(text_value.strip())
            has_ref = isinstance(text_ref, str) and bool(text_ref.strip())
            history_ref = rule.get("history_ref")
            if text_value is not None and not has_text:
                raise ValueError(prefix + "text должен быть непустой строкой")
            if text_ref is not None and (not has_ref or not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9_.:-]+", text_ref)):
                raise ValueError(prefix + "text_ref должен содержать устойчивый id правила")
            if has_text and has_ref:
                raise ValueError(prefix + "нельзя одновременно задавать text и text_ref")
            if history_ref is not None and (not isinstance(history_ref, str) or
                    not re.fullmatch(r"history/[A-Za-zА-Яа-яЁё0-9_.-]+\.json(?:#[A-Za-zА-Яа-яЁё0-9_.:-]+)?", history_ref)):
                raise ValueError(prefix + "неверный history_ref")
            if not (has_text or has_ref or history_ref):
                raise ValueError(prefix + "действующему правилу требуется text или text_ref")
            modes = rule.get("modes")
            if (not isinstance(modes, list) or not modes or
                    any(not isinstance(mode, str) or mode not in MODE_FILES for mode in modes) or
                    len(set(modes)) != len(modes)):
                raise ValueError(prefix + "modes должен содержать неповторяющиеся известные виды работ")
            replaces = rule.get("replaces", [])
            if (not isinstance(replaces, list) or
                    any(not isinstance(previous, str) or not previous.strip() for previous in replaces) or
                    len(set(replaces)) != len(replaces)):
                raise ValueError(prefix + "replaces должен быть массивом неповторяющихся id")
            mapping[rule_id] = rule
        for rule_id, rule in mapping.items():
            if rule_id in rule.get("replaces", []) or any(previous not in mapping for previous in rule.get("replaces", [])):
                raise ValueError(prefix + "неверная ссылка replaces")

        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(rule_id: str) -> None:
            if rule_id in visiting:
                raise ValueError(prefix + "циклическое замещение правил")
            if rule_id in visited:
                return
            visiting.add(rule_id)
            for previous in mapping[rule_id].get("replaces", []):
                visit(previous)
            visiting.remove(rule_id)
            visited.add(rule_id)

        for rule_id in mapping:
            visit(rule_id)

        # A superseded record disappears globally. Require active successors for
        # each former mode, including replacements split across several records.
        covered: dict[str, set[str]] = {rule_id: set() for rule_id in mapping}
        for rule in records:
            if rule["status"] != "active":
                continue
            for mode in rule["modes"]:
                for previous in KnowledgeBase._replaced_ids(mapping, rule["id"], mode):
                    covered[previous].add(mode)
        for rule in records:
            missing = set(rule["modes"]) - covered[rule["id"]]
            if rule["status"] == "superseded" and missing:
                raise ValueError(prefix + f"замещение {rule['id']} теряет виды работ: {', '.join(sorted(missing))}. "
                                 "Сохраните старое правило active для остальных режимов либо добавьте их замену.")
            if rule["status"] == "active" and missing and not (rule.get("text") or rule.get("text_ref")):
                raise ValueError(prefix + f"действующему правилу {rule['id']} требуется text или text_ref")

    @staticmethod
    def _replaced_ids(mapping: dict[str, dict[str, Any]], rule_id: str, mode: str) -> set[str]:
        result: set[str] = set()
        pending = list(mapping[rule_id].get("replaces", []))
        while pending:
            previous = pending.pop()
            if previous not in result and mode in mapping[previous]["modes"]:
                result.add(previous)
                pending.extend(mapping[previous].get("replaces", []))
        return result

    @staticmethod
    def _override_states(records: list[dict[str, Any]], mode: str | None = None) -> dict[str, dict[str, Any]]:
        """One mode-aware replacement graph for delivery and the catalogue UI."""
        if mode is not None and mode not in MODE_FILES:
            raise ValueError(f"Неизвестный режим: {mode}")
        mapping = {row["id"]: row for row in records}
        effective: dict[str, list[str]] = {key: [] for key in mapping}
        replaced: dict[str, list[str]] = {key: [] for key in mapping}
        replaced_by: dict[str, set[str]] = {key: set() for key in mapping}
        modes = [mode] if mode else list(MODE_FILES)
        for selected_mode in modes:
            active = [row for row in records if row["status"] == "active" and selected_mode in row["modes"]]
            descendants = {row["id"]: KnowledgeBase._replaced_ids(mapping, row["id"], selected_mode) for row in active}
            removed = set().union(*descendants.values())
            selected = [row for row in active if row["id"] not in removed]
            for row in selected:
                effective[row["id"]].append(selected_mode)
                for previous in descendants[row["id"]]:
                    replaced_by[previous].add(row["id"])
            for key in removed:
                replaced[key].append(selected_mode)
        result = {}
        for key, row in mapping.items():
            if mode and mode not in row["modes"]:
                status = "not_applicable"
            elif row["status"] == "superseded":
                status = "superseded"
            elif effective[key] and replaced[key]:
                status = "partially_replaced"
            elif effective[key]:
                status = "active"
            else:
                status = "replaced"
            result[key] = {"effective_status": status, "effective_modes": effective[key],
                           "replaced_modes": replaced[key], "replaced_by": sorted(replaced_by[key])}
        return result

    def snapshot_for_mode(self, mode: str) -> KnowledgeSnapshot:
        """Read each source once; all outputs are derived from these same bytes."""
        names = self.document_names_for_mode(mode)
        if mode != "barrier_assessment" and (self.paths.knowledge / "room_classification.json").is_file():
            names.append("room_classification.json")
        if (self.paths.knowledge / "rule_overrides.json").is_file():
            names.append("rule_overrides.json")
        if (self.paths.knowledge / "rule_blocks.json").is_file():
            names.append("rule_blocks.json")
        documents = tuple(self.load_document(name) for name in dict.fromkeys(names))
        sources = {doc.name: doc.text for doc in documents}
        resolver = self._validate_resolver_sources(sources, mode)
        pending = self._pending_from_sources(sources, mode, resolver)
        module_plan = self._module_plan(sources, mode, resolver, pending=pending)
        parts: list[str] = []
        if module_plan:
            parts.append(module_plan.preamble())
        rendered_documents: dict[str, str] = {}
        for doc in documents:
            if doc.name == "rule_blocks.json":
                continue
            if doc.name != "rule_overrides.json":
                template = module_plan.filter_template(doc.name, doc.text) if module_plan and doc.name.endswith(".md") else doc.text
                if module_plan and pending and doc.name in {"room_classification.json", "barrier_drawing_profile.json"}:
                    continue
                if module_plan and not template.strip():
                    continue
                rendered = resolver.render(template, source=doc.name) if resolver and doc.path.suffix.lower() == ".md" else template
                rendered_documents[doc.name] = rendered
                parts.append(f"\n===== {doc.name} =====\n{rendered.strip()}\n")
                continue
            records = json.loads(doc.text)["rules"]
            states = self._override_states(records, mode)
            selected = [rule for rule in records if states[rule["id"]]["effective_modes"]]
            if selected:
                parts.append("\n===== Действующие пользовательские уточнения =====\n")
                for rule in selected:
                    body = self._override_text(rule)
                    if resolver:
                        body = resolver.render(body, source="rule_overrides.json:" + rule["id"])
                    parts.append(f"\nПравило {rule['id']} (заменяет: {', '.join(rule.get('replaces', [])) or 'нет'}):\n{body}\n")
        context = "".join(parts).strip() + "\n"
        return KnowledgeSnapshot(mode=mode, context=context, digest=hashlib.sha256(context.encode("utf-8")).hexdigest(),
                                 documents=documents, instructions_pending=pending,
                                 rendered_documents=tuple(rendered_documents.items()),
                                 module_manifest_json=json.dumps(module_plan.modules, ensure_ascii=False, sort_keys=True) if module_plan else "")

    def _pending_from_sources(self, sources: dict[str, str], mode: str, resolver: RuleResolver | None) -> bool:
        marker = "алгоритм этого вида проверки ещё не передан пользователем"
        probe = RuleResolver(resolver.catalogue, mode, emit_once=False) if resolver else None
        return any(marker in (probe.render(sources[name], source=name) if probe else sources[name]).lower()
                   for name in MODE_FILES[mode] if name in sources and name.endswith(".md"))

    def _module_plan(self, sources: dict[str, str], mode: str | None, resolver: RuleResolver | None,
                     *, pending: bool = False) -> ModulePlan | None:
        if mode is None or resolver is None or resolver.catalogue.get("template_sources_only") is not True:
            return None
        records = json.loads(sources.get("rule_overrides.json", '{"rules": []}'))["rules"]
        states = self._override_states(records, mode)
        forced = [row["text_ref"] for row in records if row.get("text_ref") and states[row["id"]]["effective_modes"]]
        return ModulePlan(resolver.catalogue, sources, mode, pending=pending,
                          algorithm_documents=MODE_FILES[mode], override_ids=forced)

    def instructions_pending(self, mode: str) -> bool:
        return self.snapshot_for_mode(mode).instructions_pending

    def context_for_mode(self, mode: str) -> str:
        return self.snapshot_for_mode(mode).context

    def digest_for_mode(self, mode: str) -> str:
        return self.snapshot_for_mode(mode).digest

    def resolved_document(self, name: str, mode: str | None = None, *,
                          snapshot: KnowledgeSnapshot | tuple[KnowledgeDocument, ...] | None = None) -> str:
        """Read a complete effective document; raw source APIs retain their original hashes."""
        documents = snapshot.documents if isinstance(snapshot, KnowledgeSnapshot) else snapshot
        if documents is None:
            names = [name]
            if name != "rule_blocks.json" and (self.paths.knowledge / "rule_blocks.json").is_file():
                names.append("rule_blocks.json")
            if mode and name != "rule_overrides.json" and (self.paths.knowledge / "rule_overrides.json").is_file():
                names.append("rule_overrides.json")
            if mode in PENDING_MODES:
                names.extend(MODE_FILES[mode])
            documents = tuple(self.load_document(item) for item in dict.fromkeys(names))
        sources = {doc.name: doc.text for doc in documents}
        if name not in sources:
            raise FileNotFoundError(name)
        resolver = self._validate_resolver_sources(sources, mode)
        pending = self._pending_from_sources(sources, mode, resolver) if mode else False
        plan = self._module_plan(sources, mode, resolver, pending=pending) if name.endswith(".md") else None
        return self._resolved_text(name, sources, resolver, mode, plan=plan)

    def _resolved_text(self, name: str, sources: dict[str, str], resolver: RuleResolver | None,
                       mode: str | None = None, *, plan: ModulePlan | None = None) -> str:
        if name == "rule_blocks.json":
            rows = resolver.rows() if resolver else []
            if plan:
                rows = [row for row in rows if row["id"] in plan.included]
            elif mode:
                rows = [row for row in rows if rule_scope(row, mode)["applicability_status"] != "not_applicable"]
            return "\n\n".join(resolver.render("{{rule:" + row["id"] + "}}", source=name) for row in rows)
        if name == "rule_overrides.json":
            records = json.loads(sources[name])["rules"]
            states = self._override_states(records, mode)
            return "\n\n".join((resolver.render(self._override_text(row), source=name) if resolver else self._override_text(row))
                               for row in records if states[row["id"]]["effective_modes"])
        template = plan.filter_template(name, sources[name]) if plan and name.endswith(".md") else sources[name]
        return resolver.render(template, source=name) if resolver and name.endswith(".md") else template

    def search(self, query: str, limit: int = 20, *, mode: str | None = None,
               include_history: bool = False) -> list[dict[str, Any]]:
        query_lower = query.strip().lower()
        if not query_lower:
            return []
        terms = [term for term in query_lower.split() if len(term) > 1]
        results: list[dict[str, Any]] = []
        applicable_names = set(self.document_names_for_mode(mode)) | {"rule_blocks.json", "rule_overrides.json", "room_classification.json"} if mode else None
        documents = [self.load_document(path.name) for path in sorted(self.paths.knowledge.iterdir())
                     if path.is_file() and path.suffix.lower() in {".md", ".json", ".txt"}
                     and (applicable_names is None or path.name in applicable_names)]
        sources = {doc.name: doc.text for doc in documents}
        resolver = self._validate_resolver_sources(sources, mode)
        plan = self._module_plan(sources, mode, resolver,
                                 pending=self._pending_from_sources(sources, mode, resolver) if mode else False)
        for doc in documents:
            if plan and plan.pending and doc.name in {"room_classification.json", "barrier_drawing_profile.json"}:
                continue
            if resolver:
                # Each search result is a complete document, using one captured revision.
                resolver.emitted.clear()
                resolver.used_ids.clear()
            text = self._resolved_text(doc.name, sources, resolver, mode, plan=plan)
            lower = text.lower()
            score = sum(lower.count(term) for term in terms)
            if score:
                first = min((lower.find(term) for term in terms if term in lower), default=0)
                start = max(0, first - 180)
                end = min(len(text), first + 520)
                results.append(
                    {
                        "file": doc.name,
                        "score": score,
                        "fragment": text[start:end].replace("\n", " "),
                    }
                )
        if include_history:
            for entry in self.knowledge_history():
                path = Path(entry["path"])
                text = path.read_text(encoding="utf-8-sig")
                score = sum(text.lower().count(term) for term in terms)
                if score:
                    first = min(text.lower().find(term) for term in terms if term in text.lower())
                    results.append({"file": entry["name"], "score": score,
                                    "fragment": text[max(0, first-180):first+520].replace("\n", " "), "historical": True})
        results.sort(key=lambda row: (-row["score"], row["file"]))
        return results[: max(1, min(limit, 100))]

    def effective_rule_catalogue(self, mode: str | None = None, include_history: bool = False) -> list[dict[str, Any]]:
        registry = self.paths.knowledge / "rule_blocks.json"
        if not registry.is_file():
            return []
        doc = self.load_document(registry.name)
        sources = {doc.name: doc.text}
        if (self.paths.knowledge / "rule_overrides.json").is_file():
            sources["rule_overrides.json"] = self.load_document("rule_overrides.json").text
        if mode in PENDING_MODES:
            for name in MODE_FILES[mode]:
                sources[name] = self.load_document(name).text
        resolver = self._validate_resolver_sources(sources, mode)
        pending = self._pending_from_sources(sources, mode, resolver) if mode else False
        overrides = json.loads(sources.get("rule_overrides.json", '{"rules": []}'))["rules"]
        states = self._override_states(overrides, mode)
        forced = {row["text_ref"] for row in overrides if row.get("text_ref") and states[row["id"]]["effective_modes"]}
        rows = []
        canonical = {}
        for original in resolver.rows():
            row = copy.deepcopy(original)
            row.update(source=doc.name, editable=row.get("status", "active") == "active", source_sha256=doc.sha256,
                       effective_status="active", replaced_by=[], record_type="canonical")
            scope = rule_scope(row, mode)
            if row["id"] in forced and scope["applicability_status"] == "not_applicable":
                scope.update(applicability_status="conditional", applicability_reason="Прямое действующее уточнение выбранного режима сохраняет полный текст; проверить его условия и границы применения")
            row.update(scope)
            if scope["applicability_status"] == "not_applicable":
                row["effective_status"] = "not_applicable"
                if not include_history:
                    continue
            elif scope["applicability_status"] == "conditional":
                row["effective_status"] = "conditional"
                if pending:
                    row["applicability_status"] = "deferred"
                    row["applicability_reason"] = "Тематическое выполнение отложено до утверждения алгоритма; полный текст сохранён в базе"
            canonical[row["id"]] = row
            rows.append(row)
        history = self._history_rule_rows(mode, states) if include_history else []
        archived_text = {row["history_ref"]: row.get("text", "") for row in history}
        for original in overrides:
            if mode and mode not in original["modes"]:
                continue
            state = states[original["id"]]
            if not include_history and not state["effective_modes"]:
                continue
            row = copy.deepcopy(original)
            row.update(state)
            canonical_id = row.get("text_ref")
            canonical_rule = canonical.get(canonical_id, {})
            row.update(source="rule_overrides.json", record_type="override", editable=False,
                       canonical_id=canonical_id,
                       text=canonical_rule.get("text", row.get("text", archived_text.get(row.get("history_ref"), ""))))
            row.setdefault("title", canonical_rule.get("title", row["id"]))
            rows.append(row)
        rows.extend(history)
        return rows

    def _history_rule_rows(self, mode: str | None = None,
                           states: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        rows = []
        for entry in self.knowledge_history():
            path = Path(entry["path"])
            if path.suffix.lower() != ".json":
                continue
            archived = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(archived, dict):
                continue
            records = archived.get("rules", [])
            is_edit = isinstance(archived.get("before"), dict)
            if is_edit:
                records = [archived["before"]]
            is_snapshot = len(Path(entry["name"]).parts) > 2
            for original in records:
                if not isinstance(original, dict) or (mode and mode not in original.get("modes", [])):
                    continue
                row = copy.deepcopy(original)
                state = (states or {}).get(row.get("id"), {})
                status = "archive_snapshot" if is_snapshot else "historical" if is_edit else state.get("effective_status", "historical")
                row.update(source=entry["name"], editable=False, historical=True, record_type="history",
                           effective_status=status, replaced_by=[] if is_snapshot or is_edit else state.get("replaced_by", []),
                           history_ref=entry["name"] + "#" + str(row.get("id", "")))
                row.setdefault("title", row.get("id", "Историческое правило"))
                rows.append(row)
        return rows

    def read_rule(self, rule_id: str) -> dict[str, Any]:
        doc = self.load_document("rule_blocks.json")
        resolver = self._validate_resolver_sources({doc.name: doc.text})
        for row in resolver.rows():
            if row["id"] == rule_id:
                row.update(source=doc.name, editable=True, source_sha256=doc.sha256,
                           effective_status="active", replaced_by=[], record_type="canonical")
                row.update(rule_scope(row, None))
                if row["applicability_status"] == "conditional":
                    row["effective_status"] = "conditional"
                historical_ids = {rule_id, *row.get("legacy_override_ids", [])}
                row["history"] = [entry for entry in self._history_rule_rows() if entry.get("id") in historical_ids]
                resolver.emit_once = False
                row["preview"] = resolver.render("{{rule:" + rule_id + "}}", source="rule_blocks.json")
                return row
        raise KeyError(f"Неизвестное действующее правило: {rule_id}")

    def validate_rule_text(self, rule_id: str, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Текст правила не может быть пустым")
        doc = self.load_document("rule_blocks.json")
        catalogue = json.loads(doc.text)
        row = next((item for item in catalogue["rules"] if item["id"] == rule_id), None)
        if row is None or row.get("status", "active") != "active":
            raise ValueError("Редактировать можно только действующее правило")
        row["text"] = text.strip()
        self.validate_document(doc.name, json.dumps(catalogue, ensure_ascii=False))

    def save_rule_text(self, rule_id: str, text: str, *, expected_sha256: str | None = None) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Текст правила не может быть пустым")
        with normative_update_lock(self.paths.logs / "knowledge_rules.lock"):
            doc = self.load_document("rule_blocks.json")
            if expected_sha256 is not None and expected_sha256 != doc.sha256:
                raise ValueError("Правила изменены в другом окне. Перечитайте правило перед сохранением.")
            catalogue = json.loads(doc.text)
            rule = next((row for row in catalogue["rules"] if row["id"] == rule_id), None)
            if rule is None or rule.get("status", "active") != "active":
                raise ValueError("Редактировать можно только действующее правило")
            before = copy.deepcopy(rule)
            rule["text"] = text.strip()
            candidate = json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n"
            self.validate_document(doc.name, candidate)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
            backup = self.paths.logs / (doc.name + "." + stamp + ".bak")
            history = self.paths.knowledge / "history" / ("rule-edit-" + stamp + ".json")
            # Backups and history are recoverable artifacts; no source changes precede validation.
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(doc.path.read_bytes())
            atomic_write_text(history, json.dumps({"schema_version": 1, "kind": "rule_edit", "id": rule_id,
                              "created_at": datetime.now(timezone.utc).isoformat(), "source_sha256": doc.sha256,
                              "before": before, "after": copy.deepcopy(rule)}, ensure_ascii=False, indent=2) + "\n")
            try:
                atomic_write_text(doc.path, candidate)
            except BaseException:
                # The exact backup remains available; an uncommitted edit must
                # not appear among historical revisions.
                history.unlink(missing_ok=True)
                raise
            return {"id": rule_id, "saved": True, "backup_path": str(backup), "history_path": str(history),
                    "source_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest()}

    def knowledge_history(self) -> list[dict[str, Any]]:
        directory = self.paths.knowledge / "history"
        root = directory.resolve()
        return [{"path": str(path), "name": "history/" + path.relative_to(directory).as_posix(), "kind": "history"}
                for path in sorted(directory.rglob("*"), reverse=True)
                if path.is_file() and path.suffix.lower() in {".json", ".md"}
                and path.resolve().is_relative_to(root)]

    def structured_rules(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in ("room_classification.json", "normative_sources.json", "barrier_drawing_profile.json"):
            path = self.paths.knowledge / name
            if path.exists():
                doc = self.load_document(name)
                self.validate_document(name, doc.text)
                result[name] = json.loads(doc.text)
        return result
