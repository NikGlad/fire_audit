import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import FireAuditGUI
from fire_audit.knowledge import KnowledgeBase
from fire_audit.paths import AppPaths


class KnowledgeEditorTests(unittest.TestCase):
    """Exercise the save handler without starting Tk or touching live rules."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = AppPaths(Path(self.temp.name))
        self.paths.knowledge.mkdir()
        self.paths.logs.mkdir()
        self.name = "rule_overrides.json"
        self.path = self.paths.knowledge / self.name
        self.original = '{"rules": []}\n'
        self.path.write_text(self.original, encoding="utf-8")
        self.original_bytes = self.path.read_bytes()
        self.knowledge = KnowledgeBase(self.paths)
        self.gui = SimpleNamespace(
            app=SimpleNamespace(paths=self.paths, knowledge=self.knowledge),
            knowledge_list=Mock(),
            knowledge_editor=Mock(),
            status_var=Mock(),
        )
        self.gui.knowledge_list.curselection.return_value = (0,)
        self.gui.knowledge_list.get.return_value = self.name

    @staticmethod
    def catalogue(*, modes=None, text=None):
        return json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "shared_boundary",
                        "status": "active",
                        "modes": modes if modes is not None else ["barrier_assessment"],
                        "replaces": [],
                        "text": text or "Если отделение требуется, проверить весь внутренний контур.",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    def test_invalid_catalogue_is_rejected_before_confirmation_and_write(self):
        cases = {
            "unknown mode": self.catalogue(modes=["unknown_mode"]),
            "project room": self.catalogue(text="Помещение №7 принять В3."),
            "malformed JSON": "{bad JSON",
        }
        for label, editor_text in cases.items():
            with self.subTest(case=label):
                self.gui.knowledge_editor.get.return_value = editor_text
                with (
                    patch.object(self.knowledge, "validate_document", wraps=self.knowledge.validate_document) as validate,
                    patch("app.messagebox.showerror") as showerror,
                    patch("app.messagebox.askyesno") as confirm,
                    patch("app.shutil.copy2") as copy,
                    patch("app.atomic_write_text") as write,
                ):
                    FireAuditGUI.save_knowledge_file(self.gui)
                validate.assert_called_once_with(self.name, editor_text.rstrip() + "\n")
                showerror.assert_called_once()
                confirm.assert_not_called()
                copy.assert_not_called()
                write.assert_not_called()
                self.gui.status_var.set.assert_not_called()
                self.assertEqual(self.path.read_bytes(), self.original_bytes)
                self.assertEqual(list(self.paths.logs.iterdir()), [])

    def test_valid_shared_rule_is_validated_then_saved_with_original_backup(self):
        editor_text = self.catalogue() + "\n\n"
        self.gui.knowledge_editor.get.return_value = editor_text
        expected = editor_text.rstrip() + "\n"
        events = []
        original_validate = self.knowledge.validate_document

        def validate_before_confirmation(name, text):
            events.append("validated")
            return original_validate(name, text)

        def confirm_save(*args, **kwargs):
            events.append("confirmed")
            return True

        with (
            patch.object(self.knowledge, "validate_document", side_effect=validate_before_confirmation) as validate,
            patch("app.messagebox.showerror") as showerror,
            patch("app.messagebox.askyesno", side_effect=confirm_save) as confirm,
        ):
            FireAuditGUI.save_knowledge_file(self.gui)

        validate.assert_called_once_with(self.name, expected)
        confirm.assert_called_once()
        showerror.assert_not_called()
        self.assertEqual(events, ["validated", "confirmed"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), expected)
        backups = list(self.paths.logs.glob(self.name + ".*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original_bytes)
        self.gui.status_var.set.assert_called_once()
        self.assertIn(backups[0].name, self.gui.status_var.set.call_args.args[0])

    def test_declined_save_keeps_rules_and_does_not_create_backup(self):
        self.gui.knowledge_editor.get.return_value = self.catalogue()
        with (
            patch.object(self.knowledge, "validate_document", wraps=self.knowledge.validate_document) as validate,
            patch("app.messagebox.showerror") as showerror,
            patch("app.messagebox.askyesno", return_value=False) as confirm,
        ):
            FireAuditGUI.save_knowledge_file(self.gui)

        validate.assert_called_once()
        confirm.assert_called_once()
        showerror.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertEqual(list(self.paths.logs.iterdir()), [])
        self.gui.status_var.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
