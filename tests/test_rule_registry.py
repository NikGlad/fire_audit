import copy
import unittest

from fire_audit.rule_registry import RuleRegistryError, RuleResolver


def rule(rule_id, text, **extra):
    return dict(id=rule_id, title=rule_id, text=text, modes=['compliance'], **extra)


def catalogue(*rules):
    return dict(schema_version=1, rules=list(rules))


class RuleRegistryTests(unittest.TestCase):
    def test_reference_is_expanded_once_across_documents(self):
        resolver = RuleResolver(catalogue(rule('ONE', 'Полный текст со всеми исключениями.')), 'compliance')
        first = resolver.render('{{rule:ONE}}', 'first.md')
        second = resolver.render('{{rule:ONE}}', 'second.md')
        self.assertIn('Полный текст со всеми исключениями.', first)
        self.assertNotIn('Полный текст со всеми исключениями.', second)
        self.assertIn('См. правило ONE', second)
        self.assertEqual(resolver.used_ids, {'ONE'})

    def test_preview_can_expand_every_reference(self):
        resolver = RuleResolver(catalogue(rule('A', 'Условие.')), emit_once=False)
        self.assertEqual(resolver.render('{{rule:A}} {{rule:A}}').count('Условие.'), 2)

    def test_nested_and_required_rules_are_fully_delivered(self):
        resolver = RuleResolver(catalogue(
            rule('A', 'Исключение.'), rule('B', 'Основание {{rule:A}}'),
            rule('C', 'Действие.', requires=['B']),
        ), 'compliance')
        rendered = resolver.render('{{rule:C}}')
        for text in ('Исключение.', 'Основание', 'Действие.'):
            self.assertIn(text, rendered)
        self.assertEqual(resolver.used_ids, {'A', 'B', 'C'})

    def test_unknown_reference_and_missing_dependency_are_rejected(self):
        for record in (rule('A', '{{rule:MISSING}}'), rule('A', 'Текст.', requires=['MISSING'])):
            with self.subTest(record=record), self.assertRaises(RuleRegistryError):
                RuleResolver(catalogue(record)).validate()
        with self.assertRaisesRegex(RuleRegistryError, 'неизвестный ID'):
            RuleResolver(catalogue(rule('A', 'Текст.'))).render('{{rule:OLD}}')

    def test_malformed_reference_is_not_delivered_as_text(self):
        for text in ('{{rule:A', '{{rule:}}', '{{rule:A B}}'):
            with self.subTest(text=text), self.assertRaises(RuleRegistryError):
                RuleResolver(catalogue(rule('A', 'Текст.'))).render(text)

    def test_include_and_dependency_cycles_are_rejected(self):
        for rules in (
            (rule('A', '{{rule:A}}'),),
            (rule('A', '{{rule:B}}'), rule('B', '{{rule:A}}')),
            (rule('A', 'А.', requires=['B']), rule('B', 'Б.', requires=['A'])),
        ):
            with self.subTest(rules=rules), self.assertRaisesRegex(RuleRegistryError, 'цикл'):
                RuleResolver(catalogue(*rules)).validate()

    def test_different_copies_with_same_id_are_rejected(self):
        with self.assertRaisesRegex(RuleRegistryError, 'расхождение включённых копий'):
            RuleResolver(catalogue(rule('A', 'Старая редакция.'), rule('A', 'Новая редакция.'))).validate()

    def test_even_identical_redefinitions_are_rejected(self):
        with self.assertRaisesRegex(RuleRegistryError, 'единственного источника'):
            RuleResolver(catalogue(rule('A', 'Текст.'), rule('A', 'Текст.'))).validate()

    def test_checklist_grows_from_actual_ids(self):
        data = catalogue(
            rule('KO-01', 'Первый контроль.', checklist_group='КО', checklist_id='КО-01', checklist_title='Первый'),
            rule('KO-13', 'Последний контроль.', checklist_group='КО', checklist_id='КО-13', checklist_title='Новый'),
        )
        resolver = RuleResolver(data, 'compliance')
        self.assertIn('КО-13. Новый', resolver.render('{{checklist:КО}}'))
        changed = copy.deepcopy(data)
        changed['rules'].append(rule('KO-14', 'Ещё контроль.', checklist_group='КО', checklist_id='КО-14'))
        self.assertIn('КО-14', RuleResolver(changed).render('{{checklist:КО}}'))
        self.assertNotIn('КО-14', resolver.render('{{checklist:КО}}'))

    def test_unknown_checklist_is_rejected(self):
        with self.assertRaisesRegex(RuleRegistryError, 'контрольный перечень'):
            RuleResolver(catalogue(rule('A', 'Текст.'))).render('{{checklist:РО}}')

    def test_invalid_display_titles_are_rejected_before_rendering(self):
        for field in ('title', 'checklist_title'):
            for value in (None, [], '', ' '):
                record = rule('A', 'Текст.')
                record[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(RuleRegistryError):
                    RuleResolver(catalogue(record)).validate()

    def test_checklist_title_does_not_repeat_the_id(self):
        resolver = RuleResolver(catalogue(rule('KO-01', 'Контроль.', checklist_group='КО',
                                              checklist_id='КО-01', checklist_title='КО-01. Полнота')))
        self.assertIn('- КО-01. Полнота', resolver.render('{{checklist:КО}}'))

    def test_mode_scope_cannot_silently_drop_dependency(self):
        data = catalogue(rule('A', '{{rule:B}}'), rule('B', 'Частное правило.'))
        data['rules'][1]['modes'] = ['barrier_assessment']
        with self.assertRaisesRegex(RuleRegistryError, 'не действует'):
            RuleResolver(data).validate()
        with self.assertRaisesRegex(RuleRegistryError, 'не действует'):
            RuleResolver(catalogue(rule('A', 'Текст.')), 'stage').render('{{rule:A}}')

    def test_history_is_not_an_active_rule_source(self):
        with self.assertRaisesRegex(RuleRegistryError, 'истории'):
            RuleResolver(catalogue(rule('A', 'Прежний текст.', status='superseded'))).validate()

    def test_catalogue_and_rows_are_detached_from_mutation(self):
        data = catalogue(rule('A', 'Сохранённая редакция.'))
        resolver = RuleResolver(data)
        data['rules'][0]['text'] = 'Другой текст.'
        resolver.rows()[0]['text'] = 'Ещё один текст.'
        self.assertIn('Сохранённая редакция.', resolver.render('{{rule:A}}'))

    def test_invalid_schema_modes_and_ids_are_rejected(self):
        for data in ({}, catalogue(rule('../A', 'Текст.')), catalogue(rule('A', '')),
                     dict(schema_version=2, rules=[])):
            with self.subTest(data=data), self.assertRaises(RuleRegistryError):
                RuleResolver(data).validate()
        with self.assertRaises(RuleRegistryError):
            RuleResolver(catalogue(rule('A', 'Текст.')), 'unknown').validate()


if __name__ == '__main__':
    unittest.main()
