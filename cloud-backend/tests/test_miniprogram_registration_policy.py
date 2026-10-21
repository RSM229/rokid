# -*- coding: utf-8 -*-
"""注册准入策略：微信授权 + **必填邀请码**，短信验证码整体下线。

跑法：cd cloud-backend && python tests/test_miniprogram_registration_policy.py

原版本（2026-10-20 前）用 unittest.mock patch settings 测「公开注册开关 + 角色白名单」，
但 2026-10-21 注册准入改为邀请码制后，被测对象 register_account / RegisterRequest /
allow_public_registration / public_registration_roles 已整体删除，旧测试全部失效；
且它 import fastapi —— 本机 venv 缺重依赖根本跑不起来。

改用 AST 对**生产源码**做设计不变量断言（不执行、不连库、零第三方依赖），锁住：
  1. WechatRegisterRequest.invite_code 是**必填**（无默认值）——类型层面杜绝漏传；
  2. wechat_register 里「邀请码校验」在「建号 db.add(User)」之前——准入闸门在前，
     码无效就不该留下半成品数据；「次数消耗」在建号之后——任一步失败整体回滚；
  3. wechat_register / auth.py 内不再有短信符号（sms_code / _verify_sms_code /
     _otp_login / get_sms_sender / VerificationCode）；
  4. 已删除的旧注册通道（register_account / RegisterRequest /
     settings.allow_public_registration / settings.public_registration_roles）
     在整个 app/ 内无残留引用；
  5. 项目码注册即入项目：写 ProjectMember + 排他岗位校验 _assert_exclusive_ok。
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MINI = os.path.join(ROOT, "app", "routers", "miniprogram_auth.py")
AUTH = os.path.join(ROOT, "app", "routers", "auth.py")
APP_DIR = os.path.join(ROOT, "app")

# 🔴 这些符号本次已从全项目删除，任何 .py 再引用即残留 bug
FORBIDDEN_GLOBAL = [
    "register_account", "get_sms_sender", "SMSSender", "MockSMSSender",
    "TencentCloudSMSSender", "VerificationCode", "_verify_sms_code", "_otp_login",
]
# 这两个是 settings 上的属性（config.py 已删），只查 settings.xxx 属性访问
FORBIDDEN_SETTINGS_ATTR = ["allow_public_registration", "public_registration_roles"]

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


def parse(path: str) -> ast.Module:
    return ast.parse(open(path, encoding="utf-8").read(), filename=path)


def find_class(tree: ast.Module, name: str):
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    return None


def find_func(tree: ast.Module, name: str):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def first_line_of_call(fn, callee_name: str):
    """函数体内第一次调用名为 callee_name 的行号（找不到返回 None）。"""
    best = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if fname == callee_name and (best is None or n.lineno < best):
                best = n.lineno
    return best


def first_line_of_add_user(fn):
    """建号点行号：第一次出现 ``User(...)`` 构造调用。

    🔴 不能只认 ``db.add(User(...))`` 这种直接嵌套写法 —— 本项目是
    ``user = User(...)`` 先构造、再 ``db.add(user)``（中间还夹着
    ProjectMember/Binding 的 add），嵌套匹配会漏掉真正的建号点。
    """
    best = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "User":
            if best is None or n.lineno < best:
                best = n.lineno
    return best


def is_required_field(value_node) -> bool:
    """pydantic 字段是否必填。

    规则：``x: str``（无默认）或 ``x: str = Field(min_length=...)``
    （Field **只有 kwargs、没有位置参数**）都算必填；
    ``x: str = "abc"`` 或 ``Field("abc", ...)`` 这种带位置默认值的才算可选。
    """
    if value_node is None:
        return True
    if isinstance(value_node, ast.Call):
        fname = None
        f = value_node.func
        if isinstance(f, ast.Name):
            fname = f.id
        elif isinstance(f, ast.Attribute):
            fname = f.attr
        if fname == "Field":
            return not value_node.args  # 无位置参数 ⇒ 无默认值 ⇒ 必填
    return False


def names_referenced(tree: ast.Module) -> set:
    """收集源码里所有被当作标识符引用的名字（Name + Attribute.attr + import）。"""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                out.add(a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                out.add((a.asname or a.name).split(".")[0])
    return out


def main() -> int:
    mini_src = open(MINI, encoding="utf-8").read()
    mini_tree = parse(MINI)
    auth_tree = parse(AUTH)

    print("用例组 1：WechatRegisterRequest.invite_code 必填")
    cls = find_class(mini_tree, "WechatRegisterRequest")
    check("WechatRegisterRequest 存在", cls is not None)
    if cls is not None:
        fields = {}
        for st in cls.body:
            if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                fields[st.target.id] = st.value  # value=None 表示无默认值＝必填
        check("含 invite_code 字段", "invite_code" in fields, str(list(fields)))
        check("🔴 invite_code 为必填（无默认值）",
              "invite_code" in fields and is_required_field(fields.get("invite_code")),
              f"invite_code 默认值={ast.dump(fields['invite_code'])[:120] if fields.get('invite_code') is not None else None}")
        check("含 bind_token 字段", "bind_token" in fields)
        check("🔴 不含 sms_code 字段", "sms_code" not in fields, str(list(fields)))

    print("用例组 2：旧短信注册通道已删除")
    check("miniprogram_auth 无 register_account", find_func(mini_tree, "register_account") is None)
    check("miniprogram_auth 无 RegisterRequest 类", find_class(mini_tree, "RegisterRequest") is None)
    check("auth 无 register 函数", find_func(auth_tree, "register") is None)
    check("auth 无 send_sms_code 函数", find_func(auth_tree, "send_sms_code") is None)
    check("auth 无 _otp_login", find_func(auth_tree, "_otp_login") is None)
    check("auth 无 _verify_sms_code", find_func(auth_tree, "_verify_sms_code") is None)
    check("auth 无 RegisterRequest 类", find_class(auth_tree, "RegisterRequest") is None)
    mini_names = names_referenced(mini_tree)
    auth_names = names_referenced(auth_tree)
    for sym in ["sms_code", "_verify_sms_code", "_otp_login", "get_sms_sender", "VerificationCode"]:
        check(f"auth 无 {sym} 引用", sym not in auth_names)
    for sym in ["sms_code", "_verify_sms_code", "get_sms_sender", "VerificationCode", "RegisterRequest"]:
        check(f"miniprogram_auth 无 {sym} 引用", sym not in mini_names)

    print("用例组 3：wechat_register 邀请码准入在建号之前")
    fn = find_func(mini_tree, "wechat_register")
    check("wechat_register 存在", fn is not None)
    if fn is not None:
        vline = first_line_of_call(fn, "validate_invite_code")
        addline = first_line_of_add_user(fn)
        check("调用了 validate_invite_code", vline is not None)
        check("有 db.add(User(...)) 建号点", addline is not None)
        check("🔴 邀请码校验在建号之前（码无效不留半成品）",
              vline is not None and addline is not None and vline < addline,
              f"validate={vline} add_user={addline}")
        consume = None
        for n in ast.walk(fn):
            if isinstance(n, ast.Attribute) and n.attr == "used_count":
                consume = n.lineno
                break
        check("🔴 次数消耗在建号之后（任一步失败整体回滚）",
              consume is not None and addline is not None and consume > addline,
              f"add_user={addline} consume={consume}")
        fn_src = ast.get_source_segment(mini_src, fn) or ""
        check("🔴 项目码时写 ProjectMember（注册即入项目）", "ProjectMember(" in fn_src)
        check("用码上 org_id 作企业归属", "invite_org_id" in fn_src)
        check("排他岗位校验 _assert_exclusive_ok 被调用",
              first_line_of_call(fn, "_assert_exclusive_ok") is not None)
        check("org_role 仍写死 None（注册通道绝不给公司级角色）",
              "org_role=None" in fn_src)

    print("用例组 4：全 app/ 无已删符号残留引用")
    residual = []
    for dirpath, _dirs, files in os.walk(APP_DIR):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dirpath, f)
            try:
                t = parse(p)
            except SyntaxError as e:
                residual.append(f"{p}: 语法错误 {e}")
                continue
            nm = names_referenced(t)
            for sym in FORBIDDEN_GLOBAL:
                if sym in nm:
                    residual.append(f"{os.path.relpath(p, ROOT)}: 引用已删符号 {sym}")
            for n in ast.walk(t):
                if (isinstance(n, ast.Attribute) and n.attr in FORBIDDEN_SETTINGS_ATTR
                        and isinstance(n.value, ast.Name) and n.value.id == "settings"):
                    residual.append(f"{os.path.relpath(p, ROOT)}: settings.{n.attr}")
    check("app/ 内无残留引用", not residual, "; ".join(residual[:6]))

    print()
    print(f"结果：{PASSED} 通过 / {FAILED} 失败")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
