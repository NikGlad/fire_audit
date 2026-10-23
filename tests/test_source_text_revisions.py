import unittest
from copy import deepcopy
from fire_audit.source_text_revisions import authorized_source_text_revisions


class SourceTextRevisionTests(unittest.TestCase):
    def fixture(self):
        old = 'Предусмотреть 6 м. Варианты решения: 1. Выполнить. Ответ заказчика: принято.'
        new = old.replace('6 м', '8 м')
        a = {'handle': 'T', 'type': 'MText', 'contents': old, 'location': {'x': 0, 'y': 0}}
        return {'protocol': {'items': [{'source': {'main_text_id': 'T', 'original_number': 1,
                    'entity_ids': ['T', 'N'], 'full_text': old}}]},
                'graphics_before': [a, {'handle': 'N', 'textString': 'Замечание №1'}],
                'verified_graphics': [{**a, 'contents': new}, {'handle': 'N', 'textString': 'Замечание №1'}],
                'changed_entity_ids': ['T'], 'targeted_source_text_revisions': [{'entity_id': 'T', 'number': 1,
                    'user_authorized': True, 'fresh_read_verified': True, 'history_preserved': True,
                    'reason': 'Новые исходные данные', 'normative_basis': 'Проверенная нормативная таблица',
                    'before': old, 'after': new, 'replacements': [{'from': '6 м', 'to': '8 м'}]}]}

    def test_precise_numeric_update(self):
        self.assertTrue(authorized_source_text_revisions(self.fixture()))

    def test_evidence_is_required(self):
        for field in ['user_authorized', 'fresh_read_verified', 'history_preserved', 'normative_basis', 'reason']:
            r = self.fixture(); r['targeted_source_text_revisions'][0][field] = False
            with self.subTest(field=field): self.assertFalse(authorized_source_text_revisions(r))

    def test_unlogged_changes_and_geometry_are_rejected(self):
        for kind in ['number', 'location', 'contents', 'missing', 'duplicate']:
            r = self.fixture()
            if kind == 'number': r['verified_graphics'][1]['textString'] = 'Замечание №2'
            if kind == 'location': r['verified_graphics'][0]['location'] = {'x': 1, 'y': 0}
            if kind == 'contents': r['verified_graphics'][0]['contents'] += ' Дописано'
            if kind == 'missing': r['verified_graphics'].pop()
            if kind == 'duplicate': r['targeted_source_text_revisions'] *= 2
            with self.subTest(kind=kind): self.assertFalse(authorized_source_text_revisions(r))

    def test_variants_and_customer_reply_are_protected(self):
        r = self.fixture(); item = r['targeted_source_text_revisions'][0]
        item['replacements'] = [{'from': '1. Выполнить', 'to': '2. Не выполнять'}]
        item['after'] = item['before'].replace('1. Выполнить', '2. Не выполнять')
        r['verified_graphics'][0]['contents'] = item['after']
        self.assertFalse(authorized_source_text_revisions(r))

    def test_malformed_or_unread_records_rejected(self):
        for value in [None, [], [None], [{'entity_id': 'UNKNOWN'}]]:
            r = self.fixture(); r['targeted_source_text_revisions'] = value
            with self.subTest(value=value): self.assertFalse(authorized_source_text_revisions(r))

    def test_both_work_modes_require_precise_revision_evidence(self):
        from tests.test_protocol_validation import ProtocolValidationTests
        from fire_audit.validation import validate_registry
        fixtures = ProtocolValidationTests()
        for mode in ('protocol', 'actualization_protocol'):
            r = fixtures.fixture() if mode == 'protocol' else fixtures.actualization_fixture()
            sample = self.fixture(); revision = deepcopy(sample['targeted_source_text_revisions'][0])
            revision['number'] = 19
            for field, text in [('graphics_before', revision['before']), ('verified_graphics', revision['after'])]:
                next(e for e in r[field] if e['handle'] == 'T').update(type='MText', contents=text)
            r['protocol']['items'][0]['source']['full_text'] = revision['before']
            r['targeted_source_text_revisions'] = [revision]
            r['graphics_layer_check']['original_texts_and_numbers_unchanged'] = False
            if mode == 'actualization_protocol': r['graphics_layer_check']['excluded_frame_unchanged'] = False
            with self.subTest(mode=mode):
                result = validate_registry(r, True)
                self.assertTrue(result['ok'], result['errors'])
                r['targeted_source_text_revisions'] = []
                self.assertFalse(validate_registry(r, True)['ok'])
