import copy
import unittest

from fire_audit.barrier_remarks import SOURCE_EXPLANATIONS_LAYER, inventory_digest
from fire_audit.barrier_remarks_capture import capture_source_layer


SOURCE = r"C:\projects\source.dwg"
COPY = r"C:\projects\review.dwg"


def entity(handle="a1", entity_type="AcDbMText", **extra):
    return {"handle": handle, "type": entity_type, "layer": SOURCE_EXPLANATIONS_LAYER,
            "space": "Model", "text": "Замечание заказчика", **extra}


def page(items, matched=None, more=False):
    return {"entities": items, "count": len(items),
            "matched": len(items) if matched is None else matched, "hasMore": more}


class FakeClient:
    """No executable bridge and no connection to AutoCAD in these tests."""
    def __init__(self, pages=None, layers=None, before=None, after=None, second_pages=None,
                 blocks=None, definitions=None, second_blocks=None, second_definitions=None):
        first_pages = copy.deepcopy([page([entity()])] if pages is None else pages)
        self.pages = first_pages + copy.deepcopy(first_pages if second_pages is None else second_pages)
        self.layers = copy.deepcopy(layers if layers is not None else {
            "layers": [{"name": "0"}, {"name": SOURCE_EXPLANATIONS_LAYER}], "count": 2})
        self.before = copy.deepcopy(before if before is not None else {
            "path": SOURCE, "modified": False, "lastSavedDate": "2026-10-15T09:00:00",
            "fileModifiedDate": "2026-10-15T09:00:00", "entityCount": 10,
        })
        self.after = copy.deepcopy(self.before if after is None else after)
        self.blocks = copy.deepcopy({"blocks": [], "count": 0} if blocks is None else blocks)
        self.second_blocks = copy.deepcopy(self.blocks if second_blocks is None else second_blocks)
        self.definitions = copy.deepcopy({} if definitions is None else definitions)
        self.second_definitions = copy.deepcopy(self.definitions if second_definitions is None else second_definitions)
        self.definition_calls = {}
        self.block_calls = 0
        self.calls = []
        self.info_count = 0

    def call(self, tool, args):
        self.calls.append((tool, copy.deepcopy(args)))
        if tool == "get_drawing_info":
            self.info_count += 1
            return copy.deepcopy(self.before if self.info_count == 1 else self.after)
        if tool == "list_layers":
            return copy.deepcopy(self.layers)
        if tool == "get_entities":
            return copy.deepcopy(self.pages.pop(0))
        if tool == "list_blocks":
            self.block_calls += 1
            return copy.deepcopy(self.blocks if self.block_calls == 1 else self.second_blocks)
        if tool == "get_block_definition":
            name = args["name"]
            self.definition_calls[name] = self.definition_calls.get(name, 0) + 1
            definitions = self.definitions if self.definition_calls[name] == 1 else self.second_definitions
            return copy.deepcopy(definitions[name])
        raise AssertionError("No mutation or document-switch tool is permitted: " + tool)


class SourceLayerCaptureTests(unittest.TestCase):
    def capture(self, client):
        return capture_source_layer(client, SOURCE, [SOURCE, COPY])

    def test_raw_geometry_and_text_are_preserved_in_all_spaces(self):
        items = [entity(text="Ответ заказчика: Error, 15.10.2026", properties={"width": 4}),
                 entity("b2", "AcDbPolyline", vertices=[[0, 0], [2, 3]], space="Лист 1")]
        client = FakeClient([page(items)])
        result = self.capture(client)
        expected = copy.deepcopy(items)
        for item in expected:
            item["handle"] = item["handle"].upper()
        self.assertEqual(result["entities"], expected)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["scope"], "all")
        self.assertEqual(result["digest"], inventory_digest(result))
        self.assertEqual(client.calls, [
            ("get_drawing_info", {}), ("list_layers", {}),
            ("list_blocks", {"includeAnonymous": True, "includeXrefDependent": True}),
            ("get_entities", {"layer": SOURCE_EXPLANATIONS_LAYER, "space": "all",
                              "format": "objects", "includeProperties": True, "limit": 500, "offset": 0}),
            ("list_layers", {}),
            ("get_entities", {"layer": SOURCE_EXPLANATIONS_LAYER, "space": "all",
                              "format": "objects", "includeProperties": True, "limit": 500, "offset": 0}),
            ("list_blocks", {"includeAnonymous": True, "includeXrefDependent": True}),
            ("get_drawing_info", {})])
        self.assertEqual(items[0]["handle"], "a1")

    def test_pagination_uses_actual_offset_and_all_pages(self):
        client = FakeClient([page([entity("1")], 3, True),
                             page([entity("2"), entity("3")], 3, False)])
        result = self.capture(client)
        self.assertEqual(result["matched"], 3)
        self.assertEqual([args["offset"] for name, args in client.calls if name == "get_entities"], [0, 1, 0, 1])

    def test_empty_existing_layer_is_complete_only_with_full_page_metadata(self):
        result = self.capture(FakeClient([page([])]))
        self.assertTrue(result["layer_exists"])
        self.assertEqual(result["matched"], 0)

    def test_missing_layer_is_proven_by_complete_layer_catalog(self):
        client = FakeClient(pages=[], layers={"layers": [{"name": "0"}], "count": 1})
        result = self.capture(client)
        self.assertFalse(result["layer_exists"])
        self.assertEqual(result["entities"], [])
        self.assertEqual([name for name, _ in client.calls],
                         ["get_drawing_info", "list_layers", "list_blocks", "list_layers",
                          "list_blocks", "get_drawing_info"])

    def test_incomplete_layer_catalog_cannot_prove_absence(self):
        for layers in [
            {"layers": [], "count": 1}, {"layers": [], "count": 0, "hasMore": True},
            {"layers": [], "count": 0, "truncated": True}, {"layers": []},
            {"layers": [], "count": False}, {"layers": [], "count": 0, "matched": 4},
            {"layers": [{"name": "0"}, {"name": "0"}], "count": 2},
        ]:
            with self.subTest(layers=layers), self.assertRaises(ValueError):
                self.capture(FakeClient(layers=layers))

    def test_invalid_page_completeness_fields_are_blocking(self):
        valid = page([entity()])
        changes = [
            {"count": 0}, {"count": True}, {"count": "1"}, {"count": -1},
            {"matched": 2}, {"matched": "1"}, {"matched": True}, {"matched": -1},
            {"hasMore": True}, {"hasMore": "false"}, {"hasMore": None},
            {"truncated": True}, {"error": "interrupted"}, {"success": False},
            {"entities": ["A1"]}, {"entities": None},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.capture(FakeClient([{**valid, **change}]))
        for field in ("count", "matched", "hasMore"):
            incomplete = {key: value for key, value in valid.items() if key != field}
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.capture(FakeClient([incomplete]))

    def test_missing_last_page_or_repeated_page_is_blocking(self):
        scenarios = [
            [page([entity()], 2, True), page([], 2, False)],
            [page([entity()], 2, True), page([], 2, True)],
            [page([entity()], 2, True), page([entity()], 2, False)],
            [page([entity()], 2, True), page([entity("b2")], 3, True)],
            [page([entity("a1"), entity("A1")])],
        ]
        for pages in scenarios:
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                self.capture(FakeClient(pages))

    def test_wrong_layer_and_unreadable_properties_are_blocking(self):
        for invalid in [
            entity(layer="другой слой"), entity(propertiesError="cannot read geometry"),
            entity(properties={"error": "cannot read text"}), entity(handle="not-a-handle"),
            entity(propertiesTruncated=True), entity(type="UnknownObject"),
        ]:
            with self.subTest(entity=invalid), self.assertRaises(ValueError):
                self.capture(FakeClient([page([invalid])]))

    def test_block_proxy_and_block_multileader_are_not_treated_as_empty(self):
        for invalid in [
            entity(entity_type="AcDbBlockReference"), entity(entity_type="BlockReference"),
            entity(entity_type="AcDbProxyEntity"), entity(entity_type="Line", isProxy=True),
            entity(entity_type="MLeader", contentType="BlockContent"),
        ]:
            client = FakeClient([page([invalid])])
            with self.subTest(entity=invalid), self.assertRaisesRegex(ValueError, "вложенн"):
                self.capture(client)
            self.assertTrue(all(name in {"get_drawing_info", "list_layers", "get_entities", "list_blocks"}
                                for name, _ in client.calls))

    def test_supported_geometry_only_layer_is_captured(self):
        items = []
        for index, kind in enumerate(("AcDbLine", "Arc", "Circle", "Hatch", "Leader", "MLeader",
                                      "Spline", "Solid", "AcDbAlignedDimension", "DBText"), 1):
            item = entity(format(index, "X"), kind)
            if kind != "DBText":
                item.pop("text")
            items.append(item)
        self.assertEqual(len(self.capture(FakeClient([page(items)]))["entities"]), len(items))

    def test_wrong_drawing_or_changed_state_never_yields_inventory(self):
        original = FakeClient().before
        for change in [
            {"path": COPY}, {"modified": True}, {"lastSavedDate": "2026-10-15T10:00:00"},
            {"fileModifiedDate": "2026-10-15T10:00:00"}, {"entityCount": 11},
        ]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "изменились"):
                self.capture(FakeClient(after={**original, **change}))
        with self.assertRaisesRegex(ValueError, "другой DWG"):
            self.capture(FakeClient(before={**original, "path": r"C:\unrelated.dwg"}))
        for field in ("modified", "lastSavedDate"):
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.capture(FakeClient(before={key: value for key, value in original.items() if key != field}))

    def test_allowed_copy_and_windows_path_normalization(self):
        info = {**FakeClient().before, "path": "c:/PROJECTS/review.dwg"}
        result = self.capture(FakeClient(before=info))
        self.assertEqual(result["captured_document_path"], info["path"])
        self.assertEqual(result["source_path"], SOURCE)

    def test_object_limit_is_blocking(self):
        with self.assertRaisesRegex(ValueError, "100000"):
            self.capture(FakeClient([page([entity()], 100001, True)]))

    def test_second_read_detects_changed_text_at_same_handle_and_modified_true(self):
        info = {**FakeClient().before, "modified": True}
        client = FakeClient(before=info, second_pages=[page([entity(text="Изменённый ответ заказчика")])])
        with self.assertRaisesRegex(ValueError, "между двумя полными чтениями"):
            self.capture(client)

    def test_entity_order_is_stable_and_not_a_change(self):
        client = FakeClient([page([entity("b2"), entity("a1")])],
                            second_pages=[page([entity("a1"), entity("b2")])])
        self.assertEqual([item["handle"] for item in self.capture(client)["entities"]], ["A1", "B2"])

    def test_case_variant_source_layer_is_read_with_actual_name(self):
        name = SOURCE_EXPLANATIONS_LAYER.upper()
        client = FakeClient([page([entity(layer=name)])], layers={"layers": [{"name": name}], "count": 1})
        result = self.capture(client)
        self.assertTrue(result["layer_exists"])
        self.assertEqual(result["actual_layer_name"], name)
        self.assertEqual(result["entities"][0]["layer"], name)
        self.assertTrue(all(args["layer"] == name for tool, args in client.calls if tool == "get_entities"))

    def test_hidden_source_inside_other_layer_insert_is_blocking(self):
        # Direct layer read is empty; a child TEXT is on the target layer inside
        # an INSERT on layer 0. Whole-definition counts must find it anyway.
        for layer in (SOURCE_EXPLANATIONS_LAYER, SOURCE_EXPLANATIONS_LAYER.upper(),
                      "xref|" + SOURCE_EXPLANATIONS_LAYER):
            client = FakeClient(pages=[page([])], blocks={
                "blocks": [{"name": "B", "entityCount": 2}], "count": 1}, definitions={
                    "B": {"name": "B", "byLayer": {"0": 1, layer: 1},
                          "entities": [{"type": "Line", "layer": "0"}], "hasMore": True}})
            with self.subTest(layer=layer), self.assertRaisesRegex(ValueError, "вложенные источники"):
                self.capture(client)
            self.assertIn(("get_block_definition", {
                "name": "B", "includeProperties": False, "limit": 1, "offset": 0}), client.calls)

    def test_all_block_summary_schemas_and_layout_exclusion(self):
        schemas = [{"byLayer": {"0": 2}}, {"countsByLayer": [{"layer": "0", "count": 2}]},
                   {"entitiesByLayer": [{"name": "0", "count": 2}]},
                   {"counts": {"byLayer": {"0": 2}}}]
        for summary in schemas:
            client = FakeClient(blocks={"count": 2, "blocks": [
                {"name": "*Model_Space", "entityCount": 100, "isLayout": True},
                {"name": "*U1", "entityCount": 2}]}, definitions={"*U1": summary})
            with self.subTest(summary=summary):
                self.assertTrue(self.capture(client)["nested_sources_checked"])
            reads = [args["name"] for tool, args in client.calls if tool == "get_block_definition"]
            self.assertEqual(reads, ["*U1", "*U1"])

    def test_unknown_or_inconsistent_block_summary_cannot_prove_absence(self):
        for definition in [{}, {"entities": []}, {"byLayer": {}}, {"byLayer": {"0": 1}},
                           {"byLayer": {"0": 2}, "countsByLayer": {"other": 2}},
                           {"byLayer": {"0": True}}, {"byLayer": [], "truncated": True},
                           {"byLayer": {"0": 2}, "entityCount": 3}]:
            client = FakeClient(blocks={"count": 1, "blocks": [{"name": "B", "entityCount": 2}]},
                                definitions={"B": definition})
            with self.subTest(definition=definition), self.assertRaises(ValueError):
                self.capture(client)

    def test_incomplete_or_changed_block_catalog_is_blocking(self):
        for catalog in [{"blocks": [], "count": 1}, {"blocks": [], "count": 0, "hasMore": True},
                        {"blocks": [], "count": 0, "xrefDependentExcluded": 1},
                        {"blocks": [], "count": 0, "anonymousExcluded": 1},
                        {"blocks": [], "count": 0, "matched": 1},
                        {"blocks": [{"name": "B"}], "count": 1}]:
            with self.subTest(catalog=catalog), self.assertRaises(ValueError):
                self.capture(FakeClient(blocks=catalog))
        with self.assertRaisesRegex(ValueError, "Каталог блоков изменился"):
            self.capture(FakeClient(second_blocks={"count": 1, "blocks": [{"name": "B", "entityCount": 0}]}))

    def test_second_definition_read_detects_nested_source_with_same_catalog_and_modified_true(self):
        client = FakeClient(before={**FakeClient().before, "modified": True},
                            blocks={"count": 1, "blocks": [{"name": "B", "entityCount": 2}]},
                            definitions={"B": {"byLayer": {"0": 2}}},
                            second_definitions={"B": {"byLayer": {"0": 1, SOURCE_EXPLANATIONS_LAYER: 1}}})
        with self.assertRaisesRegex(ValueError, "вложенные источники"):
            self.capture(client)
        self.assertEqual(client.definition_calls, {"B": 2})

    def test_empty_unloaded_or_unresolved_xref_is_never_complete(self):
        for status in ("Unloaded", "Unresolved", "Resolved"):
            client = FakeClient(pages=[page([])], blocks={"count": 1, "blocks": [
                {"name": "EXT", "entityCount": 0, "isXref": True, "xrefStatus": status}]},
                definitions={"EXT": {"byLayer": {}, "entityCount": 0}})
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "внешней ссылке XREF"):
                self.capture(client)
            self.assertEqual(client.definition_calls, {})

    def test_xref_dependent_definition_is_blocking_even_without_parent_catalog_record(self):
        variants = [{"name": "B", "xrefDependent": True},
                    {"name": "B", "isXrefDependent": True},
                    {"name": "EXT|B", "isXref": False, "xrefDependent": False}]
        for variant in variants:
            block = {"entityCount": 0, **variant}
            client = FakeClient(blocks={"count": 1, "blocks": [block]},
                                definitions={block["name"]: {"byLayer": {}}})
            with self.subTest(block=block), self.assertRaisesRegex(ValueError, "внешней ссылке XREF"):
                self.capture(client)

    def test_only_reserved_space_names_are_skipped_without_layout_flag(self):
        reserved = ["*Model_Space", "*Paper_Space", "*Paper_Space0", "*Paper_Space123"]
        similar = ["Model_Space", "*Model_Space1", "*Paper_SpaceNotes", "*Paper_Space1_suffix"]
        client = FakeClient(blocks={"count": len(reserved + similar), "blocks": [
            {"name": name, "entityCount": 0} for name in reserved + similar]},
            definitions={name: {"byLayer": {}} for name in similar})
        self.assertTrue(self.capture(client)["nested_sources_checked"])
        self.assertEqual(client.definition_calls, {name: 2 for name in similar})


if __name__ == "__main__":
    unittest.main()
