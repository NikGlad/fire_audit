"""Manual smoke command, isolated temporary project store, no CAD writes."""
import shutil
import tempfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as gui_module


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name in ("config", "knowledge", "templates"):
            shutil.copytree(gui_module.ROOT/name, root/name)
        gui_module.ROOT = root
        gui = gui_module.FireAuditGUI()
        gui.withdraw()
        try:
            gui.update_idletasks()
            titles = [gui.tabs.tab(tab, "text") for tab in gui.tabs.tabs()]
            assert "Контроль" not in titles
            assert "Как работать" not in titles
            assert "Задание" in titles
            assert "Дополнительные данные" in titles
            assert "Оценка преград" not in titles
            assert gui.mode_values["Оценка соответствия АР"] == "compliance"
            assert "Оценка соответствия" not in gui.mode_values
            assert gui.mode_var.get() == "Оценка соответствия АР"
            assert gui.mode_values["Оценка Преград"] == "barrier_assessment"
            assert gui.mode_values["Оценка ДУ"] == "du_assessment"
            from fire_audit.runs import MODE_LABELS
            assert set(gui.mode_values.values()) == set(MODE_LABELS)
            project = gui.app.projects.create("GUI synthetic fixture", "ui-test")
            project["dates"]["gpzu"] = "2026-04-23"
            gui.app.projects.save("ui-test", project)
            gui.refresh_projects()
            gui._select_project_by_slug("ui-test")
            pp = gui.barrier_panel
            assert pp is gui.additional_data_panel
            assert pp.slug == "ui-test"
            assert pp.values["stu_status"].get() == "Не установлено"
            assert set(pp.special_texts) == {"lifts_and_hoists","corridor_division","slabs_and_openings","storage_and_equipment"}
            assert pp.pump_text.get() == ""
            pp.pump_text.set("Общая насосная АУП и ВПВ, 1 этаж, помещение Н-1; ответ заказчика — тест")
            pp.special_texts["corridor_division"].insert("1.0", "Тестовый ответ заказчика")
            pp.save()
            assert gui.app.projects.load("ui-test")["barrier_assessment"]["corridor_division"] == "Тестовый ответ заказчика"
            saved_pumps = gui.app.projects.load("ui-test")["barrier_assessment"]["fire_pump_rooms_text"]
            assert "помещение Н-1" in saved_pumps
            pp.reload()
            assert "ответ заказчика" in pp.pump_text.get()
            gui.app.projects.create("Second GUI fixture", "ui-other")
            gui.refresh_projects()
            gui._select_project_by_slug("ui-other")
            assert pp.pump_text.get() == ""
            gui._select_project_by_slug("ui-test")
            assert "помещение Н-1" in pp.pump_text.get()
            run = gui.app.runs.create("ui-test", "compliance")
            gui.refresh_runs(); gui.run_tree.selection_set(run["run"]["run_id"])
            assert Path(run["quality_file"]).is_file()
            assert "AUTOM8LABS" in Path(run["task_file"]).read_text(encoding="utf-8")
            print("GUI smoke OK:", ", ".join(titles))
        finally:
            gui.destroy()


if __name__ == "__main__": main()
