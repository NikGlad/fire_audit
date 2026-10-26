import copy
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fire_audit.knowledge import KnowledgeBase, MODE_FILES
from fire_audit.io_utils import atomic_write_text
from fire_audit.paths import AppPaths
from mcp_server import MCPServer


class RuleRegistryIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = AppPaths(self.root)
        self.paths.ensure()
        self.knowledge = KnowledgeBase(self.paths)
        names = set()
        for mode in MODE_FILES:
            names.update(self.knowledge.document_names_for_mode(mode))
        for name in names:
            (self.paths.knowledge / name).write_text('{}' if name.endswith('.json') else '# ' + name + '\n', encoding='utf-8')
        self.catalogue = {'schema_version': 1, 'rules': [
            {'id': 'shared_test', 'title': 'Общее тестовое правило', 'text': 'CANONICAL_BODY',
             'modes': list(MODE_FILES), 'requires': [], 'status': 'active'},
            {'id': 'parent_test', 'title': 'Вложенное правило', 'text': 'PARENT {{rule:shared_test}}',
             'modes': list(MODE_FILES), 'requires': [], 'status': 'active'},
        ]}
        self.write('rule_blocks.json', self.catalogue)
        self.write('rule_overrides.json', {'rules': []})
        self.wrapper = '25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md'
        (self.paths.knowledge / self.wrapper).write_text('# Общие правила\n{{rule:parent_test}}\n{{rule:shared_test}}\n', encoding='utf-8')

    def write(self, name, value):
        path = self.paths.knowledge / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def test_snapshot_reads_sources_once_and_expands_without_raw_catalogue(self):
        original = Path.read_bytes
        reads = Counter()

        def capture(path):
            reads[path.name] += 1
            return original(path)

        with patch.object(Path, 'read_bytes', capture):
            snapshot = self.knowledge.snapshot_for_mode('compliance')
        self.assertEqual(reads, Counter({doc.name: 1 for doc in snapshot.documents}))
        self.assertIn('rule_blocks.json', [doc.name for doc in snapshot.documents])
        self.assertEqual(snapshot.context.count('CANONICAL_BODY'), 1)
        self.assertNotIn('{{rule:', snapshot.context)
        self.assertNotIn('"schema_version":', snapshot.context)
        self.assertEqual(snapshot.digest, hashlib.sha256(snapshot.context.encode('utf-8')).hexdigest())
        raw = self.knowledge.load_document(self.wrapper)
        self.assertIn('{{rule:', raw.text)
        self.assertEqual(raw.sha256, hashlib.sha256(raw.path.read_bytes()).hexdigest())

    def test_shared_update_changes_new_snapshots_not_old_snapshot(self):
        old = {mode: self.knowledge.snapshot_for_mode(mode) for mode in MODE_FILES}
        self.catalogue['rules'][0]['text'] = 'CHANGED_CANONICAL_BODY'
        self.write('rule_blocks.json', self.catalogue)
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                current = self.knowledge.snapshot_for_mode(mode)
                self.assertNotEqual(old[mode].digest, current.digest)
                self.assertNotIn('CHANGED_CANONICAL_BODY', old[mode].context)
                self.assertEqual(current.context.count('CHANGED_CANONICAL_BODY'), 1)

    def test_editor_candidate_rejects_broken_dependency_without_writes(self):
        before = (self.paths.knowledge / 'rule_blocks.json').read_bytes()
        invalid = copy.deepcopy(self.catalogue)
        invalid['rules'] = [invalid['rules'][1]]
        with self.assertRaises(ValueError):
            self.knowledge.validate_document('rule_blocks.json', json.dumps(invalid))
        with self.assertRaises(ValueError):
            self.knowledge.validate_document(self.wrapper, '{{rule:missing_rule}}')
        self.assertEqual(before, (self.paths.knowledge / 'rule_blocks.json').read_bytes())

    def test_mode_aware_overrides_allow_history_only_when_fully_replaced(self):
        old = {'id': 'old', 'status': 'active', 'modes': ['compliance', 'stage'], 'replaces': [],
               'history_ref': 'history/overrides_20261026.json#old'}
        new = {'id': 'new', 'status': 'active', 'modes': ['compliance'], 'replaces': ['old'],
               'text_ref': 'shared_test'}
        with self.assertRaises(ValueError):
            self.knowledge.validate_document('rule_overrides.json', json.dumps({'rules': [old, new]}))
        new['modes'].append('stage')
        self.write('rule_overrides.json', {'rules': [old, new]})
        self.assertIn('CANONICAL_BODY', self.knowledge.snapshot_for_mode('stage').context)

    def test_legacy_text_override_still_works_without_registry(self):
        (self.paths.knowledge / 'rule_blocks.json').unlink()
        (self.paths.knowledge / self.wrapper).write_text('Legacy instruction', encoding='utf-8')
        self.write('rule_overrides.json', {'rules': [
            {'id': 'legacy', 'status': 'active', 'modes': ['compliance'], 'replaces': [], 'text': 'LEGACY_BODY'}]})
        self.assertIn('LEGACY_BODY', self.knowledge.snapshot_for_mode('compliance').context)

    def test_default_search_excludes_history_and_replaced_inline_text(self):
        self.write('history/overrides_20261026.json', {'rules': [
            {'id': 'old', 'text': 'ARCHIVED_NEEDLE', 'modes': ['compliance']} ]})
        self.write('rule_overrides.json', {'rules': [
            {'id': 'old', 'status': 'superseded', 'modes': ['compliance'], 'replaces': [], 'text': 'ARCHIVED_NEEDLE'},
            {'id': 'new', 'status': 'active', 'modes': ['compliance'], 'replaces': ['old'], 'text_ref': 'shared_test'}]})
        self.assertEqual([], self.knowledge.search('ARCHIVED_NEEDLE'))
        self.assertTrue(self.knowledge.search('ARCHIVED_NEEDLE', include_history=True))

    def test_rule_edit_validates_then_records_history_and_backup(self):
        original = (self.paths.knowledge / 'rule_blocks.json').read_bytes()
        entry = self.knowledge.read_rule('shared_test')
        self.assertIn('CANONICAL_BODY', entry['preview'])
        with self.assertRaises(ValueError):
            self.knowledge.validate_rule_text('shared_test', '{{rule:missing_rule}}')
        with self.assertRaises(ValueError):
            self.knowledge.save_rule_text('shared_test', 'New shared text', expected_sha256='outdated')
        self.assertEqual(original, (self.paths.knowledge / 'rule_blocks.json').read_bytes())
        saved = self.knowledge.save_rule_text('shared_test', 'New shared text', expected_sha256=entry['source_sha256'])
        self.assertEqual(original, Path(saved['backup_path']).read_bytes())
        self.assertEqual(self.knowledge.read_rule('shared_test')['text'], 'New shared text')
        history = json.loads(Path(saved['history_path']).read_text(encoding='utf-8'))
        self.assertEqual(history['before']['text'], 'CANONICAL_BODY')
        self.assertEqual(history['after']['text'], 'New shared text')

    def test_mcp_contract_returns_expanded_instruction(self):
        name = '21_ПОСТРОЕНИЕ_И_ПРОВЕРКА_ПП.md'
        (self.paths.knowledge / name).write_text('{{rule:parent_test}}', encoding='utf-8')
        server = MCPServer.__new__(MCPServer)
        server.app = SimpleNamespace(knowledge=self.knowledge)
        response = server.dispatch_tool('get_barrier_plan_contract', {})
        self.assertIn('CANONICAL_BODY', response['instruction'])
        self.assertNotIn('{{rule:', response['instruction'])

    def test_resolved_document_can_use_frozen_sources_after_change(self):
        snapshot = self.knowledge.snapshot_for_mode('compliance')
        self.catalogue['rules'][0]['text'] = 'NEW_VERSION'
        self.write('rule_blocks.json', self.catalogue)
        frozen = self.knowledge.resolved_document(self.wrapper, 'compliance', snapshot=snapshot)
        self.assertIn('CANONICAL_BODY', frozen)
        self.assertNotIn('NEW_VERSION', frozen)

    def test_mcp_quality_control_uses_same_captured_revision(self):
        name = '22_ПРЯМАЯ_РАБОТА_ПОМОЩНИКА.md'
        (self.paths.knowledge / name).write_text('{{rule:parent_test}}', encoding='utf-8')
        (self.paths.knowledge / '00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md').write_text('{{rule:shared_test}}', encoding='utf-8')
        snapshot = self.knowledge.snapshot_for_mode('compliance')
        self.catalogue['rules'][0]['text'] = 'LATER_REVISION'
        self.write('rule_blocks.json', self.catalogue)
        server = MCPServer.__new__(MCPServer)
        server.app = SimpleNamespace(
            knowledge=self.knowledge,
            control=Mock(), cad_guard=Mock(),
            projects=SimpleNamespace(load=lambda slug: {'name': slug}, validate=lambda *a, **kw: ([], []),
                                     project_dir=lambda slug: self.root / slug),
            runs=SimpleNamespace(project_history=lambda slug: {'runs': [], 'by_work_kind': {}}),
            norms=SimpleNamespace(status=lambda: {}),
        )
        with patch.object(self.knowledge, 'snapshot_for_mode', return_value=snapshot), \
                patch('mcp_server.inputs_for', return_value={}), \
                patch.object(self.knowledge, 'load_document', side_effect=AssertionError('Sources must not be reread')):
            response = server.dispatch_tool('get_project_context', {'slug': 'test', 'mode': 'compliance'})
        self.assertEqual(response['knowledge_digest'], snapshot.digest)
        self.assertIn('CANONICAL_BODY', response['rules'])
        self.assertIn('См. правило shared_test', response['quality_control'])
        self.assertIn(response['quality_control'].strip(), response['rules'])
        self.assertEqual(response['quality_control'], dict(snapshot.rendered_documents)[name])
        self.assertNotIn('LATER_REVISION', response['quality_control'])
        self.assertNotIn('LATER_REVISION', response['rules'])

    def test_catalogue_effective_overrides_match_snapshot_in_every_mode(self):
        records = [
            {'id': 'old', 'status': 'active', 'modes': list(MODE_FILES), 'replaces': [], 'text': 'OLD_BODY'},
            {'id': 'middle', 'status': 'active', 'modes': list(MODE_FILES), 'replaces': ['old'],
             'history_ref': 'history/overrides_20261026.json#middle'},
            {'id': 'latest', 'status': 'active', 'modes': list(MODE_FILES), 'replaces': ['middle'], 'text_ref': 'shared_test'},
        ]
        self.write('rule_overrides.json', {'rules': records})
        for mode in MODE_FILES:
            with self.subTest(mode=mode):
                snapshot = self.knowledge.snapshot_for_mode(mode)
                rows = self.knowledge.effective_rule_catalogue(mode, include_history=True)
                metadata = {row['id']: row for row in rows if row['record_type'] == 'override'}
                active = {key for key, row in metadata.items() if row['effective_modes']}
                delivered = {key for key in metadata if ('Правило ' + key + ' (заменяет:') in snapshot.context}
                self.assertEqual(active, delivered)
                self.assertEqual(active, {'latest'})
                self.assertEqual(metadata['old']['effective_status'], 'replaced')
                self.assertEqual(metadata['middle']['effective_status'], 'replaced')
                self.assertEqual(metadata['old']['replaced_by'], ['latest'])
                self.assertEqual(metadata['latest']['canonical_id'], 'shared_test')
                self.assertFalse(metadata['latest']['editable'])

    def test_partial_override_replacement_retains_remaining_mode(self):
        self.write('rule_overrides.json', {'rules': [
            {'id': 'old', 'status': 'active', 'modes': ['stage', 'compliance'], 'replaces': [], 'text': 'OLD_BODY'},
            {'id': 'new', 'status': 'active', 'modes': ['compliance'], 'replaces': ['old'], 'text_ref': 'shared_test'},
        ]})
        rows = {row['id']: row for row in self.knowledge.effective_rule_catalogue(include_history=True)
                if row['record_type'] == 'override'}
        self.assertEqual(rows['old']['effective_status'], 'partially_replaced')
        self.assertEqual(rows['old']['effective_modes'], ['stage'])
        self.assertEqual(rows['old']['replaced_modes'], ['compliance'])
        self.assertIn('OLD_BODY', self.knowledge.snapshot_for_mode('stage').context)
        self.assertNotIn('OLD_BODY', self.knowledge.snapshot_for_mode('compliance').context)

    def test_archival_snapshot_does_not_mean_rule_was_replaced(self):
        original = {'id': 'shared_test', 'status': 'active', 'modes': ['compliance'], 'text': 'ORIGINAL_BODY'}
        self.write('history/20261026_before_registry/rule_overrides.json', {'rules': [original]})
        rows = self.knowledge.effective_rule_catalogue(include_history=True)
        history = [row for row in rows if row.get('historical')]
        self.assertEqual(history[0]['effective_status'], 'archive_snapshot')
        self.assertEqual(history[0]['replaced_by'], [])
        self.assertEqual(history[0]['text'], 'ORIGINAL_BODY')

    def test_candidate_rejects_rule_mode_loss_in_shared_wrapper(self):
        candidate = copy.deepcopy(self.catalogue)
        for row in candidate['rules']:
            row['modes'] = ['compliance']
        with self.assertRaisesRegex(ValueError, 'не действует'):
            self.knowledge.validate_document('rule_blocks.json', json.dumps(candidate))

    def test_search_reads_current_sources_once(self):
        reads = Counter()
        original = Path.read_bytes

        def capture(path):
            reads[path.name] += 1
            return original(path)

        with patch.object(Path, 'read_bytes', capture):
            results = self.knowledge.search('CANONICAL_BODY')
        self.assertTrue(results)
        self.assertEqual(set(reads.values()), {1})

    def test_failed_source_replace_does_not_record_a_completed_edit(self):
        source = self.paths.knowledge / 'rule_blocks.json'
        original = source.read_bytes()

        def fail_source(path, text):
            if Path(path) == source:
                raise OSError('Cannot replace source')
            return atomic_write_text(path, text)

        with patch('fire_audit.knowledge.atomic_write_text', side_effect=fail_source):
            with self.assertRaisesRegex(OSError, 'Cannot replace source'):
                self.knowledge.save_rule_text('shared_test', 'Candidate update')
        self.assertEqual(original, source.read_bytes())
        self.assertEqual([], self.knowledge.knowledge_history())
        backups = list(self.paths.logs.glob('rule_blocks.json.*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_migrated_templates_reject_inline_text_and_accept_references(self):
        self.catalogue['template_sources_only'] = True
        self.write('rule_blocks.json', self.catalogue)
        original = (self.paths.knowledge / self.wrapper).read_bytes()
        with self.assertRaisesRegex(ValueError, 'шаблон инструкции'):
            self.knowledge.validate_document(self.wrapper, '# Heading\nNew inline rule')
        self.knowledge.validate_document(self.wrapper, '# Heading\n\n{{rule:shared_test}}\n')
        self.assertEqual(original, (self.paths.knowledge / self.wrapper).read_bytes())

    def test_migrated_overrides_reject_effective_inline_text(self):
        self.catalogue['template_sources_only'] = True
        self.write('rule_blocks.json', self.catalogue)
        row = {'id': 'new', 'status': 'active', 'modes': ['compliance'], 'replaces': [], 'text': 'INLINE_BODY'}
        with self.assertRaisesRegex(ValueError, 'text_ref'):
            self.knowledge.validate_document('rule_overrides.json', json.dumps({'rules': [row]}))
        del row['text']
        row['text_ref'] = 'shared_test'
        self.knowledge.validate_document('rule_overrides.json', json.dumps({'rules': [row]}))


if __name__ == '__main__':
    unittest.main()
