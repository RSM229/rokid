"""验证 login 的角色白名单与匹配逻辑（纯逻辑，不连库）。

覆盖 Step 2 引入公司级角色后 `POST /api/auth/login` 的判定矩阵：

    - 白名单：项目级 UserRole ∪ 公司级 ORG_ROLES
    - 匹配：公司级 req.role 走 org_role 比对；项目级走 users.role
    - token.role：公司级身份登录时记为 org_role

本机 default venv 无 sqlalchemy（且无外网 PyPI），故不从 `app.models.user`
导入，而是 AST 解析该文件本身的源码取常量 —— 保证验证对象仍是真实文件。

用法::

    python scripts/verify_login_roles.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_PY = ROOT / "app" / "models" / "user.py"

PASS = 0
FAIL = 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}: got={got!r} want={want!r}")


# ---------------- 从 app/models/user.py 抽常量（不导入 ORM） ----------------

tree = ast.parse(USER_PY.read_text(encoding="utf-8"))

_module_strs: dict[str, str] = {}          # ORG_ROLE_ADMIN = "admin"
_enum_members: dict[str, str] = {}         # CHIEF_SUPERVISOR -> chief_supervisor
_org_roles_raw: list[ast.AST] = []
_labels_raw: dict[str, ast.AST] = {}       # 键名（Attr 则取成员名）-> value 节点

for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        t = node.targets[0]
        if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                _module_strs[t.id] = node.value.value

    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if node.target.id == "ORG_ROLES" and node.value is not None:
            # 形态：frozenset({ORG_ROLE_SUPER_ADMIN, ORG_ROLE_ADMIN})
            # 元素是**裸 Name**，不是字符串常量 —— 须回查模块级常量。
            for elt in ast.walk(node.value):
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    _org_roles_raw.append(elt)
                elif isinstance(elt, ast.Name):
                    if elt.id in _module_strs:
                        _org_roles_raw.append(ast.Constant(value=_module_strs[elt.id]))

    if isinstance(node, ast.ClassDef) and node.name == "UserRole":
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                if isinstance(stmt.value.value, str) and isinstance(stmt.targets[0], ast.Name):
                    _enum_members[stmt.targets[0].id] = stmt.value.value

    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if node.target.id == "ROLE_LABELS" and node.value is not None:
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Attribute) and isinstance(k.value, ast.Attribute):
                    # UserRole.CHIEF_SUPERVISOR.value
                    #   k = Attribute(attr='value', value=Attribute(attr='CHIEF_SUPERVISOR', value=Name('UserRole')))
                    member = getattr(k.value, "attr", None)
                    if member and member in _enum_members:
                        _labels_raw[_enum_members[member]] = v
                    elif member:
                        print(f"  [warn] ROLE_LABELS 键未解析: {member}")
                elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                    _labels_raw[k.value] = v

ROLE_VALUES: list[str] = list(_enum_members.values())
ORG_ROLES: set[str] = {_module_strs.get(e.value, e.value) for e in _org_roles_raw if isinstance(e, ast.Constant)}  # type: ignore[union-attr]
ROLE_LABELS: dict[str, str] = {
    k: v.value
    for k, v in _labels_raw.items()
    if isinstance(v, ast.Constant) and isinstance(v.value, str)
}

print(f"[抽取] UserRole={len(ROLE_VALUES)} 项  ORG_ROLES={sorted(ORG_ROLES)}  ROLE_LABELS={len(ROLE_LABELS)} 项")
assert ROLE_VALUES, "未抽到 UserRole 枚举值"
assert ORG_ROLES, "未抽到 ORG_ROLES"
assert ROLE_LABELS, "未抽到 ROLE_LABELS"


# ------------- 复刻 login 的判定（与 app/routers/auth.py 同构） -------------

VALID_ROLES = ROLE_VALUES + sorted(ORG_ROLES)
SUPER_ADMIN = "super_admin"


def whitelist_ok(role: str) -> bool:
    return role in VALID_ROLES


def role_matches(*, req_role: str, user_role: str, user_org_role: str | None) -> bool:
    """True = 允许登录（复刻 auth.py 的 if/elif 链）。"""
    is_super = user_org_role == SUPER_ADMIN
    is_org_level = user_org_role in ORG_ROLES

    if req_role in ORG_ROLES:
        return user_org_role == req_role
    if is_super:
        return True          # 超管可代任意项目岗位登录
    if is_org_level:
        return False         # 非超管的公司级账号不得以项目岗位登录
    return user_role == req_role


def resolve_login_role(*, req_role: str, user_role: str, user_org_role: str | None) -> str:
    """token 里记的 role（复刻 login_role 赋值）。"""
    if user_org_role == SUPER_ADMIN and req_role in ORG_ROLES:
        return req_role
    if user_org_role in ORG_ROLES:
        return user_org_role or user_role
    return user_role


print("\n== 1. 白名单：项目级 7 项 + 公司级 2 项 ==")
check("项目级数量=7", len(ROLE_VALUES), 7)
check("含 super_admin", "super_admin" in VALID_ROLES, True)
check("含 admin", "admin" in VALID_ROLES, True)
check("总数=9", len(VALID_ROLES), 9)
check("无重复", len(set(VALID_ROLES)), len(VALID_ROLES))
print("  白名单 =", VALID_ROLES)

print("\n== 2. 中文不得进白名单（S2 全链路英文 key） ==")
for zh in ("超级管理员", "管理员", "总监理工程师", "监理员", "总监代表"):
    check(f"'{zh}' 被拒", whitelist_ok(zh), False)

print("\n== 3. 复现截图 Bug：admin 账号（role=chief_supervisor, org_role=super_admin）==")
check("选『超级管理员』→ 白名单通过（修复前在此 400）", whitelist_ok("super_admin"), True)
check("选『超级管理员』→ 匹配放行", role_matches(req_role="super_admin", user_role="chief_supervisor", user_org_role="super_admin"), True)
check("token.role = super_admin", resolve_login_role(req_role="super_admin", user_role="chief_supervisor", user_org_role="super_admin"), "super_admin")
check("超管以总监身份登录亦放行（原行为保留）", role_matches(req_role="chief_supervisor", user_role="chief_supervisor", user_org_role="super_admin"), True)
check("超管以监理员身份登录亦放行", role_matches(req_role="inspector", user_role="chief_supervisor", user_org_role="super_admin"), True)
check("超管代岗位登录时 role 仍记为 super_admin", resolve_login_role(req_role="inspector", user_role="chief_supervisor", user_org_role="super_admin"), "super_admin")

print("\n== 4. 公司级『管理员』账号（org_role=admin）==")
check("选『管理员』→ 放行", role_matches(req_role="admin", user_role="inspector", user_org_role="admin"), True)
check("token.role = admin", resolve_login_role(req_role="admin", user_role="inspector", user_org_role="admin"), "admin")
check("选『超级管理员』→ 拒", role_matches(req_role="super_admin", user_role="inspector", user_org_role="admin"), False)
check("选项目岗位『监理员』→ 拒（防 role/permissions 错位）", role_matches(req_role="inspector", user_role="inspector", user_org_role="admin"), False)

print("\n== 5. 纯项目级账号（org_role=NULL）==")
check("主岗一致 → 放行", role_matches(req_role="chief_supervisor", user_role="chief_supervisor", user_org_role=None), True)
check("主岗不一致 → 拒", role_matches(req_role="inspector", user_role="chief_supervisor", user_org_role=None), False)
check("选公司级 → 拒", role_matches(req_role="admin", user_role="chief_supervisor", user_org_role=None), False)
check("选 super_admin → 拒", role_matches(req_role="super_admin", user_role="chief_supervisor", user_org_role=None), False)
check("token.role = 主岗", resolve_login_role(req_role="chief_supervisor", user_role="chief_supervisor", user_org_role=None), "chief_supervisor")

print("\n== 6. ROLE_LABELS 覆盖全部白名单 ==")
check("无缺 label", [r for r in VALID_ROLES if r not in ROLE_LABELS], [])
check("super_admin 中文", ROLE_LABELS.get("super_admin"), "超级管理员")
check("admin 中文", ROLE_LABELS.get("admin"), "管理员")

print("\n== 7. GET /auth/roles?client=web 与 login 白名单口径一致 ==")
# auth.py: web 分支 = UserRole ∪ ORG_ROLES；miniprogram 分支 = UserRole
web_roles = ROLE_VALUES + sorted(ORG_ROLES)
mp_roles = list(ROLE_VALUES)
check("web 可选 = login 白名单", sorted(web_roles), sorted(VALID_ROLES))
check("小程序不含公司级", [r for r in mp_roles if r in ORG_ROLES], [])

print(f"\n==== 结果：{PASS} 通过 / {FAIL} 失败 ====")
sys.exit(1 if FAIL else 0)
