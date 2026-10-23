from pathlib import Path
import unittest

from fire_audit.core import FireAuditApp


ROOT = Path(__file__).resolve().parent.parent
HEADING = "### Принятие исходных данных из ответов заказчика"


class ProtocolCustomerInputsTests(unittest.TestCase):
    def test_shared_rule_is_identical(self):
        sections = []
        for name in ("11_АКТУАЛИЗАЦИЯ.md", "16_ПРОТОКОЛ.md"):
            text = (ROOT / "knowledge" / name).read_text(encoding="utf-8-sig")
            self.assertEqual(text.count(HEADING), 1)
            sections.append(text.split(HEADING, 1)[1].split("### Новые сведения и обозначения", 1)[0])
        self.assertEqual(*sections)

    def test_both_modes_include_inputs_and_verification_boundary(self):
        app = FireAuditApp(ROOT)
        for mode in ("protocol", "actualization_protocol"):
            with self.subTest(mode=mode):
                context = app.knowledge.context_for_mode(mode)
                self.assertIn(HEADING, context)
                self.assertIn("высокостеллажного хранения", context)
                self.assertIn("принимать как исходные данные для проверки", context)
                self.assertIn("Актуальные ответы имеют приоритет", context)
                self.assertIn("Приём исходных данных не равнозначен подтверждению соответствия", context)


if __name__ == "__main__":
    unittest.main()
