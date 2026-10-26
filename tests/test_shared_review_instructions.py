"""Regression checks for shared instruction delivery, without touching AutoCAD."""
import unittest
from pathlib import Path

from fire_audit.knowledge import KnowledgeBase, MODE_FILES
from fire_audit.paths import get_paths


class SharedReviewInstructionsTests(unittest.TestCase):
    def setUp(self):
        self.k = KnowledgeBase(get_paths(Path(__file__).resolve().parents[1]))

    def test_updated_documents_have_shared_scope(self):
        for name in self.k.document_names_for_mode('stage') + self.k.document_names_for_mode('compliance'):
            if name.startswith(('02_', '03_', '04_', '05_', '06_', '08_', '25_')):
                doc = self.k.load_document(name)
                self.k.validate_document(name, doc.text)

    def test_dedup_and_reuse_are_delivered_to_every_mode(self):
        for mode in MODE_FILES:
            context = self.k.context_for_mode(mode)
            self.assertIn('Общая графика для нескольких самостоятельных замечаний', context, mode)
            self.assertIn('Совпадение номера пункта не доказывает дубль', context, mode)

    def test_compliance_preserves_catalogue_and_missing_stage_work(self):
        context = self.k.context_for_mode('compliance')
        self.assertIn('Замечания этапизации не добавлять к этому каталогу', context)
        self.assertIn('как незавершённые позиции этапизации', context)
        self.assertIn('существующее корректное облако повторно не создавать', context)
        self.assertIn('Выходы из санузлов, туалетов и туалетных кабин, ванных, душевых и душевых кабин', context)

    def test_stage_frame_is_checked_and_specific_ray_is_required(self):
        context = self.k.context_for_mode('stage')
        self.assertIn('поэлементно проверить и актуализировать все системы и общие требования внутри рамки', context)
        self.assertIn('Наличие замечания без требуемого луча/размера не является завершённым оформлением', context)
        self.assertIn('autocad.reused_objects', context)

    def test_frame_exclusion_is_not_global(self):
        doc = self.k.resolved_document('25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md')
        self.assertNotIn('Исключённую пользователем рамку требований пожарной безопасности не перепроверять и не изменять;', doc)
        self.assertIn('относится к режиму актуализации по документу 11', doc)

    def test_people_layer_and_full_norm_rules_reach_every_mode(self):
        for mode in MODE_FILES:
            context = self.k.context_for_mode(mode)
            self.assertIn('Численность: отдельный слой для всех объектов', context, mode)
            self.assertIn('Полный пункт нормы и разные условия аналогичных замечаний', context, mode)
            self.assertIn('а не делить её на число лестниц или выходов', context, mode)
            self.assertIn('Подтверждённое нарушение должно иметь завершённое оформление', context, mode)
            self.assertIn('graphics.applied = false', context, mode)

    def test_layer_rule_no_longer_forbids_people_count_layer(self):
        doc = self.k.resolved_document('05_ОФОРМЛЕНИЕ_AUTOCAD.md')
        self.assertNotIn('только два точных имени слоёв', doc)
        self.assertNotIn('в том числе `экспертиза обмеры`,', doc)
        self.assertIn('Самостоятельные подписи количества людей', doc)
        self.assertIn('не перенос всех размеров', doc)
        self.assertIn('при режиме только чтения', doc)

    def test_existing_measurements_and_landing_door_rules_reach_all_modes(self):
        for mode in MODE_FILES:
            context = self.k.context_for_mode(mode)
            self.assertIn('Существующая обмерная графика как проверяемый источник', context, mode)
            self.assertIn('Лестничная площадка и открытая дверь — п. 4.4.2 СП 1', context, mode)
            self.assertIn('Стрелку подъёма не принимать автоматически за направление эвакуации', context, mode)
            self.assertIn('Не выдавать условие 90° за дословное требование СП', context, mode)
            self.assertIn('без переноса её в другой слой', context, mode)

    def test_reused_measurements_are_not_forced_into_new_graphics_layer(self):
        doc = self.k.resolved_document('05_ОФОРМЛЕНИЕ_AUTOCAD.md')
        self.assertIn('разрешено повторно использовать без переноса слоя', doc)
        self.assertNotIn('Итоговые линии и полилинии длины пути эвакуации должны находиться', doc)
        self.assertIn('Не ставить этот флаг вопреки фактическим слоям', doc)


if __name__ == '__main__':
    unittest.main()
