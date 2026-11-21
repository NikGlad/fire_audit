import copy
import unittest

from fire_audit.validation import _source_layout_preserved


class NumberFrameLayoutTests(unittest.TestCase):
    def setUp(self):
        rectangle = [[0, 20], [10, 20], [10, 24], [0, 24], [0, 20]]
        self.rows = [
            dict(source_id="T", result_id="t", source_anchor=[1, 1], result_anchor=[101, 1]),
            dict(source_id="N", result_id="n", source_anchor=[1, 5], result_anchor=[101, 5],
                 source_bounds=[1, 5, 8, 6], result_bounds=[101, 5, 108, 6]),
            dict(source_id="F", result_id="f", source_vertices=rectangle,
                 result_vertices=[[x + 100, y - 16] for x, y in rectangle],
                 source_closed=True, result_closed=True),
            dict(source_id="P", result_id="p", source_anchor=[-3, 1], result_anchor=[97, 1]),
        ]
        self.ac = {"created_objects": [dict(entity_id=h, role=role) for h, role in
                   zip("tnfp", ("main_text", "number", "frame", "purple_explanation"))],
                   "source_group": dict(remark_text_entity_ids=["T"], number_entity_ids=["N"],
                       frame_entity_ids=["F"], purple_explanation_entity_ids=["P"],
                       created_copy_entity_ids=list("tnfp"), relative_positions_preserved=False,
                       displaced_number_frame=dict(reopen_evidence="saved DWG", members=self.rows))}

    def test_corrected_copy_with_complete_evidence(self):
        self.assertTrue(_source_layout_preserved(self.ac))

    def test_only_the_frame_can_move_and_must_enclose_the_final_number(self):
        for index, fields in [
            (0, dict(result_anchor=[105, 1])), (1, dict(result_bounds=[101, 5, 112, 6])),
            (1, dict(source_bounds=[1, 21, 8, 22])), (2, dict(result_closed=False)),
            (2, dict(result_vertices=[[100, 4], [112, 4], [112, 8], [100, 8], [100, 4]])),
            (3, dict(source_id="T")), (3, dict(result_id="f")), (3, dict(result_anchor=[float("nan"), 1])),
        ]:
            with self.subTest(fields=fields):
                ac = copy.deepcopy(self.ac)
                ac["source_group"]["displaced_number_frame"]["members"][index].update(fields)
                self.assertFalse(_source_layout_preserved(ac))
        self.rows.pop()
        self.assertFalse(_source_layout_preserved(self.ac))

    def test_legacy_requires_preserved_layout(self):
        trace = self.ac["source_group"]
        del trace["displaced_number_frame"]
        self.assertFalse(_source_layout_preserved(self.ac))
        trace["relative_positions_preserved"] = True
        self.assertTrue(_source_layout_preserved(self.ac))
