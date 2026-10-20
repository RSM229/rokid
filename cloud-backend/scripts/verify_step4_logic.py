# -*- coding: utf-8 -*-
"""Step 4（人员准入审批流）静态逻辑验证。

纯静态（AST + 源码断言），不连数据库、不产生副作用。覆盖设计说明 §8.8 的
关键不变量，避免「改了注册忘了登录」这类只做一半的改动。

运行：python scripts/verify_step4_logic.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "app" / "routers" / "auth.py"
MP = ROOT / "app" / "routers" / "miniprogram_auth.py"
INVITE_MODEL = ROOT / "app" / "models" / "invite_code.py"
USER_MODEL = ROOT / "app" / "models" / "user.py"

results: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    results.append((bool(cond), label))


def src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def find_func(tree: ast.AST, name: str) -> ast.AST | None:
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def func_src(p: Path, name: str) -> str:
    """取函数体源码（用于断言"函数内出现了什么"）。"""
    lines = src(p).splitlines()
    tree = ast.parse("\n".join(lines))
    fn = find_func(tree, name)
    if fn is None:
        return ""
    return "\n".join(lines[fn.lineno - 1: fn.end_lineno])


def routes(p: Path) -> dict[str, str]:
    """{path: func_name}"""
    tree = ast.parse(src(p))
    out: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                if (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in ("get", "post", "put", "delete")
                    and d.args
                    and isinstance(d.args[0], ast.Constant)
                ):
                    out[d.args[0].value] = n.name
    return out


# ---------------- 1. 数据层 ----------------
m = src(INVITE_MODEL)
check("org_id" in m, "invite_codes 模型含 org_id 字段（A-1 唯一 schema 变更）")
check("ForeignKey(\"organizations.id\")" in m, "invite_codes.org_id 指向 organizations")
check("nullable=True" in m.split("org_id")[1][:220], "invite_codes.org_id 为 nullable（可安全补列）")

# ---------------- 2. 注册链路 ----------------
web_reg = func_src(AUTH, "register")
mp_reg = func_src(MP, "register_account")
check('@router.post("/register", response_model=TokenResponse)' not in src(AUTH),
      "Web register 不再声明 TokenResponse（注册=提交申请）")
check("audit_status=AUDIT_PENDING" in web_reg, "Web 注册写 audit_status=pending")
check("org_id=invite_result.get(\"org_id\")" in web_reg, "Web 注册 org_id 继承邀请码")
check("create_access_token" not in web_reg, "Web 注册不再发放 token")
check("audit_status=AUDIT_PENDING" in mp_reg, "小程序注册写 audit_status=pending")
check("org_id=invite_result.get(\"org_id\")" in mp_reg, "小程序注册 org_id 继承邀请码")
check("_token_response" not in mp_reg, "小程序注册不再发放 token")
check("get_public_registration_roles" not in src(MP),
      "小程序端已废弃岗位白名单（§8.8.6 第 4 条）")

# ---------------- 3. 登录拦截 ----------------
login = func_src(AUTH, "login")
check("AUDIT_PENDING" in login, "Web login 拦截 pending")
check("AUDIT_REJECTED" in login, "Web login 拦截 rejected")
tr = func_src(MP, "_token_response")
check("AUDIT_PENDING" in tr, "小程序 _token_response 拦截 pending")
check("AUDIT_REJECTED" in tr, "小程序 _token_response 拦截 rejected")
rub = func_src(MP, "_release_unusable_binding")
check("AUDIT_REJECTED" in rub, "被拒账号视为不可用 → 微信不被占死")

# ---------------- 4. 审批接口 ----------------
r = routes(AUTH)
check("/registrations" in r and r["/registrations"] == "list_registrations", "GET /registrations 存在")
check(r.get("/registrations/{user_id}/approve") == "approve_registration", "POST approve 存在")
check(r.get("/registrations/{user_id}/reject") == "reject_registration", "POST reject 存在")

appr = func_src(AUTH, "approve_registration")
for marker, label in [
    ('audit_status = AUDIT_APPROVED', "批准：① 置 approved"),
    ("approved_by = manager.id", "批准：① 记 approved_by"),
    ("approved_at = datetime.now()", "批准：① 记 approved_at"),
    ("target.org_id = manager.org_id", "批准：② 补齐 org_id"),
    ("target.role = main_role", "批准：③ 主岗兜底"),
    ("db.add(ProjectMember(", "批准：④ 落项目成员（归属）"),
    ('action="user_approve"', "批准：⑤ 写 security_audit"),
    ("target.id == manager.id", "批准：禁批自己"),
    ("_assert_exclusive_ok", "批准：排他岗位校验"),
    ("_validate_org_role", "批准：org_role 仅超管"),
    ("_validate_member_roles", "批准：岗位合法性校验"),
]:
    check(marker in appr, label)

rej = func_src(AUTH, "reject_registration")
check("audit_status = AUDIT_REJECTED" in rej, "拒绝：置 rejected")
check("reject_reason" in rej, "拒绝：记理由")
check('action="user_reject"' in rej, "拒绝：写 security_audit")
check("target.id == manager.id" in rej, "拒绝：禁批自己")

# ---------------- 5. 权限口径 ----------------
t = ast.parse(src(USER_MODEL))
perm: dict[str, list[str]] = {}
for n in ast.walk(t):
    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "ROLE_PERMISSIONS":
        for k, v in zip(n.value.keys, n.value.values):  # type: ignore[attr-defined]
            key = k.attr if isinstance(k, ast.Attribute) else (k.id if isinstance(k, ast.Name) else None)
            if key and isinstance(v, ast.List):
                perm[key] = [e.value for e in v.elts if isinstance(e, ast.Constant)]
check("user_approve" in perm.get("ORG_ROLE_SUPER_ADMIN", []), "user_approve 归超管")
check("user_approve" in perm.get("ORG_ROLE_ADMIN", []), "user_approve 归公司管理员")
check(
    all("user_approve" not in perms for k, perms in perm.items()
        if k not in ("ORG_ROLE_SUPER_ADMIN", "ORG_ROLE_ADMIN")),
    "user_approve 不下放给项目级岗位",
)

# ---------------- 汇总 ----------------
ok = sum(1 for c, _ in results if c)
total = len(results)
print(f"Step 4 静态验证: {ok}/{total} 通过\n")
for c, label in results:
    print(f"  {'✅' if c else '❌'} {label}")
if ok != total:
    print(f"\n❌ 有 {total - ok} 项未通过")
    sys.exit(1)
print("\n✅ 全部通过")
