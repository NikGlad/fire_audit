import hashlib
import json
import shutil
import tempfile
import unittest
from collections import Counter
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from fire_audit.knowledge import KnowledgeBase, MODE_FILES
from fire_audit.paths import AppPaths
from fire_audit.rule_registry import RuleResolver


class SharedKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / 'knowledge', self.root / 'knowledge')
        self.knowledge = KnowledgeBase(AppPaths(self.root))

    def test_occupancy_rule_is_shared_and_not_bound_to_room_numbers(self):
        filename = '25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md'
        text = self.knowledge.resolved_document(filename)
        self.assertIn('Расчёт вместимости переговорных и кабинетов обучения', text)
        self.assertIn('по числу стульев', text)
        self.assertIn('1 человека на 6 м²', text)
        self.assertIn('округлением вверх', text)
        self.knowledge.validate_shared_scope(filename, text)
        for mode in ('compliance', 'actualization_protocol', 'stage', 'barrier_assessment'):
            with self.subTest(mode=mode):
                self.assertIn('Расчёт вместимости переговорных и кабинетов обучения',
                              self.knowledge.context_for_mode(mode))

    def test_door_fire_rating_and_self_closing_policy_reaches_every_mode(self):
        heading = 'Обязательное замечание по огнестойкости и самозакрыванию дверей'
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.context_for_mode(mode)
                self.assertIn(heading, context)
                self.assertIn('всегда принимать соответствующее требование невыполненным', context)
                self.assertIn('не доказанная фактическая характеристика', context)
                self.assertIn('Охваченное место повторно не выдавать', context)

    def test_confirmed_passport_positions_have_priority_in_every_mode(self):
        heading = 'Приоритет подтверждённых положений паспорта объекта'
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.context_for_mode(mode)
                self.assertIn(heading, context)
                self.assertIn('обязательной исходной посылкой проверки именно этого объекта', context)
                self.assertIn('не должна молча отменять такое положение', context)
                self.assertIn('любых других норм', context)

        article89 = self.knowledge.context_for_mode('article89')
        self.assertIn('Статус лестниц по паспорту объекта', article89)
        self.assertIn('в том числе открытую лестницу 2-го типа', article89)
        self.assertIn('не выдавать замечание по статье 89 только из-за типа', article89)

    def test_user_replies_do_not_interrupt_work_in_any_mode(self):
        heading = 'Ответы и дополнения не прерывают выполнение'
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.context_for_mode(mode)
                self.assertIn(heading, context)
                self.assertIn('без повторной команды «продолжай»', context)
                self.assertIn('Сам факт отправки вопроса не является основанием остановить всю работу', context)
                self.assertIn('автоматически возобновить проверку с этого места', context)
                self.assertIn('не разрешает выдумывать данные', context)
        self.assertIn('не заканчивать поручение только подтверждением получения ответа',
                      self.knowledge.context_for_mode('compliance'))

    def test_delayed_answers_allow_progress_without_rechecking_closed_items(self):
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.context_for_mode(mode)
                self.assertIn('Ожидание ответов и повторное использование завершённых проверок', context)
                self.assertIn('Пользователь может ответить не сразу', context)
                self.assertIn('при необходимости в следующих блоках', context)
                self.assertIn('Пункт с ожидающим ответом не считать закрытым', context)
                self.assertIn('разрешение не подразумевается молчанием', context)
                self.assertIn('Полностью проверенное замечание или требование повторно не проверять', context)
                self.assertIn('зависимость, выявленная дальнейшей проверкой', context)
                self.assertNotIn('незакрытые пункты запрещают переход к следующему блоку', context)
                self.assertNotIn('Запрещено оставлять их «на потом» и переходить ниже', context)

    def test_corridor_60m_partition_assumption_reaches_all_modes(self):
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.context_for_mode(mode)
                self.assertIn('Принятое условие для деления коридоров на участки не более 60 м', context)
                self.assertIn('обмер длины единого коридора выполнять без учёта разделяющих его участки '
                              'обычных перегородок и дверей', context)
                self.assertIn('это допущение не распространяется на уже предусмотренные '
                              'противопожарные преграды, в том числе более высокого предела '
                              'огнестойкости, которые фактически разделяют коридор', context)
                self.assertIn('Если пользователь не дал отдельных вводных, условно принимать эти '
                              'перегородки и двери не соответствующими требованиям противопожарного '
                              'деления и не засчитывать их как границы нормируемых участков', context)
                self.assertIn('Не запрашивать их противопожарные характеристики для применения этого условия', context)
                self.assertIn('Отдельные прямые вводные пользователя имеют приоритет в явно указанной '
                              'им области; сохранить их источник и проверить относящееся проектное решение', context)
                self.assertIn('не установленная фактическая характеристика конструкций', context)
                self.assertIn('Не переносить это допущение автоматически на другие требования', context)
                self.assertNotIn('всегда принимать показанные перегородки и двери непротивопожарными', context)

    def test_corridor_length_includes_all_connected_sections(self):
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.context_for_mode(mode)
                self.assertIn('учитывать все участки и ответвления единого коридора', context)
                self.assertIn('длину определять суммированием их длин', context)
                self.assertIn('Каждый участок включать в сумму один раз', context)
                self.assertIn('Не складывать длины разных этажей', context)
                self.assertIn('Не выдавать её за дословную цитату СП', context)
                self.assertIn('ранее заданный вопрос закрыть указанием пользователя', context)

    def test_production_height_is_self_assessed_without_questions_in_every_mode(self):
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.context_for_mode(mode)
                self.assertIn('Высота производственных помещений по п. 6.2.2 СП 4 — без запроса', context)
                self.assertIn('Не запрашивать у пользователя сведения о высоте, наличии или регулярности непоказанных проходов', context)
                self.assertIn('не объявлять локальный обмер минимальной высотой всех участков', context)
                self.assertIn('2,2 м до низа выступающих конструкций', context)
                self.assertIn('1,8 м на нерегулярных проходах', context)
                self.assertIn('не отменяет независимые требования и вопросы по другим пунктам', context)

    def test_exit_height_is_self_assessed_without_questions_in_every_mode(self):
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.context_for_mode(mode)
                self.assertIn('Высота эвакуационных выходов по п. 4.2.18 СП 1 — без запроса', context)
                self.assertIn('запрос о высоте выходов не давать', context)
                self.assertIn('Не запрашивать ведомость заполнения проёмов', context)
                self.assertIn('не подтверждение высоты всех непоказанных дверей', context)
                self.assertIn('не снимает независимую проверку ширины', context)

    @staticmethod
    def rule(rule_id, modes=None, replaces=None, status='active'):
        return {'id': rule_id, 'modes': modes or ['compliance'],
                'replaces': replaces or [], 'status': status, 'text': f'TEXT_{rule_id}'}

    def test_both_door_leaves_follow_scoped_customer_input(self):
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                context = self.knowledge.context_for_mode(mode)
                self.assertIn('Учёт открывания обеих створок двупольной двери', context)
                self.assertIn('условие «для данной проверки» не превращать в безусловное допущение для других объектов', context)
                self.assertIn('сумма номинальных ширин полотен не заменяет обмер просвета', context)
                self.assertIn('не повторять уже закрытый вопрос', context)

    def validate_catalogue(self, rules):
        self.knowledge.validate_document('rule_overrides.json', json.dumps({'rules': self.catalogue_fixture(rules)}))

    def catalogue_fixture(self, rules):
        """Keep replacement tests on the current single-source storage format."""
        rules = json.loads(json.dumps(rules))
        path = self.root / 'knowledge/rule_blocks.json'
        registry = json.loads(path.read_text(encoding='utf-8'))
        registry['rules'] = [row for row in registry['rules'] if not row['id'].startswith('test_override_')]
        for index, row in enumerate(rules):
            text = row.get('text')
            if isinstance(text, str) and text.strip():
                key = f'test_override_{index}'
                registry['rules'].append({'id': key, 'text': text, 'modes': list(MODE_FILES)})
                row['text_ref'] = key
                del row['text']
        path.write_text(json.dumps(registry, ensure_ascii=False), encoding='utf-8')
        return rules

    def save_catalogue_fixture(self, rules):
        (self.root / 'knowledge/rule_overrides.json').write_text(
            json.dumps({'rules': self.catalogue_fixture(rules)}, ensure_ascii=False), encoding='utf-8')

    def test_number_variants_and_category_assignments_are_project_facts(self):
        examples = ('Помещение 129.', 'Помещение 1.233 отделить.', 'Помещение №7 принять В3.',
                    'помещении1.233', 'Помещения 02.313, 02.314 отделить.',
                    'Между помещениями 02.311 и 02.313 нужна одна линия.',
                    '02.105 принять категорией В3.', '02.105 принимаем В3.')
        for text in examples:
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, 'действуют для всех объектов'):
                    self.knowledge.validate_document('example.md', text)

    def test_date_and_concrete_parameter_assignments_are_project_facts(self):
        examples = ('Дата ГПЗУ 2026-06-30.', 'Дата ГПЗУ: 30.06.2026.',
                    'Для объекта принять II степень огнестойкости.',
                    'Для данного объекта принять класс С0.', 'Степень огнестойкости: II.',
                    'Высота здания = 11,5 м.', 'Категория помещения: В3.',
                    'Класс функциональной пожарной опасности: Ф5.2.',
                    'Категории утверждены.', 'СТУ нет.')
        for text in examples:
            with self.subTest(text=text):
                self.assertTrue(self.knowledge.scope_issues('example.md', text))

    def test_numbered_remarks_and_room_facts_are_not_shared_rules(self):
        examples = ('для замечаний № 1, № 20 и № 22', 'По замечанию 76 предусмотреть дверь.',
                    'Замечания 76 и 77 выполнить.', 'В замечании №76 указано отделение.',
                    'Между помещениями №1 и №2 провести линию.', 'Для помещения№7 принять В3.')
        for text in examples:
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, 'действуют для всех объектов'):
                    self.knowledge.validate_document('example.md', text)
        for text in ('Для замечаний по п. 5.2.7 СП 2 проверить область применения.',
                     'Проверить пп. 7.1.11 и 5.2.7, таблицу 21 № 123-ФЗ.',
                     'Требование помещения категории В3 определять по норме.',
                     'Порядок проверки замечания\n\n1. Прочитать норму.',
                     'Порядок проверки помещения\n\n1. Проверить границы.'):
            with self.subTest(text=text):
                self.knowledge.validate_shared_scope('example.md', text)

    def test_object_file_paths_are_rejected_but_general_sources_are_allowed(self):
        forbidden = (r'C:\Objects\Factory\plan.dwg', r'C:\Objects\Factory\data.xlsx',
                     r'C:\Objects\Factory\report.pdf', r'C:\Objects\Factory\passport.docx',
                     r'\\server\objects\factory\plan.dxf', '/tmp/objects/factory/report.pdf')
        for text in forbidden:
            with self.subTest(text=text):
                self.assertTrue(self.knowledge.scope_issues('example.md', text))
        allowed = (r'C:\shared\Регламент поиска.docx', r'C:\norms\СП 2.13130.pdf',
                   'https://github.com/NikGlad/fire-docs.git', 'https://example.org/norms/SP2.pdf',
                   'ЗАДАНИЕ_ПО_РЕЗУЛЬТАТАМ_АНАЛИЗА.docx', 'knowledge/rule_overrides.json',
                   '../Все сообщения пользователя из чата.docx', './archive.pdf',
                   '../../archive/messages.docx', r'..\archive\messages.docx')
        for text in allowed:
            with self.subTest(text=text):
                self.assertEqual(self.knowledge.scope_issues('example.md', text), [])

    def test_all_supplied_knowledge_documents_pass_validation(self):
        documents = [path for path in (self.root / 'knowledge').iterdir()
                     if path.is_file() and path.suffix.lower() in {'.md', '.json'}]
        names = {path.name for path in documents}
        self.assertIn('09_ИСТОЧНИКИ_И_ГРАНИЦЫ.md', names)
        self.assertIn('normative_sources.json', names)
        for path in documents:
            with self.subTest(document=path.name):
                self.knowledge.validate_document(path.name, path.read_text(encoding='utf-8-sig'))

    def test_conditions_and_normative_values_remain_valid(self):
        examples = (
            'Если СТУ нет, проверить общие требования.',
            'Если категории утверждены, использовать подтверждённые сведения.',
            'Если степень огнестойкости: II, проверить таблицу 21.',
            'При дате ГПЗУ после 01.01.2026 применять п. 5.2.7 СП 2.',
            'Для зданий высотой 28 м и более проверить предел (R)EI(W) 45.',
            'Для I–III степеней огнестойкости проверить перегородки 1-го типа.',
            'Высота помещения 12 м не является номером.',
            'Площадь помещения 50 м² проверить обмером.',
            'Для неподтверждённых категорий помещений Ф5 принимать В1, но не А и не Б.',
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(self.knowledge.scope_issues('example.md', text), [])

    def test_escaped_json_cannot_hide_project_facts(self):
        text = json.dumps({'instruction': 'Помещение №7 принять В3.'}, ensure_ascii=True)
        with self.assertRaisesRegex(ValueError, 'действуют для всех объектов'):
            self.knowledge.validate_document('example.json', text)
        for value in ({'gpzu': '2026-06-30'}, {'fire_resistance_degree': 'II'}, {'height_m': 11.5}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.knowledge.validate_document('example.json', json.dumps(value))

    def test_invalid_json_and_catalogue_types_raise_clear_errors(self):
        with self.assertRaisesRegex(ValueError, 'некорректный JSON'):
            self.knowledge.validate_document('example.json', '{bad')
        values = ([], None, {}, {'rules': {}}, {'rules': ['not a record']})
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.knowledge.validate_document('rule_overrides.json', json.dumps(value))

    def test_catalogue_fields_are_validated_before_any_write(self):
        path = self.root / 'knowledge/rule_overrides.json'
        original = path.read_bytes()
        examples = (('id', ''), ('id', []), ('status', []), ('status', 'unknown'),
                    ('text', []), ('text', ' '), ('modes', []), ('modes', 'compliance'),
                    ('modes', [[]]), ('modes', ['unknown']), ('modes', ['compliance', 'compliance']),
                    ('replaces', 'old'), ('replaces', [{}]), ('replaces', ['missing']))
        for key, value in examples:
            rule = self.rule('new')
            rule[key] = value
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    self.validate_catalogue([rule])
                self.assertEqual(path.read_bytes(), original)

    def test_duplicate_ids_and_replacements_are_rejected(self):
        for rules in ([self.rule('same'), self.rule('same')],
                      [self.rule('old'), self.rule('new', replaces=['old', 'old'])],
                      [self.rule('self', replaces=['self'])]):
            with self.subTest(rules=rules):
                with self.assertRaises(ValueError):
                    self.validate_catalogue(rules)

    def test_cycles_are_rejected_across_statuses_and_disjoint_modes(self):
        rules = [self.rule('old', ['compliance'], ['new'], 'superseded'),
                 self.rule('new', ['stage'], ['old'])]
        with self.assertRaisesRegex(ValueError, 'циклическое'):
            self.validate_catalogue(rules)

    def test_superseded_rule_cannot_silently_lose_a_mode(self):
        rules = [self.rule('old', ['compliance', 'stage'], status='superseded'),
                 self.rule('new', ['compliance'], ['old'])]
        with self.assertRaisesRegex(ValueError, 'теряет виды работ: stage'):
            self.validate_catalogue(rules)
        rules.append(self.rule('stage_successor', ['stage'], ['old']))
        self.validate_catalogue(rules)

    def test_partial_replacement_preserves_other_modes_of_an_active_rule(self):
        self.save_catalogue_fixture([self.rule('old', ['compliance', 'stage']),
                                     self.rule('new', ['compliance'], ['old'])])
        compliance = self.knowledge.context_for_mode('compliance')
        stage = self.knowledge.context_for_mode('stage')
        self.assertNotIn('TEXT_old', compliance)
        self.assertIn('TEXT_new', compliance)
        self.assertIn('TEXT_old', stage)
        self.assertNotIn('TEXT_new', stage)

    def test_transitive_replacement_does_not_revive_old_text(self):
        rules = [self.rule('old'), self.rule('middle', replaces=['old'], status='superseded'),
                 self.rule('latest', replaces=['middle'])]
        self.save_catalogue_fixture(rules)
        context = self.knowledge.context_for_mode('compliance')
        self.assertNotIn('TEXT_old', context)
        self.assertNotIn('TEXT_middle', context)
        self.assertIn('TEXT_latest', context)

    def test_snapshot_reads_every_included_file_once_and_tracks_json_sources(self):
        original_read = Path.read_bytes
        calls = Counter()

        def read_bytes(path):
            calls[path.name] += 1
            return original_read(path)

        with patch.object(Path, 'read_bytes', read_bytes):
            snapshot = self.knowledge.snapshot_for_mode('compliance')
        names = [doc.name for doc in snapshot.documents]
        self.assertIn('room_classification.json', names)
        self.assertIn('rule_overrides.json', names)
        self.assertEqual(calls, Counter({name: 1 for name in names}))
        barrier = self.knowledge.snapshot_for_mode('barrier_assessment')
        self.assertNotIn('room_classification.json', [doc.name for doc in barrier.documents])
        self.assertIn('barrier_drawing_profile.json', [doc.name for doc in barrier.documents])

    def test_snapshot_is_immutable_and_self_consistent_after_source_change(self):
        snapshot = self.knowledge.snapshot_for_mode('compliance')
        doc = next(item for item in snapshot.documents if item.name == 'rule_blocks.json')
        original_digest = snapshot.digest
        registry = json.loads(doc.text)
        included_id = next(key for kind, key in RuleResolver.references(snapshot.documents[0].text) if kind == 'rule')
        next(row for row in registry['rules'] if row['id'] == included_id)['text'] += '\nSHARED_REVISION_MARKER\n'
        doc.path.write_text(json.dumps(registry, ensure_ascii=False), encoding='utf-8')
        self.assertNotIn('SHARED_REVISION_MARKER', snapshot.context)
        self.assertEqual(snapshot.digest, hashlib.sha256(snapshot.context.encode('utf-8')).hexdigest())
        self.assertNotEqual(original_digest, self.knowledge.snapshot_for_mode('compliance').digest)
        with self.assertRaises(FrozenInstanceError):
            snapshot.context = 'changed'
        with self.assertRaises(FrozenInstanceError):
            doc.text = 'changed'

    def test_load_document_hashes_the_same_bytes_that_it_decodes(self):
        path = self.root / 'knowledge/example.md'
        raw = b'\xef\xbb\xbfline one\r\nline two\r\n'
        path.write_bytes(raw)
        doc = self.knowledge.load_document('example.md')
        self.assertEqual(doc.text, 'line one\nline two\n')
        self.assertEqual(doc.sha256, hashlib.sha256(raw).hexdigest())

    def test_all_modes_include_shared_policy_and_compatible_wrappers(self):
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                snapshot = self.knowledge.snapshot_for_mode(mode)
                self.assertEqual(snapshot.mode, mode)
                self.assertIn('25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md', [d.name for d in snapshot.documents])
                self.assertEqual(self.knowledge.context_for_mode(mode), snapshot.context)
                self.assertEqual(self.knowledge.digest_for_mode(mode), snapshot.digest)
                self.assertEqual(self.knowledge.instructions_pending(mode), snapshot.instructions_pending)

    def test_context_and_editor_share_the_same_validation(self):
        rules = [self.rule('invalid', ['no-such-mode'])]
        text = json.dumps({'rules': rules})
        with self.assertRaises(ValueError) as editor_error:
            self.knowledge.validate_document('rule_overrides.json', text)
        self.save_catalogue_fixture(rules)
        with self.assertRaises(ValueError) as snapshot_error:
            self.knowledge.snapshot_for_mode('compliance')
        self.assertEqual(str(editor_error.exception), str(snapshot_error.exception))


if __name__ == '__main__':
    unittest.main()
