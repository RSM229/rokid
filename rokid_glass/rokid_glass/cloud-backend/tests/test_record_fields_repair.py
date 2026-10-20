"""隔离SQLite、真实路由与文书输出回归；不连接生产或调用模型。"""
import asyncio
import importlib
import os
import pkgutil
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
from sqlalchemy import select, text, inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi import HTTPException
from pydantic import ValidationError
from docx import Document as WordDocument
from pypdf import PdfReader
from PIL import Image
from app.config import settings
from app.db import Base, _reconcile_missing_columns
import app.models
for module in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module('app.models.' + module.name)
from app.models.user import Project, User, ALL_PERMISSIONS
from app.models.project_member import ProjectMember
from app.models.document import Document
from app.models.document_signoff import DocumentSignoff
from app.models.work_record import WorkRecord
from app.models.parallel import ParallelInspection
from app.models.meeting import MeetingMinute
from app.models.signature import Signature
from app.models.hazard import Hazard
from app.models.rectification import RectificationRecord
from app.routers import work_record, parallel, document, signature, project
from app.routers.form_doc import _content_from_record, generate_form_from_record
from app.services.record_fields import validate_record
from app.services.period_report_service import generate_supervision_log, generate_aggregate_doc
from app.services.form_renderer import render_form
from app.services.passwords import pwd_context
from app.services.weather import daily_weather


class RecordRepairTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[4] / '.planning/record-field-audit-20261012')
        self.old_export, self.old_upload = settings.export_dir, settings.upload_dir
        settings.export_dir, settings.upload_dir = Path(self.tmp.name)/'exports', Path(self.tmp.name)/'uploads'
        settings.export_dir.mkdir(); settings.upload_dir.mkdir()
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.p = Project(name='回归项目', code='R1', construction_unit='实际施工单位', supervision_org='项目监理部', acceptance_participants='施工单位张工、监理单位李工')
        self.p2 = Project(name='其他项目', code='R2')
        self.db.add_all([self.p,self.p2]); await self.db.flush()
        self.u = User(username='tester', real_name='检查人', password_hash='test', role='inspector', project_id=self.p.id, org_role='super_admin')
        self.v = User(username='reviewer', real_name='复核人', password_hash='test', role='special_supervisor', project_id=self.p.id)
        self.db.add_all([self.u,self.v]); await self.db.flush()
        self.u.effective_permissions=set(ALL_PERMISSIONS);self.v.effective_permissions=set(ALL_PERMISSIONS)
        self.db.add_all([ProjectMember(user_id=self.u.id,project_id=self.p.id,role='inspector'), ProjectMember(user_id=self.v.id,project_id=self.p.id,role='special_supervisor')])
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close(); await self.engine.dispose()
        settings.export_dir, settings.upload_dir = self.old_export, self.old_upload
        self.tmp.cleanup()

    def rec(self, kind='巡视', **kw):
        return WorkRecord(record_type=kind, project_id=self.p.id, creator_id=self.u.id, section='A栋', work_content='检查钢筋', problems='钢筋间距偏大', treatment='重新绑扎', record_date=date(2026,10,8), **kw)

    async def test_mapping_preserves_problems_measures_and_dates(self):
        for kind in ['巡视','旁站','验收']:
            r=self.rec(kind, station_location='A栋', station_process='混凝土浇筑', station_start_time=datetime(2026,10,8,8,20), station_end_time=datetime(2026,10,8,10,25), worker_count=0, acceptance_result='不合格')
            c=_content_from_record(r,self.u)
            self.assertIn('钢筋间距偏大',c['problems']);self.assertIn('重新绑扎',c['problems'])
            self.assertEqual(c['date'],'2026-10-08')
            if kind=='旁站':self.assertEqual(c['start_time'],'2026-10-08 08:20')

    async def test_station_edit_saves_times_and_refreshes_doc(self):
        r=self.rec('旁站',station_location='A栋',station_process='混凝土浇筑',station_start_time=datetime(2026,10,8,8), station_end_time=datetime(2026,10,8,9))
        self.db.add(r);await self.db.flush()
        doc,_,_=await generate_form_from_record(self.db,self.u,self.p,r)
        await self.db.commit()
        await work_record.update_work_record(r.id,work_record.UpdateWorkRecordRequest(station_end_time=datetime(2026,10,8,11),treatment='处理措施修订'),self.u,self.db)
        await self.db.refresh(r);await self.db.refresh(doc)
        self.assertEqual(r.station_end_time.hour,11)
        self.assertEqual(doc.content['end_time'],'2026-10-08 11:00')
        self.assertIn('处理措施修订',doc.content['problems'])
        self.assertEqual(doc.content_revision,2)

    async def test_reversed_times_and_invalid_result_rejected(self):
        r=self.rec('旁站',station_start_time=datetime(2026,10,8,10),station_end_time=datetime(2026,10,8,11))
        self.db.add(r);await self.db.commit()
        with self.assertRaises(HTTPException):
            await work_record.update_work_record(r.id,work_record.UpdateWorkRecordRequest(station_end_time=datetime(2026,10,8,9)),self.u,self.db)
        with self.assertRaises(HTTPException):
            await work_record.create_acceptance(work_record.CreateAcceptanceRequest(acceptance_result='自动合格'),self.u,self.db)

    async def test_inspection_required_count_zero_allowed(self):
        r=self.rec(worker_count=None)
        with self.assertRaises(HTTPException):validate_record(r,self.p)
        r.worker_count=0;validate_record(r,self.p)
        with self.assertRaises(ValidationError):work_record.CreateInspectionRequest(worker_count=-1)

    async def test_parallel_crud_details_project_boundary(self):
        req=parallel.ParallelCreate(inspect_date='2026-10-08',part='A栋',item='钢筋',method='实测',basis='设计图纸',result_detail='实测间距200mm',conclusion='合格',treatment='无')
        response=await parallel.create_inspection(req,self.u,self.db)
        listing=await parallel.list_inspections(None,self.u,self.db)
        self.assertEqual(listing['items'][0]['basis'],'设计图纸')
        self.assertEqual(listing['items'][0]['method'],'实测')
        updated=await parallel.update_inspection(response['id'],parallel.ParallelUpdate(item='钢筋复查'),self.u,self.db)
        self.assertEqual(updated['basis'],'设计图纸');self.assertEqual(updated['result_detail'],'实测间距200mm')
        self.u.project_id=self.p2.id
        with self.assertRaises(HTTPException):await parallel.get_inspection(response['id'],self.u,self.db)

    async def test_parallel_invalid_never_defaults_to_pass(self):
        for bad in ['未知','', '是']:
            with self.assertRaises(ValidationError):parallel.ParallelCreate(inspect_date='2026-10-08',part='A',item='钢筋',result_detail='实测',conclusion=bad)
        with self.assertRaises(ValidationError):parallel.ParallelCreate(inspect_date='2026-10-08',part='A',item='钢筋',result_detail=' ',conclusion='合格')

    async def test_parallel_independent_document(self):
        p=ParallelInspection(project_id=self.p.id,user_id=self.u.id,inspect_date='2026-10-08',part='A栋',item='钢筋',basis='规范',method='实测',result_detail='检测记录',conclusion='不合格',treatment='返工')
        self.db.add(p);await self.db.commit()
        result=await parallel.generate_document(p.id,self.u,self.db)
        doc=await self.db.get(Document,result['doc_id'])
        self.assertEqual(doc.doc_type,'平行检查记录');self.assertEqual(doc.content['method'],'实测')
        result2=await parallel.generate_document(p.id,self.u,self.db);self.assertEqual(result2['doc_id'],doc.id)
        words='\n'.join(c.text for t in WordDocument(str(settings.export_dir/Path(doc.docx_path).name)).tables for row in t.rows for c in row.cells)
        self.assertIn('实测',words);self.assertIn('返工',words)

    async def test_existing_required_signoffs_are_preserved(self):
        from app.services.document_signoffs import required_slots_for, assert_required_signoffs
        expected = {"旁站记录": {"inspector"}, "巡视记录": {"inspector"}, "平行检查记录": {"reviewer"}, "监理日志": {"supervisor"}, "监理通知单": {"supervisor", "chief"}, "验收记录": {"acceptor"}}
        for kind, slots in expected.items():
            doc = Document(project_id=self.p.id, creator_id=self.u.id, doc_type=kind, title=kind, content={})
            self.db.add(doc); await self.db.flush()
            self.assertEqual(required_slots_for(doc), slots)
            with self.assertRaises(HTTPException):
                await assert_required_signoffs(self.db, doc)
            for slot in slots:
                self.db.add(DocumentSignoff(document_id=doc.id, revision=1, slot=slot, signer_id=self.u.id, signer_name=self.u.real_name, signature_id=1, signature_hash='test', content_hash='test', signed_pdf_path='test'))
            await self.db.flush()
            await assert_required_signoffs(self.db, doc)

    async def test_acceptance_has_own_form_and_participants(self):
        res=await work_record.create_acceptance(work_record.CreateAcceptanceRequest(section='隐蔽部位',acceptance_type='隐蔽验收',acceptance_content='钢筋隐蔽验收',acceptance_result='合格',record_date=date(2026,10,8)),self.u,self.db)
        doc=await self.db.get(Document,res['doc_id'])
        self.assertEqual(doc.form_code,'acceptance_record');self.assertEqual(doc.doc_type,'验收记录')
        self.assertEqual(doc.content['project_name'],'回归项目');self.assertIn('张工',doc.content['acceptance_participants'])
        words='\n'.join(p.text for p in WordDocument(str(settings.export_dir/Path(doc.docx_path).name)).paragraphs)
        self.assertIn('验收单位及人员',words);self.assertIn('合格',words)
        with self.assertRaises(HTTPException):await work_record.submit_work_record(res['record_id'],self.u,self.db)

    async def test_daily_log_full_sources_no_cross_project_leak(self):
        d=date(2026,10,8)
        self.db.add(self.rec(worker_count=5))
        self.db.add(ParallelInspection(project_id=self.p.id,user_id=self.u.id,inspect_date=d.isoformat(),part='A栋',item='平行样本',result_detail='平行检测200',basis='规范',method='实测',conclusion='不合格',treatment='返工'))
        self.db.add(MeetingMinute(project_id=self.p.id,user_id=self.u.id,meeting_date=d.isoformat(),title='工地会议',content='会议决议'))
        other=Hazard(project_id=self.p2.id,reporter_id=self.u.id,top1_risk='其他项目秘密')
        self.db.add(other);await self.db.flush()
        self.db.add(RectificationRecord(hazard_id=other.id,created_at=datetime(2026,10,8,9),recheck_remark='禁止混入'))
        await self.db.commit()
        res=await generate_supervision_log(self.db,self.p.id,d,supervisor=self.u,extras={'temp_high':30,'temp_low':20,'weather':'晴'})
        doc=await self.db.get(Document,res['doc_id'])
        self.assertIn('平行检测200',doc.content['parallel_section'])
        self.assertIn('返工',doc.content['problems_section']);self.assertIn('重新绑扎',doc.content['problems_section'])
        self.assertIn('会议决议',doc.content['meeting_section'])
        self.assertNotIn('禁止混入',str(doc.content));self.assertEqual(doc.content['temperature_high'],30)
        self.assertTrue(doc.pdf_path);self.assertGreater(len(PdfReader(str(settings.export_dir/Path(doc.pdf_path).name)).pages),0)
        docx=render_form('gd_b_220_rizhi',doc.content)
        words='\n'.join(c.text for t in WordDocument(str(docx)).tables for row in t.rows for c in row.cells)
        self.assertIn('平行检测200',words);self.assertIn('会议决议',words)

    async def test_weather_failure_and_manual_zero(self):
        result=await daily_weather(self.p,date(2026,10,8),{'temp_low':0,'temp_high':8,'weather':'雨'})
        self.assertEqual(result['temp_low'],0);self.assertEqual(result['weather_source'],'人工填写')
        with patch.dict(os.environ,{'WEATHER_FORECAST_URL':'','WEATHER_ARCHIVE_URL':''}):
            result=await daily_weather(self.p,date(2026,10,8))
        self.assertEqual(len(result['weather_missing']),3)

    async def make_signature(self,user):
        folder=settings.upload_dir/'signatures';folder.mkdir(exist_ok=True)
        path=folder/f'{user.id}.png';Image.new('RGBA',(80,40),(0,0,0,180)).save(path)
        sig=Signature(user_id=user.id,name='本人签名',image_path=f'/uploads/signatures/{user.id}.png',password_hash=pwd_context.hash('repair-test-123'))
        self.db.add(sig);await self.db.commit();return sig

    async def test_multisign_history_independence_and_revision(self):
        doc=Document(project_id=self.p.id,creator_id=self.u.id,doc_type='平行检查记录',title='签认回归',content={'project_name':self.p.name,'date':'2026-10-08','section':'A栋','acceptance_content':'钢筋','conclusion':'合格','record':'实测'},status='草稿')
        self.db.add(doc);await self.db.commit()
        sig1=await self.make_signature(self.u);sig2=await self.make_signature(self.v)
        await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig1.id,password='repair-test-123',slot='inspector'),self.u,self.db)
        await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig2.id,password='repair-test-123',slot='reviewer'),self.v,self.db)
        history=await signature.document_signoffs(doc.id,self.u,self.db)
        self.assertEqual(len(history['history']),2);self.assertTrue(all(x['current'] for x in history['history']))
        self.assertEqual(doc.content['review_date'],doc.reviewed_at.date().isoformat())
        self.assertGreater(len(PdfReader(str(settings.export_dir/Path(doc.signed_pdf_path).name)).pages),0)
        await document.update_document(doc.id,document.DocUpdateRequest(content={'record':'改后实测'}),self.u,self.db)
        history=await signature.document_signoffs(doc.id,self.u,self.db)
        self.assertFalse(any(x['current'] for x in history['history']));self.assertIsNone(doc.signed_pdf_path)
        self.assertNotIn('review_date',doc.content)

    async def test_signature_password_and_ownership(self):
        doc=Document(project_id=self.p.id,creator_id=self.u.id,doc_type='验收记录',title='验收签认',content={'project_name':self.p.name,'date':'2026-10-08','section':'A栋','acceptance_content':'钢筋','acceptance_result':'合格','acceptance_participants':'施工张工、监理李工'},status='草稿')
        self.db.add(doc);await self.db.commit()
        sig=await self.make_signature(self.u)
        with self.assertRaises(HTTPException):await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig.id,password='wrong',slot='acceptor'),self.u,self.db)
        with self.assertRaises(HTTPException):await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig.id,password='repair-test-123',slot='acceptor'),self.v,self.db)
        await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig.id,password='repair-test-123',slot='acceptor'),self.u,self.db)
        res=await document.submit_for_approval(doc.id,self.u,self.db)
        self.assertIn(res['status'],['待审批','已归档'])

    async def test_missing_weather_blocks_submission(self):
        doc=Document(project_id=self.p.id,creator_id=self.u.id,doc_type='监理日志',title='缺气象日志',content={'date':'2026-10-08'},status='草稿')
        self.db.add(doc);await self.db.commit()
        with self.assertRaises(HTTPException):await document.submit_for_approval(doc.id,self.u,self.db)

    async def test_schema_migration_idempotent(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(_reconcile_missing_columns)
            await conn.run_sync(_reconcile_missing_columns)
            names=await conn.run_sync(lambda c:{x['name'] for x in inspect(c).get_columns('work_records')})
        self.assertTrue({'worker_count','station_process','record_date','inspection_start_time','acceptance_participants'}<=names)

    async def test_upgrade_adds_old_columns_without_changing_history(self):
        legacy = create_async_engine('sqlite+aiosqlite:///:memory:')
        from sqlalchemy import MetaData
        metadata = MetaData()
        additions = {
            'projects': {'construction_unit','supervision_unit','supervision_org','acceptance_participants','latitude','longitude','seal_signature_id'},
            'work_records': {'worker_count','station_process','record_date','inspection_start_time','inspection_end_time','acceptance_participants'},
            'documents': {'content_revision','reviewed_at','reviewed_by'},
            'parallel_inspections': {'method','generated_doc_id'},
        }
        for table in Base.metadata.tables.values():
            if table.name == 'document_signoffs': continue
            old = table.to_metadata(metadata)
            removed = additions.get(table.name,set())
            for constraint in list(old.constraints):
                if any(c.name in removed for c in constraint.columns): old.constraints.remove(constraint)
            for key in removed:
                if key in old.c: old._columns.remove(old.c[key])
        async with legacy.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.execute(text("INSERT INTO work_records(id,record_type,status,creator_id,project_id,viewed,created_at,updated_at,work_content) VALUES(99,'巡视','草稿',99,7,0,'2026-10-08 10:00','2026-10-08 10:00','历史现场内容')"))
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(lambda c:_reconcile_missing_columns(c,set(additions)))
            await conn.run_sync(lambda c:_reconcile_missing_columns(c,set(additions)))
            row=(await conn.execute(text('SELECT id,work_content,worker_count,station_process FROM work_records WHERE id=99'))).first()
            self.assertEqual(tuple(row),(99,'历史现场内容',None,None))
        await legacy.dispose()

    async def test_http_request_roundtrip_and_invalid_update(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.db import get_db
        from app.routers.auth import get_current_user
        app=FastAPI();app.include_router(parallel.router,prefix='/parallel')
        async def session():yield self.db
        async def current():return self.u
        app.dependency_overrides[get_db]=session;app.dependency_overrides[get_current_user]=current
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://local') as client:
            r=await client.post('/parallel',json={'inspect_date':'2026-10-08','part':'A栋','item':'钢筋','method':'实测','result_detail':'实测200','conclusion':'合格'})
            self.assertEqual(r.status_code,200);pid=r.json()['id']
            r=await client.put(f'/parallel/{pid}',json={'conclusion':'未知'})
            self.assertEqual(r.status_code,400)
            r=await client.get(f'/parallel/{pid}');self.assertEqual(r.json()['conclusion'],'合格')
            r=await client.post('/parallel',json={'inspect_date':'2026-10-08','part':'A栋','item':'钢筋','result_detail':'','conclusion':'合格'})
            self.assertEqual(r.status_code,422)

    async def test_weather_provider_mock_uses_exact_day_and_timezone(self):
        from unittest.mock import Mock
        self.p.latitude='23.1';self.p.longitude='113.2'
        response=Mock();response.json.return_value={'daily':{'time':['2026-10-08'],'temperature_2m_max':[28], 'temperature_2m_min':[18], 'weather_code':[3]}}
        client=AsyncMock();client.get.return_value=response
        with patch.dict(os.environ,{'WEATHER_ARCHIVE_URL':'https://example.invalid/v1/archive','WEATHER_FORECAST_URL':'https://example.invalid/v1/forecast'}),patch('app.services.weather.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value=client
            result=await daily_weather(self.p,date(2026,10,8))
        self.assertEqual(result['temp_high'],28);self.assertEqual(result['weather'],'阴')
        self.assertEqual(client.get.call_args.kwargs['params']['timezone'],'Asia/Shanghai')
        self.assertEqual(client.get.call_args.kwargs['params']['start_date'],'2026-10-08')

    async def test_work_record_state_cannot_bypass_submit(self):
        r=self.rec(worker_count=None);self.db.add(r);await self.db.commit()
        with self.assertRaises(HTTPException):
            await work_record.update_work_record(r.id,work_record.UpdateWorkRecordRequest(status='已提交'),self.u,self.db)
        self.assertEqual(r.status,'草稿')

    async def test_document_export_word_after_edit(self):
        res=await work_record.create_acceptance(work_record.CreateAcceptanceRequest(section='A栋',acceptance_content='钢筋',acceptance_result='合格'),self.u,self.db)
        doc=await self.db.get(Document,res['doc_id'])
        await document.update_document(doc.id,document.DocUpdateRequest(content={'acceptance_content':'更新后验收内容'}),self.u,self.db)
        res=await document.export_docx(doc.id,self.u,self.db)
        words='\n'.join(p.text for p in WordDocument(str(settings.export_dir/Path(res['docx_url']).name)).paragraphs)
        self.assertIn('更新后验收内容',words)

    async def test_project_fields_roundtrip(self):
        await project.update_project(self.p.id,project.UpdateProjectRequest(construction_unit='更正施工单位',supervision_org='项目监理机构',latitude='23.1',longitude='113.2'),self.u,self.db)
        response=project._project_info(self.p)
        self.assertEqual(response.construction_unit,'更正施工单位');self.assertEqual(response.latitude,'23.1')
        with self.assertRaises(HTTPException):await project.update_project(self.p.id,project.UpdateProjectRequest(latitude='999'),self.u,self.db)

    async def test_signature_slot_role_and_history_preservation(self):
        doc=Document(project_id=self.p.id,creator_id=self.u.id,doc_type='巡视记录',title='巡视',content={'project_name':self.p.name,'date':'2026-10-08','construction_unit':'施工单位','section':'A栋','worker_count':0,'content':'检查'},status='草稿')
        self.db.add(doc);await self.db.commit();sig=await self.make_signature(self.u)
        with self.assertRaises(HTTPException):await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig.id,password='repair-test-123',slot='chief'),self.u,self.db)
        await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig.id,password='repair-test-123',slot='inspector'),self.u,self.db)
        with self.assertRaises(HTTPException):await document.delete_document(doc.id,self.u,self.db)

    async def test_day_aggregate_contains_all_daily_sources(self):
        self.db.add(MeetingMinute(project_id=self.p.id,user_id=self.u.id,meeting_date='2026-10-08',title='日聚合会议',content='同源会议数据'))
        await self.db.commit()
        result=await generate_aggregate_doc(self.db,self.p.id,'day','2026-10-08',supervisor=self.u,use_llm=False)
        doc=await self.db.get(Document,result['doc_id'])
        self.assertIn('同源会议数据',doc.content['meeting_section']);self.assertTrue(result['docx_url'])

    async def test_notice_word_contains_problem_organization_and_date(self):
        path=render_form('gd_b_215_tongzhi',{'project_name':'回归项目','doc_number':'GD-B-215-TEST','date':'2026-10-08','section':'A栋','to_unit':'施工项目部','subject':'安全检查','content':'发现钢筋问题；要求重新绑扎','problems':'钢筋问题','supervision_org':'真实项目监理机构','requirement':'重新绑扎'})
        words='\n'.join(c.text for t in WordDocument(str(path)).tables for row in t.rows for c in row.cells)
        self.assertIn('发现钢筋问题',words);self.assertIn('真实项目监理机构',words)
        self.assertIn('2026',words);self.assertIn('10',words)

    async def test_repeat_generation_keeps_signed_snapshot(self):
        r=self.rec('旁站',station_location='A栋',station_process='浇筑',station_start_time=datetime(2026,10,8,8),station_end_time=datetime(2026,10,8,9))
        self.db.add(r);await self.db.flush()
        doc,_,_=await generate_form_from_record(self.db,self.u,self.p,r);await self.db.commit()
        sig=await self.make_signature(self.u)
        await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig.id,password='repair-test-123',slot='inspector'),self.u,self.db)
        signed=doc.signed_pdf_path
        again,_,_=await generate_form_from_record(self.db,self.u,self.p,r)
        self.assertEqual(again.id,doc.id);self.assertEqual(again.signed_pdf_path,signed);self.assertEqual(again.content_revision,1)

    async def test_missing_template_fallback_keeps_real_project_and_number(self):
        r=self.rec(worker_count=2);self.db.add(r);await self.db.flush()
        with patch('app.routers.form_doc.render_form',side_effect=FileNotFoundError('模板缺失')):
            doc=await work_record.auto_create_record_document(self.db,self.u,r,self.p)
        self.assertIsNotNone(doc)
        self.assertEqual(doc.content['project_name'],self.p.name)
        self.assertEqual(doc.content['construction_unit'],self.p.construction_unit)
        self.assertTrue(doc.content['doc_number'])
        self.assertIn('重新绑扎',doc.content['problems'])

    async def test_signatures_reject_unversioned_content_change(self):
        doc=Document(project_id=self.p.id,creator_id=self.u.id,doc_type='平行检查记录',title='修改检测',content={'project_name':self.p.name,'date':'2026-10-08','section':'A栋','acceptance_content':'钢筋','conclusion':'合格','record':'原始实测'},status='草稿')
        self.db.add(doc);await self.db.commit()
        sig1=await self.make_signature(self.u);sig2=await self.make_signature(self.v)
        await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig1.id,password='repair-test-123',slot='inspector'),self.u,self.db)
        doc.content={**doc.content,'record':'未按修订流程修改的正文'};await self.db.commit()
        with self.assertRaises(HTTPException):
            await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig2.id,password='repair-test-123',slot='reviewer'),self.v,self.db)


if __name__=='__main__':
    unittest.main(verbosity=2)
