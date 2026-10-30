# -*- coding: utf-8 -*-
"""项目生命周期端点的企业隔离回归。

``complete_project`` / ``reopen_project`` 仅有 ``project_complete`` 权限闸，缺 ``org_id``
隔离；本轮补 ``_require_same_org``。这里直接测该纯函数（不连库）：
  - 跨企业 → 403
  - 同企业 → 放行
  - 项目 org 为空（历史数据）→ 放行
  - 账号 org 为空（超管 / 无企业）→ 不受限
"""
from __future__ import annotations

import unittest

from fastapi import HTTPException

from app.routers.project import _require_same_org


class _U:
    def __init__(self, org_id):
        self.org_id = org_id


class _P:
    def __init__(self, org_id):
        self.org_id = org_id


class ProjectOrgIsolationTest(unittest.TestCase):
    def test_cross_org_blocked(self):
        with self.assertRaises(HTTPException) as ctx:
            _require_same_org(_U(1), _P(2))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_same_org_allowed(self):
        _require_same_org(_U(1), _P(1))  # 不抛

    def test_legacy_null_project_org_allowed(self):
        _require_same_org(_U(1), _P(None))  # 历史项目 org 为空，放行

    def test_null_user_org_unrestricted(self):
        _require_same_org(_U(None), _P(2))  # 超管 / 无企业账号不限制


if __name__ == "__main__":
    unittest.main(verbosity=2)
