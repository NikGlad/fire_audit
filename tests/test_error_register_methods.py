import unittest
from pathlib import Path
from fire_audit.knowledge import KnowledgeBase, MODE_FILES
from fire_audit.paths import AppPaths


class ErrorRegisterMethodsTests(unittest.TestCase):
    def setUp(self):
        self.kb = KnowledgeBase(AppPaths(Path(__file__).resolve().parents[1]))
        self.name = "31_РЕЕСТР_ОШИБОК_И_МЕТОДИКА.md"

    def test_methods_loaded_in_every_mode(self):
        for mode in MODE_FILES:
            snapshot = self.kb.snapshot_for_mode(mode)
            self.assertIn(self.name, [d.name for d in snapshot.documents])
            self.assertIn("РО-04.", snapshot.context)

    def test_methods_have_no_project_facts(self):
        text = self.kb.load_document(self.name).text
        self.assertEqual([], self.kb.scope_issues(self.name, text))
        for index in range(1, 10):
            self.assertIn(f"## РО-{index:02}.", text)
        self.assertIn("не сохранять DWG", text)
        self.assertIn("Отсутствие его в готовом пользовательском образце", text)

    def test_f11_block17_request_is_mandatory(self):
        text = self.kb.load_document(self.name).text
        for phrase in (
            "больниц, диспансеров и подобных учреждений класса Ф1.1",
            "всегда включать",
            "Просим уточнить, предусматривается наличие нетранспортабельных людей, которые во время пожара не смогут перемещаться в зоны МГН, и в каких помещениях?",
            "требуется обосновать в СТУ.",
            "Требование распространяется на все этажи в здании.",
            "> 1. Устранить замечание.",
            "не отменяет обязательную выдачу",
            "не изменяя DWG",
        ):
            self.assertIn(phrase, text)
        for mode in ("stage", "compliance", "actualization_protocol"):
            self.assertIn("РО-09.", self.kb.snapshot_for_mode(mode).context)


if __name__ == "__main__":
    unittest.main()
