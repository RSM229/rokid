# -*- coding: utf-8 -*-
"""``export_users`` 项目范围自测（不连库、不调外部服务）。

跑法：cd cloud-backend && python tests/test_export_project_scope.py

本机 venv 缺 fastapi/pydantic/sqlalchemy，无法直接 import app.routers.auth；
沿用 test_project_scope.py 的范式：AST 从**生产源码**切出 ``export_users``
的范围判定段（到 ``users = (await db.execute(query))...`` 为止），用桩件执行，
保证测的是生产源码而不是另一份拷贝。

重点锁的行为：
  1. 公司级 + project_id → or_(所属项目=pid, id in 该项目成员[含离场])
  2. 公司级 + 不带 project_id → 不加项目过滤（全量，与改造前一致）
  3. 🔴 非公司级 + 伪造 project_id → **参数被忽略**，仍限本项目成员
     （账号表含初始口令明文，认了这个参数 = 真实越权）
  4. 项目不存在 → 404
  5. 跨企业（org_id 不匹配）→ 403
  6. 指定项目的成员子查询**不带 left_at 过滤**（与前端 members(pid, true) 一致）
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "app", "routers", "auth.py")

CUT_MARKER = "users = (await db.execute(query)).scalars().all()"


class HTTPException(Exception):
    def __init__(self, status_code: int = 400, detail: str = ""):
        self.status_code = status_code
        self.detail = detail


class FakeCol:
    """记录比较操作的假列。name 形如 'User.project_id'。"""

    def __init__(self, name: str):
        self.name = name

    def __getattr__(self, item):
        if item == "name":
            return object.__getattribute__(self, "name")
        return FakeCol(f"{self.name}.{item}")

    def __call__(self, *args):  # is_(None) / in_(x) / asc() / like(x)
        return ("call", self.name, args)

    def __eq__(self, other):
        return ("eq", self.name, other)

    def __hash__(self):
        return hash(self.name)

    def in_(self, arg):
        return ("in", self.name, arg)

    def is_(self, arg):
        return ("is", self.name, arg)

    def asc(self):
        return ("asc", self.name)


class _AnyAttr:
    """User / ProjectMember / Project 的假模型类：任意属性返回 FakeCol。"""

    def __init__(self, prefix: str):
        self._prefix = prefix

    def __getattr__(self, item):
        return FakeCol(f"{self._prefix}.{item}")


class FakeQuery:
    def __init__(self, label: str):
        self.label = label
        self.wheres: list = []

    def order_by(self, *args):
        return self

    def where(self, *conds):
        # 生产代码里有 .where(A, B) 两条件写法（非公司级分支的成员子查询）
        self.wheres.extend(conds)
        return self


def make_select(main_query: FakeQuery):
    created: list[FakeQuery] = []

    def select(*cols):
        # 第一个 select(User) 用主 query；后续 select(ProjectMember.user_id) 各建一个
        name = getattr(cols[0], "name", "?") if cols else "?"
        q = main_query if not created and name == "User" else FakeQuery(name)
        created.append(q)
        return q

    return select, created


class FakeProject:
    def __init__(self, pid: int, name: str = "测试项目", org_id=None):
        self.id = pid
        self.name = name
        self.org_id = org_id


class FakeUser:
    def __init__(self, project_id=None, super_admin=False, org_admin=False, org_id=None):
        self.project_id = project_id
        self.org_id = org_id
        self._super = super_admin
        self._org = org_admin

    def is_super_admin(self) -> bool:
        return self._super

    def is_org_admin(self) -> bool:
        return self._org


class FakeResult:
    def scalars(self):
        return self

    def all(self):
        return []


class FakeDb:
    def __init__(self, projects: dict[int, FakeProject]):
        self.projects = projects

    async def get(self, model, pid):
        return self.projects.get(pid)

    async def execute(self, query):
        return FakeResult()


def build_env():
    """从生产源码切出 export_users 的范围判定段，装配桩件后返回可调用对象。"""
    source = open(SRC, encoding="utf-8").read()
    tree = ast.parse(source)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "export_users"
    )
    seg = ast.get_source_segment(source, fn)
    assert seg is not None
    # get_source_segment 会带上装饰器（@router.get(...)），桩环境里没有 router，剥掉
    seg = "\n".join(ln for ln in seg.splitlines() if not ln.lstrip().startswith("@"))
    # 只保留到取 users 为止（后面是拼 xlsx，与范围判定无关）
    cut = seg.index(CUT_MARKER) + len(CUT_MARKER)
    body = seg[:cut] + "\n    return query\n"

    main_query = FakeQuery("main")
    select_fn, created = make_select(main_query)

    def or_(*args):
        return ("or",) + args

    ns = {
        "Query": lambda *a, **k: None,
        "Depends": lambda *a, **k: None,
        "HTTPException": HTTPException,
        "require_permission": lambda *a, **k: None,
        "get_db": lambda: None,
        "AsyncSession": object,
        "select": select_fn,
        "or_": or_,
        "User": _AnyAttr("User"),
        "ProjectMember": _AnyAttr("ProjectMember"),
        "Project": FakeProject,
        "_can_manage_all_projects": lambda u: u.is_super_admin() or u.is_org_admin(),
        "_created_queries": created,
        "_main_query": main_query,
    }
    exec(compile(body, "<export_users_cut>", "exec"), ns)
    return ns["export_users"], ns


def run_case(manager, project_id, projects):
    fn, ns = build_env()
    db = FakeDb(projects)
    try:
        asyncio.run(fn(project_id=project_id, manager=manager, db=db))
        return None, ns["_main_query"], ns["_created_queries"]
    except HTTPException as e:
        return e, ns["_main_query"], ns["_created_queries"]


def flatten(cond) -> str:
    """把条件结构拍平成字符串方便断言。"""
    if isinstance(cond, tuple):
        return "(" + ",".join(flatten(c) for c in cond) + ")"
    if isinstance(cond, FakeQuery):
        return f"<Q:{cond.label};{[flatten(w) for w in cond.wheres]}>"
    return repr(cond)


PASSED = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def main():
    P1 = FakeProject(1, "项目一")
    P5 = FakeProject(5, "项目五")
    P_OTHER_ORG = FakeProject(9, "别的企业项目", org_id=2)
    projects = {1: P1, 5: P5, 9: P_OTHER_ORG}

    print("用例组 1：公司级（超管）+ project_id=5")
    exc, mq, created = run_case(FakeUser(project_id=1, super_admin=True), 5, projects)
    check("不抛异常", exc is None, str(exc))
    flat = [flatten(w) for w in mq.wheres]
    check("主查询恰好 1 个 where（or 复合条件）", len(mq.wheres) == 1, str(flat))
    or_flat = flat[0] if flat else ""
    check("or 含「所属项目=5」", "('eq','User.project_id',5)" in or_flat.replace('"', "'"), or_flat)
    check("or 含「id in 项目5成员子查询」", "'in','User.id'" in or_flat.replace('"', "'"), or_flat)
    # 成员子查询：project_id==5 且 **无 left_at 过滤**（含离场，与前端一致）
    member_qs = [q for q in created[1:] if q.label.endswith("user_id")]
    check("存在成员子查询", len(member_qs) == 1, str([q.label for q in created]))
    if member_qs:
        mw = [flatten(w) for w in member_qs[0].wheres]
        check("成员子查询按 project_id=5", any("ProjectMember.project_id" in w and "5" in w for w in mw), str(mw))
        check("🔴 成员子查询**不含** left_at（含离场成员）", not any("left_at" in w for w in mw), str(mw))

    print("用例组 2：公司级 + 不带 project_id（全量，行为与改造前一致）")
    exc, mq, created = run_case(FakeUser(project_id=1, super_admin=True), None, projects)
    check("不抛异常", exc is None, str(exc))
    check("主查询无 where（不过滤项目）", len(mq.wheres) == 0, str([flatten(w) for w in mq.wheres]))

    print("用例组 3：🔴 非公司级（总监）伪造 project_id=5 → 必须被忽略")
    exc, mq, created = run_case(FakeUser(project_id=1), 5, projects)
    check("不抛异常（静默忽略，与 X-Project-Id 头处理一致）", exc is None, str(exc))
    flat = [flatten(w) for w in mq.wheres]
    check("主查询 1 个 where（本项目成员过滤）", len(mq.wheres) == 1, str(flat))
    check("过滤的是**自己的项目 1**而非伪造的 5", "'User.id'" in flat[0].replace('"', "'") and "5" not in flat[0], str(flat))
    member_qs = [q for q in created[1:] if q.label.endswith("user_id")]
    if member_qs:
        mw = [flatten(w) for w in member_qs[0].wheres]
        check("成员子查询按 project_id=1", any("ProjectMember.project_id" in w and ",1)" in w for w in mw), str(mw))
        check("成员子查询含 left_at 过滤（在岗口径，与原逻辑一致）", any("left_at" in w for w in mw), str(mw))

    print("用例组 4：项目不存在 → 404")
    exc, mq, _ = run_case(FakeUser(super_admin=True), 999, projects)
    check("抛 404", exc is not None and exc.status_code == 404, str(exc))

    print("用例组 5：跨企业 → 403")
    exc, mq, _ = run_case(FakeUser(super_admin=True, org_id=1), 9, projects)
    check("抛 403", exc is not None and exc.status_code == 403, str(exc))
    exc, mq, _ = run_case(FakeUser(super_admin=True, org_id=2), 9, projects)
    check("同企业放行", exc is None, str(exc))

    print("用例组 6：公司级「管理员」（org_admin）同样认 project_id")
    exc, mq, created = run_case(FakeUser(project_id=1, org_admin=True), 5, projects)
    check("不抛异常", exc is None, str(exc))
    check("加了 or 复合过滤", len(mq.wheres) == 1, str([flatten(w) for w in mq.wheres]))

    print()
    print(f"结果：{PASSED} 通过 / {FAILED} 失败")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
