import copy
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from fire_audit.core import FireAuditApp
from fire_audit.barrier_dxf import library
from fire_audit.barrier_inputs import defaults, snapshot_attachment, snapshot_manual_categories
from fire_audit.io_utils import read_json, write_json
from test_barrier_workflow import fixture_plan

ROOT=Path(__file__).resolve().parents[1]


class FileBridge:
    """Offline state-machine fixture; live AutoCAD is tested separately."""
    def __init__(self, source): self.current=Path(source)
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def call(self,name,args=None):
        assert name=='get_drawing_info'
        return {'path':str(self.current),'units':'Millimeters'}
    def call_write(self,name,args):
        if name=='open_drawing': self.current=Path(args['path'])
        elif name=='save_drawing':
            target=Path(args['filePath']);shutil.copy2(self.current,target)
            if target.suffix=='.dwg': self.current=target
        else: raise AssertionError(name)


class BarrierServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for folder in ('config','knowledge','templates'):shutil.copytree(ROOT/folder,self.root/folder)
        self.app=FireAuditApp(self.root)
        self.source=self.root/'source.dwg'
        doc=library().new('R2013');doc.units=4
        h=doc.modelspace().add_line((0,0),(10000,0)).dxf.handle;doc.saveas(self.source)
        self.plan=fixture_plan(h)
        p=self.app.projects.create('Технический тест','test');p['dates']['gpzu']='2025-11-12';p['dwg']['path']=str(self.source)
        data=defaults();data.update(categories_approved=True,category_approval_source='Тест',stu_status='absent',
            additional_documents_reviewed=True,preparation_completed=True,source_explanations_reviewed=True,room_match_completed=True,
            rooms=[{'floor':'1','number':'101','name':'Склад','category':'В2','area_m2':60}])
        data['category_source']=snapshot_manual_categories(self.app.paths,'test',data)
        self.note=self.root/'fixture.txt';self.note.write_text('Технический источник. Не нормативный документ.',encoding='utf-8')
        a=snapshot_attachment(self.app.paths,'test',self.note,'additional');data['attachments']=[a]
        self.attachment=Path(a['path'])
        self.plan['sources']=[{'id':'test-source','kind':'attachment','path':a['path'],'page':1,
                              'quote':'Технический источник.','interpretation':'Только технический тест'}]
        p['barrier_assessment']=data;self.app.projects.save('test',p)
        self.run=self.app.runs.create('test','barrier_assessment')['run']['run_id']
        self.cad=FileBridge(self.source);self.app.barrier_workflow.bridge_factory=lambda:self.cad
        self.flow=self.app.barrier_workflow

    def test_geometry_cycle_does_not_bypass_normative_completion_and_repeated_execution_block(self):
        sha=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.flow.prepare('test',self.run,self.plan)
        result=self.flow.build_copy_from_ui('test',self.run)
        self.assertTrue(result['ok'],result)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),sha)
        loaded=self.app.runs.load('test',self.run)
        self.assertEqual(loaded['run']['status'],'geometry_verified')
        self.assertFalse(loaded['registry']['dwg_actions']['work_completed'])
        self.assertTrue(loaded['registry']['barrier_assessment']['verification']['ok'])
        with self.assertRaises(ValueError):self.flow.build_copy_from_ui('test',self.run)
        with self.assertRaises(ValueError):self.flow.prepare('test',self.run,self.plan)
        with self.assertRaisesRegex(ValueError, 'контроль замечаний'):
            self.app.runs.record_result_dwg('test', self.run, result['result_dwg'],
                source_document_contains_results=False, copy_did_not_change_open_source_identity=True)
        checks={"scope_complete":True,"sources_verified":True,"mode_rules_verified":True,
            "geometry_verified":True,"object_properties_verified":True,"visual_review_complete":True,
            "source_dwg_unchanged":True,"result_saved_and_reopened":True,"registry_complete":True}
        with self.assertRaisesRegex(ValueError, 'контроль замечаний'):
            self.app.runs.record_quality_control('test',self.run,{"checks":checks,
                "created_or_changed_entity_ids":[], "unresolved":[]})
        # An offline geometry fixture does not perform the independent normative
        # scan or populate the itemized completion registers. Generic QC flags
        # must not let it pass as a fully reviewed fire-barrier scheme.
        # Even a manually asserted completion flag cannot bypass either gate.
        registry_path = self.app.runs.run_dir('test', self.run) / 'РЕЕСТР_ПРОВЕРКИ.json'
        registry = read_json(registry_path)
        registry['dwg_actions']['work_completed'] = True
        write_json(registry_path, registry)
        validation = self.app.runs.validate('test',self.run,True)
        self.assertFalse(validation['ok'])
        self.assertTrue(any('контроль замечаний' in error for error in validation['errors']))
        self.assertTrue(any('Не закрыт реестр' in error for error in validation['errors']))
        self.assertTrue(any('самостоятельный нормативный обход' in error for error in validation['errors']))
        # An unrelated new line must invalidate a previously verified result.
        doc=library().readfile(self.cad.current);doc.modelspace().add_line((1,1),(2,2));doc.saveas(self.cad.current)
        self.assertFalse(self.flow.verify('test',self.run)['ok'])

    def test_changed_input_or_plan_cannot_be_applied(self):
        self.flow.prepare('test',self.run,self.plan)
        self.attachment.write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):self.flow.build_copy_from_ui('test',self.run)
        state_path=self.app.runs.run_dir('test',self.run)/'ПЛАН_ПП.json'
        state=read_json(state_path);state['plan']['items'][0]['points'][1]=[50,0];write_json(state_path,state)
        self.assertTrue(self.flow.status('test',self.run)['errors'])

    def test_resaved_source_is_accepted_only_when_fresh_geometry_matches(self):
        self.flow.prepare('test',self.run,self.plan)
        prepared=read_json(self.app.runs.run_dir('test',self.run)/'ПЛАН_ПП.json')
        with self.source.open('ab') as stream:
            stream.write(b'\n')
        self.assertNotEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),prepared['source_disk_sha256'])
        result=self.flow.build_copy_from_ui('test',self.run)
        self.assertTrue(result['ok'],result)
        state=read_json(self.app.runs.run_dir('test',self.run)/'ПЛАН_ПП.json')
        self.assertEqual(state['source_resave_accepted']['prepared_sha256'],prepared['source_disk_sha256'])
        self.assertEqual(state['source_resave_accepted']['build_sha256'],state['build_source_disk_sha256'])

    def test_resaved_source_with_geometry_change_is_rejected(self):
        self.flow.prepare('test',self.run,self.plan)
        changed=library().readfile(self.source)
        changed.modelspace().add_line((0,100),(10000,100))
        changed.saveas(self.source)
        with self.assertRaisesRegex(ValueError,'Открытый чертёж изменён'):
            self.flow.build_copy_from_ui('test',self.run)
        state=read_json(self.app.runs.run_dir('test',self.run)/'ПЛАН_ПП.json')
        self.assertEqual(state['status'],'prepared')
        self.assertNotIn('source_resave_accepted',state)

    def test_manual_source_is_versioned(self):
        p=self.app.projects.load('test');data=p['barrier_assessment'];original=data['category_source']
        data['rooms'][0]['category']='В3'
        newer=snapshot_manual_categories(self.app.paths,'test',data)
        self.assertNotEqual(original['sha256'],newer['sha256'])
        self.assertTrue(Path(original['path']).is_file())
        self.assertEqual(hashlib.sha256(Path(newer['path']).read_bytes()).hexdigest(),newer['sha256'])
