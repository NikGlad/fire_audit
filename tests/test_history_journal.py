import json
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fire_audit.core import FireAuditApp
from fire_audit.history_journal import completion_errors, history, SOURCE_KINDS
from fire_audit.io_utils import read_json, write_json
from fire_audit.knowledge import MODE_FILES
from mcp_server import MCPServer


class HistoryJournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = Path(__file__).resolve().parents[1]
        for name in ('config', 'knowledge', 'templates'):
            shutil.copytree(source / name, self.root / name)
        self.app = FireAuditApp(self.root)
        project = self.app.projects.create('Тест истории', 'history-test')
        project['dates']['gpzu'] = '2026-01-01'
        self.app.projects.save('history-test', project)
        created = self.app.runs.create('history-test', 'stage')
        self.run_id = created['run']['run_id']
        self.folder = Path(created['folder'])
        self.project_dir = self.app.projects.project_dir('history-test')

    def event(self, key='answer-1'):
        return {'id': key, 'kind': 'user_answer', 'event_date': '2026-11-04',
                'source': 'Тестовый диалог, сообщение 1', 'text': 'Не более 8 человек',
                'interpretation': 'Ограничение для тестовой комнаты', 'related_finding_ids': ['F-1']}

    def review(self):
        data = history(self.project_dir, self.run_id)
        return {'conversation_through': 'Тестовый диалог, сообщение 1',
                'reviewed_event_ids': [e['event_id'] for e in data['events'] if e['kind'] != 'reconciliation'],
                'unrecorded_events': [],
                'source_inventory': [{'kind': k, 'reference': 'Тест: ' + k} for k in SOURCE_KINDS],
                'dispositions': [{'event_id': e['event_id'], 'status': 'unresolved',
                                  'reason': 'Нужна проверка плана', 'evidence': 'Реестр: вопрос Q-1'}
                                 for e in data['events'] if e['kind'] == 'user_answer']}

    def test_automatic_versions_preserve_replaced_finding_and_passport(self):
        self.app.runs.add_finding('history-test', self.run_id, {'id': 'F-1', 'decision': {'reason': 'первое основание'}})
        self.app.runs.add_finding('history-test', self.run_id, {'id': 'F-1', 'decision': {'reason': 'уточнённое основание'}})
        p = self.app.projects.load('history-test')
        p['geometry']['building_height_m'] = 12
        self.app.projects.save('history-test', p)
        events = history(self.project_dir, self.run_id)['events']
        revisions = [c for e in events if e['kind'] == 'state_snapshot' for c in e['payload']['changes']
                     if c['path'] == 'findings/F-1']
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[-1]['before']['decision']['reason'], 'первое основание')
        self.assertEqual(revisions[-1]['after']['decision']['reason'], 'уточнённое основание')
        self.assertTrue(any(e['run_id'] == '' and e['payload']['document'] == 'project.json' for e in events))

    def test_idempotent_event_cannot_be_rewritten_and_cross_object_reference_rejected(self):
        event = self.event()
        self.app.runs.record_history_event('history-test', self.run_id, event)
        self.app.runs.record_history_event('history-test', self.run_id, event)
        altered = {**event, 'text': 'Другое значение'}
        with self.assertRaisesRegex(ValueError, 'другим содержанием'):
            self.app.runs.record_history_event('history-test', self.run_id, altered)
        with self.assertRaisesRegex(ValueError, 'не найдено'):
            self.app.runs.record_history_event('history-test', self.run_id, {**event, 'id': 'correction', 'supersedes': 'other-object'})
        correction = {**altered, 'id': 'answer-2', 'supersedes': event['id']}
        self.app.runs.record_history_event('history-test', self.run_id, correction)
        events = history(self.project_dir, self.run_id)['events']
        self.assertEqual(len([e for e in events if e['event_id'] == event['id']]), 1)

    def test_missing_events_and_missing_dispositions_block_reconciliation(self):
        self.app.runs.record_history_event('history-test', self.run_id, self.event())
        review = self.review()
        review['reviewed_event_ids'].pop()
        with self.assertRaisesRegex(ValueError, 'Не сверены'):
            self.app.runs.reconcile_run_history('history-test', self.run_id, review)
        review = self.review()
        review['dispositions'] = []
        with self.assertRaisesRegex(ValueError, 'Нет решения'):
            self.app.runs.reconcile_run_history('history-test', self.run_id, review)
        review = self.review()
        review['unrecorded_events'] = ['забытый ответ']
        with self.assertRaisesRegex(ValueError, 'пропущенные'):
            self.app.runs.reconcile_run_history('history-test', self.run_id, review)

    def test_new_answer_external_file_edit_and_registry_edit_invalidate_review(self):
        file = self.root / 'plan.txt'
        file.write_text('version 1')
        review = self.review()
        review['source_inventory'][2]['path'] = str(file)
        self.app.runs.reconcile_run_history('history-test', self.run_id, review)
        self.assertEqual(completion_errors(self.project_dir, self.run_id), [])
        file.write_text('version 2')
        self.assertIn('Файлы источников', completion_errors(self.project_dir, self.run_id)[0])
        self.app.runs.reconcile_run_history('history-test', self.run_id, self.review())
        self.app.runs.record_history_event('history-test', self.run_id, self.event())
        self.assertIn('появились изменения', completion_errors(self.project_dir, self.run_id)[0])
        self.app.runs.reconcile_run_history('history-test', self.run_id, self.review())
        path = self.folder / 'РЕЕСТР_ПРОВЕРКИ.json'
        reg = read_json(path)
        reg['customer_amendments'] = [{'id': 'external', 'text': 'Прямая правка'}]
        path.write_text(json.dumps(reg), encoding='utf-8')
        self.assertIn('появились изменения', completion_errors(self.project_dir, self.run_id)[0])
        self.assertTrue(any(e['payload'].get('origin') == 'checkpoint' for e in history(self.project_dir, self.run_id)['events']))

    def test_validate_gate_is_stable_and_does_not_require_an_extra_review_for_timestamp(self):
        first = self.app.runs.validate('history-test', self.run_id, True)
        self.assertTrue(any('История не сверена' in e for e in first['errors']))
        self.app.runs.reconcile_run_history('history-test', self.run_id, self.review())
        for _ in range(2):
            result = self.app.runs.validate('history-test', self.run_id, True)
            self.assertFalse(any('История не сверена' in e or 'После сверки истории' in e for e in result['errors']))

    def test_pagination_and_history_cross_mode_preserve_archive(self):
        before = (self.folder / 'РЕЕСТР_ПРОВЕРКИ.json').read_bytes()
        data = self.app.runs.get_run_history('history-test', self.run_id, limit=1)
        self.assertTrue(data['has_more'])
        ids = [e['event_id'] for e in data['events']]
        while data['has_more']:
            data = self.app.runs.get_run_history('history-test', self.run_id, data['next_after_seq'], 1)
            ids.extend(e['event_id'] for e in data['events'])
        self.assertEqual(len(ids), data['total_events'])
        next_run = self.app.runs.create('history-test', 'protocol')
        summary = read_json(Path(next_run['folder']) / 'СВОД_ПРЕЕМСТВЕННОСТИ.json')
        self.assertTrue(summary['event_journal']['available'])
        self.assertEqual((self.folder / 'РЕЕСТР_ПРОВЕРКИ.json').read_bytes(), before)

    def test_mcp_dispatch_and_instructions_available_in_all_modes(self):
        server = MCPServer(self.root)
        result = server.dispatch_tool('record_history_event', {'slug': 'history-test', 'run_id': self.run_id, 'event': self.event()})
        self.assertEqual(result['event_id'], 'answer-1')
        result = server.dispatch_tool('get_run_history', {'slug': 'history-test', 'run_id': self.run_id, 'limit': 1})
        self.assertTrue(result['has_more'])
        for mode in MODE_FILES:
            text = self.app.knowledge.snapshot_for_mode(mode).context
            self.assertIn('[Правило shared_history_completeness_20261104]', text, mode)
        task = (self.folder / 'ЗАДАНИЕ_ДЛЯ_ЧАТА.md').read_text(encoding='utf-8')
        self.assertIn('reconcile_run_history', task)

    def test_source_version_is_saved_at_receipt_and_old_version_survives(self):
        file = self.root / 'source.txt'
        file.write_text('first')
        event = {**self.event(), 'kind': 'plan_change', 'source_path': str(file)}
        self.app.runs.record_history_event('history-test', self.run_id, event)
        file.write_text('second')
        with self.assertRaisesRegex(ValueError, 'другим содержанием'):
            self.app.runs.record_history_event('history-test', self.run_id, event)
        self.app.runs.record_history_event('history-test', self.run_id,
                                           {**event, 'id': 'plan-2', 'supersedes': event['id']})
        versions = [e['payload']['source_file']['sha256'] for e in history(self.project_dir, self.run_id)['events']
                    if e['kind'] == 'plan_change']
        self.assertEqual(len(set(versions)), 2)

    def test_concurrent_events_are_not_lost(self):
        def record(number):
            self.app.runs.record_history_event('history-test', self.run_id, self.event('parallel-' + str(number)))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(record, range(8)))
        ids = {e['event_id'] for e in history(self.project_dir, self.run_id)['events'] if e['kind'] == 'user_answer'}
        self.assertEqual(ids, {'parallel-' + str(i) for i in range(8)})

    def test_legacy_run_warns_and_continuation_enables_gate_without_rewriting_run(self):
        path = self.folder / 'run.json'
        run = read_json(path)
        run.pop('history_policy_version')
        write_json(path, run)
        frozen = path.read_bytes()
        result = self.app.runs.validate('history-test', self.run_id, True)
        self.assertTrue(any('Исторический запуск' in x for x in result['warnings']))
        self.app.runs.record_history_event('history-test', self.run_id, self.event())
        result = self.app.runs.validate('history-test', self.run_id, True)
        self.assertTrue(any('История не сверена' in x for x in result['errors']))
        self.assertEqual(path.read_bytes(), frozen)


if __name__ == '__main__':
    unittest.main()
