"""验证公司级角色授予逻辑（纯逻辑，不连库）。

覆盖 create_user / update_user 新增的 org_role 处理与防锁死规则：

    - `_validate_org_role`：空值归一化、非法值拒绝、非超管拒绝
    - update_user 的两条防锁死：超管不得自我降级、系统须保留最后一名超管

本机 default venv 无 sqlalchemy（且无外网 PyPI），故 AST 解析
`app/routers/auth.py` 与 `app/models/user.py` 取常量与关键分支结构，
不导入 ORM。

用法::

    python scripts/verify_org_role_grant.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUTH_PY = ROOT / "app" / "routers" / "auth.py"
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


# ---------- 从源码抽常量 ----------

user_tree = ast.parse(USER_PY.read_text(encoding="utf-8"))
auth_src = AUTH_PY.read_text(encoding="utf-8")
auth_tree = ast.parse(auth_src)

_module_strs: dict[str, str] = {}
_org_roles_raw: list[ast.AST] = []
for node in user_tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        t = node.targets[0]
        if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                _module_strs[t.id] = node.value.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if node.target.id == "ORG_ROLES" and node.value is not None:
            for elt in ast.walk(node.value):
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    _org_roles_raw.append(elt)
                elif isinstance(elt, ast.Name) and elt.id in _module_strs:
                    _org_roles_raw.append(ast.Constant(value=_module_strs[elt.id]))

ORG_ROLES = {e.value for e in _org_roles_raw if isinstance(e, ast.Constant)}  # type: ignore[attr-defined]
SUPER_ADMIN = _module_strs.get("ORG_ROLE_SUPER_ADMIN", "super_admin")
assert ORG_ROLES, "未抽到 ORG_ROLES"

print(f"[抽取] ORG_ROLES={sorted(ORG_ROLES)}  SUPER_ADMIN={SUPER_ADMIN}")

# ---------- 定位关键代码结构 ----------

funcs = {
    n.name: n
    for n in ast.walk(auth_tree)
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
}
assert "_validate_org_role" in funcs, "未找到 _validate_org_role"
assert "create_user" in funcs, "未找到 create_user"
assert "update_user" in funcs, "未找到 update_user"


def func_src(name: str) -> str:
    node = funcs[name]
    lines = auth_src.splitlines()
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


VAL_SRC = func_src("_validate_org_role")
CREATE_SRC = func_src("create_user")
UPDATE_SRC = func_src("update_user")

print("\n== 1. _validate_org_role 的规则结构（源码断言）==")
check("空串归一化为 None", 'value == ""' in VAL_SRC, True)
check("识别 none/null/0 别名", '{"none", "null", "0"}' in VAL_SRC, True)
check("非超管被拒（403）", "is_super_admin" in VAL_SRC and "403" in VAL_SRC, True)
check("非法值被拒（400）", "ORG_ROLES" in VAL_SRC and "400" in VAL_SRC, True)


def normalize(raw: str | None, manager_is_super: bool) -> str | tuple[str, str]:
    """复刻 _validate_org_role。返回归一化值，或 ('ERR', 码)。"""
    if raw is None:
        return None
    value = raw.strip()
    if value == "" or value.lower() in {"none", "null", "0"}:
        return None
    if not manager_is_super:
        return ("ERR", "403")
    if value not in ORG_ROLES:
        return ("ERR", "400")
    return value


print("\n== 2. _validate_org_role 行为矩阵 ==")
check("None（未传）→ None", normalize(None, True), None)
check("''（清空）→ None", normalize("", True), None)
check("'   '（空白）→ None", normalize("   ", True), None)
check("'none' → None", normalize("none", True), None)
check("'NULL' → None（大小写无关）", normalize("NULL", True), None)
check("'admin'（超管提交）→ admin", normalize("admin", True), "admin")
check("'super_admin'（超管提交）→ super_admin", normalize("super_admin", True), "super_admin")
check("'super_admin'（非超管提交）→ 403", normalize("super_admin", False), ("ERR", "403"))
check("''（非超管清空）→ None（清空不需超管）", normalize("", False), None)
check("'root'（非法值）→ 400", normalize("root", True), ("ERR", "400"))
check("'超级管理员'（中文）→ 400", normalize("超级管理员", True), ("ERR", "400"))
check("'admin '（尾空格）→ admin", normalize("admin ", True), "admin")

print("\n== 3. create_user / update_user 已接入校验 ==")
check("create_user 调用 _validate_org_role", "_validate_org_role(" in CREATE_SRC, True)
check("create_user 写入 org_role", "org_role=org_role" in CREATE_SRC, True)
check("update_user 调用 _validate_org_role", "_validate_org_role(" in UPDATE_SRC, True)
check("update_user 区分 None 与空串", "if req.org_role is not None:" in UPDATE_SRC, True)

print("\n== 4. update_user 两条防锁死规则（源码断言）==")
check("规则①：超管不得自我降级", "target.id == manager.id" in UPDATE_SRC and "不能降低当前登录超管" in UPDATE_SRC, True)
check("规则②：保留最后一名超管", "系统必须保留至少一名启用状态的超级管理员" in UPDATE_SRC, True)
check("规则②统计口径含 is_active", "is_active.is_(True)" in UPDATE_SRC, True)
check("规则②排除自身后再计数", "User.id != target.id" in UPDATE_SRC, True)

print("\n== 5. list_users 出参含 org_role（前端展示依赖）==")
LIST_SRC = func_src("list_users")
check('返回项含 "org_role"', '"org_role": item.org_role' in LIST_SRC, True)

print("\n== 6. 防锁死行为矩阵 ==")


def update_org_role(
    *,
    target_id: int,
    manager_id: int,
    target_is_super: bool,
    new_role: str | None,
    other_super_count: int,
) -> str:
    """复刻 update_user 中 org_role 分支的决策（返回 'ok' 或错误码）。

    new_role: 归一化后的值（'' 与 'none' 等在 _validate_org_role 已压成 None 语义，
    这里用 '' 代表"清除 org_role"）。
    """
    if new_role is None:
        return "ok"  # 未传 → 不修改
    clearing = new_role == ""
    if clearing:
        # 清空 = 降级
        if target_id == manager_id and target_is_super:
            return "400-self-downgrade"
        if target_is_super and other_super_count == 0:
            return "400-last-super"
        return "ok"
    if new_role == SUPER_ADMIN:
        return "ok"  # 提升或保持，无风险
    # new_role 为 admin（即从 super_admin 降为 admin）
    if target_is_super:
        if target_id == manager_id:
            return "400-self-downgrade"
        if other_super_count == 0:
            return "400-last-super"
    return "ok"


check("超管 A 把超管 B 降为 admin（还有 A）→ 允许", update_org_role(target_id=2, manager_id=1, target_is_super=True, new_role="admin", other_super_count=1), "ok")
check("超管 A 给自己降级 → 拒（400）", update_org_role(target_id=1, manager_id=1, target_is_super=True, new_role="admin", other_super_count=1), "400-self-downgrade")
check("超管 A 清掉自己 org_role → 拒（400）", update_org_role(target_id=1, manager_id=1, target_is_super=True, new_role="", other_super_count=1), "400-self-downgrade")
check("唯一超管（其他人无超管）自我降级 → 拒", update_org_role(target_id=1, manager_id=1, target_is_super=True, new_role="admin", other_super_count=0), "400-self-downgrade")
check("把普通用户提为 super_admin → 允许", update_org_role(target_id=5, manager_id=1, target_is_super=False, new_role="super_admin", other_super_count=1), "ok")
check("把 admin 提为 super_admin → 允许", update_org_role(target_id=5, manager_id=1, target_is_super=False, new_role="super_admin", other_super_count=1), "ok")
check("清除某 admin 的 org_role → 允许", update_org_role(target_id=6, manager_id=1, target_is_super=False, new_role="", other_super_count=1), "ok")
check("超管保持 super_admin 不变 → 允许", update_org_role(target_id=2, manager_id=1, target_is_super=True, new_role="super_admin", other_super_count=1), "ok")

print(f"\n==== 结果：{PASS} 通过 / {FAIL} 失败 ====")
sys.exit(1 if FAIL else 0)
