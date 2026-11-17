import copy
import unittest

from fire_audit.validation import (
    _validate_compliance_source_copy, _verified_source_explanation_side, validate_registry,
)
from test_validation_user_exceptions import reused_group


class ExplanationRestorationTests(unittest.TestCase):
    def restored(self):
        f = reused_group()
        ac = f['autocad']
        row = ac['reused_objects'].pop()
        row.update(action='created', contents='Approved explanation')
        row['properties_after'].update(contents=row['contents'])
        ac['created_objects'] = [row]
        sg = ac['source_group']
        sg['created_copy_entity_ids'] = [row['entity_id']]
        sg['restored_explanation'] = dict(
            copy_method='copy_existing_explanation', source_entity_ids=['template-purple'],
            created_entity_ids=[row['entity_id']], source_readback_verified=True,
            source_text=row['contents'], source_text_style='Standard', evidence='live readback')
        return f

    def test_restore_missing_explanation_without_recreating_core(self):
        self.assertEqual(_validate_compliance_source_copy(self.restored()), [])

    def test_missing_or_inconsistent_provenance_rejected(self):
        for key, value in [('source_readback_verified', False), ('source_entity_ids', ['wrong']),
                           ('source_text', 'changed'), ('source_text_style', 'other'), ('evidence', '')]:
            f = self.restored(); f['autocad']['source_group']['restored_explanation'][key] = value
            self.assertTrue(_validate_compliance_source_copy(f), key)
        for key, value in [('role', 'main_text'), ('layer', 'wrong'), ('properties_re_read', False)]:
            f = self.restored(); f['autocad']['created_objects'][0][key] = value
            self.assertTrue(_validate_compliance_source_copy(f), key)

    def test_unrelated_created_core_is_not_permitted(self):
        f = self.restored()
        r = copy.deepcopy(f['autocad']['reused_objects'][0]); r.update(action='created', entity_id='new-main')
        f['autocad']['created_objects'].append(r)
        self.assertTrue(_validate_compliance_source_copy(f))

    def test_source_relative_layout_requires_measured_proof(self):
        f = reused_group(); sg = f['autocad']['source_group']
        sg['explanation_layout'] = dict(verified=True, evidence='source and result readbacks',
            source_main_bounds=[0, 0, 10, 10], source_explanation_bounds=[-12, 0, -2, 10],
            result_main_bounds=[100, 100, 110, 110], result_explanation_bounds=[88, 100, 98, 110])
        self.assertEqual(_verified_source_explanation_side(f), 'left')
        sg['explanation_layout']['result_explanation_bounds'] = [112, 100, 122, 110]
        self.assertIsNone(_verified_source_explanation_side(f))
        sg['explanation_layout']['verified'] = False
        self.assertIsNone(_verified_source_explanation_side(f))

    def test_linked_group_may_reference_same_template_explanation(self):
        owner = reused_group(); owner['output']['explanation'] = 'Approved explanation'
        other = copy.deepcopy(owner); other['id'] = 'dependent'
        other['autocad']['linked_finding_id'] = 'owner'; other['output']['linked_finding_id'] = 'owner'
        for r in other['autocad']['reused_objects']: r['related_finding_id'] = 'dependent'
        errors = validate_registry({'findings': [owner, other]}, for_application=True)['errors']
        self.assertFalse(any('дословно повторяет' in e for e in errors))
        other['autocad']['reused_objects'][0]['entity_id'] = 'different'
        errors = validate_registry({'findings': [owner, other]}, for_application=True)['errors']
        self.assertTrue(any('дословно повторяет' in e for e in errors))


if __name__ == '__main__':
    unittest.main()
