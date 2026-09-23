import copy
import unittest
from unittest.mock import Mock
from pathlib import Path
import test_barrier_service as fixtures
from fire_audit import barrier_control
from fire_audit.barrier_dxf import library
from fire_audit.io_utils import read_json, write_json


class BarrierControlTests(unittest.TestCase):
    setUp=fixtures.BarrierServiceTests.setUp

    def test_unprepared_run_has_six_stages_and_no_false_completion(self):
        self.app.control.load=Mock(side_effect=AssertionError('AR control must not be used'))
        self.app.control.status=Mock(side_effect=AssertionError('AR control must not be used'))
        data=barrier_control.snapshot(self.app,'test',self.run)
        self.assertEqual(len(data['steps']),6)
        self.assertFalse(data['complete']);self.assertFalse(data['can_build']);self.assertFalse(data['can_verify'])
        self.assertIn('помощнику',data['next_step'])
        self.assertIn('Склад',barrier_control.rooms_text(data))
        self.app.control.load.assert_not_called()

    def test_prepared_built_verified_and_changed_result_are_distinguished(self):
        self.flow.prepare('test',self.run,self.plan)
        data=barrier_control.snapshot(self.app,'test',self.run)
        self.assertTrue(data['can_build']);self.assertFalse(data['can_verify'])
        self.assertIn('Вес',barrier_control.plan_text(data).replace('вес','Вес'))
        result=self.flow.build_copy_from_ui('test',self.run,expected_plan_digest=data['state']['plan_digest'])
        self.assertTrue(result['ok'])
        data=barrier_control.snapshot(self.app,'test',self.run)
        self.assertTrue(data['complete']);self.assertTrue(data['can_verify']);self.assertFalse(data['can_build'])
        self.assertEqual(sum(s['done'] for s in data['steps']),6)
        doc=library().readfile(self.cad.current);doc.modelspace().add_line((1,1),(2,2));doc.saveas(self.cad.current)
        data=barrier_control.snapshot(self.app,'test',self.run)
        self.assertFalse(data['complete']);self.assertTrue(data['can_verify'])
        self.assertIn('повторная сверка',data['summary'])
        self.flow.verify('test',self.run)
        self.assertTrue(barrier_control.snapshot(self.app,'test',self.run)['errors'])

    def test_new_plan_cannot_replace_reviewed_plan_at_confirmation(self):
        self.flow.prepare('test',self.run,self.plan)
        viewed=barrier_control.snapshot(self.app,'test',self.run)['state']['plan_digest']
        changed=copy.deepcopy(self.plan);changed['items'][2]['text']='Уточнённый текст теста'
        self.flow.prepare('test',self.run,changed)
        with self.assertRaisesRegex(ValueError,'после открытия просмотра'):
            self.flow.build_copy_from_ui('test',self.run,expected_plan_digest=viewed)
        self.assertEqual(self.cad.current,self.source)

    def test_stale_passport_and_frozen_rules_block_actions(self):
        self.flow.prepare('test',self.run,self.plan)
        rules=self.app.runs.run_dir('test',self.run)/'ПРАВИЛА_ЗАПУСКА.md'
        before=rules.read_bytes();rules.write_text('Changed',encoding='utf-8')
        data=barrier_control.snapshot(self.app,'test',self.run)
        self.assertFalse(data['can_build']);self.assertTrue(data['stale'])
        with self.assertRaisesRegex(ValueError,'версия правил'):
            self.flow.build_copy_from_ui('test',self.run)
        rules.write_bytes(before)
        project=self.app.projects.load('test');project['notes']='Новое уточнение';self.app.projects.save('test',project)
        self.assertFalse(barrier_control.snapshot(self.app,'test',self.run)['can_build'])

    def test_interrupted_run_is_never_offered_for_automatic_retry(self):
        self.flow.prepare('test',self.run,self.plan)
        path=self.app.runs.run_dir('test',self.run)/'ПЛАН_ПП.json'
        state=read_json(path);state['status']='interrupted_requires_review';write_json(path,state)
        data=barrier_control.snapshot(self.app,'test',self.run)
        self.assertFalse(data['can_build']);self.assertFalse(data['complete'])
        self.assertIn('повторение запрещено',data['next_step'])

    def test_quality_uses_pp_checks(self):
        result=self.app.quality.run_barriers()
        self.assertTrue(result['software_ok'])
        self.assertEqual(result['mode'],'barrier_assessment')
        self.assertGreaterEqual(len(result['software_checks']),8)
