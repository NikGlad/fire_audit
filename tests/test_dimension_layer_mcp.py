import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fire_audit.runs import RunStore
from mcp_server import MCPServer, TOOLS


class DimensionLayerMCPTests(unittest.TestCase):
    def test_public_tool_dispatches_the_evidence_to_the_guarded_service(self):
        changes = [{'handle': 'AB', 'target_layer': 'экспертиза противопожарные преграды'}]
        store = RunStore.__new__(RunStore)
        server = MCPServer.__new__(MCPServer)
        server.app = SimpleNamespace(runs=store, control=Mock(), cad_guard=Mock())
        with patch('fire_audit.runs.barrier_remarks_service.record_dimension_layer_changes',
                   return_value={'complete': False}) as service:
            result = server.dispatch_tool('record_barrier_dimension_layer_changes',
                                          {'slug': 'synthetic', 'run_id': 'test', 'changes': changes})
        service.assert_called_once_with(store, 'synthetic', 'test', changes)
        self.assertEqual(result, {'complete': False})

    def test_public_schema_requires_baseline_classification_and_geometry(self):
        entries = [tool for tool in TOOLS if tool['name'] == 'record_barrier_dimension_layer_changes']
        self.assertEqual(len(entries), 1)
        schema = entries[0]['inputSchema']
        self.assertEqual(set(schema['required']), {'slug', 'run_id', 'changes'})
        item = schema['properties']['changes']['items']
        self.assertTrue({'baseline_digest', 'inventory_digest', 'classification_decision_id',
                         'measurement_points', 'measurement_value', 'ray_reviews'} <= set(item['required']))
        self.assertFalse(item['additionalProperties'])
        self.assertFalse(entries[0]['annotations']['readOnlyHint'])


if __name__ == '__main__':
    unittest.main()
