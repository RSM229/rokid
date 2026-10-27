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
from app.services.record_fields import project_context, validate_record
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

    async def test_project_context_accepts_optional_fields_and_schema_aliases(self):
        self.assertEqual(project_context(SimpleNamespace(name='对象项目')), {
            'project_name': '对象项目', 'owner_unit': '', 'construction_unit': '',
            'supervision_unit': '', 'supervision_org': '', 'acceptance_participants': '',
        })
        self.assertEqual(project_context({
            'project': '字典项目', 'contractor': '施工方', '建设单位': '建设方',
            'supervision_department': '监理部',
        }), {
            'project_name': '字典项目', 'owner_unit': '建设方', 'construction_unit': '施工方',
            'supervision_unit': '', 'supervision_org': '监理部', 'acceptance_participants': '',
        })

    async def test_mapping_preserves_problems_measures_and_dates(self):
        for kind in ['巡视','旁站','验收']:
            r=self.rec(kind, station_location='A栋', station_process='混凝土浇筑', station_start_time=datetime(2026,10,8,8,20), station_end_time=datetime(2026,10,8,10,25), worker_count=0, acceptance_result='不合格')
            c=_content_from_record(r,self.u)
            self.assertIn('钢筋间距偏大',c['problems']);self.assertIn('重新绑扎',c['problems'])
            self.assertEqual(c['date'],'2026-10-08')
            if kind=='旁站':self.assertEqual(c['start_time'],'2026-10-08 08:20')

    async def test_acceptance_participants_do_not_become_inspector_identity(self):
        record=self.rec(
            '验收',
            acceptance_participants='施工单位张工、监理单位李工',
            content_json={'confirmed_fields': {'inspector':'水印检查人员陈宇文'}},
        )
        content=_content_from_record(record,self.u,self.p)
        self.assertEqual(content['acceptance_participants'],'施工单位张工、监理单位李工')
        self.assertEqual(content['inspector'],'水印检查人员陈宇文')

    async def test_station_edit_saves_times_and_refreshes_doc(self):
        r=self.rec('旁站',station_location='A栋',station_process='混凝土浇筑',station_start_time=datetime(2026,10,8,8), station_end_time=datetime(2026,10,8,9),station_content='初始旁站内容',worker_count=5,content_json={'source':'agent_review_plan','prov_fields': {'worker_count':'5','material_info':'混凝土C30','qa_onsite':'是','content':'旧Agent内容','end_time':'2026-10-08 07:00'}})
        self.db.add(r);await self.db.flush()
        doc,_,_=await generate_form_from_record(self.db,self.u,self.p,r)
        await self.db.commit()
        await work_record.update_work_record(r.id,work_record.UpdateWorkRecordRequest(station_end_time=datetime(2026,10,8,11),station_content='用户修订旁站内容',treatment='处理措施修订'),self.u,self.db)
        await self.db.refresh(r);await self.db.refresh(doc)
        self.assertEqual(r.station_end_time.hour,11)
        self.assertEqual(doc.content['end_time'],'2026-10-08 11:00')
        self.assertEqual(doc.content['content'],'用户修订旁站内容')
        self.assertIn('处理措施修订',doc.content['problems'])
        self.assertEqual(r.content_json['record_field_edits']['end_time']['value'],'2026-10-08 11:00')
        self.assertEqual(r.content_json['record_field_edits']['content']['value'],'用户修订旁站内容')
        from app.services.agent_form_requirements import resolve_record_form
        requirements = resolve_record_form(r, self.p, 'gd_b_212_pangzhan')
        self.assertEqual(requirements['values']['end_time'],'2026-10-08 11:00')
        self.assertEqual(requirements['values']['content'],'用户修订旁站内容')
        self.assertEqual(doc.content_revision,2)
        regenerated,_,_=await generate_form_from_record(self.db,self.u,self.p,r)
        self.assertTrue(regenerated.docx_path)
        self.assertIn('用户修订旁站内容', '\n'.join(cell.text for table in WordDocument(str(settings.export_dir/Path(regenerated.docx_path).name)).tables for row in table.rows for cell in row.cells))

    async def test_cleared_agent_field_is_not_refilled_by_old_candidate(self):
        r=self.rec('巡视',content_json={
            'source':'agent_review_plan',
            'prov_fields': {'content':'旧Agent巡视内容'},
            'form_field_candidates': {'content':'旧OCR候选内容'},
        })
        self.db.add(r);await self.db.flush()
        await work_record.update_work_record(r.id,work_record.UpdateWorkRecordRequest(work_content=''),self.u,self.db)
        await self.db.refresh(r)
        rendered=_content_from_record(r,self.u,self.p)
        self.assertEqual(rendered['content'],'')
        from app.services.agent_form_requirements import resolve_record_form
        requirements=resolve_record_form(r,self.p,'gd_b_214_xunshi')
        self.assertIn('content',requirements['missing_fields'])

    async def test_reversed_times_and_invalid_result_rejected(self):
        r=self.rec('旁站',station_start_time=datetime(2026,10,8,10),station_end_time=datetime(2026,10,8,11))
        self.db.add(r);await self.db.commit()
        with self.assertRaises(HTTPException):
            await work_record.update_work_record(r.id,work_record.UpdateWorkRecordRequest(station_end_time=datetime(2026,10,8,9)),self.u,self.db)
        with self.assertRaises(HTTPException):
            await work_record.create_acceptance(work_record.CreateAcceptanceRequest(acceptance_result='自动合格'),self.u,self.db)

    async def test_inspection_required_count_zero_allowed(self):
        r=self.rec(worker_count=None)
        validate_record(r,self.p)
        r.worker_count=0;validate_record(r,self.p)
        with self.assertRaises(ValidationError):work_record.CreateInspectionRequest(worker_count=-1)

    async def test_parallel_crud_details_project_boundary(self):
        req=parallel.ParallelCreate(inspect_date='2026-10-08',part='A栋',item='钢筋',method='实测',basis='设计图纸',result_detail='实测间距200mm',conclusion='合格',treatment='无')
        response=await parallel.create_inspection(req,self.u,self.db)
        listing=await parallel.list_inspections(keyword=None,user=self.u,db=self.db)
        self.assertEqual(listing['items'][0]['basis'],'设计图纸')
        self.assertEqual(listing['items'][0]['method'],'实测')
        updated=await parallel.update_inspection(response['id'],parallel.ParallelUpdate(item='钢筋复查'),self.u,self.db)
        self.assertEqual(updated['basis'],'设计图纸');self.assertEqual(updated['result_detail'],'实测间距200mm')
        self.u.project_id=self.p2.id
        with self.assertRaises(HTTPException):await parallel.get_inspection(response['id'],user=self.u,db=self.db,all_projects=False)

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
        expected = {"旁站记录": {"inspector"}, "巡视记录": {"inspector"}, "平行检查记录": {"inspector"}, "监理日志": {"inspector"}, "验收记录": {"acceptor"}}
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

        notice = Document(project_id=self.p.id, creator_id=self.u.id, doc_type="监理通知单",
                          form_code="gd_b_215_tongzhi", title="监理通知单", content={})
        self.db.add(notice); await self.db.flush()
        self.assertEqual(required_slots_for(notice), {"project_seal"})
        with self.assertRaises(HTTPException):
            await assert_required_signoffs(self.db, notice)
        self.db.add(DocumentSignoff(document_id=notice.id, revision=1, slot="project_seal", signer_id=self.u.id, signer_name=self.u.real_name, signature_id=1, signature_hash='test', content_hash='test', signed_pdf_path='test'))
        await self.db.flush()
        with self.assertRaises(HTTPException):
            await assert_required_signoffs(self.db, notice)
        self.db.add(DocumentSignoff(document_id=notice.id, revision=1, slot="supervisor", signer_id=self.u.id, signer_name=self.u.real_name, signature_id=1, signature_hash='test', content_hash='test', signed_pdf_path='test'))
        await self.db.flush()
        await assert_required_signoffs(self.db, notice)

        monthly = Document(project_id=self.p.id, creator_id=self.u.id, doc_type="监理月报",
                           form_code="gd_b_221_yuebao", title="监理月报", content={})
        self.db.add(monthly); await self.db.flush()
        self.assertEqual(required_slots_for(monthly), {"chief", "project_seal"})
        with self.assertRaises(HTTPException):
            await assert_required_signoffs(self.db, monthly)
        for slot in ("chief", "project_seal"):
            self.db.add(DocumentSignoff(document_id=monthly.id, revision=1, slot=slot, signer_id=self.u.id, signer_name=self.u.real_name, signature_id=1, signature_hash='test', content_hash='test', signed_pdf_path='test'))
        await self.db.flush()
        await assert_required_signoffs(self.db, monthly)

    async def test_acceptance_has_own_form_and_participants(self):
        res=await work_record.create_acceptance(work_record.CreateAcceptanceRequest(section='隐蔽部位',acceptance_type='隐蔽验收',acceptance_content='钢筋隐蔽验收',acceptance_result='合格',record_date=date(2026,10,8)),self.u,self.db)
        self.assertIsNone(res['doc_id'])
        record=await self.db.get(WorkRecord,res['record_id'])
        self.assertEqual(record.content_json['acceptance_type'],'隐蔽验收')
        self.assertEqual(record.content_json['acceptance_content'],'钢筋隐蔽验收')
        self.assertIn('张工',record.acceptance_participants)
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
        history=await signature.document_signoffs(doc.id,user=self.u,db=self.db)
        self.assertEqual(len(history['history']),2);self.assertTrue(all(x['current'] for x in history['history']))
        self.assertEqual(doc.content['review_date'],doc.reviewed_at.date().isoformat())
        self.assertGreater(len(PdfReader(str(settings.export_dir/Path(doc.signed_pdf_path).name)).pages),0)
        await document.update_document(doc.id,document.DocUpdateRequest(content={'record':'改后实测'}),self.u,self.db)
        history=await signature.document_signoffs(doc.id,user=self.u,db=self.db)
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
        doc=Document(project_id=self.p.id,creator_id=self.u.id,doc_type='验收记录',title='验收记录测试',form_code='acceptance_record',content={'project_name':self.p.name,'doc_number':'YS-TEST','date':'2026-10-08','acceptance_type':'隐蔽工程','section':'A栋','acceptance_content':'钢筋','self_check_conclusion':'符合要求','supervision_conclusion':'合格','photo':'无','acceptance_participants':'施工单位张工、监理单位李工','conclusion':'合格'},status='草稿')
        self.db.add(doc);await self.db.commit()
        await document.update_document(doc.id,document.DocUpdateRequest(content={'acceptance_content':'更新后验收内容'}),self.u,self.db)
        def render_test_form(_form_code, data):
            path=settings.export_dir/'acceptance-export-test.docx'
            generated=WordDocument()
            generated.add_paragraph(data['acceptance_content'])
            generated.save(path)
            return path
        with patch('app.services.form_renderer.render_form',side_effect=render_test_form):
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

    async def test_week_and_month_aggregate_render_with_period_parallel_section(self):
        """🔴 回归：周报/月报此前必然 NameError。

        generate_aggregate_doc 里引用了 `ex`（天气）和 `parallel_section`，
        但这两个变量只在 generate_supervision_log（日报）中定义 —— day 分支在
        函数开头提前 return 所以没暴露，week/month 一走到 content 组装就崩。
        崩点还被外层 try/except 吞进 report["errors"]，导致自动周报/月报
        长期静默失败、只在日志留痕。
        """
        # 10-08 是周四 → 整周为 10-05(一) 至 10-11(日)
        self.db.add_all([
            ParallelInspection(project_id=self.p.id,user_id=self.u.id,inspect_date='2026-10-06',
                               part='A栋三层',item='钢筋保护层',result_detail='偏差 3mm',
                               conclusion='合格',basis='GB50204',method='实测'),
            ParallelInspection(project_id=self.p.id,user_id=self.u.id,inspect_date='2026-10-10',
                               part='B栋基础',item='回弹强度',result_detail='C30 达标',
                               conclusion='合格',basis='JGJ/T23',method='回弹仪'),
            # 区间外的记录不应进本周汇总
            ParallelInspection(project_id=self.p.id,user_id=self.u.id,inspect_date='2026-09-01',
                               part='上周部位',item='不应出现',result_detail='x',conclusion='合格'),
        ])
        await self.db.commit()

        week=await generate_aggregate_doc(self.db,self.p.id,'week','2026-10-08',supervisor=self.u,use_llm=False)
        week_doc=await self.db.get(Document,week['doc_id'])
        self.assertEqual(week['form_code'],'gd_b_223_zhoubao')
        self.assertIn('A栋三层 钢筋保护层',week_doc.content['parallel_section'])
        self.assertIn('B栋基础 回弹强度',week_doc.content['parallel_section'])
        self.assertNotIn('不应出现',week_doc.content['parallel_section'])
        # 跨多日无单一天气，天气类字段应留空而不是崩
        self.assertEqual(week_doc.content['weather_source'],'')
        self.assertEqual(week_doc.content['weather_missing'],[])
        self.assertTrue(week['docx_url'])

        month=await generate_aggregate_doc(self.db,self.p.id,'month','2026-10',supervisor=self.u,use_llm=False)
        month_doc=await self.db.get(Document,month['doc_id'])
        self.assertEqual(month['form_code'],'gd_b_221_yuebao')
        self.assertIn('A栋三层 钢筋保护层',month_doc.content['parallel_section'])
        # 月区间含 09-01？否 —— month 用 2026-10，9 月记录仍在区间外
        self.assertNotIn('不应出现',month_doc.content['parallel_section'])
        self.assertTrue(month['docx_url'])

    async def test_week_aggregate_without_parallel_records_uses_period_wording(self):
        """无平行检验记录时给周期措辞，而不是残留日报的「当日」措辞。

        🔴 文案本身不锁死：平行检验汇总已统一由 aggregate_records 产出
        （generate_aggregate_doc 不再重复查询与格式化），所以空值文案以
        aggregate_records 为准。这里只断言「不是日报的『当日』措辞」。
        """
        result=await generate_aggregate_doc(self.db,self.p.id,'week','2026-10-08',supervisor=self.u,use_llm=False)
        doc=await self.db.get(Document,result['doc_id'])
        section=doc.content['parallel_section']
        self.assertTrue(section, '平行检验分节为空')
        self.assertNotIn('当日', section, f'周报残留了日报的「当日」措辞：{section}')

    async def test_notice_word_contains_problem_organization_and_date(self):
        path=render_form('gd_b_215_tongzhi',{'project_name':'回归项目','doc_number':'GD-B-215-TEST','date':'2026-10-08','section':'A栋','to_unit':'施工项目部','subject':'安全检查','content':'发现钢筋问题；要求重新绑扎','problems':'钢筋问题','supervision_org':'真实项目监理机构','requirement':'重新绑扎'})
        words='\n'.join(c.text for t in WordDocument(str(path)).tables for row in t.rows for c in row.cells)
        self.assertIn('发现钢筋问题',words);self.assertIn('真实项目监理机构',words)
        self.assertIn('2026',words);self.assertIn('10',words)

    async def test_repeat_generation_keeps_signed_snapshot(self):
        r=self.rec('旁站',station_location='A栋',station_process='浇筑',station_start_time=datetime(2026,10,8,8),station_end_time=datetime(2026,10,8,9),worker_count=5,content_json={'prov_fields': {'worker_count': '5','material_info':'钢筋','qa_onsite':'是'}})
        self.db.add(r);await self.db.flush()
        doc,_,_=await generate_form_from_record(self.db,self.u,self.p,r);await self.db.commit()
        sig=await self.make_signature(self.u)
        await signature.sign_document(doc.id,signature.SignDocumentRequest(signature_id=sig.id,password='repair-test-123',slot='inspector'),self.u,self.db)
        signed=doc.signed_pdf_path
        again,_,_=await generate_form_from_record(self.db,self.u,self.p,r)
        self.assertEqual(again.id,doc.id);self.assertEqual(again.signed_pdf_path,signed);self.assertEqual(again.content_revision,1)

    async def test_missing_template_fallback_keeps_real_project_and_number(self):
        r=self.rec(worker_count=2,content_json={'prov_fields': {'worker_count': '2','material_info':'钢筋','qa_onsite':'是'}});self.db.add(r);await self.db.flush()
        r.inspection_start_time=datetime(2026,10,8,9)
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

    async def test_uploaded_signature_background_is_removed_and_png_is_normalized(self):
        import io
        from PIL import Image
        source = Image.new('RGB', (3, 1), 'white')
        source.putpixel((1, 0), (20, 40, 60))
        raw = io.BytesIO(); source.save(raw, format='JPEG')
        url = signature._save_signature_image(raw.getvalue(), self.u.id, 'signature.jpg')
        saved = settings.upload_dir / url.removeprefix('/uploads/')
        with Image.open(saved) as result:
            self.assertEqual(result.mode, 'RGBA')
            self.assertLessEqual(result.getpixel((0, 0))[3], 10)
            self.assertGreater(result.getpixel((1, 0))[3], 0)

    async def test_template_signoff_image_is_inserted_only_at_role_marker(self):
        from docx import Document as WordDocument
        from app.services.form_renderer import embed_signoff_image
        output = render_form('gd_b_213_pingxing', {
            'project_name': self.p.name, 'doc_number': 'TEST-SIGN', 'date': '2026-10-08',
            'section': 'A栋', 'acceptance_content': '钢筋', 'method': '实测',
            'basis': '图纸', 'record': '实测符合', 'conclusion': '合格', 'handling': '无',
        }, settings.export_dir)
        sig_path = settings.upload_dir / 'signature-test.png'
        Image.new('RGBA', (80, 40), (0, 0, 0, 180)).save(sig_path)
        copied = settings.export_dir / 'signed-test.docx'
        import shutil
        shutil.copy2(output, copied)
        self.assertTrue(embed_signoff_image(copied, 'inspector', f'/uploads/{sig_path.relative_to(settings.upload_dir).as_posix()}', 'gd_b_213_pingxing'))
        rendered = WordDocument(str(copied))
        self.assertTrue(any('word/media/' in name for name in __import__('zipfile').ZipFile(copied).namelist()))
        self.assertFalse(embed_signoff_image(copied, 'chief', f'/uploads/{sig_path.relative_to(settings.upload_dir).as_posix()}', 'witness_record'))

    async def test_log_and_monthly_report_signatures_have_real_template_anchors(self):
        from zipfile import ZipFile
        from app.services.form_renderer import embed_signoff_image, render_form
        sig_path = settings.upload_dir / 'signature-template-roles.png'
        Image.new('RGBA', (80, 40), (0, 0, 0, 180)).save(sig_path)
        sig_url = f'/uploads/{sig_path.relative_to(settings.upload_dir).as_posix()}'

        for form_code, data, slot, extra_slots in (
            ('gd_b_220_rizhi', {
                'project_name': self.p.name, 'doc_number': 'LOG-SIGN', 'date': '2026-10-23',
                'weekday': '五', 'weather': '晴', 'construction_progress': '主体施工',
                'supervision_section': '巡视检查', 'problems_section': '无',
            }, 'inspector', ('chief',)),
            ('gd_b_221_yuebao', {
                'project_name': self.p.name, 'doc_number': 'REPORT-SIGN',
                'report_period': '2026年10月', 'submit_date': '2026-10-23',
                'supervision_org': '项目监理部', 'chief_supervisor': '',
            }, 'chief', ('project_seal',)),
        ):
            output = render_form(form_code, data, settings.export_dir)
            for current_slot in (slot, *extra_slots):
                self.assertTrue(embed_signoff_image(output, current_slot, sig_url, form_code))
            with ZipFile(output) as archive:
                self.assertTrue(any(name.startswith('word/media/') for name in archive.namelist()))

    async def test_notice_signatures_have_real_template_anchors(self):
        from zipfile import ZipFile
        from app.services.form_renderer import embed_signoff_image, render_form
        sig_path = settings.upload_dir / 'signature-notice-roles.png'
        Image.new('RGBA', (80, 40), (0, 0, 0, 180)).save(sig_path)
        sig_url = f'/uploads/{sig_path.relative_to(settings.upload_dir).as_posix()}'
        cases = (
            ('gd_b_215_tongzhi', {'project_name': self.p.name, 'doc_number': 'NOTICE-SIGN',
             'date': '2026-10-23', 'to_unit': '施工项目部', 'subject': '整改通知',
             'content': '现场问题', 'supervision_org': '项目监理机构'}),
            ('gdaq_30308_yinhuan', {'project_name': self.p.name, 'doc_number': 'HAZARD-SIGN',
             'date': '2026-10-23', 'construction_unit': '施工项目部', 'problems': ['现场问题'],
             'requirement': '限期整改'}),
        )
        for form_code, data in cases:
            output = render_form(form_code, data, settings.export_dir)
            self.assertTrue(embed_signoff_image(output, 'project_seal', sig_url, form_code))
            self.assertTrue(embed_signoff_image(output, 'chief', sig_url, form_code))
            with ZipFile(output) as archive:
                self.assertTrue(any(name.startswith('word/media/') for name in archive.namelist()))

    async def test_template_signing_updates_word_pdf_and_linked_record_together(self):
        from app.services.form_renderer import render_form
        docx_path = render_form('gd_b_213_pingxing', {
            'project_name': self.p.name, 'doc_number': 'TEST-LINK', 'date': '2026-10-08',
            'section': 'A栋', 'acceptance_content': '钢筋', 'method': '实测',
            'basis': '图纸', 'record': '实测符合', 'conclusion': '合格', 'handling': '无',
        }, settings.export_dir)
        doc = Document(project_id=self.p.id, creator_id=self.u.id, doc_type='平行检查记录',
                       title='联动签认', form_code='gd_b_213_pingxing',
                       content={'project_name': self.p.name, 'date': '2026-10-08', 'section': 'A栋',
                                'acceptance_content': '钢筋', 'conclusion': '合格', 'record': '实测符合'},
                       docx_path=f'/exports/{docx_path.name}', status='草稿')
        self.db.add(doc); await self.db.flush()
        record = self.rec('平行检验', content_json={'source': 'agent_review_plan'})
        record.generated_doc_id = doc.id
        self.db.add(record); await self.db.commit()
        sig = await self.make_signature(self.u)

        def fake_convert(path):
            target = path.with_suffix('.pdf')
            from reportlab.pdfgen import canvas
            canvas.Canvas(str(target)).save()
            return target

        with patch('app.services.form_renderer.docx_to_pdf', side_effect=fake_convert):
            result = await signature.sign_document(
                doc.id, signature.SignDocumentRequest(signature_id=sig.id, password='repair-test-123', slot='inspector'), self.u, self.db,
            )
        await self.db.refresh(record)
        self.assertEqual(doc.docx_path.rsplit('.', 1)[-1], 'docx')
        self.assertEqual(doc.pdf_path, result['signed_pdf_url'])
        self.assertEqual(doc.signed_pdf_path, doc.pdf_path)
        self.assertEqual(record.content_json['document_signoffs'][0]['signed_pdf_path'], doc.pdf_path)
        self.assertTrue(any(name.startswith('word/media/') for name in __import__('zipfile').ZipFile(settings.export_dir / doc.docx_path.removeprefix('/exports/')).namelist()))

    async def test_signing_mode_does_not_change_business_revision(self):
        doc=Document(project_id=self.p.id,creator_id=self.u.id,doc_type='巡视记录',title='签署模式',content={'project_name':self.p.name},status='草稿')
        self.db.add(doc);await self.db.commit()
        revision = doc.content_revision
        result = await signature.set_document_signing_mode(doc.id, signature.SigningModeRequest(mode='offline', slot='inspector'), self.u, self.db)
        self.assertEqual(result['revision'], revision)
        self.assertEqual(doc.content_revision, revision)
        self.assertIsNone(doc.signed_pdf_path)

    async def test_offline_signoff_waits_for_explicit_confirmation_and_is_revision_scoped(self):
        from app.services.document_signoffs import assert_required_signoffs

        doc=Document(
            project_id=self.p.id,creator_id=self.u.id,doc_type='巡视记录',title='纸质签署',
            content={'project_name':self.p.name,'construction_unit':'施工单位','date':'2026-10-27','section':'A栋','content':'巡视检查'},
            status='草稿',
        )
        self.db.add(doc);await self.db.commit()
        await signature.set_document_signing_mode(
            doc.id, signature.SigningModeRequest(mode='offline', slot='inspector'), self.u, self.db
        )
        await document.update_document(
            doc.id,
            document.DocUpdateRequest(content={
                'offline_signoffs':[{'slot':'inspector','mode':'offline','status':'completed','revision':doc.content_revision}],
                'signing':{'inspector':{'mode':'online'}},
                'content':'修改后的巡视内容',
            }),
            self.u,self.db,
        )
        self.assertEqual(doc.content['signing']['inspector']['mode'],'offline')
        self.assertNotIn('offline_signoffs',doc.content)
        with self.assertRaises(HTTPException):
            await assert_required_signoffs(self.db, doc)

        result = await signature.confirm_offline_signoff(
            doc.id, signature.OfflineSignoffRequest(slot='inspector'), self.u, self.db
        )
        self.assertFalse(result['electronic_signature_created'])
        await assert_required_signoffs(self.db, doc)
        history = await signature.document_signoffs(doc.id, user=self.u, db=self.db)
        self.assertEqual(history['slots'][0]['completion_type'],'offline')
        self.assertEqual(history['history'][0]['type'],'offline')

        from app.services.record_fields import invalidate_document
        invalidate_document(doc)
        with self.assertRaises(HTTPException):
            await assert_required_signoffs(self.db, doc)


if __name__=='__main__':
    unittest.main(verbosity=2)
