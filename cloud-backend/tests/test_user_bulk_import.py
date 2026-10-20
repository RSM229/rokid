# -*- coding: utf-8 -*-
"""批量导入人员表解析：本地纯函数自测（不连库、不调模型、不写文件）。

跑法：cd cloud-backend && python tests/test_user_bulk_import.py

本机 venv 缺 fastapi/pydantic，无法直接 import app.routers.auth；
这里从源码里切出「批量导入」的常量与纯函数段单独执行，保证测的是生产源码。
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import os
import re
import sys
from enum import Enum

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "app", "routers", "auth.py")


class HTTPException(Exception):  # 最小桩：只需要 status_code / detail
    def __init__(self, status_code: int = 400, detail: str = ""):
        self.status_code = status_code
        self.detail = detail


class UserRole(str, Enum):  # 与 app.models.user.UserRole 同值
    CHIEF_SUPERVISOR = "chief_supervisor"
    DEPUTY_CHIEF = "deputy_chief"
    SPECIAL_SUPERVISOR = "special_supervisor"
    INSPECTOR = "inspector"
    DOCUMENTER = "documenter"
    WITNESS = "witness"
    SAFETY_SUPERVISOR = "safety_supervisor"


ROLE_LABELS = {
    "chief_supervisor": "总监理工程师", "deputy_chief": "总监代表",
    "special_supervisor": "专业监理工程师", "inspector": "监理员",
    "documenter": "资料员", "witness": "见证员",
    "safety_supervisor": "安全监理员",
    "super_admin": "超级管理员", "admin": "管理员",
}
ORG_ROLES = {"super_admin", "admin"}

source = open(SRC, encoding="utf-8").read()
start = source.index("DEFAULT_IMPORT_PASSWORD")
end = source.index('@router.get("/users/import-template")')
ns: dict = {
    "re": re, "csv": csv, "io": io, "openpyxl": openpyxl,
    "datetime": _dt.datetime, "HTTPException": HTTPException,
    "UserRole": UserRole, "ROLE_LABELS": ROLE_LABELS, "ORG_ROLES": ORG_ROLES,
}
exec(compile(source[start:end], SRC, "exec"), ns)

parse_rows = ns["_parse_import_rows"]
norm_role = ns["_norm_role_text"]
HEADERS = ns["_IMPORT_HEADERS"]

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + detail))
    if not cond:
        FAILED.append(name)


def make_xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_csv(rows: list[list], encoding: str = "utf-8-sig") -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode(encoding)


print("== 1. 模板表头原样导入 ==")
data = make_xlsx([
    HEADERS,
    ["zhangsan", "张三", "监理员", "", "广东监理示范工程", "13800000001", ""],
    ["", "李四", "专业监理工程师", "", "", "13800000002", ""],
])
rows = parse_rows("人员.xlsx", data)
check("解析出 2 行", len(rows) == 2, str(rows))
check("姓名", rows[0]["real_name"] == "张三", str(rows[0]))
check("主岗原文", rows[0]["role"] == "监理员")
check("项目", rows[0]["project"] == "广东监理示范工程")
check("行号从 2 起", rows[0]["line"] == 2 and rows[1]["line"] == 3, str([r["line"] for r in rows]))

print("\n== 2. 表头别名 + 带 * 号 ==")
data = make_xlsx([
    ["姓名*", "账号", "联系电话", "岗位", "项目名称"],
    ["王五", "wangwu", "13800000003", "资料员", "某项目"],
])
rows = parse_rows("a.xlsx", data)
check("能识别别名", rows and rows[0]["username"] == "wangwu" and rows[0]["phone"] == "13800000003", str(rows))

print("\n== 3. Excel 数字手机号还原 ==")
data = make_xlsx([HEADERS, ["zhaoliu", "赵六", "见证员", "", "", 13800000004, ""]])
rows = parse_rows("b.xlsx", data)
check("float 手机号变整数文本", rows[0]["phone"] == "13800000004", str(rows[0]))

print("\n== 4. 空行跳过 ==")
data = make_xlsx([HEADERS, ["", "", "", "", "", "", ""], ["sunqi", "孙七", "监理员", "", "", "", ""], ["", None, None, None, None, None, None]])
rows = parse_rows("c.xlsx", data)
check("只保留 1 行", len(rows) == 1, str(rows))
check("跳过后的行号保留原位置", rows[0]["line"] == 3, str(rows))

print("\n== 5. 缺姓名列直接报错 ==")
data = make_xlsx([["用户名", "手机号"], ["a1", "13800000005"]])
try:
    parse_rows("d.xlsx", data)
    check("应抛 400", False)
except HTTPException as exc:
    check("应抛 400", exc.status_code == 400, str(exc.status_code))
    check("提示缺姓名列", "姓名" in exc.detail, exc.detail)

print("\n== 6. CSV（含 GBK） ==")
csv_rows = [HEADERS, ["zhouba", "周八", "安全监理员", "", "", "13800000006", ""]]
for enc in ("utf-8-sig", "gbk"):
    rows = parse_rows("e.csv", make_csv(csv_rows, enc))
    check(f"{enc} 可解析", len(rows) == 1 and rows[0]["real_name"] == "周八", str(rows))

print("\n== 7. 主岗中文 / 口语 / 英文 key ==")
cases = {
    "监理员": "inspector", "监理": "inspector", "总监理工程师": "chief_supervisor",
    "总监": "chief_supervisor", "总代": "deputy_chief", "专监": "special_supervisor",
    "资料": "documenter", "见证员": "witness", "安全员": "safety_supervisor",
    "inspector": "inspector", "INSPECTOR": "inspector",
    "超级管理员": "super_admin", "": "",
    "不清楚的岗位": "",
}
for text, want in cases.items():
    got = norm_role(text)
    check(f"{text!r} → {want!r}", got == want, got)

print("\n" + ("全部通过" if not FAILED else f"失败 {len(FAILED)} 项：{FAILED}"))
sys.exit(1 if FAILED else 0)
