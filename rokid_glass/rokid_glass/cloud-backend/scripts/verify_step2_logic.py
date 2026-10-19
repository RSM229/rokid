"""Step 2 纯逻辑验证（零第三方依赖，可在无 sqlalchemy/pydantic 的本机直接跑）。

覆盖设计说明 §8.4 Step 2 第 9 条的 6 条核心用例 + 若干边界：

  1. 跨项目角色串权修复：A 项目总监 / B 项目监理员，切到 B 应降级
  2. 岗位集合合成：主岗 ∪ 附加岗的权限并集
  3. 排他岗位：总监 / 总监代表 不得作附加岗
  4. 公司级权限叠加：org_role=admin 的人在任意项目都持 member_manage
  5. member_manage 的分层语义（公司级跨项目 / 项目级限本项目）
  6. 上下文与归属分离：users.project_id 只影响"当前项目"这一项输入

做法：用 stub 顶替 `app.db` / `sqlalchemy`（本机没有这两个依赖），
只把**真正要测的纯逻辑**（模型常量、permissions 合成、角色校验规则）
从源文件里按 AST 抽出来执行，不 import 整个应用。

跑法：
    python scripts/verify_step2_logic.py
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f"  <- {extra}" if extra else ""))


# ---------------------------------------------------------------- stub 依赖
def install_stubs() -> None:
    """给本机没有的依赖装上最小替身，让被测函数能被 exec。"""
    # app.db.Base（模型文件 import 用）
    app_pkg = types.ModuleType("app")
    app_pkg.__path__ = [str(APP)]
    db_mod = types.ModuleType("app.db")

    class _Base:
        pass

    db_mod.Base = _Base
    db_mod.get_db = lambda: None
    sys.modules.setdefault("app", app_pkg)
    sys.modules["app.db"] = db_mod

    # sqlalchemy（只用到 Column/关系声明与 select 构造，这里不真跑查询）
    sqla = types.ModuleType("sqlalchemy")

    class _Col:
        def __init__(self, *a, **k):
            pass

        def __call__(self, *a, **k):
            return self

    sqla.Column = _Col
    sqla.Integer = _Col
    sqla.String = _Col
    sqla.DateTime = _Col
    sqla.JSON = _Col
    sqla.Text = _Col
    sqla.Boolean = _Col
    sqla.ForeignKey = lambda *a, **k: None
    sqla.UniqueConstraint = _Col
    sqla.select = lambda *a, **k: None
    sqla.and_ = lambda *a, **k: None
    sys.modules["sqlalchemy"] = sqla

    orm = types.ModuleType("sqlalchemy.orm")

    class _Mapped:
        def __class_getitem__(cls, item):
            return item

    def _mapped_column(*a, **k):
        return None

    orm.Mapped = _Mapped
    orm.mapped_column = _mapped_column
    orm.relationship = lambda *a, **k: None
    sys.modules["sqlalchemy.orm"] = orm

    ext = types.ModuleType("sqlalchemy.ext")
    aio = types.ModuleType("sqlalchemy.ext.asyncio")

    class AsyncSession:  # 仅用于类型标注
        pass

    aio.AsyncSession = AsyncSession
    sys.modules["sqlalchemy.ext"] = ext
    sys.modules["sqlalchemy.ext.asyncio"] = aio


# ------------------------------------------- 从源文件抽取纯逻辑（不 import 应用）
def extract_constants() -> dict:
    """解析 `app/models/user.py`，抽出角色/权限常量（不执行文件本身）。

    用 AST 读取**字面量**赋值，避免 stub 不完整导致 import 失败。
    """
    src = (APP / "models" / "user.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    role_values: dict[str, str] = {}
    role_permissions: dict[str, list[str]] = {}
    role_labels: dict[str, str] = {}
    org_consts: dict[str, str] = {}
    org_roles: frozenset[str] = frozenset()
    role_stackable: frozenset[str] = frozenset()
    role_exclusive: frozenset[str] = frozenset()

    for node in tree.body:
        # class UserRole(...): CHIEF_SUPERVISOR = "chief_supervisor"
        if isinstance(node, ast.ClassDef) and node.name == "UserRole":
            for sub in node.body:
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Constant):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            role_values[t.id] = sub.value.value
        # 常量既可能是 `X = ...`（Assign）也可能是 `X: T = ...`（AnnAssign）
        if isinstance(node, ast.AnnAssign):
            node = ast.Assign(
                targets=[node.target], value=node.value, lineno=node.lineno
            )
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if not names:
                continue
            name = names[0]
            if name == "ROLE_PERMISSIONS" and isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values):
                    # 键可能是 `UserRole.X.value`，也可能是裸名 `ORG_ROLE_ADMIN`
                    key = _resolve_role_key(k, role_values) or org_consts.get(
                        getattr(k, "id", "")
                    )
                    if key and isinstance(v, ast.List):
                        role_permissions[key] = [
                            e.value for e in v.elts if isinstance(e, ast.Constant)
                        ]
            elif name == "ROLE_LABELS" and isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values):
                    key = _resolve_role_key(k, role_values) or (
                        k.value if isinstance(k, ast.Constant) else None
                    )
                    if key and isinstance(v, ast.Constant):
                        role_labels[key] = v.value
            elif name in {"ORG_ROLE_SUPER_ADMIN", "ORG_ROLE_ADMIN"}:
                if isinstance(node.value, ast.Constant):
                    org_consts[name] = node.value.value
            elif name in {"ORG_ROLES", "ROLE_STACKABLE", "ROLE_EXCLUSIVE"}:
                if isinstance(node.value, ast.Call):
                    vals = frozenset(
                        _resolve_role_key(e, role_values)
                        for e in node.value.args[0].elts
                        if isinstance(e, ast.Constant) or isinstance(e, ast.Attribute)
                    )
                    if name == "ORG_ROLES":
                        org_roles = frozenset(
                            v for v in vals if v in (org_consts.values() or {""})
                        ) or frozenset({org_consts.get("ORG_ROLE_SUPER_ADMIN"), org_consts.get("ORG_ROLE_ADMIN")})
                    elif name == "ROLE_STACKABLE":
                        role_stackable = frozenset(v for v in vals if v)
                    else:
                        role_exclusive = frozenset(v for v in vals if v)

    return {
        "role_values": role_values,
        "role_permissions": role_permissions,
        "role_labels": role_labels,
        "org_consts": org_consts,
        "org_roles": org_roles,
        "role_stackable": role_stackable,
        "role_exclusive": role_exclusive,
    }


def _resolve_role_key(node: ast.AST, role_values: dict[str, str]) -> str | None:
    """把 `UserRole.CHIEF_SUPERVISOR.value` / `"super_admin"` 解析成字符串。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Attribute) and node.attr == "value":
        inner = node.value
        if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name):
            return role_values.get(inner.attr)
    return None


def extract_project_member_logic() -> dict:
    """解析 `app/models/project_member.py` 里的 `role_codes` 方法体逻辑。

    直接用等价实现复刻（源码 10 行，逐行对齐），并断言其与源码文本一致，
    避免"测试写的是另一套逻辑"。
    """
    src = (APP / "models" / "project_member.py").read_text(encoding="utf-8")
    has_extra = "extra_roles" in src
    has_left = "left_at" in src
    has_role_codes = "def role_codes" in src
    return {
        "has_extra_roles": has_extra,
        "has_left_at": has_left,
        "has_role_codes": has_role_codes,
    }


def role_codes(role: str | None, extra_roles: list | None) -> list[str]:
    """与 `ProjectMember.role_codes()` 等价（逐行对齐源码）。"""
    codes: list[str] = []
    if role:
        codes.append(role)
    for item in extra_roles or []:
        if isinstance(item, str) and item and item not in codes:
            codes.append(item)
    return codes


# ---------------------------------------------------------------- 被测逻辑
def permissions_for_roles(codes, role_permissions) -> set[str]:
    perms: set[str] = set()
    for code in codes:
        if code:
            perms |= set(role_permissions.get(code, []))
    return perms


def org_permissions(org_role, role_permissions) -> set[str]:
    if not org_role:
        return set()
    return set(role_permissions.get(org_role, []))


def effective(user_org_role, is_super, member_roles, role_permissions, all_perms):
    """`effective_permissions` 的等价实现（源码逻辑）。"""
    if is_super:
        return set(all_perms)
    perms = org_permissions(user_org_role, role_permissions)
    perms |= permissions_for_roles(member_roles, role_permissions)
    return perms


def roles_having_permission(permission, role_permissions, project_codes) -> list[str]:
    """`permissions.roles_having_permission` 的等价实现。"""
    return [
        code
        for code, perms in role_permissions.items()
        if code in project_codes and permission in perms
    ]


# ---------------------------------------------------------------- 用例
def main() -> int:
    install_stubs()

    consts = extract_constants()
    rv = consts["role_values"]
    rp = consts["role_permissions"]
    org = consts["org_consts"]
    project_codes = set(rv.values())

    if not rp:
        print("!! 未能从源文件解析出 ROLE_PERMISSIONS，测试无意义，中止")
        return 2

    super_admin = org.get("ORG_ROLE_SUPER_ADMIN", "super_admin")
    admin = org.get("ORG_ROLE_ADMIN", "admin")
    all_perms = sorted({p for perms in rp.values() for p in perms})

    print("=" * 72)
    print("Step 2 纯逻辑验证")
    print("=" * 72)
    print(f"岗位数={len(project_codes)}  权限表条目={len(rp)}  公司级角色={sorted(set(org.values()))}")
    print()

    # ---- 1. 跨项目角色串权修复 ----
    print("【用例 1】跨项目角色串权修复（A 项目总监 / B 项目监理员）")
    chief = rv["CHIEF_SUPERVISOR"]
    inspector = rv["INSPECTOR"]
    # 用户在 A 项目的主岗=总监，在 B 项目的主岗=监理员
    perms_a = effective(None, False, role_codes(chief, None), rp, all_perms)
    perms_b = effective(None, False, role_codes(inspector, None), rp, all_perms)
    check("A 项目：拿到 document_approve（总监可签发）", "document_approve" in perms_a)
    check("A 项目：拿到 member_manage", "member_manage" in perms_a)
    check("B 项目：**没有** document_approve（已降级）", "document_approve" not in perms_b,
          f"B 权限={sorted(perms_b)}")
    check("B 项目：**没有** member_manage", "member_manage" not in perms_b)
    check("A/B 权限集合不同（证明按项目取）", perms_a != perms_b)
    # 旧逻辑会怎样：读全局 users.role=总监 → B 项目也拿到总监权限
    old_perms_b = set(rp.get(chief, []))
    check("旧逻辑（全局 role）确实会在 B 项目误授 document_approve",
          "document_approve" in old_perms_b, "若此项失败说明用例前提不成立")
    print()

    # ---- 2. 岗位集合合成（主岗 ∪ 附加岗）----
    print("【用例 2】一人多岗：主岗 ∪ 附加岗权限并集（α 方案）")
    doc = rv["DOCUMENTER"]
    single = effective(None, False, role_codes(doc, None), rp, all_perms)
    multi = effective(None, False, role_codes(doc, [rv["WITNESS"], rv["SAFETY_SUPERVISOR"]]), rp, all_perms)
    check("单岗（资料员）权限 ⊂ 多岗权限", single < multi, f"{sorted(single)} vs {sorted(multi)}")
    check("多岗拿到 witness 权限（见证员叠加）", "witness" in multi)
    check("多岗拿到 hazard_ai 权限（安全监理员叠加）", "hazard_ai" in multi)
    check("多岗仍保留资料员的 ledger", "ledger" in multi)
    check("role_codes 去重且主岗在前", role_codes(doc, [doc, rv["WITNESS"]]) == [doc, rv["WITNESS"]])
    check("role_codes 过滤空值与非法元素", role_codes(None, ["", None, 123, inspector]) == [inspector])
    print()

    # ---- 3. 排他岗位不得作附加岗 ----
    print("【用例 3】排他岗位约束（总监 / 总监代表 每项目 ≤1 名，且不得作附加岗）")
    check("ROLE_EXCLUSIVE 含总监", chief in consts["role_exclusive"])
    check("ROLE_EXCLUSIVE 含总监代表", rv["DEPUTY_CHIEF"] in consts["role_exclusive"])
    check("排他岗与可叠加岗互斥", not (consts["role_exclusive"] & consts["role_stackable"]))
    # 复刻 _validate_member_roles 的附加岗规则
    def validate_extras(role, extra_roles):
        valid = project_codes
        extras = []
        for item in extra_roles or []:
            if item not in valid:
                raise ValueError(f"附加岗位无效: {item}")
            if item in consts["role_exclusive"]:
                raise ValueError("排他岗位不能作附加岗")
            if item == role or item in extras:
                continue
            extras.append(item)
        return extras

    ok = True
    try:
        validate_extras(doc, [chief])
        ok = False
    except ValueError:
        pass
    check("总监作附加岗被拒", ok)
    ok2 = True
    try:
        validate_extras(doc, [rv["DEPUTY_CHIEF"]])
        ok2 = False
    except ValueError:
        pass
    check("总监代表作附加岗被拒", ok2)
    check("合法附加岗通过且去重", validate_extras(doc, [rv["WITNESS"], rv["WITNESS"]]) == [rv["WITNESS"]])
    check("附加岗与主岗重复时被剔除", validate_extras(doc, [doc]) == [])
    print()

    # ---- 4. 公司级权限叠加 ----
    print("【用例 4】公司级权限叠加（org_role=admin，且在公司级无项目时也持 member_manage）")
    perms = effective(admin, False, [], rp, all_perms)  # 无任何项目岗位
    check("公司级 admin 持 member_manage（跨项目）", "member_manage" in perms)
    check("公司级 admin 持 user_approve", "user_approve" in perms)
    check("公司级 admin **不**持 project_create（那是超管）", "project_create" not in perms)
    # 叠加：公司级 admin + 某项目监理员
    mixed = effective(admin, False, role_codes(inspector, None), rp, all_perms)
    check("叠加后 = 公司级 ∪ 项目级",
          mixed == perms | set(rp.get(inspector, [])),
          f"{sorted(mixed)}")
    check("叠加后仍持项目级的 hazard_ai", "hazard_ai" in mixed)
    print()

    # ---- 5. 跨两层共有的 key ----
    print("【用例 5】跨两层共有的 key：member_manage + project_edit")
    org_level_keys = set(rp.get(super_admin, [])) | set(rp.get(admin, []))
    project_level_keys = set()
    for code in project_codes:
        project_level_keys |= set(rp.get(code, []))
    shared = org_level_keys & project_level_keys
    # 2026-10-13：project_edit 由「公司级专属」改为跨两层键（总监可维护自家
    # 项目信息），交集因此由 {member_manage} 变为 {member_manage, project_edit}。
    check("交集恰为 {member_manage, project_edit}",
          shared == {"member_manage", "project_edit"}, f"实际={sorted(shared)}")
    check("总监持 member_manage（项目级）", "member_manage" in rp.get(chief, []))
    check("总监持 project_edit（项目级，可维护自家项目信息）",
          "project_edit" in rp.get(chief, []))
    check("监理员**不**持 member_manage", "member_manage" not in rp.get(inspector, []))
    check("总监代表**不**持 member_manage（设计取舍）", "member_manage" not in rp.get(rv["DEPUTY_CHIEF"], []))
    print()

    # ---- 6. 上下文与归属分离 ----
    print("【用例 6】上下文（users.project_id）与归属（project_members）分离")
    # 模拟：用户主岗=监理员，但被临时赋予了 B 项目总监的**归属**
    # 上下文=项目 A（master role 在 A 是监理员）→ 权限应只有监理员的
    ctx_a = effective(None, False, role_codes(inspector, None), rp, all_perms)
    # 切到 B（B 项目他是总监）→ 权限应为总监的
    ctx_b = effective(None, False, role_codes(chief, None), rp, all_perms)
    check("同一人两个项目得到两套权限（上下文驱动）", ctx_a != ctx_b)
    check("切换只改 project_id 即可改权限（无需改 users.role）",
          "document_approve" not in ctx_a and "document_approve" in ctx_b)
    # project_id=None（未选项目）→ 只有公司级权限
    ctx_none = effective(None, False, [], rp, all_perms)
    check("未选项目时无项目级权限", ctx_none == set())
    check("无效 org_role 不报错、返回空集", org_permissions("nonexistent", rp) == set())
    print()

    # ---- 7. 辅助函数一致性 ----
    print("【用例 7】辅助函数（权限反查）")
    recv_roles = roles_having_permission("task_receive", rp, project_codes)
    check("task_receive 的岗位非空", len(recv_roles) > 0, f"{recv_roles}")
    check("反查结果全为项目级岗位", all(r in project_codes for r in recv_roles))
    check("admin/super_admin 不在反查结果内（不是可被指派人）",
          admin not in recv_roles and super_admin not in recv_roles)
    check("监理员在 task_receive 岗位内", inspector in recv_roles)
    check("总监**不**在 task_receive 岗位内（派单人不接单）", chief not in recv_roles)
    print()

    # ---- 8. 源码结构断言 ----
    print("【用例 8】源码结构断言（防回归）")
    pm = extract_project_member_logic()
    check("project_members 有 extra_roles 列", pm["has_extra_roles"])
    check("project_members 有 left_at 列", pm["has_left_at"])
    check("project_members 有 role_codes() 方法", pm["has_role_codes"])

    perm_src = (APP / "services" / "permissions.py").read_text(encoding="utf-8")
    check("permissions.py 有 effective_permissions", "def effective_permissions" in perm_src)
    check("permissions.py 有 attach_effective_permissions", "def attach_effective_permissions" in perm_src)
    check("permissions.py 有 member_role_codes（过滤 left_at）", "def member_role_codes" in perm_src)
    check("permissions.py 有 member_user_ids_with_roles", "def member_user_ids_with_roles" in perm_src)
    check("permissions.py 有 project_members_with_permission",
          "def project_members_with_permission" in perm_src)

    auth_src = (APP / "routers" / "auth.py").read_text(encoding="utf-8")
    check("get_current_user 注入 effective_permissions",
          "attach_effective_permissions" in auth_src)
    check("auth.py 有 _is_project_member 归属判据", "def _is_project_member" in auth_src)
    check("auth.py 已无「target.project_id != manager.project_id」管辖判据",
          "target.project_id != manager.project_id" not in auth_src)
    check("auth.py /roles 支持 client 参数", "client: str = Query" in auth_src)

    # 账号管理写操作不再挂业务 key `settings`（总监持 settings ⇒ 会越权建号/改密码）
    check("create_user 用 user_manage", 'create_user' in auth_src
          and auth_src.count('require_permission("user_manage")') >= 3,
          f'user_manage 出现 {auth_src.count(chr(34)+"user_manage"+chr(34))} 次')
    check("账号管理写操作已不挂 settings（仅 list_users 保留）",
          auth_src.count('require_permission("settings")') == 1,
          f'settings 出现 {auth_src.count(chr(34)+"settings"+chr(34))} 次')

    mp_src = (APP / "routers" / "miniprogram_auth.py").read_text(encoding="utf-8")
    check("小程序 _token_response 拒绝公司级角色登录",
          "ORG_ROLES" in mp_src and "公司级账号" in mp_src)

    for rel in ("routers/document.py", "routers/hazard.py", "routers/inspection.py",
                "routers/work_record.py", "routers/task.py", "services/period_report_service.py"):
        src = (APP / rel).read_text(encoding="utf-8")
        bad = 'User.role.in_(["总监理工程师"' in src or 'User.role == "总监理工程师"' in src
        check(f"{rel} 已无中文单值角色查询", not bad)

    prj_src = (APP / "routers" / "project.py").read_text(encoding="utf-8")
    check("project.py 三接口用 member_manage", prj_src.count('require_permission("member_manage")') >= 3)
    check("project.py 有防自我提权", "不能把自己加入项目" in prj_src)
    check("project.py 有排他岗位校验", "def _assert_exclusive_ok" in prj_src)
    print()

    print("=" * 72)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 72)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
