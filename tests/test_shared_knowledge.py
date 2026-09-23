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


class SharedKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / 'knowledge', self.root / 'knowledge')
        self.knowledge = KnowledgeBase(AppPaths(self.root))

    @staticmethod
    def rule(rule_id, modes=None, replaces=None, status='active'):
        return {'id': rule_id, 'modes': modes or ['compliance'],
                'replaces': replaces or [], 'status': status, 'text': f'TEXT_{rule_id}'}

    def validate_catalogue(self, rules):
        self.knowledge.validate_document('rule_overrides.json', json.dumps({'rules': rules}))

    def save_catalogue_fixture(self, rules):
        (self.root / 'knowledge/rule_overrides.json').write_text(
            json.dumps({'rules': rules}, ensure_ascii=False), encoding='utf-8')

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
                self.knowledge.validate_document('example.md', text)

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
        doc = snapshot.documents[0]
        original_digest = snapshot.digest
        doc.path.write_text(doc.text + '\nSHARED_REVISION_MARKER\n', encoding='utf-8')
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
