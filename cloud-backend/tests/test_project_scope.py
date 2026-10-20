# -*- coding: utf-8 -*-
"""「全部项目」只读视角的范围函数自测（不连库、不调外部服务）。

跑法：cd cloud-backend && python tests/test_project_scope.py

本机 venv 缺 fastapi/pydantic，无法直接 import app.routers.auth；
这里从源码里切出 ``_can_manage_all_projects`` / ``require_project_id`` /
``project_scope_id`` / ``can_read_project`` 四个纯函数单独执行，
保证测的是生产源码而不是另一份拷贝。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "app", "routers", "auth.py")


class HTTPException(Exception):
    def __init__(self, status_code: int = 400, detail: str = ""):
        self.status_code = status_code
        self.detail = detail


class FakeUser:
    """只实现权限判据需要的两个方法。"""

    def __init__(self, project_id=None, super_admin=False, org_admin=False):
        self.project_id = project_id
        self._super = super_admin
        self._org = org_admin

    def is_super_admin(self) -> bool:
        return self._super

    def is_org_admin(self) -> bool:
        return self._org


def _extract(name: str, src: str) -> str:
    """按 ``def name(`` 切出完整函数源码（到下一个顶层 def/装饰器为止）。"""
    m = re.search(r"^def %s\(" % re.escape(name), src, re.M)
    if not m:
        raise AssertionError("源码里找不到函数：%s" % name)
    start = m.start()
    m2 = re.search(r"^(?:def |async def |@)", src[m.end():], re.M)
    end = m.end() + m2.start() if m2 else len(src)
    return src[start:end].rstrip() + "\n"


def load_scope_funcs():
    src = open(SRC, encoding="utf-8").read()
    ns = {
        "HTTPException": HTTPException,
        "User": FakeUser,
    }
    for fn in ("_can_manage_all_projects", "require_project_id"):
        exec(_extract(fn, src), ns)
    # project_scope_id / can_read_project 带类型注解 User，需要真实符号
    body = _extract("project_scope_id", src) + "\n" + _extract("can_read_project", src)
    exec(body, ns)
    return ns


def main() -> int:
    ns = load_scope_funcs()
    project_scope_id = ns["project_scope_id"]
    can_read_project = ns["can_read_project"]

    checks: list[tuple[str, bool]] = []

    def eq(label, got, want):
        checks.append((label, got == want))

    super_admin = FakeUser(project_id=3, super_admin=True)
    org_admin = FakeUser(project_id=3, org_admin=True)
    engineer = FakeUser(project_id=3)
    homeless = FakeUser(project_id=None)
    homeless_super = FakeUser(project_id=None, super_admin=True)

    # --- project_scope_id ---
    eq("超管+全部项目 → None（不限项目）", project_scope_id(super_admin, True), None)
    eq("管理员+全部项目 → None", project_scope_id(org_admin, True), None)
    eq("超管+未开全部项目 → 本项目", project_scope_id(super_admin, False), 3)
    eq("普通账号+要求全部项目 → 仍限本项目", project_scope_id(engineer, True), 3)
    eq("普通账号默认 → 本项目", project_scope_id(engineer), 3)

    # 无归属项目时必须报 400（与改造前一致）
    try:
        project_scope_id(homeless, False)
        checks.append(("无项目+单项目视角 → 抛 400", False))
    except HTTPException as exc:
        checks.append(("无项目+单项目视角 → 抛 400", exc.status_code == 400))
    # 超管无归属项目 + 全部项目视角 → 允许跨项目读（不应 400）
    try:
        got = project_scope_id(homeless_super, True)
        checks.append(("超管无项目+全部项目 → None 不报错", got is None))
    except HTTPException:
        checks.append(("超管无项目+全部项目 → None 不报错", False))

    # --- can_read_project ---
    eq("超管+全部项目 → 可读别的项目", can_read_project(9, super_admin, True), True)
    eq("超管+全部项目 → 可读本项目", can_read_project(3, super_admin, True), True)
    eq("超管+单项目视角 → 别的项目不可读", can_read_project(9, super_admin, False), False)
    eq("超管+单项目视角 → 本项目可读", can_read_project(3, super_admin, False), True)
    eq("普通账号+全部项目 → 别的项目不可读", can_read_project(9, engineer, True), False)
    eq("普通账号 → 本项目可读", can_read_project(3, engineer), True)
    eq("project_id 为 None 的对象 → 一律不可读", can_read_project(None, super_admin, True), False)
    eq("无归属项目账号 → 不可读任何对象", can_read_project(3, homeless, True), False)

    # --- require_project_id：全部项目视角下的写入归属 ---
    # 前端 `ensureWritableProject()` 选好项目后以 `X-Project-Id` 带给后端，
    # `get_current_user` 把它挂到 `user._write_project_id`，这里优先采用。
    require_project_id = ns["require_project_id"]

    writer_super = FakeUser(project_id=3, super_admin=True)
    writer_super._write_project_id = 9
    eq("超管+选定写入项目 → 写到 9", require_project_id(writer_super), 9)

    writer_admin = FakeUser(project_id=3, org_admin=True)
    writer_admin._write_project_id = 9
    eq("管理员+选定写入项目 → 写到 9", require_project_id(writer_admin), 9)

    # 普通账号即使被挂上目标项目也不生效（防伪造头）
    writer_normal = FakeUser(project_id=3)
    writer_normal._write_project_id = 9
    eq("普通账号+伪造目标项目 → 仍写本项目", require_project_id(writer_normal), 3)

    # 未选项目（无 header）时行为与改造前完全一致
    plain_super = FakeUser(project_id=3, super_admin=True)
    eq("超管未选写入项目 → 回落到本项目", require_project_id(plain_super), 3)

    homeless_super2 = FakeUser(project_id=None, super_admin=True)
    homeless_super2._write_project_id = 9
    eq("超管无归属项目+选定写入项目 → 写到 9", require_project_id(homeless_super2), 9)

    try:
        require_project_id(FakeUser(project_id=None))
        checks.append(("无项目且未选写入项目 → 抛 400", False))
    except HTTPException as exc:
        checks.append(("无项目且未选写入项目 → 抛 400", exc.status_code == 400))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("  OK   " if ok else "  FAIL ") + name)
    print("\n%d/%d 通过" % (len(checks) - len(failed), len(checks)))
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
