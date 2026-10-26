import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fire_audit.continuity_summary import SUMMARY_NAME, build_continuity_summary
from fire_audit.core import FireAuditApp
from fire_audit.io_utils import read_json, write_json


class ContinuitySummaryTests(unittest.TestCase):
    def test_long_chain_keeps_every_source_without_certifying_or_mixing_modes(self):
        entries = [{'run_id': f'r{index}', 'registry_file': f'archive/r{index}.json',
                    'continuity_items': [{'id': 'same-key', 'kind': 'architecture', 'description': f'fact {index}', 'source': f'plan {index}'}]}
                   for index in reversed(range(50))]
        other = {'run_id': 'pp', 'registry_file': 'archive/pp.json', 'continuity_items': [
            {'id': 'same-key', 'kind': 'architecture', 'description': 'other work', 'source': 'PP plan'},
            {'id': 'open', 'kind': 'question', 'description': 'Unresolved'},
            {'id': 'answered', 'kind': 'question', 'description': 'Answer retained', 'status': 'resolved', 'source': 'Customer response'}]}
        history = {'runs': [other, *entries], 'by_work_kind': {'compliance': entries, 'barrier_assessment': [other]}}
        original = copy.deepcopy(history)
        summary = build_continuity_summary(history, 'compliance', archive_path='full_archive.json',
                                           passport_changes={'count': 1}, plan_revision={'status': 'new'})
        self.assertEqual(summary['nearest_iteration']['run_id'], 'r49')
        self.assertEqual(summary['full_archive']['run_count'], 51)
        own = next(row for row in summary['state_candidates'] if row['work_kind'] == 'compliance')
        self.assertEqual(own['record']['description'], 'fact 49')
        self.assertEqual(len(own['earlier_sources']), 49)
        self.assertEqual(len(summary['state_candidates']), 4)
        self.assertTrue(summary['question_candidate_keys'])
        self.assertNotIn('open_question_keys', summary)
        answered = next(row for row in summary['state_candidates'] if row['record']['id'] == 'answered')
        self.assertEqual(answered['record']['status'], 'resolved')
        self.assertEqual(answered['verification_status'], 'verification_required')
        self.assertIn(answered['key'], summary['question_candidate_keys'])
        self.assertEqual(summary['verification_status'], 'verification_required')
        self.assertTrue(summary['impact_review']['required'])
        self.assertEqual(history, original)

    def test_run_freezes_summary_full_archive_and_approved_texts_without_rewriting_old_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = Path(__file__).resolve().parents[1]
            for name in ('config', 'knowledge', 'templates'):
                shutil.copytree(source / name, root / name)
            app = FireAuditApp(root)
            project = app.projects.create('Synthetic', 'test')
            project['dates']['gpzu'] = '2026-04-23'
            app.projects.save('test', project)
            first = app.runs.create('test', 'actualization_protocol')
            app.runs.record_continuity('test', first['run']['run_id'], [
                {'id': 'source-feature', 'kind': 'architecture', 'description': 'Synthetic feature', 'source': 'Synthetic plan'}])
            old = {path: path.read_bytes() for path in Path(first['folder']).glob('*') if path.is_file()}
            second = app.runs.create('test', 'actualization_protocol', plan_change_status='new')
            summary = read_json(Path(second['folder']) / SUMMARY_NAME)
            self.assertEqual(summary['nearest_iteration']['run_id'], first['run']['run_id'])
            self.assertEqual(summary['verification_status'], 'verification_required')
            self.assertEqual(len(second['run']['eligible_rule_text_sources']), 3)
            self.assertTrue(second['run']['knowledge_modules'])
            registry_path = Path(second['folder']) / 'РЕЕСТР_ПРОВЕРКИ.json'
            registry = read_json(registry_path)
            self.assertEqual(registry['protocol']['scope_policy_version'], 2)
            self.assertEqual(registry['cycle_id'], second['run']['run_id'])
            registry['eligible_rule_text_sources'][0]['full_text'] = 'Unapproved replacement'
            write_json(registry_path, registry)
            result = app.runs.validate('test', second['run']['run_id'])
            self.assertTrue(any('eligible_rule_text_sources' in error for error in result['errors']))
            self.assertEqual(old, {path: path.read_bytes() for path in old})
            task = Path(second['task_file']).read_text(encoding='utf-8')
            self.assertIn(SUMMARY_NAME, task)
            self.assertIn('матрицу влияния', task)
            self.assertNotIn('Прочитай всю историю этого вида', task)
            for mode in ('ar_assessment_transfer', 'ar_plan_preparation', 'ar_plan_comparison', 'du_assessment'):
                with self.subTest(pending_mode=mode):
                    pending = app.runs.create('test', mode)['run']
                    self.assertFalse(pending['hard_constraints']['workflow_instructions_ready'])
                    self.assertFalse(pending['hard_constraints']['autocad_actions_enabled'])
                    self.assertTrue(pending['project_snapshot'])
                    self.assertTrue(any(row['id'] == 'core.user_inputs' for row in pending['knowledge_modules']))


if __name__ == '__main__':
    unittest.main()
