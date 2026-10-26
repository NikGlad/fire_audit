import copy
import json
import tempfile
import unittest
from pathlib import Path

from fire_audit.knowledge import KnowledgeBase, MODE_FILES
from fire_audit.knowledge_modules import ModulePlan, CORE_USER_RULE_IDS
from fire_audit.paths import AppPaths


class InstructionModuleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = AppPaths(Path(temporary.name))
        self.paths.ensure()
        self.knowledge = KnowledgeBase(self.paths)
        names = {name for mode in MODE_FILES for name in self.knowledge.document_names_for_mode(mode)}
        for name in names:
            (self.paths.knowledge / name).write_text('{}' if name.endswith('.json') else '# Template\n', encoding='utf-8')
        specs = [
            ('doc_00_001', '00_ПРИОРИТЕТЫ_И_БЕЗОПАСНОСТЬ.md', 'PRESERVE_SOURCE_AND_HISTORY'),
            ('doc_25_054', '25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md', 'CONFIRMED_PASSPORT_PRIORITY'),
            ('doc_25_055', '25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md', 'CORE_WITH_DEPENDENCY {{rule:conditional_dependency}}'),
            ('conditional_dependency', '31_РЕЕСТР_ОШИБОК_И_МЕТОДИКА.md', 'COMPLETE_DEPENDENCY_TEXT'),
            ('hospital_condition', '31_РЕЕСТР_ОШИБОК_И_МЕТОДИКА.md', 'UNKNOWN_HOSPITAL_FULL_RULE'),
            ('doc_27_003', '27_НЕЗАВИСИМЫЕ_ТРЕБОВАНИЯ_ДВЕРИ_ПЕРЕКРЫТИЯ.md', 'PP_ONLY_METHOD'),
            ('mandatory_request', '25_ОБЩИЕ_ПРАВИЛА_И_ДАННЫЕ_ОБЪЕКТА.md', 'DIRECT_MANDATORY_REQUEST'),
            ('pending_algorithm', '12_ПЕРЕНОС_ОЦЕНКИ_АР.md', 'Алгоритм этого вида проверки ещё не передан пользователем'),
        ]
        self.catalogue = {'schema_version': 1, 'template_sources_only': True, 'rules': [
            {'id': key, 'origin_document': origin, 'text': text, 'modes': list(MODE_FILES), 'status': 'active'}
            for key, origin, text in specs]}
        for name in {row['origin_document'] for row in self.catalogue['rules']}:
            references = ['{{rule:' + row['id'] + '}}' for row in self.catalogue['rules'] if row['origin_document'] == name]
            (self.paths.knowledge / name).write_text('# Template\n\n' + '\n\n'.join(references), encoding='utf-8')
        self.write_catalogue()
        (self.paths.knowledge / 'rule_overrides.json').write_text(json.dumps({'rules': [
            {'id': 'request_override', 'status': 'active', 'modes': list(MODE_FILES), 'text_ref': 'mandatory_request', 'replaces': []}
        ]}), encoding='utf-8')

    def write_catalogue(self):
        (self.paths.knowledge / 'rule_blocks.json').write_text(json.dumps(self.catalogue, ensure_ascii=False), encoding='utf-8')

    def test_three_modes_preserve_core_and_unknown_conditions_with_explicit_exclusions(self):
        for mode in ('compliance', 'barrier_assessment', 'ar_assessment_transfer'):
            with self.subTest(mode=mode):
                snapshot = self.knowledge.snapshot_for_mode(mode)
                manifest = json.loads(snapshot.module_manifest_json)
                self.assertIn('PRESERVE_SOURCE_AND_HISTORY', snapshot.context)
                self.assertIn('CONFIRMED_PASSPORT_PRIORITY', snapshot.context)
                self.assertIn('COMPLETE_DEPENDENCY_TEXT', snapshot.context)
                self.assertIn('DIRECT_MANDATORY_REQUEST', snapshot.context)
                self.assertEqual('PP_ONLY_METHOD' in snapshot.context, mode == 'barrier_assessment')
                self.assertEqual('UNKNOWN_HOSPITAL_FULL_RULE' in snapshot.context, mode != 'ar_assessment_transfer')
                self.assertEqual(snapshot.instructions_pending, mode == 'ar_assessment_transfer')
                self.assertTrue(all(row['version'] and row['modes'] and 'dependencies' in row for row in manifest))
                classified = {key for row in manifest for key in row['included_rule_ids']}
                excluded = {item['id'] for row in manifest for item in row['excluded_rules']}
                self.assertFalse(classified & excluded)
                if mode != 'barrier_assessment':
                    self.assertIn('doc_27_003', excluded)

    def test_scope_is_consistent_in_catalogue_search_and_document_view(self):
        rows = self.knowledge.effective_rule_catalogue('compliance')
        self.assertNotIn('doc_27_003', [row['id'] for row in rows])
        hospital = next(row for row in rows if row['id'] == 'hospital_condition')
        self.assertEqual(hospital['effective_status'], 'conditional')
        self.assertEqual(self.knowledge.search('PP_ONLY_METHOD', mode='compliance'), [])
        self.assertTrue(self.knowledge.search('PP_ONLY_METHOD', mode='barrier_assessment'))
        rendered = self.knowledge.resolved_document('27_НЕЗАВИСИМЫЕ_ТРЕБОВАНИЯ_ДВЕРИ_ПЕРЕКРЫТИЯ.md', 'compliance')
        self.assertNotIn('PP_ONLY_METHOD', rendered)
        self.assertIn('PP_ONLY_METHOD', self.knowledge.resolved_document('27_НЕЗАВИСИМЫЕ_ТРЕБОВАНИЯ_ДВЕРИ_ПЕРЕКРЫТИЯ.md'))

    def test_module_version_covers_scope_and_dependencies(self):
        sources = {'25.md': '{{rule:hospital_condition}}'}
        first = ModulePlan(self.catalogue, sources, 'compliance', pending=False, algorithm_documents=[], override_ids=[])
        changed = copy.deepcopy(self.catalogue)
        next(row for row in changed['rules'] if row['id'] == 'hospital_condition')['modes'] = ['compliance']
        second = ModulePlan(changed, sources, 'compliance', pending=False, algorithm_documents=[], override_ids=[])
        self.assertNotEqual(first.modules[0]['version'], second.modules[0]['version'])

    def test_every_pending_core_rule_exists_in_real_registry(self):
        real = json.loads((Path(__file__).resolve().parents[1] / 'knowledge/rule_blocks.json').read_text(encoding='utf-8'))
        self.assertFalse(CORE_USER_RULE_IDS - {row['id'] for row in real['rules']})


if __name__ == '__main__':
    unittest.main()
