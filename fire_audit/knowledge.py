from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io_utils import sha256_file
from .paths import AppPaths


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
        "16_ПРОТОКОЛ.md",
    ],
    "barrier_assessment": [
        "00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md",
        "23_НОРМАТИВНАЯ_МАТРИЦА_ПП.md",
        "18_ОЦЕНКА_ПРЕГРАД.md",
        "21_ПОСТРОЕНИЕ_И_ПРОВЕРКА_ПП.md",
        "26_СОСТАВ_СХЕМЫ_ПП.md",
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
            if not isinstance(rule.get("text"), str) or not rule["text"].strip():
                raise ValueError(prefix + "text должен быть непустой строкой")
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

    def snapshot_for_mode(self, mode: str) -> KnowledgeSnapshot:
        """Read each source once; all outputs are derived from these same bytes."""
        names = self.document_names_for_mode(mode)
        if mode != "barrier_assessment" and (self.paths.knowledge / "room_classification.json").is_file():
            names.append("room_classification.json")
        if (self.paths.knowledge / "rule_overrides.json").is_file():
            names.append("rule_overrides.json")
        documents = tuple(self.load_document(name) for name in dict.fromkeys(names))
        parts: list[str] = []
        for doc in documents:
            self.validate_document(doc.name, doc.text)
            if doc.name != "rule_overrides.json":
                parts.append(f"\n===== {doc.name} =====\n{doc.text.strip()}\n")
                continue
            records = json.loads(doc.text)["rules"]
            mapping = {rule["id"]: rule for rule in records}
            active = [rule for rule in records if rule["status"] == "active" and mode in rule["modes"]]
            replaced = set().union(*(self._replaced_ids(mapping, rule["id"], mode) for rule in active))
            selected = [rule for rule in active if rule["id"] not in replaced]
            if selected:
                parts.append("\n===== Действующие пользовательские уточнения =====\n")
                for rule in selected:
                    parts.append(f"\nПравило {rule['id']} (заменяет: {', '.join(rule.get('replaces', [])) or 'нет'}):\n{rule['text']}\n")
        context = "".join(parts).strip() + "\n"
        marker = "алгоритм этого вида проверки ещё не передан пользователем"
        pending = any(marker in doc.text.lower() for doc in documents
                      if doc.name in MODE_FILES[mode] and doc.path.suffix.lower() == ".md")
        return KnowledgeSnapshot(mode=mode, context=context, digest=hashlib.sha256(context.encode("utf-8")).hexdigest(),
                                 documents=documents, instructions_pending=pending)

    def instructions_pending(self, mode: str) -> bool:
        return self.snapshot_for_mode(mode).instructions_pending

    def context_for_mode(self, mode: str) -> str:
        return self.snapshot_for_mode(mode).context

    def digest_for_mode(self, mode: str) -> str:
        return self.snapshot_for_mode(mode).digest

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        query_lower = query.strip().lower()
        if not query_lower:
            return []
        terms = [term for term in query_lower.split() if len(term) > 1]
        results: list[dict[str, Any]] = []
        for item in self.inventory():
            path = Path(item["path"])
            if path.suffix.lower() not in {".md", ".json", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8-sig")
            lower = text.lower()
            score = sum(lower.count(term) for term in terms)
            if score:
                first = min((lower.find(term) for term in terms if term in lower), default=0)
                start = max(0, first - 180)
                end = min(len(text), first + 520)
                results.append(
                    {
                        "file": path.name,
                        "score": score,
                        "fragment": text[start:end].replace("\n", " "),
                    }
                )
        results.sort(key=lambda row: (-row["score"], row["file"]))
        return results[: max(1, min(limit, 100))]

    def structured_rules(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in ("room_classification.json", "normative_sources.json", "barrier_drawing_profile.json"):
            path = self.paths.knowledge / name
            if path.exists():
                doc = self.load_document(name)
                self.validate_document(name, doc.text)
                result[name] = json.loads(doc.text)
        return result
