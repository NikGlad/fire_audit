import unittest
from copy import deepcopy

from fire_audit.annotation_revisions import changed_input_revision_state
from fire_audit.protocol_validation import validate_protocol_registry


class InputRevisionTests(unittest.TestCase):
    def fixture(self):
        old = 'Предусмотреть 6 м. Вариант: выполнить. Ответ заказчика: прежние данные.'
        reason = '01.01.2025 пояснение пожарного консультанта: Замечание изменено в связи с уточнением вводных.'
        appendix = r'\P{\C30;' + reason + '}'
        new = old.replace('6 м', r'{\C3;8 м}') + appendix
        props = dict(layer='экспертиза', colorIndex=1, style='Standard', rotation=0)
        main = dict(props, handle='T', type='MText', contents=old, location={'x': 0, 'y': 0},
                    width=100, height=3, actualWidth=100, actualHeight=40)
        number = dict(props, handle='N', type='DBText', textString='Замечание №1', position={'x': 0, 'y': 5})
        frame = dict(props, handle='F', type='Polyline', closed=True,
                     vertices=[{'x': 0, 'y': 5}, {'x': 100, 'y': 5}, {'x': 100, 'y': 10}, {'x': 0, 'y': 10}])
        ray = dict(props, handle='R', type='Line', startPoint={'x': 100, 'y': 5}, endPoint={'x': 500, 'y': 500})
        explanation = dict(deepcopy(main), handle='E', layer='экспертиза пояснения', contents='Характеристика: прежняя.')
        before = [main, number, frame, ray, explanation]; after = deepcopy(before)
        for e in after[:3]:
            for p in e.get('vertices', [e.get('location', e.get('position'))]):
                p['x'] += 10; p['y'] += 20
        after[0]['contents'] = new
        after[3]['startPoint'] = {'x': 110, 'y': 25}
        after[4]['contents'] = 'Характеристика: новая.'
        inputs = [{'parameter': 'Характеристика', 'before': 'Прежняя', 'after': 'Новая', 'source': 'Указание исполнителя'}]
        evidence = dict(revision_kind='changed_inputs', user_authorized=True, fresh_read_verified=True,
                        history_preserved=True, reason='Новые вводные', normative_basis='Проверенная норма',
                        norms_reread=True, normative_edition='Редакция по дате проверки', changed_inputs=inputs)
        revision = dict(evidence, entity_id='T', number=1, before=old, after=new,
                        consultant_explanation=reason, consultant_appendix=appendix,
                        replacements=[{'from': '6 м', 'to': r'{\C3;8 м}'}])
        extra = dict(evidence, entity_id='E', before=explanation['contents'], after=after[4]['contents'],
                     dependency_reference='Характеристика в паспорте', replacements=[{'from': 'прежняя', 'to': 'новая'}])
        layout = dict(main_text_id='T', reason='Устранить наложение', authorization_reference='Правило оформления',
                      fresh_read_verified=True, visual_review_verified=True)
        item = {'source': {'main_text_id': 'T', 'number_id': 'N', 'frame_id': 'F', 'original_number': 1,
                           'entity_ids': ['T', 'N', 'F', 'R'], 'full_text': old, 'issued_group': True, 'inventory_complete': True},
                'response': {'status': 'not_required', 'entity_ids': []},
                'consultant': {'comment_required': False, 'entity_ids': [], 'omission_reason': 'reply_not_required'},
                'transition': {'kind': 'none', 'entity_ids': []}, 'input_revision_explanation': appendix}
        return {'mode': 'protocol', 'graphics_before': before, 'verified_graphics': after,
                'changed_entity_ids': ['T', 'N', 'F', 'R', 'E'], 'created_entity_ids': [], 'deleted_entity_ids': [],
                'targeted_source_text_revisions': [revision], 'explanation_text_revisions': [extra],
                'annotation_layout_revisions': [dict(layout, kind='group_translation', entity_ids=['T', 'N', 'F'], delta={'x': 10, 'y': 20}),
                                                dict(layout, kind='ray_origin', entity_ids=['R'])],
                'final_numbering': {'status': 'preserved'},
                'graphics_layer_check': dict(project_geometry_unchanged=True, original_texts_and_numbers_unchanged=False,
                                            no_entities_deleted=True, changes_re_read=True),
                'protocol': {'scope_policy_version': 2, 'scope_source': {'kind': 'user_changed_inputs', 'reference': 'Указание'},
                             'scope_main_text_ids': ['T'], 'scope_entries': [{'main_text_id': 'T', 'inclusion_reason': 'Новые вводные',
                                                                           'source_reference': 'Указание', 'reply_required': False}],
                             'items': [item], 'changed_input_review': {'author_role': 'contractor', 'instruction_reference': 'Указание',
                                'impact_matrix_reference': 'Матрица', 'all_dependencies_reviewed': True}}}

    def test_documented_input_changes_and_layout_close_without_fake_customer_response(self):
        r = self.fixture()
        result = validate_protocol_registry(r, True)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(set(r['changed_entity_ids']), changed_input_revision_state(r)['entity_ids'])

    def test_incomplete_group_and_changed_ray_target_rejected(self):
        for case in ('partial_group', 'wrong_delta', 'target', 'unrelated', 'missing_snapshot', 'unverified', 'far_origin', 'extra_vertex', 'bulge'):
            r = self.fixture()
            if case == 'partial_group': r['annotation_layout_revisions'][0]['entity_ids'].remove('N')
            if case == 'wrong_delta': r['verified_graphics'][1]['position']['x'] += 1
            if case == 'target': r['verified_graphics'][3]['endPoint']['x'] += 1
            if case == 'unrelated': r['changed_entity_ids'].append('PROJECT_WALL')
            if case == 'missing_snapshot': r['graphics_before'].pop()
            if case == 'unverified': r['annotation_layout_revisions'][0]['visual_review_verified'] = False
            if case == 'far_origin': r['verified_graphics'][3]['startPoint']['x'] = 999
            if case == 'extra_vertex': r['verified_graphics'][2]['vertices'].append({'x': 10, 'y': 20})
            if case == 'bulge': r['verified_graphics'][2]['vertices'][0]['bulge'] = .5
            with self.subTest(case=case): self.assertFalse(validate_protocol_registry(r, True)['ok'])

    def test_numbers_history_styles_layers_and_undocumented_edits_stay_protected(self):
        for case in ('number', 'history', 'layer', 'style', 'extra_text', 'extra_layer', 'created', 'deleted', 'comment_color'):
            r = self.fixture()
            if case == 'number': r['verified_graphics'][1]['textString'] = 'Замечание №2'
            if case == 'history':
                rev = r['targeted_source_text_revisions'][0]
                rev['replacements'].append({'from': 'прежние данные', 'to': 'новые данные'})
                rev['after'] = rev['after'].replace('прежние данные', 'новые данные')
                r['verified_graphics'][0]['contents'] = rev['after']
            if case == 'layer': r['verified_graphics'][0]['layer'] = 'экспертиза устранено'
            if case == 'style': r['verified_graphics'][0]['style'] = 'Other'
            if case == 'extra_text': r['verified_graphics'][4]['contents'] += ' Лишний текст.'
            if case == 'extra_layer': r['graphics_before'][4]['layer'] = r['verified_graphics'][4]['layer'] = 'Проект'
            if case == 'created': r['created_entity_ids'] = ['NEW']
            if case == 'deleted': r['deleted_entity_ids'] = ['OLD']
            if case == 'comment_color': r['targeted_source_text_revisions'][0]['consultant_appendix'] = 'не оранжевый'
            with self.subTest(case=case): self.assertFalse(validate_protocol_registry(r, True)['ok'])

    def test_missing_input_review_does_not_authorize_edits(self):
        for field in ('author_role', 'instruction_reference', 'impact_matrix_reference', 'all_dependencies_reviewed'):
            r = self.fixture(); r['protocol']['changed_input_review'].pop(field)
            with self.subTest(field=field): self.assertFalse(validate_protocol_registry(r, True)['ok'])

    def test_no_layout_journal_never_authorizes_movement(self):
        r = self.fixture(); r['annotation_layout_revisions'] = []
        self.assertFalse(validate_protocol_registry(r, True)['ok'])

    def test_forged_customer_response_does_not_use_input_exception(self):
        r = self.fixture(); r['protocol']['items'][0]['response']['status'] = 'received'
        self.assertFalse(validate_protocol_registry(r, True)['ok'])

    def test_malformed_new_journal_records_are_errors(self):
        for field in ('targeted_source_text_revisions', 'explanation_text_revisions', 'annotation_layout_revisions'):
            for value in (None, [None], 'invalid'):
                r = self.fixture(); r[field] = value
                with self.subTest(field=field, value=value):
                    self.assertTrue(changed_input_revision_state(r)['errors'])
