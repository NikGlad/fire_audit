import hashlib
import json
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from mcp_server import MCPServer


class ProjectContextSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(__file__).resolve().parents[1]
        for name in ('config', 'knowledge', 'templates'):
            shutil.copytree(source / name, self.root / name)
        self.server = MCPServer(self.root)
        self.knowledge = self.server.app.knowledge
        for slug, date, degree, category, room in (
            ('first', '2025-11-12', 'IV', 'В3', 'PRIVATE-101'),
            ('second', '2026-04-23', 'II', 'В1', 'PRIVATE-909'),
        ):
            project = self.server.app.projects.create(slug, slug)
            project['dates']['gpzu'] = date
            project['classification']['fire_resistance_degree'] = degree
            project['classification']['building_category'] = category
            project['notes'] = 'Исходные данные только этого объекта: ' + room
            project['people']['rooms'] = {room: {'people': 7}}
            self.server.app.projects.save(slug, project)

    def context(self, slug='first'):
        return self.server.dispatch_tool('get_project_context', {
            'slug': slug, 'mode': 'compliance',
        })

    def assert_consistent(self, context):
        self.assertEqual(
            context['knowledge_digest'],
            hashlib.sha256(context['rules'].encode('utf-8')).hexdigest(),
        )
        self.assertIn(context['quality_control'].strip(), context['rules'])

    def test_context_reads_each_shared_document_once(self):
        reads = Counter()
        read_bytes = Path.read_bytes

        def counted_read(path):
            if path.parent == self.root / 'knowledge':
                reads[path.name] += 1
            return read_bytes(path)

        with patch.object(Path, 'read_bytes', counted_read):
            with patch.object(self.knowledge, 'snapshot_for_mode',
                              wraps=self.knowledge.snapshot_for_mode) as capture:
                context = self.context()
        capture.assert_called_once_with('compliance')
        expected_names = {document.name for document in self.knowledge.snapshot_for_mode('compliance').documents}
        self.assertEqual(reads, Counter({name: 1 for name in expected_names}))
        self.assert_consistent(context)

    def test_concurrent_edit_cannot_mix_digest_rules_or_quality_revision(self):
        snapshot_for_mode = self.knowledge.snapshot_for_mode
        expected = snapshot_for_mode('compliance')
        marker = 'Дополнительный общий контроль текущих исходных данных.'
        quality_marker = 'Дополнительная общая проверка полноты доказательств.'

        def capture_then_edit(mode):
            snapshot = snapshot_for_mode(mode)
            for filename, text in (
                ('03_ОЦЕНКА_СООТВЕТСТВИЯ.md', marker),
                ('22_ПРЯМАЯ_РАБОТА_ПОМОЩНИКА.md', quality_marker),
            ):
                path = self.root / 'knowledge' / filename
                path.write_text(path.read_text(encoding='utf-8') + '\n' + text + '\n', encoding='utf-8')
            return snapshot

        with patch.object(self.knowledge, 'snapshot_for_mode', side_effect=capture_then_edit) as capture:
            context = self.context()
        capture.assert_called_once_with('compliance')
        self.assertEqual(context['rules'], expected.context)
        self.assertEqual(context['knowledge_digest'], expected.digest)
        self.assertEqual(context['instructions_ready'], not expected.instructions_pending)
        self.assertNotIn(marker, context['rules'])
        self.assertNotIn(quality_marker, context['quality_control'])
        self.assert_consistent(context)
        updated = self.context()
        self.assertIn(marker, updated['rules'])
        self.assertIn(quality_marker, updated['quality_control'])
        self.assertNotEqual(updated['knowledge_digest'], context['knowledge_digest'])
        self.assert_consistent(updated)

    def test_two_projects_receive_same_rules_without_each_others_facts(self):
        first = self.context('first')
        second = self.context('second')
        self.assertEqual(first['rules'], second['rules'])
        self.assertEqual(first['knowledge_digest'], second['knowledge_digest'])
        self.assertNotEqual(first['project'], second['project'])
        self.assertNotIn('PRIVATE-101', json.dumps(second, ensure_ascii=False))
        self.assertNotIn('PRIVATE-909', json.dumps(first, ensure_ascii=False))
        self.assertEqual(first['project']['dates']['gpzu'], '2025-11-12')
        self.assertEqual(second['project']['dates']['gpzu'], '2026-04-23')
        self.assertEqual(first['project']['classification']['fire_resistance_degree'], 'IV')
        self.assertEqual(second['project']['classification']['fire_resistance_degree'], 'II')
        for context in (first, second):
            self.assertNotIn('PRIVATE-', context['rules'])
            self.assert_consistent(context)

    def test_new_shared_rule_is_available_to_both_existing_projects(self):
        before = {slug: self.context(slug) for slug in ('first', 'second')}
        path = self.root / 'knowledge/03_ОЦЕНКА_СООТВЕТСТВИЯ.md'
        marker = 'Для каждого замечания сопоставить область применения с назначением текущего помещения.'
        path.write_text(path.read_text(encoding='utf-8') + '\n' + marker + '\n', encoding='utf-8')
        after = {slug: self.context(slug) for slug in ('first', 'second')}
        for slug in ('first', 'second'):
            self.assertIn(marker, after[slug]['rules'])
            self.assertNotEqual(before[slug]['knowledge_digest'], after[slug]['knowledge_digest'])
            self.assertEqual(before[slug]['project'], after[slug]['project'])
            self.assert_consistent(after[slug])
        self.assertEqual(after['first']['rules'], after['second']['rules'])


if __name__ == '__main__':
    unittest.main()
