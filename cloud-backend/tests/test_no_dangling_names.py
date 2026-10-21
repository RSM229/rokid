# -*- coding: utf-8 -*-
"""轻量未定义名字检查（无第三方依赖）。

动机：本机 venv 装不上 pyflakes（网络受限），而本次改动大量**删代码**
（删了 _verify_sms_code / _otp_login / RegisterRequest / get_sms_sender /
VerificationCode / _PHONE_RE 等），最容易留下的就是「函数删了但调用点还在」
—— 这类 NameError 本地不跑就发现不了，部署时才炸。

做法：对每个文件，收集模块级可见名字（导入 + 顶层定义 + 内建 + 局部赋值），
再遍历所有 Name(Load) 与 Attribute 根名字，报出无法解析的。
只查「模块级/函数级都找不到」的名字，容忍度故意放宽（宁可漏报不可误报刷屏），
但对本次删除的那批符号做**定向精确检查**。
"""
from __future__ import annotations

import ast
import builtins
import pathlib
import sys

FILES = [
    "app/routers/auth.py",
    "app/routers/miniprogram_auth.py",
    "app/config.py",
    "app/db.py",
]

# 🔴 本次删掉的符号：任何文件里再出现（非注释）即为残留 bug
REMOVED = [
    "_verify_sms_code", "_otp_login", "RegisterRequest", "SendSmsCodeRequest",
    "get_public_registration_roles", "get_sms_sender", "SMSSender",
    "MockSMSSender", "TencentCloudSMSSender", "VerificationCode",
    "sms_provider", "sms_code_ttl", "sms_tencent_secret_id",
    "allow_public_registration", "public_registration_roles",
]


def module_level_names(tree: ast.Module) -> set[str]:
    """模块级可见名字：导入绑定 + 顶层 def/class/赋值 + __all__ 之类。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    continue
                names.add(a.asname or a.name)
    for node in tree.body:  # 只看顶层定义
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For)):
            # 条件/异常分支里的顶层定义也算（本项目常见 import 放 if 里）
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(sub.name)
                elif isinstance(sub, ast.Import):
                    for a in sub.names:
                        names.add((a.asname or a.name).split(".")[0])
                elif isinstance(sub, ast.ImportFrom):
                    for a in sub.names:
                        names.add(a.asname or a.name)
    return names


def local_names(tree: ast.Module) -> set[str]:
    """所有函数/推导式内部出现的绑定名字（参数、赋值、import、with、except…）。

    粗糙但足够：目的是避免把函数内的局部变量误报成未定义。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 🔴 收集**嵌套函数名本身**：本项目大量用「工厂函数内定义并返回内层
            #    函数」（require_permission→permission_checker）、以及函数内 def 的
            #    辅助闭包（import_users 内的 fail、config 校验里的 missing_or_placeholder）。
            #    只收集 args 不收集 name，会把这些全误报成未定义。
            names.add(node.name)
            a = node.args
            for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                names.add(arg.arg)
            if a.vararg:
                names.add(a.vararg.arg)
            if a.kwarg:
                names.add(a.kwarg.arg)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                if al.name != "*":
                    names.add((al.asname or al.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
            names.update(node.names)
        elif isinstance(node, ast.comprehension) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def check_file(path: pathlib.Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    known = (
        module_level_names(tree)
        | local_names(tree)
        | set(dir(builtins))
        | {"__name__", "__file__", "__doc__", "self", "cls"}
    )

    problems: list[str] = []
    removed_set = set(REMOVED)

    # ① 定向检查：已删除符号不得再作为**标识符**出现。
    #    🔴 用 AST 而非文本行扫描 —— docstring(ast.Constant) 与注释天然不在
    #    Name/Attribute/Import 节点里，避免把「文档里提到旧名字」误报成残留
    #    （例如 miniprogram_auth 的 docstring 写了「旧 RegisterRequest 已下线」）。
    for node in ast.walk(tree):
        hit = None
        if isinstance(node, ast.Name) and node.id in removed_set:
            hit = node.id
        elif isinstance(node, ast.Attribute) and node.attr in removed_set:
            hit = node.attr
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name in removed_set:
                    hit = a.name
        elif isinstance(node, ast.Import):
            for a in node.names:
                if (a.asname or a.name).split(".")[0] in removed_set:
                    hit = a.name
        if hit:
            problems.append(f"{path}:{node.lineno}: 🔴 已删除符号仍被引用: {hit}")

    # ② 通用未定义名字（只报 Load 上下文，且排除属性访问的字段名）
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in known:
                problems.append(f"{path}:{node.lineno}: 未定义名字: {node.id}")

    return problems


def main() -> int:
    # 本脚本在 tests/ 下，项目根 = 上一级（cloud-backend/）
    root = pathlib.Path(__file__).resolve().parent.parent
    all_problems: list[str] = []
    for rel in FILES:
        p = root / rel
        if not p.is_file():
            print(f"❌ 文件不存在: {p}")
            return 1
        all_problems.extend(check_file(p))

    print("=" * 70)
    for rel in FILES:
        print(f"  checked: {rel}")
    print("=" * 70)
    if all_problems:
        print(f"发现 {len(all_problems)} 个问题:")
        for p in all_problems:
            print(" ", p)
        return 1
    print("✅ 无未定义名字，无已删除符号残留")
    return 0


if __name__ == "__main__":
    sys.exit(main())
