import copy
import unittest

from fire_audit.barrier_inputs import category_project_for_plan, additional_inputs_markdown
from fire_audit.barriers import preparation_errors


class CategoryDocumentTests(unittest.TestCase):
    def setUp(self):
        self.old = {"floor": "1", "number": "101", "name": "Склад", "category": "В2"}
        self.project = {"barrier_assessment": {
            "rooms": [self.old],
            "categories_approved": True,
            "category_approval_source": "Подтверждение заказчика",
            "category_source": {"path": "previous.json", "sha256": "old"},
            "attachments": [{"path": "categories.pdf", "sha256": "new", "role": "categories"}],
        }}
        self.plan = {
            "category_review": {"rooms": [{**self.old, "category": "В3", "source_id": "doc"}]},
            "sources": [{"id": "doc", "kind": "attachment", "path": "categories.pdf",
                         "page": 2, "quote": "101 Склад В3", "interpretation": "Сверено с планом"}],
        }

    def test_reviewed_document_overrides_legacy_without_mutating_passport(self):
        before = copy.deepcopy(self.project)
        result = category_project_for_plan(self.project, self.plan)
        self.assertEqual(result["barrier_assessment"]["rooms"][0]["category"], "В3")
        self.assertEqual(result["barrier_assessment"]["category_source"]["sha256"], "new")
        self.assertEqual(self.project, before)

    def test_document_requires_read_evidence_and_registered_source(self):
        for field, value in (("quote", "101 Склад В2"), ("path", "other.pdf"), ("page", 0)):
            with self.subTest(field=field):
                plan = copy.deepcopy(self.plan)
                plan["sources"][0][field] = value
                with self.assertRaises(ValueError):
                    category_project_for_plan(self.project, plan)
        with self.assertRaises(ValueError):
            category_project_for_plan(self.project, {})

    def test_legacy_rows_and_generic_attachments_remain_supported(self):
        self.project["barrier_assessment"]["attachments"][0]["role"] = "additional"
        self.assertIs(category_project_for_plan(self.project, {}), self.project)
        self.assertEqual(category_project_for_plan(self.project, self.plan)
                         ["barrier_assessment"]["rooms"][0]["category"], "В3")

    def test_document_only_inputs_do_not_require_manual_table(self):
        data = self.project["barrier_assessment"]
        data["rooms"] = []
        data["category_source"] = {}
        data["stu_status"] = "absent"
        data["preparation_completed"] = True
        profile = {"status": "verified", "unresolved_fields": []}
        self.assertEqual(preparation_errors(self.project, profile), [])
        self.assertIn("category_review", additional_inputs_markdown(self.project))
