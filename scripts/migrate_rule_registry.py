"""One-time, loss-checked migration of common instructions to stable rule IDs.

The dated source archive is immutable. Re-running an already completed migration
does not regenerate IDs or overwrite subsequent editorial changes.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

STAMP = "20261026"
RULE = re.compile(r"\{\{rule:([A-Za-zА-Яа-яЁё0-9_.:-]+)\}\}")
CHECKLIST = re.compile(r"\{\{checklist:([^}]+)\}\}")
COMMON_PREFIXES = {"00", "02", "14", "20", "22", "24", "25", "27", "30", "31"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mode_files(root: Path) -> dict[str, list[str]]:
    tree = ast.parse(read(root / "fire_audit/knowledge.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "MODE_FILES" for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("MODE_FILES not found")


def effective_modes(records: list[dict], modes: list[str]) -> dict[str, list[str]]:
    mapping = {r["id"]: r for r in records}
    result = {key: [] for key in mapping}
    for mode in modes:
        active = [r for r in records if r["status"] == "active" and mode in r["modes"]]
        replaced = set()
        for row in active:
            pending = list(row.get("replaces", []))
            visited = set()
            while pending:
                key = pending.pop()
                if key not in visited and mode in mapping[key]["modes"]:
                    visited.add(key)
                    replaced.add(key)
                    pending.extend(mapping[key].get("replaces", []))
        for row in active:
            if row["id"] not in replaced:
                result[row["id"]].append(mode)
    return result


def expand(text: str, rules: dict[str, dict], stack: tuple[str, ...] = ()) -> str:
    """Roundtrip expansion without display labels or once-per-output deduplication."""
    def resolve(match: re.Match) -> str:
        key = match.group(1)
        if key in stack:
            raise ValueError(f"Rule cycle: {stack} -> {key}")
        return expand(rules[key]["text"], rules, (*stack, key))
    return RULE.sub(resolve, text)


def consolidate_exact_rules(rules: list[dict], texts: dict[str, str], catalogue: dict, modes: list[str]) -> dict[str, str]:
    """Remove identical definitions, preserving separate checklist identities."""
    aliases = {}
    changed = True
    while changed:
        changed = False
        first = {}
        kept = []
        for row in rules:
            body = row["text"]
            if body in first and not row.get("checklist_group") and not first[body].get("checklist_group"):
                target = first[body]
                aliases[row["id"]] = target["id"]
                target["modes"] = [m for m in modes if m in set(target["modes"] + row["modes"])]
                for legacy in row.get("legacy_override_ids", []):
                    if legacy not in target.setdefault("legacy_override_ids", []):
                        target["legacy_override_ids"].append(legacy)
                changed = True
            else:
                first[body] = row
                kept.append(row)
        rules[:] = kept
        if changed:
            def canonical(key):
                while key in aliases:
                    key = aliases[key]
                return key
            def rewritten(text):
                return RULE.sub(lambda m: "{{rule:" + canonical(m.group(1)) + "}}", text)
            for row in rules:
                row["text"] = rewritten(row["text"])
                row["requires"] = list(dict.fromkeys(canonical(key) for key in row.get("requires", [])))
            for name in texts:
                texts[name] = rewritten(texts[name])
            for row in catalogue["rules"]:
                if "text_ref" in row:
                    row["text_ref"] = canonical(row["text_ref"])
            aliases = {key: canonical(value) for key, value in aliases.items()}
    return aliases


def factor_repeated_lines(rules: list[dict], modes: list[str]) -> list[dict]:
    """Share verbatim line contents, retaining each original list/space prefix.

    Unlike semantic normalization this only separates literal leading list
    syntax and surrounding whitespace. Plain expansion must remain byte-equal.
    """
    groups = defaultdict(list)
    for row in rules:
        for number, line in enumerate(row["text"].split("\n")):
            stripped = line.strip()
            core = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", stripped)
            if len(core) < 100 or core.startswith("#") or "{{rule:" in core or "{{checklist:" in core:
                continue
            start = line.index(core)
            groups[core].append((row, number, line[:start], line[start + len(core):]))
    definitions = {row["text"]: row for row in rules}
    replacements = defaultdict(dict)
    manifest = []
    number = 1
    existing_ids = {row["id"] for row in rules}
    for core, copies in groups.items():
        if len(copies) < 2:
            continue
        owner = definitions.get(core)
        if owner is None:
            while f"shared_line_{number:03d}" in existing_ids:
                number += 1
            key = f"shared_line_{number:03d}"
            number += 1
            owner = {"id": key, "title": core[:160], "text": core,
                     "modes": [], "requires": [], "status": "active",
                     "origin_document": copies[0][0]["origin_document"]}
            rules.append(owner)
            definitions[core] = owner
            existing_ids.add(key)
        owner["modes"] = [m for m in modes if m in set(owner["modes"]) or any(m in item[0]["modes"] for item in copies)]
        changed_at = []
        for row, line_number, prefix, suffix in copies:
            if row["id"] == owner["id"]:
                continue
            replacements[row["id"]][line_number] = prefix + "{{rule:" + owner["id"] + "}}" + suffix
            changed_at.append({"id": row["id"], "line": line_number + 1, "prefix": prefix, "suffix": suffix})
        manifest.append({"canonical_id": owner["id"], "characters": len(core), "replaced_occurrences": changed_at})
    for row in rules:
        if row["id"] in replacements:
            row["text"] = "\n".join(replacements[row["id"]].get(i, line) for i, line in enumerate(row["text"].split("\n")))
    return manifest


def migration(root: Path, output: Path) -> dict:
    knowledge = root / "knowledge"
    manifest_path = knowledge / "history/migration_20261026.json"
    registry_path = knowledge / "rule_blocks.json"
    if manifest_path.exists():
        if not registry_path.exists():
            raise RuntimeError("Migration manifest exists without rule registry; refusing recovery overwrite")
        prior = json.loads(read(manifest_path))
        return {"already_migrated": True, "summary": prior["summary"]}
    if registry_path.exists():
        raise RuntimeError("Existing rule registry has no migration manifest; refusing overwrite")

    names = sorted(p.name for p in knowledge.glob("*.md"))
    originals = {name: read(knowledge / name) for name in names}
    raw_sources = {name: (knowledge / name).read_bytes() for name in [*names, "rule_overrides.json"]}
    source_catalogue = json.loads(raw_sources["rule_overrides.json"].decode("utf-8-sig"))
    configured = mode_files(root)
    modes = list(configured)
    scopes = {}
    for name in names:
        common = name[:2] in COMMON_PREFIXES or name == "29_ПРЕЕМСТВЕННОСТЬ_ПРОВЕРОК.md"
        scopes[name] = modes[:] if common else [m for m, files in configured.items() if name in files]
        # Unloaded source documents retain their text and may be selected explicitly.
        if not scopes[name]:
            scopes[name] = modes[:]
    texts = originals.copy()
    changes = []

    def named(prefix: str) -> str:
        found = [n for n in names if n.startswith(prefix)]
        if len(found) != 1:
            raise ValueError(f"Ambiguous prefix {prefix}: {found}")
        return found[0]

    def replace(name: str, old: str, new: str, reason: str, authority: list[str]):
        if texts[name].count(old) != 1:
            raise ValueError(f"Expected exactly one replacement target in {name}: {old[:80]}")
        line = texts[name][:texts[name].index(old)].count("\n") + 1
        texts[name] = texts[name].replace(old, new, 1)
        changes.append({"document": name, "original_line": line, "old_text": old,
                        "new_text": new, "reason": reason, "authority": authority})

    # Only explicit, already effective corrections approved for this migration.
    name = named("01_")
    old = next(p for p in texts[name].split("\n\n") if p.startswith("Каждый завершённый запуск"))
    retained = old[old.index("Для вида задания «Анализ и задание в Word»"):]
    replace(name, old, "Для одного поручения и всех его продолжений использовать единственную закреплённую рабочую копию результата DWG по разделу «Обязательное сохранение отдельного результата DWG» документа 00. Новый запуск не создаёт новый результат DWG. " + retained,
            "Отменённое требование отдельного DWG для каждого запуска", ["00:77–92; прямое уточнение 24.09.2026"])
    name = named("00_")
    old = "- `Актуализация` является отдельным четвёртым режимом. До получения его инструкции никакие объекты чертежа нельзя самовольно относить к этому режиму или обрабатывать по аналогии с другими режимами."
    replace(name, old, "- `Актуализация + протокол` выполняется по утверждённой инструкции документа 11 в режиме `actualization_protocol`; не относить к нему объекты самовольно по аналогии с другими видами работ.",
            "Инструкция актуализации уже утверждена; удалено устаревшее ожидание", ["11:3,7–11"])
    name = named("08_")
    replace(name, "режим `Актуализация` до получения отдельной инструкции не применяется к объектам по аналогии;", "режим `Актуализация + протокол` применяется по утверждённой инструкции документа 11, а не по аналогии;",
            "Инструкция актуализации уже утверждена", ["11:3,7–11"])
    for prefix in ("11_", "16_"):
        name = named(prefix)
        replace(name, "Перед завершением убедиться, что каждый поступивший ответ имеет такое сопоставление и пояснение со всеми тремя составляющими.",
                "Перед завершением убедиться, что каждый поступивший ответ имеет такое сопоставление и внутреннее решение со всеми тремя составляющими. Для принятого ответа применять специальное правило раздела «Принятый ответ: перенос без подтверждающего комментария»: решение сохранять в реестре, не переносить на чертёж; для остальных предусмотренных случаев пояснение остаётся обязательным.",
                "Явное исключение accepted_response имеет приоритет над общей обязательностью комментария", ["11:43–49", "16:33–39"])
    name = named("11_")
    old = next(p for p in texts[name].split("\n\n") if p.startswith("Пояснение пожарного консультанта по каждому"))
    retained = old[old.index("Правила размещения пояснений в DWG"):]
    replace(name, old, "Каждый поступивший ответ заказчика обязательно сопоставить с исходным замечанием и проанализировать по разделу «Сопоставление каждого ответа и пояснение консультанта». Необходимость комментария определять по специальному разделу «Принятый ответ: перенос без подтверждающего комментария»: полностью принятый ответ не сопровождается новым подтверждающим комментарием, внутреннее решение сохраняется в реестре; в остальных предусмотренных случаях комментарий обязателен. " + retained,
            "Снята отменённая безусловная обязанность комментария для принятого ответа; прочие условия сохранены", ["11:43–49", "16:33–39"])
    name = named("23_")
    old = "По указанию пользователя от 14.10.2026 размеры всех проверенных простенков создавать и оставлять в итоговом DWG в слое `экспертиза противопожарные преграды пояснения`, включая соответствующие требованиям."
    replace(name, old, "Размеры всех проверенных простенков создавать и оставлять в итоговом DWG, включая соответствующие требованиям. Слой каждого рабочего размера определять по действующему общему правилу: при наличии проверенного луча — `экспертиза противопожарные преграды`, без луча — `экспертиза противопожарные преграды пояснения`; связь луча с размером проверять по документу 21.",
            "Последнее утверждённое правило распределяет размеры по проверенным лучам", ["21:148", "barrier_dimension_layer_depends_on_verified_ray_20261023"])
    name = named("08_")
    old = next(p for p in texts[name].split("\n\n") if p.startswith("Пройти РО-01"))
    rest = old[old.index("Для каждого применимого урока"):]
    replace(name, old, "Пройти все применимые пункты документа `31_РЕЕСТР_ОШИБОК_И_МЕТОДИКА.md` по автоматически сформированному перечню:\n\n{{checklist:РО}}\n\nПроверить последний датированный объектный реестр ошибок. " + rest,
            "Статический диапазон не включал РО-09 и РО-10; перечень строится из действующих ID без расширения применимости", ["31:3–18,88–94"])
    old = next(p for p in texts[name].split("\n\n") if p.startswith("Дополнительно пройти КО-01"))
    rest = old[old.index("Для каждого относящегося пункта"):]
    replace(name, old, "Дополнительно пройти все применимые пункты документа 30 по автоматически сформированному перечню:\n\n{{checklist:КО}}\n\n" + rest,
            "Статический диапазон не включал КО-13; перечень строится из действующих ID без расширения применимости", ["30:117–119"])
    expected = texts.copy()

    rules = []
    by_id = {}
    by_original = {}
    sequence = Counter()
    occurrences = defaultdict(list)

    def add(key: str, body: str, origin: str, scope: list[str], title: str = "", **metadata) -> str:
        if key in by_id:
            raise ValueError(f"Duplicate assigned ID: {key}")
        row = {"id": key, "title": title or body.splitlines()[0][:160], "text": body,
               "modes": scope, "requires": [], "status": "active", "origin_document": origin, **metadata}
        rules.append(row)
        by_id[key] = row
        by_original.setdefault(body, key)
        return "{{rule:" + key + "}}"

    # Keep the entire common protocol in one source. Its internal paragraphs can
    # themselves be addressed, but there is only one owning protocol container.
    actual, protocol = named("11_"), named("16_")
    start = "## Протокол отдельно и в составе актуализации"
    protocol_body = texts[protocol][texts[protocol].index(start):texts[protocol].index("## Состояние инструкции")].rstrip("\n")
    if texts[actual].count(protocol_body) != 1:
        raise ValueError("Common protocol differs; manual reconciliation required")
    protocol_key = "protocol_shared_001"
    protocol_scope = [m for m in modes if m in ("protocol", "actualization_protocol")]
    add(protocol_key, protocol_body, actual, protocol_scope, "Протокол отдельно и в составе актуализации")
    for name in (actual, protocol):
        texts[name] = texts[name].replace(protocol_body, "{{rule:" + protocol_key + "}}", 1)
    containers = {**texts, "@protocol": protocol_body}
    container_scopes = {**scopes, "@protocol": protocol_scope}

    # Exact long-line duplicates are factored before paragraph/section IDs.
    counts = Counter()
    for name, body in containers.items():
        for line in body.splitlines():
            if len(line) >= 160 and not line.lstrip().startswith("#"):
                counts[line] += 1
                occurrences[line].append(name)
    duplicates = [line for line in counts if counts[line] > 1]
    for index, line in enumerate(duplicates, 1):
        locations = occurrences[line]
        scope = [m for m in modes if any(m in container_scopes[n] for n in locations)]
        token = add(f"shared_{index:03d}", line, locations[0].replace("@protocol", actual), scope)
        for name in set(locations):
            containers[name] = "\n".join(token if item == line else item for item in containers[name].split("\n"))

    def paragraphs(body: str, name: str, prefix: str, scope: list[str]) -> str:
        result = []
        for part in re.split(r"(\n{2,})", body):
            if not part.strip() or part.lstrip().startswith("#") or RULE.fullmatch(part.strip()) or CHECKLIST.fullmatch(part.strip()):
                result.append(part)
                continue
            sequence[prefix] += 1
            key = f"{prefix}_{sequence[prefix]:03d}"
            token = add(key, part, name, scope)
            result.append(token)
        return "".join(result)

    for name in names:
        if name.startswith(("30_", "31_")):
            group = "КО" if name.startswith("30_") else "РО"
            pattern = re.compile(r"(?m)^(## (" + group + r"-\d+)\b[^\n]*\n)(.*?)(?=^## |\Z)", re.S | re.M)
            def section(match: re.Match) -> str:
                header, label, body = match.group(1), match.group(2), match.group(3)
                lead = body[:len(body)-len(body.lstrip("\n"))]
                trail = body[len(body.rstrip("\n")):]
                content = body.strip("\n")
                key = "error_" + ("ko" if group == "КО" else "ro") + "_" + label.split("-")[1]
                token = add(key, content, name, scopes[name], header.strip("# \n"),
                            checklist_group=group, checklist_id=label, checklist_title=header.strip("# \n"))
                return header + lead + token + trail
            containers[name] = pattern.sub(section, containers[name])
        prefix = "doc_" + name[:2]
        if name.startswith("29_"):
            prefix += "_control" if "КОНТРОЛЬ" in name else "_continuity"
        texts[name] = paragraphs(containers[name], name, prefix, scopes[name])
    by_id[protocol_key]["text"] = paragraphs(containers["@protocol"], actual, "protocol", protocol_scope)

    catalogue = copy.deepcopy(source_catalogue)
    effective = effective_modes(catalogue["rules"], modes)
    historical = []
    for row in catalogue["rules"]:
        body = row.pop("text")
        if effective[row["id"]]:
            if body in by_original:
                key = by_original[body]
                by_id[key]["modes"] = [m for m in modes if m in set(by_id[key]["modes"] + row["modes"])]
            else:
                key = row["id"]
                add(key, body, "rule_overrides.json", row["modes"], row.get("title", key))
            row["text_ref"] = key
            by_id[key].setdefault("legacy_override_ids", []).append(row["id"])
        else:
            archived = next(copy.deepcopy(r) for r in source_catalogue["rules"] if r["id"] == row["id"])
            historical.append(archived)
            row["history_ref"] = f"history/overrides_{STAMP}.json#{row['id']}"
    catalogue["description"] = "Дополнительные утверждённые пользователем правила. ID, modes, status и replaces сохраняют область действия и приоритет. Действующий текст хранится только в rule_blocks.json по text_ref. Неэффективная во всех режимах редакция доступна только по history_ref; частичная замена не удаляет остальные режимы."

    exact_aliases = consolidate_exact_rules(rules, texts, catalogue, modes)
    nested_line_references = factor_repeated_lines(rules, modes)
    exact_aliases.update(consolidate_exact_rules(rules, texts, catalogue, modes))
    by_id = {row["id"]: row for row in rules}

    # A nested child must be available to every consumer, including a canonical
    # paragraph reused by an override with a wider (unchanged) scope.
    changed = True
    while changed:
        changed = False
        for parent in rules:
            for match in RULE.finditer(parent["text"]):
                child = by_id[match.group(1)]
                merged = [m for m in modes if m in set(parent["modes"] + child["modes"])]
                if merged != child["modes"]:
                    child["modes"] = merged
                    changed = True

    # Verify every character of the revised source before any working write.
    for name in names:
        actual_text = expand(texts[name], by_id)
        if actual_text != expected[name]:
            raise AssertionError(f"Roundtrip changed unapproved source text: {name}")
    for row in catalogue["rules"]:
        original = next(r for r in source_catalogue["rules"] if r["id"] == row["id"])
        if "text_ref" in row and expand(by_id[row["text_ref"]]["text"], by_id) != original["text"]:
            raise AssertionError(f"Override changed: {row['id']}")
        for key in ("id", "status", "modes", "replaces"):
            if row.get(key) != original.get(key):
                raise AssertionError(f"Override metadata changed: {row['id']}.{key}")
    summary = {"source_markdown_files": len(names), "canonical_rules": len(rules),
               "exact_duplicate_long_lines": len(duplicates),
               "additional_exact_definitions_removed": len(exact_aliases),
               "nested_exact_line_groups": len(nested_line_references),
               "repeated_characters_removed": sum(len(line) * (counts[line]-1) for line in duplicates) + len(protocol_body),
               "shared_protocol_lines": len(protocol_body.splitlines()),
               "checklist_rules": dict(Counter(r["checklist_group"] for r in rules if "checklist_group" in r)),
               "live_overrides": sum("text_ref" in r for r in catalogue["rules"]),
               "archived_overrides": len(historical), "explicit_editorial_replacements": len(changes),
               "roundtrip_exact_documents": len(names)}
    manifest = {"schema_version": 1, "migration": "ОБ-07", "date": "2026-10-26", "summary": summary,
                "source_sha256": {name: sha(raw) for name, raw in raw_sources.items()},
                "replacements": changes,
                "exact_definition_aliases": exact_aliases,
                "nested_exact_line_references": nested_line_references,
                "duplicates": [{"id": f"shared_{i:03d}", "source_occurrences": occurrences[line], "count": counts[line]} for i, line in enumerate(duplicates, 1)],
                "roundtrip": "Exact normalized-newline equality with source after only replacements listed above; checklist directives are expanded at runtime.",
                "override_effective_modes_before": effective}

    # No mutation above this point. Archives are raw-byte copies, not rendered or
    # project-dependent data. Existing differing archives are never overwritten.
    archive = knowledge / f"history/{STAMP}_before_registry"
    baseline = output / "baseline_knowledge"
    for folder in (archive, baseline):
        folder.mkdir(parents=True, exist_ok=True)
        for name, raw in raw_sources.items():
            target = folder / name
            if target.exists() and target.read_bytes() != raw:
                raise RuntimeError(f"Refusing to overwrite a differing baseline: {target}")
            if not target.exists():
                target.write_bytes(raw)
    output.mkdir(parents=True, exist_ok=True)
    (output / "migration_manifest.json").write_text(dump(manifest), encoding="utf-8")
    (knowledge / f"history/overrides_{STAMP}.json").write_text(dump({"schema_version": 1, "rules": historical}), encoding="utf-8")
    registry_path.write_text(dump({"schema_version": 1, "template_sources_only": True, "rules": rules}), encoding="utf-8")
    (knowledge / "rule_overrides.json").write_text(dump(catalogue), encoding="utf-8")
    for name, text in texts.items():
        (knowledge / name).write_text(text, encoding="utf-8", newline="\n")
    manifest_path.write_text(dump(manifest), encoding="utf-8")
    return {"already_migrated": False, "summary": summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output or root.parent / "outputs/ob07_20261026"
    print(dump(migration(root, output)))


if __name__ == "__main__":
    main()
