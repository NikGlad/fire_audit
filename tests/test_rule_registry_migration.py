"""Evidence that the ОБ-07 mechanical migration preserves approved instructions."""
from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path
import tempfile
import unittest

from fire_audit.rule_registry import RuleResolver


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("migrate_rule_registry", ROOT / "scripts/migrate_rule_registry.py")
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


class RuleRegistryMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.knowledge = ROOT / "knowledge"
        cls.archive = cls.knowledge / "history/20261026_before_registry"
        cls.manifest = json.loads(MIGRATION.read(cls.knowledge / "history/migration_20261026.json"))
        cls.catalogue = json.loads(MIGRATION.read(cls.knowledge / "rule_blocks.json"))
        cls.rules = {r["id"]: r for r in cls.catalogue["rules"]}

    def test_all_33_documents_expand_exactly_except_manifest_changes(self):
        sources = list(self.archive.glob("*.md"))
        self.assertEqual(len(sources), 33)
        for path in sources:
            with self.subTest(document=path.name):
                expected = MIGRATION.read(path)
                for replacement in self.manifest["replacements"]:
                    if replacement["document"] == path.name:
                        self.assertEqual(expected.count(replacement["old_text"]), 1)
                        expected = expected.replace(replacement["old_text"], replacement["new_text"], 1)
                actual = MIGRATION.expand(MIGRATION.read(self.knowledge / path.name), self.rules)
                self.assertEqual(actual, expected)

    def test_archive_is_byte_exact_and_contains_only_common_sources(self):
        self.assertEqual(set(p.name for p in self.archive.iterdir()), set(self.manifest["source_sha256"]))
        for name, expected_hash in self.manifest["source_sha256"].items():
            self.assertEqual(MIGRATION.sha((self.archive / name).read_bytes()), expected_hash, name)
            self.assertTrue(name.endswith(".md") or name == "rule_overrides.json")

    def test_active_overrides_preserve_full_text_and_every_metadata_field(self):
        old = json.loads(MIGRATION.read(self.archive / "rule_overrides.json"))["rules"]
        current = {r["id"]: r for r in json.loads(MIGRATION.read(self.knowledge / "rule_overrides.json"))["rules"]}
        history = {r["id"]: r for r in json.loads(MIGRATION.read(self.knowledge / "history/overrides_20261026.json"))["rules"]}
        expected_effective = MIGRATION.effective_modes(old, list(MIGRATION.mode_files(ROOT)))
        self.assertEqual(set(current), {r["id"] for r in old})
        for row in old:
            with self.subTest(rule=row["id"]):
                revised = current[row["id"]]
                for key, value in row.items():
                    if key != "text":
                        self.assertEqual(revised[key], value)
                self.assertNotIn("text", revised)
                if expected_effective[row["id"]]:
                    self.assertEqual(MIGRATION.expand(self.rules[revised["text_ref"]]["text"], self.rules), row["text"])
                    self.assertIn(row["id"], self.rules[revised["text_ref"]]["legacy_override_ids"])
                else:
                    self.assertNotIn("text_ref", revised)
                    self.assertEqual(revised["history_ref"], "history/overrides_20261026.json#" + row["id"])
                    self.assertEqual(history[row["id"]], row)
        self.assertEqual(MIGRATION.effective_modes(list(current.values()), list(MIGRATION.mode_files(ROOT))), expected_effective)

    def test_partial_replacement_never_discards_remaining_mode(self):
        records = [
            {"id": "old", "status": "active", "modes": ["compliance", "protocol"], "replaces": []},
            {"id": "new", "status": "active", "modes": ["compliance"], "replaces": ["old"]},
        ]
        self.assertEqual(MIGRATION.effective_modes(records, ["compliance", "protocol"]),
                         {"old": ["protocol"], "new": ["compliance"]})

    def test_common_protocol_has_one_source_and_both_consumers(self):
        key = "protocol_shared_001"
        for prefix in ("11_", "16_"):
            path = next(self.knowledge.glob(prefix + "*.md"))
            self.assertEqual(MIGRATION.read(path).count("{{rule:" + key + "}}"), 1)
        self.assertEqual(set(self.rules[key]["modes"]), {"actualization_protocol", "protocol"})
        protocol = MIGRATION.expand(self.rules[key]["text"], self.rules)
        self.assertEqual(len(protocol.splitlines()), 146)
        self.assertIn("comment_required: false", protocol)
        self.assertIn("accepted_commitment", protocol)
        self.assertIn("не означает устранения нарушения", protocol)

    def test_auto_checklists_have_all_23_sections_and_keep_scope_text(self):
        groups = {}
        for rule in self.rules.values():
            if rule.get("checklist_group"):
                groups.setdefault(rule["checklist_group"], set()).add(rule["checklist_id"])
        self.assertEqual(groups["КО"], {f"КО-{i:02d}" for i in range(1, 14)})
        self.assertEqual(groups["РО"], {f"РО-{i:02d}" for i in range(1, 11)})
        source = MIGRATION.expand(MIGRATION.read(next(self.knowledge.glob("08_*.md"))), self.rules)
        self.assertIn("{{checklist:КО}}", source)
        self.assertIn("{{checklist:РО}}", source)
        self.assertNotIn("КО-01–КО-12", source)
        self.assertNotIn("РО-01–РО-08", source)
        self.assertIn("Ф1.1", MIGRATION.expand(self.rules["error_ro_09"]["text"], self.rules))

    def test_every_reference_has_valid_dependency_and_mode_coverage(self):
        resolver = RuleResolver(self.catalogue)
        resolver.validate()
        for path in self.knowledge.glob("*.md"):
            rendered = resolver.render(MIGRATION.read(path), source=path.name)
            self.assertNotRegex(rendered, r"\{\{(?:rule|checklist):")

    def test_no_duplicate_canonical_text_remains(self):
        texts = [rule["text"] for rule in self.rules.values()]
        self.assertEqual(len(texts), len(set(texts)))

    def test_no_repeated_long_literal_lines_remain_inside_rules(self):
        owners = {}
        for row in self.rules.values():
            for line in row["text"].splitlines():
                core = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line.strip())
                if len(core) >= 100 and not core.startswith("#") and "{{rule:" not in core and "{{checklist:" not in core:
                    self.assertNotIn(core, owners, f"{owners.get(core)} repeats in {row['id']}")
                    owners[core] = row["id"]

    def test_every_nonheading_source_rule_is_addressed_by_id(self):
        for path in self.knowledge.glob("*.md"):
            for number, line in enumerate(MIGRATION.read(path).splitlines(), 1):
                if line.strip() and not line.startswith("#"):
                    self.assertRegex(line, r"^\{\{(?:rule|checklist):[^}]+\}\}$", f"{path.name}:{number}")

    def test_second_migration_is_noop_and_keeps_later_editor_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "app"
            (root / "knowledge").mkdir(parents=True)
            (root / "fire_audit").mkdir()
            (root / "fire_audit/knowledge.py").write_bytes((ROOT / "fire_audit/knowledge.py").read_bytes())
            for path in self.archive.iterdir():
                (root / "knowledge" / path.name).write_bytes(path.read_bytes())
            first = MIGRATION.migration(root, Path(folder) / "output")
            self.assertFalse(first["already_migrated"])
            registry = root / "knowledge/rule_blocks.json"
            modified = json.loads(MIGRATION.read(registry))
            self.assertEqual(modified, self.catalogue)
            modified["rules"][0]["title"] += " — поздняя редакция"
            registry.write_text(MIGRATION.dump(modified), encoding="utf-8")
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertTrue(MIGRATION.migration(root, Path(folder) / "output")["already_migrated"])
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
