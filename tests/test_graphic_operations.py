import unittest
from copy import deepcopy
from fire_audit.graphic_operations import graphic_operation_state
from fire_audit.actualization_validation import validate_actualization_registry
from test_actualization_validation import ActualizationValidationTests


class GraphicOperationsTests(unittest.TestCase):
    def fixture(self):
        r = ActualizationValidationTests().fixture()
        f = r['findings'][0]
        f['status'] = f['targets'][0]['status'] = 'unresolved'
        for e in r['verified_graphics']:
            e.update(layer='экспертиза', type='Line')
        r['graphics_before'] = deepcopy(r['verified_graphics'])
        f['changed_entity_ids'] = r['changed_entity_ids'] = ['R']
        r['graphic_operations'] = {'version': 1, 'items': [{
            'entity_id': 'R', 'kind': 'layout', 'rule_reference': 'layout_rule',
            'reason': 'Retarget to verified opening', 'evidence_reference': 'readback.json',
            'second_review_confirmed': True}]}
        return r

    def test_layout_edit_does_not_resolve_target(self):
        r = self.fixture()
        result = validate_actualization_registry(r, True)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(r['findings'][0]['status'], 'unresolved')

    def test_legacy_registry_keeps_old_validation(self):
        r = self.fixture()
        del r['graphic_operations']
        self.assertFalse(validate_actualization_registry(r, True)['ok'])

    def test_layout_cannot_change_layer_or_requirement_text(self):
        for key, value in [('layer', 'экспертиза устранено'), ('contents', 'Changed requirement')]:
            r = self.fixture()
            r['verified_graphics'][3][key] = value
            self.assertFalse(validate_actualization_registry(r, True)['ok'])

    def test_layout_requires_prior_expert_entity_and_evidence(self):
        for change in ['project_layer', 'missing_before', 'missing_evidence', 'duplicate']:
            r = self.fixture()
            if change == 'project_layer':
                r['graphics_before'][3]['layer'] = r['verified_graphics'][3]['layer'] = 'Walls'
            elif change == 'missing_before':
                r['graphics_before'] = []
            elif change == 'missing_evidence':
                r['graphic_operations']['items'][0]['evidence_reference'] = ''
            else:
                r['graphic_operations']['items'] *= 2
            self.assertFalse(validate_actualization_registry(r, True)['ok'])

    def test_measurement_reuse_requires_dimension_and_project_proof(self):
        r = self.fixture(); op = r['graphic_operations']['items'][0]
        op.update(kind='measurement', reused_for_added_target=True,
                  measurement_evidence={'project_entity_ids':['W'], 'method':'between faces', 'result':'verified'})
        for e in (r['graphics_before'][3], r['verified_graphics'][3]):e['type'] = 'AlignedDimension'
        self.assertEqual(graphic_operation_state(r)['reused'], {'R'})
        op['measurement_evidence']['project_entity_ids'] = []
        self.assertTrue(graphic_operation_state(r)['errors'])

    def test_author_comment_cannot_replace_source_text(self):
        r = self.fixture(); op = r['graphic_operations']['items'][0]
        op.update(entity_id='T',kind='author_comment',author_role='consultant',text='29.10.2026 пояснения пожарного консультанта: Проверка')
        r['created_entity_ids']=['T']
        r['verified_graphics'][0].update(type='MText',colorIndex=30,contents=op['text'])
        self.assertTrue(graphic_operation_state(r)['errors'])

    def test_new_author_comment_is_checked_for_role_and_exact_text(self):
        r = self.fixture(); op = r['graphic_operations']['items'][0]
        op.update(entity_id='C',kind='author_comment',author_role='consultant',text='29.10.2026 пояснения пожарного консультанта: Проверка')
        r['created_entity_ids']=['C']
        r['verified_graphics'].append(dict(r['verified_graphics'][0], handle='C',type='MText',colorIndex=30,contents=op['text']))
        self.assertEqual(graphic_operation_state(r)['comments'], {'C'})
        r['verified_graphics'][-1]['colorIndex']=3
        self.assertTrue(graphic_operation_state(r)['errors'])

    def test_conditional_requirement_needs_applicability_and_missing_detail(self):
        r = self.fixture(); t=r['findings'][0]['targets'][0]
        t.update(origin='added_target',status='insufficient_data',general_requirement={
            'rule_reference':'doc_03_029','applicable_object_fact':'Confirmed barrier type',
            'missing_design_detail':'Protection material','not_claimed_as_measured_violation':True,
            'source_reference':'approved_requirement'})
        self.assertEqual(graphic_operation_state(r)['conditional'], {('T','R')})
        del t['general_requirement']['applicable_object_fact']
        self.assertFalse(graphic_operation_state(r)['conditional'])

    def test_correction_requires_prior_review_and_compliant_measurement(self):
        r=self.fixture(); t=r['findings'][0]['targets'][0]
        t.update(origin='added_target',status='resolved',review_correction={
            'rule_reference':'error_ro_06','previous_registry_reference':'previous.json',
            'previous_status':'unresolved','measured_mm':1500,'required_mm':1400,'method':'independent remeasurement'})
        self.assertEqual(graphic_operation_state(r)['corrected'],{('T','R')})
        t['review_correction']['measured_mm']=1300
        self.assertFalse(graphic_operation_state(r)['corrected'])

    def test_new_measurement_remains_active_with_unresolved_owner(self):
        r = self.fixture()
        r['created_entity_ids'] = ['D']
        r['verified_graphics'].append(dict(r['verified_graphics'][0], handle='D', type='AlignedDimension'))
        r['graphic_operations']['items'][0].update(entity_id='D', kind='target_measurement')
        t = r['findings'][0]['targets'][0]
        t.update(graphic_ids=['D'], evidence={'project_entity_ids':['W'], 'method':'between faces',
                                            'result':'measured', 'normative_basis':['applicable rule']})
        self.assertEqual(graphic_operation_state(r)['active'], {'D'})
        t['status'] = 'resolved'
        self.assertTrue(graphic_operation_state(r)['errors'])

    def test_new_measurement_rejects_missing_owner(self):
        r = self.fixture()
        r['created_entity_ids'] = ['D']
        r['verified_graphics'].append(dict(r['verified_graphics'][0], handle='D', type='AlignedDimension'))
        r['graphic_operations']['items'][0].update(entity_id='D', kind='target_measurement')
        self.assertTrue(graphic_operation_state(r)['errors'])
