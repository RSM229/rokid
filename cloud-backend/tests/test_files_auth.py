# -*- coding: utf-8 -*-
"""受控文件服务回归测试：封堵「未登录即可直取 /uploads、/exports 任意文件」漏洞。

历史上这两个路径挂的是 StaticFiles，无任何身份认证；本测试锁死：
  1. 未带 Authorization 头访问 → 401（不再 200 泄露文件内容）
  2. 已登录访问自己项目的导出/归档 → 200
  3. 已登录但跨项目访问他人归档 → 403
  4. 文件名编码了 _p<项目id>_ 的导出，跨项目访问 → 403
  5. 路径穿越（../） → 400
  6. 全部项目导出（_all_）仅公司级账号可下载

用 TestClient + 依赖覆盖模拟鉴权；can_read_project 用补丁替换，避免触碰数据库。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


class _FakeUser:
    def __init__(self, project_id: int = 5, super_admin: bool = False, org_admin: bool = False):
        self.project_id = project_id
        self._super = super_admin
        self._org = org_admin

    def is_super_admin(self) -> bool:
        return self._super

    def is_org_admin(self) -> bool:
        return self._org


class FilesAuthTestCase(unittest.TestCase):
    _captured: dict = {"user": None}

    @classmethod
    def setUpClass(cls):
        cls._upload_dir = settings.upload_dir
        cls._export_dir = settings.export_dir
        cls._upload_dir.mkdir(parents=True, exist_ok=True)
        cls._export_dir.mkdir(parents=True, exist_ok=True)

        cls._up = cls._upload_dir / "t_up.xlsx"
        cls._up.write_bytes(b"UPLOAD-SECRET")
        cls._ex5 = cls._export_dir / "ledger_p5_20261030.xlsx"
        cls._ex5.write_bytes(b"EXPORT-P5")
        cls._ex9 = cls._export_dir / "ledger_p9_20261030.xlsx"
        cls._ex9.write_bytes(b"EXPORT-P9")
        cls._ex_all = cls._export_dir / "ledger_all_20261030.xlsx"
        cls._ex_all.write_bytes(b"EXPORT-ALL")
        # 部署归属编码前的遗留文件：文件名无 _p<id>_ / _all_ 标记，无法判定归属
        cls._ex_legacy = cls._export_dir / "监理日志台账_20260101.xlsx"
        cls._ex_legacy.write_bytes(b"EXPORT-LEGACY")
        cls._arc = cls._export_dir / "archives" / "7" / "arc.zip"
        cls._arc.parent.mkdir(parents=True, exist_ok=True)
        cls._arc.write_bytes(b"ARCHIVE-7")

    @classmethod
    def tearDownClass(cls):
        for f in (cls._up, cls._ex5, cls._ex9, cls._ex_all, cls._ex_legacy, cls._arc):
            try:
                f.unlink()
            except OSError:
                pass

    # ---- 鉴权依赖模拟：无 Authorization 头即 401 ----
    @staticmethod
    def _fake_get_current_user(request: Request):
        if not request.headers.get("authorization"):
            raise HTTPException(status_code=401, detail="no token")
        return FilesAuthTestCase._captured["user"]

    @staticmethod
    def _fake_can_read(project_id: int, user) -> bool:
        return project_id == user.project_id or user.is_super_admin()

    def setUp(self):
        import app.routers.files as F

        app.dependency_overrides[F.get_current_user] = self._fake_get_current_user
        self._patcher = patch.object(F, "can_read_project", self._fake_can_read)
        self._patcher.start()
        self.client = TestClient(app)
        self.auth = {"Authorization": "Bearer test"}

    def tearDown(self):
        self._patcher.stop()
        app.dependency_overrides.clear()

    def test_unauthenticated_upload_rejected(self):
        # 🔴 核心回归：以前无 token 直取返回 200 泄露文件
        r = self.client.get("/uploads/t_up.xlsx")
        self.assertEqual(r.status_code, 401)

    def test_unauthenticated_export_rejected(self):
        r = self.client.get("/exports/ledger_p5_20261030.xlsx")
        self.assertEqual(r.status_code, 401)

    def test_own_project_export_allowed(self):
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5)
        r = self.client.get("/exports/ledger_p5_20261030.xlsx", headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"EXPORT-P5", r.content)

    def test_cross_project_export_blocked(self):
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5)
        r = self.client.get("/exports/ledger_p9_20261030.xlsx", headers=self.auth)
        self.assertEqual(r.status_code, 403)

    def test_cross_project_archive_blocked(self):
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5)
        r = self.client.get("/exports/archives/7/arc.zip", headers=self.auth)
        self.assertEqual(r.status_code, 403)

    def test_own_archive_allowed(self):
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=7)
        r = self.client.get("/exports/archives/7/arc.zip", headers=self.auth)
        self.assertEqual(r.status_code, 200)

    def test_super_admin_cross_archive_allowed(self):
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5, super_admin=True)
        r = self.client.get("/exports/archives/7/arc.zip", headers=self.auth)
        self.assertEqual(r.status_code, 200)

    def test_path_traversal_rejected(self):
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5)
        r = self.client.get("/uploads/..%2f..%2fapp%2fmain.py", headers=self.auth)
        self.assertIn(r.status_code, (400, 404))

    def test_all_project_export_requires_company_level(self):
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5)  # 普通项目成员
        r = self.client.get("/exports/ledger_all_20261030.xlsx", headers=self.auth)
        self.assertEqual(r.status_code, 403)
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5, org_admin=True)
        # 文件不存在是 404，但已越过 403 的权限关；这里只验证权限判定不被 403 卡住
        r2 = self.client.get("/exports/ledger_all_20261030.xlsx", headers=self.auth)
        self.assertNotEqual(r2.status_code, 403)

    # ---- /ledger/download 同样按文件名中的项目归属校验（根治台账越权下载）----
    def test_ledger_download_own_project_allowed(self):
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5)
        r = self.client.get("/api/ledger/download/ledger_p5_20261030.xlsx", headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"EXPORT-P5", r.content)

    def test_ledger_download_cross_project_blocked(self):
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5)
        r = self.client.get("/api/ledger/download/ledger_p9_20261030.xlsx", headers=self.auth)
        self.assertEqual(r.status_code, 403)

    def test_ledger_download_all_requires_company_level(self):
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5)
        r = self.client.get("/api/ledger/download/ledger_all_20261030.xlsx", headers=self.auth)
        self.assertEqual(r.status_code, 403)
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5, org_admin=True)
        r2 = self.client.get("/api/ledger/download/ledger_all_20261030.xlsx", headers=self.auth)
        self.assertNotEqual(r2.status_code, 403)

    def test_ledger_download_path_traversal_rejected(self):
        # {filename} 路径参数不含 "/"，..%2f 解码为 "/" 后路由层即不匹配（404），
        # 目录穿越在路由阶段被中和；这里只断言绝不会返回 200 泄露文件。
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=5)
        r = self.client.get("/api/ledger/download/..%2f..%2fapp%2fmain.py", headers=self.auth)
        self.assertNotEqual(r.status_code, 200)

    def test_ledger_download_legacy_untagged_allowed_for_any_authed_user(self):
        # 🔴 一致性回归：遗留文件（无 _p<id>_ / _all_ 标记）此前对普通项目成员返回
        # 403（仅公司级可下）。应与 /exports/ 受控端点一致——仅要求登录即可下载，
        # 否则合法用户无法重新下载自己此前导出的文件。
        FilesAuthTestCase._captured["user"] = _FakeUser(project_id=3)  # 与文件无显式归属关系
        r = self.client.get(
            "/api/ledger/download/监理日志台账_20260101.xlsx", headers=self.auth
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"EXPORT-LEGACY", r.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
