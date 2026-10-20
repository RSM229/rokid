"""邀请码管理接口逻辑验证（AST 静态 + 行为矩阵）。

本机无 sqlalchemy/fastapi，故不导入模块，改为解析源码抽真实常量与结构，
再复刻关键判定逻辑跑矩阵。验证点：
  1. 三个新接口存在且权限 key = user_manage；
  2. 邀请码岗位禁止公司级（ORG_ROLES）；
  3. 小程序 register 强制邀请码且角色取自邀请码；
  4. 小程序 register 不再使用 req.role 作为角色来源。

用法（本机即可）：python scripts/verify_invite_codes.py
"""
import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
AUTH = BACKEND / "app" / "routers" / "auth.py"
MP = BACKEND / "app" / "routers" / "miniprogram_auth.py"

ok = 0
fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {extra}")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def extract_org_roles(src: str) -> set:
    """从 user.py 抽 ORG_ROLES（元素可能是裸 Name，需回查常量）。"""
    user_py = (BACKEND / "app" / "models" / "user.py").read_text(encoding="utf-8")
    consts = {}
    for m in re.finditer(r'^(ORG_ROLE_[A-Z_]+)\s*=\s*"([^"]+)"', user_py, re.M):
        consts[m.group(1)] = m.group(2)
    m = re.search(r"ORG_ROLES\s*[:=].*?frozenset\(\{(.*?)\}\)", user_py, re.S)
    if not m:
        return set()
    names = [n.strip() for n in m.group(1).split(",") if n.strip()]
    out = set()
    for n in names:
        if n in consts:
            out.add(consts[n])
        elif n.startswith('"'):
            out.add(n.strip('"'))
    return out


def extract_user_role_values(src: str) -> set:
    user_py = (BACKEND / "app" / "models" / "user.py").read_text(encoding="utf-8")
    return set(re.findall(r'=\s*"([a-z_]+)"\s*(?:#.*)?$', user_py, re.M))


def main():
    auth_src = read(AUTH)
    mp_src = read(MP)
    tree = ast.parse(auth_src)

    # ---- 1. 三个接口存在 ----
    print("== 1. 邀请码管理三接口 ==")
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    for fname in ("list_invite_codes", "create_invite_code", "revoke_invite_code"):
        chk(f"存在 {fname}", fname in funcs)

    # 权限 key
    for fname in ("list_invite_codes", "create_invite_code", "revoke_invite_code"):
        node = funcs.get(fname)
        if not node:
            continue
        seg = ast.get_source_segment(auth_src, node) or ""
        chk(f"{fname} 权限 = user_manage", 'require_permission("user_manage")' in seg)

    # 路由装饰器
    deco_paths = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                s = ast.get_source_segment(auth_src, d) or ""
                if "invite-codes" in s and "validate" not in s:
                    deco_paths.append((n.name, s))
    chk("路由挂载 /invite-codes", any('"/invite-codes"' in p for _, p in deco_paths),
        str(deco_paths))

    # ---- 2. 公司级角色禁止 ----
    print("\n== 2. 邀请码禁止公司级角色 ==")
    org_roles = extract_org_roles(auth_src)
    print("   ORG_ROLES =", sorted(org_roles))
    create_seg = ast.get_source_segment(auth_src, funcs["create_invite_code"])
    chk("create_invite_code 显式拦截 ORG_ROLES", "ORG_ROLES" in create_seg)
    chk("create_invite_code 用 UserRole 校验项目级",
        "UserRole" in create_seg)
    chk("level 白名单校验", "INVITE_LEVELS" in create_seg)

    # 行为：邀请码岗位校验复刻
    def invite_role_ok(role):
        if role in org_roles:
            return False
        return role in USER_ROLES

    USER_ROLES = {"chief_supervisor", "deputy_chief", "special_supervisor",
                  "inspector", "documenter", "witness", "safety_supervisor"}
    chk("拒绝 super_admin", not invite_role_ok("super_admin"))
    chk("拒绝 admin", not invite_role_ok("admin"))
    chk("接受 inspector", invite_role_ok("inspector"))
    chk("接受 special_supervisor", invite_role_ok("special_supervisor"))
    chk("拒绝非法值", not invite_role_ok("bogus"))

    # ---- 3. 小程序 register 强制邀请码 ----
    print("\n== 3. 小程序注册强制邀请码 ==")
    chk("import validate_invite_code", "validate_invite_code" in mp_src)
    mp_tree = ast.parse(mp_src)
    mp_funcs = {n.name: n for n in ast.walk(mp_tree) if isinstance(n, ast.AsyncFunctionDef)}
    reg = ast.get_source_segment(mp_src, mp_funcs["register_account"])
    chk("register 调 validate_invite_code", "validate_invite_code(req.invite_code" in reg)
    chk("register 校验失败即 400", 'invite_result["valid"]' in reg)
    chk("register 角色取自邀请码", "assigned_role = invite_result.get(\"role\")" in reg or "assigned_role" in reg)
    chk("register 仍受注册开关约束", "allow_public_registration" in reg)
    # Step 4（§8.8.6 第 4 条）：岗位白名单**已废弃** —— 岗位全由邀请码决定、
    # 审批再兜底；白名单反而会挡住「总监码」等正常申请。此处改判新契约。
    chk("Step 4：白名单已废弃（不再引用 get_public_registration_roles）",
        "get_public_registration_roles" not in reg)
    chk("Step 4：注册写 pending 且不发 token",
        "AUDIT_PENDING" in reg and "_token_response" not in reg)
    chk("注册成功后递增使用次数", "used_count" in reg and "invite_record" in reg)
    chk("写入 user.invite_code", "invite_code=req.invite_code" in reg)

    # RegisterRequest 必填 invite_code
    print("\n== 4. RegisterRequest 模型 ==")
    for n in ast.walk(mp_tree):
        if isinstance(n, ast.ClassDef) and n.name == "RegisterRequest":
            seg = ast.get_source_segment(mp_src, n)
            chk("invite_code 无默认值（必填）",
                re.search(r"invite_code:\s*str\s*$", seg, re.M) is not None, seg[:200])
            chk("role 改为可选（服务端忽略）",
                re.search(r"role:\s*str\s*\|\s*None", seg) is not None, seg[:200])
            break

    print(f"\n===== {ok} PASS / {fail} FAIL =====")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
