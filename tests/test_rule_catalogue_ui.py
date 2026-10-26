import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from app import FireAuditGUI


class RuleCatalogueUITests(unittest.TestCase):
    """Check user actions without opening Tk or mutating the installed catalogue."""

    def setUp(self):
        self.knowledge = Mock()
        self.gui = SimpleNamespace(
            app=SimpleNamespace(knowledge=self.knowledge),
            mode_values={"Оценка Преград": "barrier_assessment", "Оценка АР": "compliance"},
            knowledge_mode_var=Mock(), knowledge_history_var=Mock(), rule_editor=Mock(),
            knowledge_query_var=Mock(),
            rule_tree=Mock(), rule_info_var=Mock(), rule_save_button=Mock(), rule_canonical_button=Mock(),
            knowledge_history_list=Mock(), knowledge_history_text=Mock(),
            knowledge_full_list=Mock(), knowledge_full_text=Mock(), status_var=Mock(),
            _rule_rows={}, _catalogue_rows=[], _selected_rule={}, _rule_loaded_text="", _rule_selected_iid="",
            _knowledge_filter=("Оценка Преград", False),
        )
        for method in ("_knowledge_mode", "_confirm_rule_discard", "load_catalogue_rule"):
            setattr(self.gui, method, MethodType(getattr(FireAuditGUI, method), self.gui))
        self.gui._rule_status_label = FireAuditGUI._rule_status_label
        self.gui._set_knowledge_text = Mock()
        self.gui.refresh_rule_catalogue = Mock()
        self.gui.load_resolved_instruction = Mock()
        self.gui.knowledge_mode_var.get.return_value = "Оценка Преград"
        self.gui.knowledge_history_var.get.return_value = False
        self.gui.knowledge_query_var.get.return_value = ""
        self.gui.rule_tree.get_children.return_value = ()

    def editable_rule(self):
        row = {"id": "common_rule", "title": "Единое правило", "text": "Прежний текст",
               "source": "rule_blocks.json", "source_sha256": "original-sha", "editable": True,
               "modes": ["barrier_assessment", "compliance"], "effective_status": "active"}
        self.gui._selected_rule = row
        self.gui._rule_loaded_text = row["text"]
        self.gui.rule_editor.get.return_value = "  Новый общий текст.  \n"
        return row

    def test_invalid_candidate_is_rejected_before_confirmation_or_save(self):
        self.editable_rule()
        self.knowledge.validate_rule_text.side_effect = ValueError("Недопустимая ссылка")
        with patch("app.messagebox.askyesno") as confirm, patch("app.messagebox.showerror") as error:
            FireAuditGUI.save_catalogue_rule(self.gui)
        self.knowledge.validate_rule_text.assert_called_once_with("common_rule", "Новый общий текст.")
        confirm.assert_not_called()
        self.knowledge.save_rule_text.assert_not_called()
        self.gui.refresh_rule_catalogue.assert_not_called()
        error.assert_called_once()

    def test_save_validates_then_confirms_and_passes_loaded_version_to_api(self):
        self.editable_rule()
        events = []
        self.knowledge.validate_rule_text.side_effect = lambda *args: events.append("validate")

        def save(*args, **kwargs):
            events.append("save")
            return {"history_path": "history/edit.json", "backup_path": "rules.bak"}

        self.knowledge.save_rule_text.side_effect = save
        with patch("app.messagebox.askyesno", side_effect=lambda *a, **kw: events.append("confirm") or True):
            FireAuditGUI.save_catalogue_rule(self.gui)
        self.assertEqual(events, ["validate", "confirm", "save"])
        self.knowledge.save_rule_text.assert_called_once_with(
            "common_rule", "Новый общий текст.", expected_sha256="original-sha")
        self.gui.refresh_rule_catalogue.assert_called_once()
        self.assertIn("history/edit.json", self.gui.status_var.set.call_args.args[0])

    def test_declined_save_does_not_call_mutating_api(self):
        self.editable_rule()
        with patch("app.messagebox.askyesno", return_value=False):
            FireAuditGUI.save_catalogue_rule(self.gui)
        self.knowledge.validate_rule_text.assert_called_once()
        self.knowledge.save_rule_text.assert_not_called()
        self.gui._set_knowledge_text.assert_not_called()

    def test_history_is_not_saved_even_if_editable_metadata_is_inconsistent(self):
        self.editable_rule()["historical"] = True
        with patch("app.messagebox.askyesno") as confirm:
            FireAuditGUI.save_catalogue_rule(self.gui)
        confirm.assert_not_called()
        self.knowledge.validate_rule_text.assert_not_called()
        self.knowledge.save_rule_text.assert_not_called()

    def test_concurrent_change_failure_preserves_unsaved_editor_text(self):
        self.editable_rule()
        self.knowledge.save_rule_text.side_effect = ValueError("Изменено в другом окне")
        with patch("app.messagebox.askyesno", return_value=True), patch("app.messagebox.showerror") as error:
            FireAuditGUI.save_catalogue_rule(self.gui)
        error.assert_called_once()
        self.gui._set_knowledge_text.assert_not_called()
        self.gui.refresh_rule_catalogue.assert_not_called()
        self.gui.status_var.set.assert_not_called()

    def test_historical_selection_uses_own_text_not_current_rule_with_same_id(self):
        self.gui._rule_rows["archived"] = {
            "id": "common_rule", "text": "Именно старая редакция", "historical": True,
            "editable": False, "effective_status": "replaced", "modes": ["barrier_assessment"],
            "replaced_by": ["replacement"], "history_ref": "history/old.json#common_rule"}
        self.gui.rule_tree.selection.return_value = ("archived",)
        FireAuditGUI.load_catalogue_rule(self.gui)
        self.knowledge.read_rule.assert_not_called()
        self.gui._set_knowledge_text.assert_called_once_with(self.gui.rule_editor, "Именно старая редакция", editable=False)
        self.gui.rule_save_button.configure.assert_called_once_with(state="disabled")
        self.assertIn("replacement", self.gui.rule_info_var.set.call_args.args[0])

    def test_current_selection_refreshes_text_and_hash_without_changing_mode_status(self):
        row = self.editable_rule()
        self.gui._selected_rule = {}
        self.gui._rule_rows["current"] = row
        self.gui.rule_tree.selection.return_value = ("current",)
        self.knowledge.read_rule.return_value = dict(row, text="Самая новая редакция", source_sha256="fresh-sha")
        FireAuditGUI.load_catalogue_rule(self.gui)
        self.knowledge.read_rule.assert_called_once_with("common_rule")
        self.assertEqual(self.gui._selected_rule["source_sha256"], "fresh-sha")
        self.gui._set_knowledge_text.assert_called_once_with(self.gui.rule_editor, "Самая новая редакция", editable=True)

    def test_override_links_to_only_editable_current_text(self):
        self.gui._selected_rule = {"id": "override", "canonical_id": "canonical"}
        self.gui._rule_rows = {
            "old": {"id": "canonical", "editable": False, "historical": True},
            "current": {"id": "canonical", "editable": True},
        }
        self.gui.load_catalogue_rule = Mock()
        FireAuditGUI.open_canonical_rule(self.gui)
        self.gui.rule_tree.selection_set.assert_called_once_with("current")
        self.gui.load_catalogue_rule.assert_called_once()

    def test_failed_filter_change_restores_filter_of_displayed_rows(self):
        self.gui.knowledge_mode_var.get.return_value = "Оценка АР"
        self.knowledge.effective_rule_catalogue.side_effect = ValueError("Ошибочная ссылка")
        with patch("app.messagebox.showerror"):
            FireAuditGUI.refresh_rule_catalogue(self.gui)
        self.gui.knowledge_mode_var.set.assert_called_once_with("Оценка Преград")
        self.gui.rule_tree.delete.assert_not_called()

    def test_filter_uses_api_effective_status_and_exposes_replaced_rows(self):
        self.gui.knowledge_history_var.get.return_value = True
        self.knowledge.knowledge_history.return_value = []
        self.knowledge.effective_rule_catalogue.return_value = [{
            "id": "old_rule", "title": "Старое правило", "modes": ["barrier_assessment"],
            "status": "active", "effective_status": "replaced", "replaced_by": ["new_rule"],
            "editable": False, "text": "Исторический текст"}]
        FireAuditGUI.refresh_rule_catalogue(self.gui)
        self.knowledge.effective_rule_catalogue.assert_called_once_with(mode="barrier_assessment", include_history=True)
        values = self.gui.rule_tree.insert.call_args.kwargs["values"]
        self.assertEqual(values[2], "Заменено в этом режиме")
        self.assertEqual(values[3], "Оценка Преград")
        self.assertEqual(values[4], "new_rule")

    def test_resolved_view_uses_expanded_api_and_selected_mode(self):
        self.gui.knowledge_full_list.curselection.return_value = (1,)
        self.gui.knowledge_full_list.get.return_value = "00_TEST.md"
        self.knowledge.resolved_document.return_value = "Полностью раскрытый текст"
        FireAuditGUI.load_resolved_instruction(self.gui)
        self.knowledge.resolved_document.assert_called_once_with("00_TEST.md", mode="barrier_assessment")
        self.gui._set_knowledge_text.assert_called_once_with(self.gui.knowledge_full_text, "Полностью раскрытый текст")

    def test_catalogue_search_filters_by_text_without_altering_api_status(self):
        self.gui.knowledge_query_var.get.return_value = "проверить дверь"
        self.knowledge.knowledge_history.return_value = []
        self.knowledge.effective_rule_catalogue.return_value = [
            {"id": "door", "text": "Проверить дверь и границы", "effective_status": "active"},
            {"id": "roof", "text": "Проверить покрытие", "effective_status": "active"},
        ]
        FireAuditGUI.refresh_rule_catalogue(self.gui)
        self.assertEqual(len(self.gui._rule_rows), 1)
        self.assertEqual(self.gui._rule_rows["rule-0"]["id"], "door")
        self.assertEqual(len(self.gui._catalogue_rows), 2)

    def test_full_mode_view_uses_same_snapshot_as_task(self):
        self.gui.knowledge_full_list.curselection.return_value = (0,)
        self.knowledge.snapshot_for_mode.return_value = SimpleNamespace(context="Снимок задания")
        FireAuditGUI.load_resolved_instruction(self.gui)
        self.knowledge.snapshot_for_mode.assert_called_once_with("barrier_assessment")
        self.gui._set_knowledge_text.assert_called_once_with(self.gui.knowledge_full_text, "Снимок задания")

    def test_history_reads_original_saved_text_without_resolving_current_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history"
            history.mkdir()
            path = history / "old.md"
            path.write_text("Старый текст {{rule:old}}", encoding="utf-8")
            self.gui.app.paths = SimpleNamespace(knowledge=root)
            self.gui._knowledge_history_rows = [{"name": "history/old.md", "path": str(path)}]
            self.gui.knowledge_history_list.curselection.return_value = (0,)
            FireAuditGUI.load_knowledge_history(self.gui)
        self.knowledge.read_rule.assert_not_called()
        self.knowledge.resolved_document.assert_not_called()
        self.gui._set_knowledge_text.assert_called_once_with(self.gui.knowledge_history_text, "Старый текст {{rule:old}}")

    def test_raw_canonical_registry_cannot_bypass_rule_edit_history(self):
        self.gui.knowledge_list = Mock()
        self.gui.knowledge_list.curselection.return_value = (0,)
        self.gui.knowledge_list.get.return_value = "rule_blocks.json"
        with patch("app.messagebox.showinfo") as info, patch("app.atomic_write_text") as write:
            FireAuditGUI.save_knowledge_file(self.gui)
        info.assert_called_once()
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
