import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from fire_audit.barrier_inputs import (defaults, inputs_for, validate_inputs,
                                       fire_pump_rooms_markdown, fire_pump_text_for_ui)
from fire_audit.core import FireAuditApp


class FirePumpInputTests(unittest.TestCase):
    def test_legacy_and_new_objects_start_unknown_without_mutation(self):
        project = {"barrier_assessment": {"special_conditions": "Сохранить"}}
        original = copy.deepcopy(project)
        answers = inputs_for(project)["fire_pump_rooms"]
        self.assertIsNone(answers["aup"]["present"])
        self.assertIsNone(answers["vpv"]["present"])
        answers["aup"]["locations"] = "Тестовое помещение"
        self.assertEqual(project, original)
        self.assertEqual(defaults()["fire_pump_rooms"]["aup"]["locations"], "")
        self.assertIsNone(validate_inputs({"stu_status": "unknown"})["fire_pump_rooms"]["aup"]["present"])

    def test_partial_answer_and_combined_station(self):
        project = {"barrier_assessment": {"fire_pump_rooms": {"aup": {"present": True}}}}
        data = inputs_for(project)
        self.assertIsNone(data["fire_pump_rooms"]["vpv"]["present"])
        self.assertIn("уточнить расположение насосных АУП", fire_pump_rooms_markdown(project))
        for answer in data["fire_pump_rooms"].values():
            answer.update(present=True, locations=" 1 этаж, помещение Н-1 ", source=" Ответ заказчика ")
        result = validate_inputs(data)
        self.assertEqual(result["fire_pump_rooms"]["aup"], result["fire_pump_rooms"]["vpv"])
        self.assertEqual(result["fire_pump_rooms"]["aup"]["locations"], "1 этаж, помещение Н-1")
        self.assertEqual(data["fire_pump_rooms"]["aup"]["source"], " Ответ заказчика ")

    def test_false_and_unknown_are_not_conflated(self):
        data = defaults()
        data["fire_pump_rooms"]["aup"]["present"] = False
        text = fire_pump_rooms_markdown({"barrier_assessment": validate_inputs(data)})
        self.assertIn("АУП: Нет.", text)
        self.assertIn("ВПВ: Не установлено.", text)
        self.assertIn("какие насосные обслуживают ВПВ", text)
        self.assertNotIn("какие насосные обслуживают АУП", text)

    def test_free_text_answer_is_preserved_and_used_in_task_context(self):
        data = defaults()
        data["fire_pump_rooms_text"] = "  Общая насосная АУП и ВПВ — помещение 120, 1 этаж; ответ заказчика.  "
        result = validate_inputs(data)
        self.assertEqual(result["fire_pump_rooms_text"],
                         "Общая насосная АУП и ВПВ — помещение 120, 1 этаж; ответ заказчика.")
        self.assertEqual(fire_pump_text_for_ui(result), result["fire_pump_rooms_text"])
        text = fire_pump_rooms_markdown({"barrier_assessment": result})
        self.assertIn("помещение 120", text)
        self.assertNotIn("АУП: Не установлено", text)

    def test_reject_invalid_answers_and_conflicting_absence(self):
        for value in ("Да", "false", 0, 1, [], {}):
            with self.subTest(value=value):
                data = defaults()
                data["fire_pump_rooms"]["aup"]["present"] = value
                with self.assertRaises(ValueError):
                    validate_inputs(data)
        for value in (None, [], {"aup": None}, {"aup": {"locations": ["Н-1"]}},
                      {"aup": {"present": False, "locations": "Н-1"}}):
            with self.subTest(value=value):
                data = defaults()
                data["fire_pump_rooms"] = value
                with self.assertRaises(ValueError):
                    validate_inputs(data)

    def test_answers_round_trip_to_saved_project_and_frozen_task_without_leaking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("config", "knowledge", "templates"):
                shutil.copytree(Path(__file__).resolve().parents[1] / name, root / name)
            app = FireAuditApp(root)
            project = app.projects.create("Насосные — тест", "pump-test")
            project["dates"]["gpzu"] = "2024-08-06"
            data = defaults()
            for answer in data["fire_pump_rooms"].values():
                answer.update(present=True, locations="1 этаж, помещение Н-1", source="Ответ заказчика")
            project["barrier_assessment"] = validate_inputs(data)
            app.projects.save("pump-test", project)
            reloaded = app.projects.load("pump-test")
            self.assertEqual(inputs_for(reloaded)["fire_pump_rooms"], data["fire_pump_rooms"])
            result = app.runs.create("pump-test", "barrier_assessment")
            task_path = Path(result["task_file"])
            original_task = task_path.read_bytes()
            text = original_task.decode("utf-8")
            self.assertIn("АУП: Есть. Помещения / этажи / оси: 1 этаж, помещение Н-1", text)
            self.assertIn("ВПВ: Есть. Помещения / этажи / оси: 1 этаж, помещение Н-1", text)
            self.assertEqual(result["run"]["project_snapshot"]["barrier_assessment"]["fire_pump_rooms"], data["fire_pump_rooms"])
            reloaded["barrier_assessment"]["fire_pump_rooms"]["aup"].update(present=False, locations="")
            app.projects.save("pump-test", reloaded)
            self.assertEqual(task_path.read_bytes(), original_task)
            self.assertFalse(app.runs.chat_handoff("pump-test", result["run"]["run_id"])["project_current"])
            other = app.projects.create("Другой объект", "other-pump-test")
            self.assertIsNone(inputs_for(other)["fire_pump_rooms"]["aup"]["present"])
            self.assertNotIn("Н-1", fire_pump_rooms_markdown(other))


if __name__ == "__main__":
    unittest.main()
