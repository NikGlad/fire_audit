import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fire_audit.barrier_inputs import read_category_workbook, merge_category_rows, validate_inputs, defaults
from fire_audit.barrier_dxf import library, build_dxf, verify_entities, protected_changes, model_snapshot
from fire_audit.barrier_workflow import validate_plan


ROOT = Path(__file__).resolve().parents[1]


def fixture_plan(handle):
    return {"review":{"gpzu_date":"2025-11-12", "edition_basis":"Технический тест", "comments_and_letters_reviewed":True, "special_requirements_reviewed":True,
                      "sp2_5_4_18_interfloor_belts_graphics_omitted":True},
        "scope":{"description":"Технический тест генератора, не оценка реального здания", "reviewer":"unit test", "inventory_complete":True,
                 "open_mezzanines_and_platforms_reviewed":True},
        "sources":[{"id":"test-source"}],
        "legend":[{"key":"partition_2", "name":"Перегородка 2-го типа (EI 15)", "color_index":5}],
        "rooms":[{"key":"1:101", "name":"Склад", "source_handles":[handle], "boundary":[[0,0],[10000,0],[10000,6000],[0,6000]],
            "openings_reviewed":True, "edges":[
                {"decision":"barrier", "items":["b1"], "basis":"Технический тест", "sources":["test-source"]},
                *[{"decision":"not_required", "basis":"Технический тест", "sources":["test-source"]} for _ in range(3)]]}],
        "open_components":[],
        "items":[
            {"id":"b1", "kind":"barrier", "legend_key":"partition_2", "points":[[0,0],[10000,0]], "source_handles":[handle], "basis":"Технический тест", "sources":["test-source"]},
            {"id":"cat", "kind":"category", "room_key":"1:101", "category":"В2", "room_name":"Склад", "position":[1000,2000]},
            {"id":"door", "kind":"callout", "element":"door", "text":"Противопожарная дверь 2-го типа (EI 30)", "position":[18000,17000], "leader_start":[18000,15000], "target":[4000,0], "independent_requirement":True, "basis":"Технический тест", "sources":["test-source"], "source_handles":[handle]},
            {"id":"d1", "kind":"dimension", "p1":[0,0], "p2":[10000,0], "base":[0,-1000]},
            {"id":"l1", "kind":"legend", "legend_key":"partition_2", "position":[16000,10000], "start":[13000,10000], "end":[15000,10000]}],
        "unresolved":[]}


class BarrierWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.ezdxf = library()
        self.doc = self.ezdxf.new("R2013")
        self.doc.units = 4
        self.original = self.doc.modelspace().add_line((0,0),(10000,0))
        self.source = self.folder/'source.dxf'
        self.doc.saveas(self.source)
        self.plan = fixture_plan(self.original.dxf.handle)
        self.project = {'dates':{'gpzu':'2025-11-12'},'barrier_assessment':{'rooms':[{'floor':'1','number':'101','name':'Склад','category':'В2'}]}}
        self.profile = json.loads((ROOT/'knowledge/barrier_drawing_profile.json').read_text(encoding='utf-8'))

    def test_builds_real_dxf_entities_and_detects_tampering(self):
        output = self.folder/'result.dxf'
        receipt = build_dxf(self.source, output, self.plan, self.profile)
        doc = self.ezdxf.readfile(output)
        self.assertEqual(protected_changes(receipt['source_entities'], doc), {'missing':[], 'changed':[]})
        self.assertEqual(verify_entities(doc, receipt['created']), [])
        barrier = doc.entitydb[receipt['created'][0]['handles'][0]]
        self.assertEqual(barrier.dxf.color, 5)
        self.assertEqual(barrier.dxf.lineweight, 50)
        category = doc.entitydb[receipt['created'][1]['handles'][0]]
        self.assertEqual(category.dxf.layer, 'экспертиза категории')
        callout_receipt = receipt['created'][2]
        ray = doc.entitydb[callout_receipt['handles'][1]]
        self.assertEqual(ray.dxf.color, 256)
        self.assertEqual(ray.dxf.linetype, 'ByLayer')
        self.assertEqual(ray.dxf.lineweight, -1)
        dim = doc.entitydb[receipt['created'][3]['handles'][0]]
        style = doc.dimstyles.get(dim.dxf.dimstyle)
        self.assertEqual(style.dxf.dimtxt, 300)
        self.assertIn('none', style.dxf.dimblk.casefold())
        self.assertGreater(len(doc.blocks.get(dim.dxf.geometry)), 0)
        barrier.dxf.color = 6
        self.assertTrue(verify_entities(doc, receipt['created']))
        barrier.dxf.color = 5
        category.dxf.layer = 'экспертиза противопожарные преграды'
        self.assertTrue(verify_entities(doc, receipt['created']))
        doc.entitydb[self.original.dxf.handle].dxf.end = (9999,0,0)
        self.assertTrue(protected_changes(receipt['source_entities'], doc)['changed'])

    def test_plan_blocks_missing_edges_sources_and_wrong_categories(self):
        self.assertEqual(validate_plan(self.plan,self.project), [])
        broken = copy.deepcopy(self.plan)
        broken['items'][0]['points'][1] = [9990,0]
        self.assertTrue(any('покрывает' in e for e in validate_plan(broken,self.project)))
        broken = copy.deepcopy(self.plan)
        broken['items'][1]['category'] = 'В1'
        self.assertTrue(any('таблице' in e for e in validate_plan(broken,self.project)))
        broken = copy.deepcopy(self.plan)
        broken['rooms'][0]['openings_reviewed'] = False
        self.assertTrue(any('проёмов' in e for e in validate_plan(broken,self.project)))
        broken = copy.deepcopy(self.plan)
        broken['items'][2]['independent_requirement'] = False
        self.assertTrue(any('дублирует' in e for e in validate_plan(broken,self.project)))

    def test_shared_boundaries_do_not_require_duplicate_graphics(self):
        plan = copy.deepcopy(self.plan)
        plan['rooms'].append({**copy.deepcopy(plan['rooms'][0]),'key':'1:102'})
        self.assertEqual(validate_plan(plan,self.project), [])
        plan['items'].append({**plan['items'][0],'id':'duplicate'})
        self.assertTrue(any('дублируется' in e for e in validate_plan(plan,self.project)))

    def test_graphic_exclusion_preserves_requirement_and_explicit_remark_priority(self):
        plan = copy.deepcopy(self.plan)
        edge = plan['rooms'][0]['edges'][1]
        edge.update(decision='excluded_from_drawing', graphic_exclusion='stairwell_wall',
                    adjacent_space_kind='stairwell', explicit_remark_requires_barrier=False,
                    normative_requirement='Требование сохранено в реестре; графика исключена правилом состава схемы',
                    source_handles=[self.original.dxf.handle])
        self.assertEqual(validate_plan(plan, self.project), [])
        for field, value in [('explicit_remark_requires_barrier', True),
                             ('normative_requirement', ''), ('source_handles', []),
                             ('adjacent_space_kind', 'internal'), ('items', ['b1'])]:
            broken = copy.deepcopy(plan)
            broken['rooms'][0]['edges'][1][field] = value
            self.assertTrue(any('исключение из схемы' in e for e in validate_plan(broken, self.project)))

        required = copy.deepcopy(self.plan)
        required_edge = required['rooms'][0]['edges'][0]
        required_edge.update(
            adjacent_space_kind='stairwell', explicit_remark_requires_barrier=True,
            explicit_remark_layer='экспертиза противопожарные преграды пояснения',
            explicit_remark_fire_resistance='EI 45',
            explicit_remark_source_handles=[self.original.dxf.handle],
        )
        self.assertEqual(validate_plan(required, self.project), [])
        for field in ('explicit_remark_requires_barrier', 'explicit_remark_layer',
                      'explicit_remark_fire_resistance', 'explicit_remark_source_handles'):
            broken = copy.deepcopy(required)
            broken['rooms'][0]['edges'][0][field] = False if field == 'explicit_remark_requires_barrier' else ''
            self.assertTrue(any('линию на стене ЛК' in e for e in validate_plan(broken, self.project)))

    def test_every_vent_chamber_requires_exact_query_and_ray(self):
        plan = copy.deepcopy(self.plan)
        plan['rooms'][0]['name'] = 'Венткамера'
        plan['rooms'][0]['edges'][0]['adjacent_space_kind'] = 'internal'
        for edge in plan['rooms'][0]['edges'][1:]:
            edge['adjacent_space_kind'] = 'outside'
        plan['items'][0]['vent_chamber_initial_requirement'] = {
            "minimum":"fire_partition_type_1", "minimum_fire_resistance":"EI45",
            "selected":"fire_partition_type_1",
            "higher_requirement_applied":False}
        plan['rooms'].append({
            "key":"1:102", "name":"Вентиляционная камера", "source_handles":[self.original.dxf.handle],
            "boundary":[[10000,0],[20000,0],[20000,6000],[10000,6000]],
            "openings_reviewed":True,
            "edges":[{"decision":"not_required", "basis":"Технический тест", "sources":["test-source"],
                      "adjacent_space_kind":"outside"} for _ in range(4)]})
        errors = validate_plan(plan, self.project)
        self.assertTrue(any('венткамеры нужен ровно один луч' in error for error in errors))
        plan['items'].append({
            "id":"vent-query", "kind":"callout", "element":"vent_chamber",
            "text":"Просим уточнить данная венткамера относится к противодымной защите? Если да, то просим уточнить, данная венткамера обслуживает только пожарный отсек, в котором расположена? Пока рассматриваем, что да.",
            "position":[18000,12000], "leader_start":[18000,11000],
            "targets":[[5000,3000],[15000,3000]], "room_keys":["1:101","1:102"],
            "assumption_until_response":True, "basis":"Постоянная инструкция пользователя",
            "sources":[], "source_handles":[self.original.dxf.handle]})
        self.assertEqual(validate_plan(plan, self.project), [])
        output = self.folder/'vent-query-result.dxf'
        receipt = build_dxf(self.source, output, plan, self.profile)
        vent_receipt = next(item for item in receipt['created'] if item['id'] == 'vent-query')
        self.assertEqual(len(vent_receipt['handles']), 3)  # one text and two rays
        result = self.ezdxf.readfile(output)
        for handle in vent_receipt['handles'][1:]:
            ray = result.entitydb[handle]
            self.assertEqual(ray.dxf.color, 256)
            self.assertEqual(ray.dxf.linetype, 'ByLayer')
            self.assertEqual(ray.dxf.lineweight, -1)
        plan['items'][-1]['text'] = 'Другой текст'
        self.assertTrue(any('обязательный текст' in error for error in validate_plan(plan, self.project)))

    def test_initial_vent_chamber_boundary_requires_type_1_or_higher(self):
        plan = copy.deepcopy(self.plan)
        plan['rooms'][0]['name'] = 'Венткамера ПД'
        plan['rooms'][0]['edges'][0]['adjacent_space_kind'] = 'internal'
        for edge in plan['rooms'][0]['edges'][1:]:
            edge['adjacent_space_kind'] = 'outside'
        plan['items'][0]['vent_chamber_initial_requirement'] = {
            "minimum":"fire_partition_type_1", "minimum_fire_resistance":"EI45",
            "selected":"fire_partition_type_1",
            "higher_requirement_applied":False}
        plan['items'].append({
            "id":"vent-query", "kind":"callout", "element":"vent_chamber",
            "text":"Просим уточнить данная венткамера относится к противодымной защите? Если да, то просим уточнить, данная венткамера обслуживает только пожарный отсек, в котором расположена? Пока рассматриваем, что да.",
            "position":[18000,12000], "leader_start":[18000,11000],
            "targets":[[5000,3000]], "room_keys":["1:101"],
            "assumption_until_response":True, "basis":"Постоянная инструкция пользователя",
            "sources":[], "source_handles":[self.original.dxf.handle]})
        self.assertEqual(validate_plan(plan, self.project), [])
        broken = copy.deepcopy(plan)
        broken['rooms'][0]['edges'][0]['decision'] = 'not_required'
        broken['rooms'][0]['edges'][0]['items'] = []
        self.assertTrue(any('должна быть противопожарной преградой' in error for error in validate_plan(broken, self.project)))
        higher = copy.deepcopy(plan)
        higher['items'][0]['vent_chamber_initial_requirement'] = {
            "minimum":"fire_partition_type_1", "minimum_fire_resistance":"EI45",
            "selected":"fire_wall_type_1",
            "higher_requirement_applied":True, "higher_requirement_basis":"Самостоятельное требование технического теста"}
        self.assertEqual(validate_plan(higher, self.project), [])
        excluded = copy.deepcopy(plan)
        edge = excluded['rooms'][0]['edges'][1]
        edge.update(decision='excluded_from_drawing', graphic_exclusion='stairwell_wall',
                    adjacent_space_kind='stairwell', explicit_remark_requires_barrier=False,
                    normative_requirement='Преграда венткамеры EI 45: требование учтено без линии на стене ЛК',
                    source_handles=[self.original.dxf.handle])
        self.assertEqual(validate_plan(excluded, self.project), [])
        edge['decision'] = 'not_required'
        self.assertTrue(any('должна быть противопожарной преградой' in error
                            for error in validate_plan(excluded, self.project)))
        corridor = copy.deepcopy(plan)
        corridor['rooms'][0]['edges'][0]['adjacent_space_kind'] = 'corridor'
        self.assertEqual(validate_plan(corridor, self.project), [])
        corridor['rooms'][0]['edges'][0]['decision'] = 'not_required'
        corridor['rooms'][0]['edges'][0]['items'] = []
        self.assertTrue(any('должна быть противопожарной преградой' in error
                            for error in validate_plan(corridor, self.project)))

    def test_open_mezzanine_without_rooms_is_part_of_host_room(self):
        plan = copy.deepcopy(self.plan)
        plan['open_components'].append({
            "key":"1:mezzanine-A", "kind":"open_mezzanine", "host_room_key":"1:101",
            "open_to_host_volume":True, "contains_rooms":False,
            "treated_as_part_of_host_room":True,
            "source_handles":[self.original.dxf.handle], "separating_barrier_item_ids":[]})
        self.assertEqual(validate_plan(plan, self.project), [])
        separated = copy.deepcopy(plan)
        separated['open_components'][0]['separating_barrier_item_ids'] = ['b1']
        self.assertTrue(any('не требуется самостоятельная преграда' in error for error in validate_plan(separated, self.project)))
        enclosed = copy.deepcopy(plan)
        enclosed['open_components'][0]['contains_rooms'] = True
        self.assertTrue(any('является частью основного помещения' in error for error in validate_plan(enclosed, self.project)))

    def test_interfloor_belt_graphics_are_always_rejected(self):
        plan = copy.deepcopy(self.plan)
        plan['items'].append({
            "id":"belt-dimension", "kind":"dimension", "p1":[0,0], "p2":[0,1200], "base":[-1000,0],
            "rule_reference":"sp2_5_4_18_interfloor_belts"})
        self.assertTrue(any('графику не наносить' in error for error in validate_plan(plan, self.project)))
        unconfirmed = copy.deepcopy(self.plan)
        unconfirmed['review']['sp2_5_4_18_interfloor_belts_graphics_omitted'] = False
        self.assertTrue(any('Не подтверждено отсутствие графики' in error for error in validate_plan(unconfirmed, self.project)))

    def test_category_import_real_source_and_equipment_preservation(self):
        path = ROOT/'projects/Горшково_ПП/sources/04.08.2026 Горшково Таблица с нагрузкой.xlsx'
        if not path.exists():
            self.skipTest('Local integration input not distributed')
        data = read_category_workbook(path)
        self.assertEqual(len(data['rooms']), 23)
        storage = next(r for r in data['rooms'] if r['number']=='209')
        self.assertEqual(storage['category'], 'В2')
        self.assertEqual(storage['area_m2'], 5.39)
        self.assertEqual(storage['label'], 'В2С')
        old = [{**storage, 'equipment_description':'Сохранить', 'matched_cad_handles':['AB']}]
        merged = merge_category_rows(old,[storage])
        self.assertEqual(merged[0]['equipment_description'],'Сохранить')
        self.assertEqual(merged[0]['matched_cad_handles'],['AB'])
        merged = merge_category_rows(old,[{**storage,'category':'В3'}])
        self.assertNotIn('matched_cad_handles', merged[0])

    def test_unknown_does_not_become_absence(self):
        data=defaults()
        self.assertEqual(data['stu_status'],'unknown')
        self.assertIsNone(data['refrigeration_present'])
        data['categories_approved']=True
        with self.assertRaises(ValueError):
            validate_inputs(data)

    def test_hidden_layer_and_changed_styles_do_not_pass(self):
        from fire_audit.barriers import LAYER
        from fire_audit.barrier_dxf import text_style, supporting_snapshot, supporting_changes
        layer=self.doc.layers.new(LAYER);layer.off();self.doc.saveas(self.source)
        with self.assertRaisesRegex(ValueError,'Слой ПП'):
            build_dxf(self.source,self.folder/'hidden.dxf',self.plan,self.profile)
        layer.on()
        name=text_style(self.doc,self.profile['category'],'Категории')
        before=supporting_snapshot(self.doc)
        self.doc.styles.get(name).dxf.height=800
        self.assertTrue(supporting_changes(before,self.doc))
        with self.assertRaisesRegex(ValueError,'Стиль ПП'):
            text_style(self.doc,self.profile['category'],'Категории')

    def test_dimension_block_renaming_keeps_content_check(self):
        output=self.folder/'dimension.dxf'
        receipt=build_dxf(self.source,output,self.plan,self.profile)
        doc=self.ezdxf.readfile(output)
        dim=doc.entitydb[receipt['created'][3]['handles'][0]]
        old=dim.dxf.geometry
        doc.blocks.rename_block(old,'*D9001');dim.dxf.geometry='*D9001'
        self.assertEqual(verify_entities(doc,receipt['created']),[])
        text=next(e for e in doc.blocks.get('*D9001') if e.dxftype()=='MTEXT')
        text.text='9999'
        self.assertTrue(verify_entities(doc,receipt['created']))

    def test_float_tolerance_does_not_hide_millimetre_change(self):
        from fire_audit.barrier_dxf import equivalent
        self.assertTrue(equivalent({'point':[50000000.123456]},{'point':[50000000.123457]}))
        self.assertFalse(equivalent({'point':[1000.0]},{'point':[1000.01]}))


if __name__=='__main__':
    unittest.main()
