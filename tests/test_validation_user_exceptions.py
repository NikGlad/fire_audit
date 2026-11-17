import copy
import unittest

from fire_audit.validation import (
    _editorial_source, _issued_graphics_required, _linked_group_errors,
    _source_layer_for_graphic, _validate_compliance_source_copy,
    _validate_created_graphics, _validate_individual_analysis,
    validate_finding, validate_registry, validate_source_reading,
)


def graphic(role, entity_id):
    layer = 'экспертиза пояснения' if role == 'purple_explanation' else 'экспертиза'
    before = dict(layer=layer, color_by_layer=True, linetype_by_layer=True, lineweight_by_layer=True)
    return dict(entity_id=entity_id, role=role, action='reused', source_kind='existing_expert_graphic',
                original_layer=layer, existing_object_verified=True, properties_re_read=True,
                properties_before=before, properties_after={'layer': layer}, changes=[],
                entity_type='MText' if role != 'frame' else 'Polyline', floor='1',
                related_finding_id='owner', purpose='Проверенная существующая группа',
                text_style='Standard', contents=role, **before)


def reused_group():
    rows = [graphic(role, 'id-' + role) for role in ['main_text', 'number', 'frame', 'purple_explanation']]
    text_ids = ['template-main', 'template-number', 'template-purple']
    return {'id': 'owner', 'mode': 'compliance', 'decision': {'status': 'violation'},
            'output': {'final_numbers': [3]},
            'autocad': {'floor': '1', 'status': 'post_checked', 'placement': {'sequence_index': 3},
                        'created_objects': [], 'reused_objects': rows,
                        'layer_verification': {'all_created_objects_collected': True, 'checked_against_autocad': True,
                            'text_parts_separated_by_layer': True,
                            'all_violation_graphics_on_expertise_layer': True,
                            'all_purple_explanations_on_expertise_explanations_layer': True},
                        'source_group': {
                            'source_template_key': 'template', 'source_group_fully_detected': True,
                            'left_explanation_checked': True, 'left_explanation_position_verified': True,
                            'left_explanation_present': True, 'left_explanation_entity_ids': ['template-purple'],
                            'purple_explanation_present': True, 'purple_explanation_entity_ids': ['template-purple'],
                            'remark_text_entity_ids': ['template-main'], 'number_entity_ids': ['template-number'],
                            'frame_entity_ids': ['template-frame'], 'source_floor': 'template area', 'new_floor': '1',
                            'copy_case': 'reuse_existing_group', 'copy_source': 'existing_result_group',
                            'copy_method': 'reuse_verified_group', 'manual_text_creation_used': False,
                            'text_styles_preserved': True, 'relative_positions_preserved': True,
                            'left_explanation_copied_when_present': False, 'created_copy_entity_ids': [],
                            'source_text_styles': [{'entity_id': i, 'text_style': 'Standard'} for i in text_ids]}}}


class EditorialSourceTests(unittest.TestCase):
    def heading(self):
        return {'id': 'heading', 'source_text_id': 'H', 'source_text': 'Освещение лестничных клеток',
                'source_classification': {'kind': 'editorial_heading', 'verified': True,
                    'reason': 'Заголовок раздела; самостоятельного требования нет', 'evidence': 'catalogue:H'},
                'decision': {'status': 'not_applicable'}, 'analysis': {'source_reading': {}}, 'output': {}}

    def test_heading_needs_no_invented_norm_pages(self):
        f = self.heading()
        self.assertTrue(_editorial_source(f))
        self.assertEqual(validate_source_reading(f), [])
        errors = _validate_individual_analysis(f)
        self.assertFalse(any('прочитанные страницы' in e or 'связанные положения' in e for e in errors))
        self.assertTrue(any('индивидуального нормативного анализа' in e for e in errors))

    def test_editorial_note_and_separator_are_supported(self):
        for kind in ['editorial_note', 'editorial_separator']:
            f = self.heading(); f['source_classification']['kind'] = kind
            self.assertTrue(_editorial_source(f))

    def test_bad_classification_does_not_skip_reading(self):
        for key, value in [('verified', False), ('reason', ''), ('evidence', ''), ('kind', 'norm')]:
            f = self.heading(); f['source_classification'][key] = value
            self.assertFalse(_editorial_source(f))
            self.assertTrue(validate_source_reading(f))

    def test_legal_requirement_cannot_be_relabelled_heading(self):
        for text in ['Выход предусмотреть (ст. 40 ч. 3 ФЗ №123)', 'Требование по п. 4.4.12 СП 1', 'Статья 89. Выходы']:
            f = self.heading(); f['source_text'] = text
            self.assertFalse(_editorial_source(f))
            self.assertTrue(validate_source_reading(f))

    def test_issued_or_graphic_output_cannot_be_editorial_exemption(self):
        for key in ['issued', 'is_general_requirement', 'remark_text']:
            f = self.heading(); f['output'][key] = True
            self.assertFalse(_editorial_source(f))
        f = self.heading(); f['autocad'] = {'ray_entity_ids': ['ray']}
        self.assertFalse(_editorial_source(f))


class ReusedGroupTests(unittest.TestCase):
    def test_verified_complete_group_does_not_need_fictitious_copy(self):
        f = reused_group()
        self.assertEqual(_validate_compliance_source_copy(f), [])
        self.assertEqual(_validate_created_graphics(f), [])

    def test_missing_group_members_and_reread_are_rejected(self):
        for role in ['main_text', 'number', 'frame', 'purple_explanation']:
            f = reused_group()
            f['autocad']['reused_objects'] = [r for r in f['autocad']['reused_objects'] if r['role'] != role]
            self.assertTrue(_validate_compliance_source_copy(f))
        f = reused_group(); f['autocad']['reused_objects'][0]['properties_re_read'] = False
        self.assertTrue(_validate_compliance_source_copy(f))

    def test_reuse_cannot_claim_created_core_or_fake_method(self):
        f = reused_group(); f['autocad']['source_group']['created_copy_entity_ids'] = ['fake']
        self.assertTrue(_validate_compliance_source_copy(f))
        f = reused_group(); f['autocad']['created_objects'] = [graphic('main_text', 'new')]
        self.assertTrue(_validate_compliance_source_copy(f))
        f = reused_group(); f['autocad']['source_group']['copy_method'] = 'manual'
        self.assertTrue(_validate_compliance_source_copy(f))

    def linked(self):
        owner = reused_group(); linked = copy.deepcopy(owner); linked['id'] = 'dependent'
        linked['autocad']['linked_finding_id'] = 'owner'
        linked['output']['linked_finding_id'] = 'owner'
        for row in linked['autocad']['reused_objects']: row['related_finding_id'] = 'dependent'
        return owner, linked

    def test_same_group_counts_once_even_with_forward_reference(self):
        owner, linked = self.linked()
        self.assertEqual(_linked_group_errors(linked, {'owner': owner}), [])
        for findings in [[owner, linked], [linked, owner]]:
            errors = validate_registry({'findings': findings}, for_application=True)['errors']
            self.assertFalse(any('повторяются номера' in e or 'связь с существующей' in e for e in errors))

    def test_distinct_groups_with_duplicate_sequence_still_fail(self):
        owner, other = self.linked(); del other['autocad']['linked_finding_id']; del other['output']['linked_finding_id']
        for row in other['autocad']['reused_objects']: row['entity_id'] += '-other'
        errors = validate_registry({'findings': [owner, other]}, for_application=True)['errors']
        self.assertTrue(any('повторяются номера' in e for e in errors))

    def test_link_must_match_existing_floor_handles_properties_and_number(self):
        owner, linked = self.linked()
        self.assertTrue(_linked_group_errors(linked, {}))
        for change in ['floor', 'handle', 'property', 'number', 'sequence', 'cycle']:
            a, b = self.linked()
            if change == 'floor': b['autocad']['floor'] = '2'
            if change == 'handle': b['autocad']['reused_objects'][0]['entity_id'] = 'other'
            if change == 'property': b['autocad']['reused_objects'][0]['contents'] = 'different'
            if change == 'number': b['output']['final_numbers'] = [4]
            if change == 'sequence': b['autocad']['placement']['sequence_index'] = 4
            if change == 'cycle': a['autocad']['linked_finding_id'] = 'dependent'
            self.assertTrue(_linked_group_errors(b, {'owner': a}), change)


class MeasurementLabelTests(unittest.TestCase):
    def test_standalone_text_stays_on_measurement_layer_with_ray(self):
        f = {'autocad': {'target_entity_ids': ['label'], 'ray_entity_ids': ['ray']}}
        item = {'entity_id': 'label', 'role': 'measurement_text', 'entity_type': 'DBText',
                'source_entity_id': 'main', 'source_layer': 'экспертиза'}
        self.assertEqual(_source_layer_for_graphic(item, f), 'экспертиза обмеры')

    def test_native_dimension_cannot_be_disguised_as_label(self):
        self.assertIsNone(_source_layer_for_graphic({'role': 'measurement_text', 'entity_type': 'AlignedDimension'}, {}))
        self.assertEqual(_source_layer_for_graphic({'role': 'dimension', 'entity_type': 'AlignedDimension'}, {}), 'экспертиза')

    def test_wrong_label_layer_is_still_reported(self):
        item = graphic('measurement_text', 'label'); item['entity_type'] = 'DBText'
        item['action'] = 'created'
        f = {'id': 'owner', 'autocad': {'created_objects': [item]}}
        self.assertTrue(any('требуется точный слой экспертиза обмеры' in e for e in _validate_created_graphics(f)))


class MandatoryStairwellTests(unittest.TestCase):
    def requirement(self, rule='doc_03_061'):
        return {'id': 'stair', 'mode': 'compliance', 'source_text': 'Требование лестничной клетки',
                'norm': {'document': 'СП 1.13130.2020', 'point': '4.4.9' if rule == 'doc_03_060' else '4.4.12'},
                'decision': {'status': 'insufficient_data'}, 'applicability': {'status': 'applicable'},
                'output': {'issued': True, 'is_general_requirement': True, 'actual_violation_asserted': False,
                    'mandatory_requirement': {'rule_id': rule, 'scope': 'stairwell_equipment',
                        'applicability_verified': True, 'unknown_characteristic_not_asserted_as_defect': True,
                        'reason': 'Обязательная выдача по правилу пользователя'}},
                'autocad': {'target_floor': '2', 'ray_required': True, 'ray_entity_ids': ['R'],
                            'target_entity_ids': ['C'], 'reused_objects': [{'entity_id': 'C', 'role': 'cloud'}]}}

    def test_both_mandatory_rules_issue_without_inventing_violation(self):
        for rule in ['doc_03_060', 'doc_03_061']:
            f = self.requirement(rule)
            self.assertTrue(_issued_graphics_required(f))
            errors = validate_finding(f, for_application=True)[0]
            self.assertFalse(any('графика запрещена' in e or 'луч строить запрещено' in e for e in errors))
            self.assertTrue(any('полное чтение' in e for e in errors))
            self.assertEqual(f['decision']['status'], 'insufficient_data')

    def test_unverified_or_unrelated_policy_cannot_issue(self):
        for key, value in [('rule_id', 'invented'), ('scope', 'population'), ('reason', ''),
                           ('applicability_verified', False), ('unknown_characteristic_not_asserted_as_defect', False)]:
            f = self.requirement(); f['output']['mandatory_requirement'][key] = value
            self.assertFalse(_issued_graphics_required(f))
        f = self.requirement(); f['output']['actual_violation_asserted'] = True
        self.assertFalse(_issued_graphics_required(f))
        f = self.requirement(); f['norm']['point'] = '8.2.7'
        self.assertFalse(_issued_graphics_required(f))

    def test_stairwell_pointer_requires_second_floor_cloud(self):
        f = self.requirement(); f['autocad']['target_floor'] = '1'
        self.assertTrue(any('на втором этаже' in e for e in validate_finding(f)[0]))
        f = self.requirement(); f['autocad']['target_entity_ids'] = ['door']
        self.assertTrue(any('проверенное облако' in e for e in validate_finding(f)[0]))


if __name__ == '__main__':
    unittest.main()
