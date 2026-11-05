import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.routers.work_record import ShareWorkRecordRequest, share_work_record


class FakeUser:
    id = 7
    real_name = "张三"
    role = "监理员"
    project_id = 1
    is_active = True

    def has_permission(self, perm):
        return False


class FakeDirector:
    id = 1
    real_name = "李总"
    role = "总监理工程师"
    project_id = 1
    is_active = True


class FakeEng:
    id = 2
    real_name = "王工"
    role = "专业监理工程师"
    project_id = 1
    is_active = True


class FakeRecord:
    id = 5
    record_type = "巡视"
    project_id = 1
    creator_id = 7
    section = "东区"
    location = "A栋"
    status = "草稿"
    inspection_photos = []
    witness_photos = []
    generated_doc_id = None


class FakeProject:
    id = 1
    name = "示范项目"


class FakeDoc:
    id = 88
    title = "巡视记录_巡视_5"
    docx_path = "/exports/x.docx"
    status = "草稿"


class FakeSpec:
    form_code = "gd_b_214_xunshi"
    name = "巡视记录"


class FakeScalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._value


class FakeScalars:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class FakeDB:
    def __init__(self, record, recipients, project=None, member=None):
        self._results = [FakeScalar(record)]
        if member is not None:
            self._results.append(FakeScalar(member))
        self._results.append(FakeScalars(recipients))
        self._project = project or FakeProject()

    async def execute(self, query):
        return self._results.pop(0)

    async def get(self, model, pk):
        return self._project

    async def commit(self):
        return None


class ShareWorkRecordTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        gen = AsyncMock(return_value=(FakeDoc(), FakeSpec(), {"doc_number": "B-00005"}))
        push = AsyncMock(return_value=True)
        self.patches = [
            patch("app.routers.form_doc.generate_form_from_record", gen),
            patch("app.services.permissions.member_user_ids_with_roles", AsyncMock(return_value=[1, 2])),
            patch("app.services.project_archive.sync_document_to_project_archive", AsyncMock()),
            patch("app.services.notification.push_to_glasses", push),
        ]
        for p in self.patches:
            p.start()
        self.gen = gen
        self.push = push

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()

    async def test_role_upload_to_director(self) -> None:
        db = FakeDB(FakeRecord(), [FakeDirector()])
        res = await share_work_record(
            5, ShareWorkRecordRequest(target="role", group="总监"), FakeUser(), db
        )
        self.assertEqual(res["recipients"], 1)
        payload = self.push.call_args[0][3]
        self.assertEqual(payload["group"], "总监")
        self.assertEqual(payload["type"], "work_record_share")
        self.assertEqual(self.push.call_args[0][0], FakeDirector.id)

    async def test_role_upload_to_department(self) -> None:
        db = FakeDB(FakeRecord(), [FakeEng(), FakeUser()])
        res = await share_work_record(
            5, ShareWorkRecordRequest(target="role", group="监理部门"), FakeUser(), db
        )
        self.assertEqual(res["recipients"], 2)

    async def test_forward_to_single_user(self) -> None:
        db = FakeDB(FakeRecord(), [FakeEng()], member=object())
        res = await share_work_record(
            5,
            ShareWorkRecordRequest(
                target="user", user_id=2, form_code="gd_b_214_xunshi"
            ),
            FakeUser(),
            db,
        )
        self.assertEqual(res["recipients"], 1)
        self.assertEqual(self.push.call_args[0][0], 2)
        self.assertEqual(self.push.call_args[0][3]["share_mode"], "user")

    async def test_unknown_target_raises(self) -> None:
        db = FakeDB(FakeRecord(), [FakeEng()])
        with self.assertRaises(HTTPException) as error:
            await share_work_record(
                5, ShareWorkRecordRequest(target="bogus"), FakeUser(), db
            )
        self.assertEqual(error.exception.status_code, 400)

    async def test_missing_recipient_raises(self) -> None:
        db = FakeDB(FakeRecord(), [])
        with self.assertRaises(HTTPException) as error:
            await share_work_record(
                5, ShareWorkRecordRequest(target="role", group="总监"), FakeUser(), db
            )
        self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
