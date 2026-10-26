import unittest
from pathlib import Path

from fire_audit.knowledge import KnowledgeBase, MODE_FILES
from fire_audit.paths import AppPaths


class AnalyticRegistryRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.knowledge = KnowledgeBase(AppPaths(Path(__file__).resolve().parents[1]))

    def test_registry_only_analytics_reaches_every_mode(self):
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.snapshot_for_mode(mode).context
                for phrase in (
                    'для всех видов работ и всех объектов: на планы не добавлять аналитические пояснения',
                    'В реестре допускается хранить аналитические выводы',
                    'почему конкретное замечание и/или запрос выданы либо не выданы',
                    'какие исходные данные и требования послужили основанием решения',
                    'Само замечание или запрос оформлять по правилам соответствующего вида работ',
                    'не отменяет обязательную фиксацию результата и доказательств',
                ):
                    self.assertIn(phrase, context)

    def test_old_permissions_are_removed_from_every_mode(self):
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.context_for_mode(mode)
                for phrase in (
                    'указать в пояснении для объекта конкретный пункт и причину',
                    'Содержательное пояснение создавать только при самостоятельной необходимости',
                    'исключения для содержательных пояснений определены документом 25',
                    'содержательное пояснение на плане только при самостоятельной необходимости',
                    'Не отменяются содержательные пояснения, прямо необходимые для результата',
                    'отсутствующие двери и выходы — пояснение',
                ):
                    self.assertNotIn(phrase, context)

    def test_missing_data_reason_stays_in_registry(self):
        text = self.knowledge.load_document('01_ОБЩИЙ_ПРОЦЕСС.md').text
        self.assertIn('записать в рабочем реестре конкретный пункт, причину, выполненную часть и следующий шаг', text)
        self.assertIn('Аналитическое пояснение на план не добавлять', text)
        self.assertIn('при наличии оснований оформить сам запрос', text)

    def test_existing_notes_and_required_outputs_are_preserved(self):
        text = self.knowledge.load_document('25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md').text
        self.assertIn('Ранее существующие комментарии, замечания и ответы не удалять и не скрывать', text)
        self.assertIn('Сами замечания и необходимые недублирующие запросы сохраняются по правилам режима', text)
        self.assertIn('дополнения о новых местах нарушения и обработка ответов заказчика', text)
        self.assertIn('В пояснении для объекта сохранять утверждённый состав характеристик', text)
        checklist = self.knowledge.load_document('08_САМОПРОВЕРКА.md').text
        self.assertIn('ранее существовавшие аналитические пояснения о невыдаче без добавления к ним новых лучей', checklist)
        self.assertNotIn('ранее существовавшие пояснения без добавления к ним новых лучей', checklist)
        for name in ('05_ОФОРМЛЕНИЕ_AUTOCAD.md', '06_ЭТАПИЗАЦИЯ.md'):
            with self.subTest(document=name):
                text = self.knowledge.load_document(name).text
                self.assertIn('От ранее существующего зелёного пояснения о невыдаче элемента новый луч не строить', text)
                self.assertIn('новые аналитические пояснения не создавать', text)

    def test_changed_documents_remain_valid_shared_rules(self):
        for name in (
            '00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md',
            '01_ОБЩИЙ_ПРОЦЕСС.md',
            '05_ОФОРМЛЕНИЕ_AUTOCAD.md',
            '06_ЭТАПИЗАЦИЯ.md',
            '07_СТАТЬЯ_89.md',
            '08_САМОПРОВЕРКА.md',
            '25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md',
        ):
            with self.subTest(document=name):
                self.knowledge.validate_document(name, self.knowledge.load_document(name).text)


if __name__ == '__main__':
    unittest.main()
