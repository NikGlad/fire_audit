import copy
import unittest

from fire_audit.validation import (
    _preserved_stage_yellow_graphic, _preserved_common_system_text,
    _validate_individual_analysis, validate_finding,
)


class PreservationValidationTests(unittest.TestCase):
    def fixture(self):
        source = 'Общее исходное требование'
        before = dict(layer='экспертиза', type='MText', color='ByLayer', colorIndex=256,
                      color_by_layer=True, linetype='ByLayer', linetype_by_layer=True,
                      lineWeight='LineWeight000', lineweight_by_layer=False,
                      contents=source, location={'x': 1, 'y': 2})
        row = dict(before, entity_id='T', role='main_text', original_layer='экспертиза',
                   existing_object_verified=True, properties_re_read=True,
                   properties_before=copy.deepcopy(before), action='modified')
        row.update(color='Yellow', colorIndex=2, color_by_layer=False)
        row['changes'] = [dict(property=k, before=v, after=row[k])
                          for k, v in before.items() if row[k] != v]
        finding = dict(id='F', mode='stage', source_text_id='T', source_text=source,
                       decision={'status': 'not_applicable', 'reason': 'Не применяется'},
                       output={'stage_is_issued': False},
                       autocad={'reused_objects': [row], 'by_layer': False})
        return finding, row

    def test_external_yellow_preserves_non_by_layer_weight(self):
        f, row = self.fixture()
        self.assertTrue(_preserved_stage_yellow_graphic(row, f))
        errors, _ = validate_finding(f)
        self.assertFalse(any('ByLayer' in e for e in errors), errors)

    def test_exception_rejects_issued_created_or_changed_source(self):
        for mutation in ('issued', 'created', 'weight', 'text', 'geometry', 'colour'):
            f, row = self.fixture()
            if mutation == 'issued': f['output']['stage_is_issued'] = True
            elif mutation == 'created': f['autocad']['reused_objects'] = []
            else:
                k, v = {'weight': ('lineWeight', 'ByLayer'), 'text': ('contents', 'changed'),
                        'geometry': ('location', {'x': 99, 'y': 2}), 'colour': ('colorIndex', 3)}[mutation]
                row[k] = v
                row['changes'] = [dict(property=k, before=v, after=row[k])
                                  for k, v in row['properties_before'].items() if row[k] != v]
            self.assertFalse(_preserved_stage_yellow_graphic(row, f), mutation)

    def system_fixture(self):
        f, row = self.fixture()
        source = 'Предусмотреть систему оповещения и управления эвакуацией людей.'
        row['contents'] = row['properties_before']['contents'] = source
        f.update(source_text=source, analysis={'exclusion_reason': 'doc_00_008',
            'second_pass': {k: True for k in ('completed', 'number_and_text_verified',
                'scope_and_exceptions_verified', 'project_facts_verified',
                'reasoning_specific_verified', 'no_copied_explanation_verified')}})
        f['analysis']['second_pass']['norm_reopened'] = False
        f['output'].update(remark_text=source, is_general_requirement=True,
                           preserved_by_explicit_user_exception=True)
        return f, row

    def test_system_exception_only_skips_normative_reopening(self):
        f, row = self.system_fixture()
        self.assertTrue(_preserved_common_system_text(f))
        self.assertFalse(any('повторная проверка' in e for e in _validate_individual_analysis(f)))
        f['analysis']['second_pass']['project_facts_verified'] = False
        self.assertTrue(any('повторная проверка' in e for e in _validate_individual_analysis(f)))

    def test_flag_does_not_excuse_changed_or_unrelated_text(self):
        for mutation in ('changed', 'unrelated', 'no_rule', 'no_snapshot'):
            f, row = self.system_fixture()
            if mutation == 'changed': f['output']['remark_text'] += ' изменено'
            elif mutation == 'unrelated':
                f['source_text'] = f['output']['remark_text'] = row['contents'] = row['properties_before']['contents'] = 'Другое требование'
            elif mutation == 'no_rule': f['analysis']['exclusion_reason'] = ''
            else: row.pop('properties_before')
            self.assertFalse(_preserved_common_system_text(f), mutation)


if __name__ == '__main__':
    unittest.main()
