# -*- coding: utf-8 -*-
"""form_renderer / 全后端 NameError 类缺陷回归守门。

跑法：cd cloud-backend && python -m unittest tests.test_nameerror_regression -v

🔴 为什么要这个文件
------------------
2026-10-27 一天内发现 **3 个同类线上缺陷**，全都是「变量只在同文件另一个
函数里定义，本函数引用它」：

  1. `period_report_service.generate_aggregate_doc` 用 `ex` / `parallel_section`
     → 周报月报必然 NameError（生产日志实证：每分钟刷一条，8 小时死循环，
       `[周期文书] 周报 [] / 月报 []` 长期为空）。已修 bb43c6f。
  2. `form_renderer._fill_units_across_row` 定义 `seen_tcs` 却用 `seen`。
  3. `form_renderer._fill_rizhi_shengtong` 定义 `seen` 却用 `seen_tcs`
     → 旧版监理日志模板（版式B）一渲染就崩。

这类 bug 的共同点是 **py_compile 和 import 都发现不了**（函数体不求值），
只有真正调用到才炸 —— 所以必须靠静态扫描 + 调用级测试双重守门。
本文件第 3 节把扫描器固化成测试，覆盖全部 app/**/*.py。
"""
from __future__ import annotations

import builtins
import symtable
import unittest
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND / "app"

BUILTIN_NAMES = set(dir(builtins))
# 模块隐式变量：symtable 不列为模块级定义，但运行期一定存在
IGNORE = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__class__", "__dict__",
    # 条件导入 / 平台差异 / 仅注解用
    "TYPE_CHECKING", "win32com", "pythoncom", "pywintypes",
    "fitz", "cv2", "numpy", "PIL",
    # FastAPI / pydantic 运行期注入
    "Depends", "Body", "Query", "Path", "Header", "Form", "File", "UploadFile",
    "registry", "DeclarativeBase",
}


def _module_level_names(top: symtable.SymbolTable) -> set[str]:
    names = set()
    for sym in top.get_symbols():
        if (sym.is_assigned() or sym.is_imported()
                or sym.is_namespace() or sym.is_parameter()):
            names.add(sym.get_name())
    for child in top.get_children():
        names.add(child.get_name())
    return names


def _walk(t, mod_names, out, path):
    if t.get_type() == "function":
        for name in t.get_globals():
            if name in IGNORE or name in BUILTIN_NAMES or name in mod_names:
                continue
            out.append((str(path), t.get_name(), t.get_lineno(), name))
    for child in t.get_children():
        _walk(child, mod_names, out, path)


def scan_undefined_globals(path: Path) -> list[tuple[str, str, int, str]]:
    """返回该文件里「解析到全局层但模块级并不存在」的可疑名字。"""
    try:
        # utf-8-sig：容许 ab9539a 起 ledger.py 带的 UTF-8 BOM（import 容忍，纯读不剥）
        src = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return [(str(path), "<read>", 0, f"READ_FAIL {exc}")]
    try:
        top = symtable.symtable(src, str(path), "exec")
    except SyntaxError as exc:
        return [(str(path), "<parse>", exc.lineno or 0, f"SYNTAX_ERROR {exc.msg}")]
    mod_names = _module_level_names(top)
    out: list[tuple[str, str, int, str]] = []
    for child in top.get_children():
        _walk(child, mod_names, out, path)
    return out


# ===========================================================================
# 1. bug2：_fill_units_across_row（定义 seen_tcs / 使用 seen）
# ===========================================================================
class _FakeTC:
    """最小 docx _tc 替身：只需支持 `is` 身份比较。"""


class _FakeCell:
    def __init__(self, text: str):
        self._tc = _FakeTC()
        self.text = text
        self.paragraphs: list = []
        self.filled: dict = {}

    def add_paragraph(self, *a, **kw):  # noqa: ANN001
        return None


class FillUnitsAcrossRowTests(unittest.TestCase):
    def test_does_not_raise_nameerror(self):
        """🔴 改前：函数定义 seen_tcs 却引用 seen → NameError: name 'seen' is not defined。"""
        from app.services import form_renderer as fr

        filled: list[tuple] = []
        orig = fr._fill_date_cell
        fr._fill_date_cell = lambda cell, units: filled.append((cell, dict(units)))
        try:
            cells = [_FakeCell("2026年"), _FakeCell("10月"), _FakeCell("27日")]
            # 两个 cell 共享同一个 _tc，验证去重按对象身份生效（注释要求存 _tc 引用而非 id）
            cells[1]._tc = cells[0]._tc
            try:
                fr._fill_units_across_row(cells, date(2026, 10, 27))
            except NameError as exc:  # pragma: no cover - 回归时才触发
                self.fail(f"_fill_units_across_row 仍抛 NameError：{exc}")
        finally:
            fr._fill_date_cell = orig

        # 去重生效：共享 _tc 的第 2 格被跳过，只填 2 格
        self.assertEqual(len(filled), 2, f"共享 tc 未被去重，实际填了 {len(filled)} 格")

    def test_none_and_empty_value_returns_early(self):
        from app.services import form_renderer as fr

        fr._fill_units_across_row([_FakeCell("2026年")], None)   # 不应抛
        fr._fill_units_across_row([_FakeCell("2026年")], "")     # 不应抛


# ===========================================================================
# 2. bug3：_fill_rizhi_shengtong（定义 seen / 使用 seen_tcs）—— 用真实模板
# ===========================================================================
class FillRizhiShengtongTests(unittest.TestCase):
    """旧版监理日志模板（15 列版式B）渲染回归。

    form_registry 里 gd_b_220_rizhi 注册的是新版式A模板，但注释明确写着
    「回退旧模板无需改代码」——一旦回退到 21监理日志.docx 就会走版式B。
    改前该路径 100% 崩：NameError: name 'seen_tcs' is not defined（line 1116）。
    """

    DATA = {
        "project_name": "回归项目", "doc_number": "GD-B-220-T",
        "date": "2026-10-26", "weekday": "日",
        "weather_morning": "晴", "weather_afternoon": "多云", "weather_night": "阴",
        "temperature_high": "30", "temperature_low": "22",
        "construction_progress": "三层主体施工", "material_section": "无材料进场",
        "supervision_section": "巡视3号楼", "acceptance_section": "无验收",
        "other_work_section": "无", "problems_section": "无安全隐患",
        "parallel_section": "本周无平行检查记录。",
        "supervisor": "张三", "supervisor_level": "监理员",
    }

    def _template(self) -> Path | None:
        from app.config import settings

        for root in (settings.knowledge_base_path / "省统表word版",
                     BACKEND / "知识库" / "省统表word版",
                     BACKEND.parent / "知识库" / "省统表word版"):
            p = root / "21监理日志.docx"
            if p.exists():
                return p
        return None

    def test_shengtong_layout_renders_without_nameerror(self):
        tpl = self._template()
        if tpl is None:
            self.skipTest("未找到旧版模板 21监理日志.docx（版式B），跳过")
        from docx import Document

        from app.services import form_renderer as fr

        doc = Document(str(tpl))
        table = fr._table0(doc)
        trs = table._tbl.findall(fr.qn("w:tr"))
        r0 = "".join(t.text or "" for t in trs[0].iter(fr.qn("w:t")))
        self.assertNotIn("工程名称", r0, "该模板不是版式B，测试前提不成立")

        try:
            fr._fill_rizhi_shengtong(doc, self.DATA)
        except NameError as exc:  # pragma: no cover - 回归时才触发
            self.fail(f"_fill_rizhi_shengtong 仍抛 NameError：{exc}")

        # 年/月/日 真的被填进去了（不是空跑）
        row0_text = " ".join(c.text for c in table.rows[0].cells)
        for token in ("2026", "10", "26"):
            self.assertIn(token, row0_text, f"日期 {token} 没填进版式B表格")
        # 天气三时段
        for w in ("晴", "多云", "阴"):
            self.assertIn(w, row0_text, f"天气 {w} 没填进版式B表格")


# ===========================================================================
# 3. 全后端静态守门：任何 app/**/*.py 都不得引用未定义的全局名
# ===========================================================================
class NoUndefinedGlobalNamesTests(unittest.TestCase):
    """🔴 这条测试本可以早就抓到 `ex` NameError（生产刷了 8 小时死循环才被发现）。

    原理：symtable 取每个函数作用域解析到全局层的名字，比对
    「模块级真正定义的名字 ∪ 内置名」，差集即可疑项。
    比 py_compile 强：py_compile 只查语法，函数体不求值，抓不到这类错。
    """

    def test_no_undefined_global_names_in_app(self):
        files = sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in str(p))
        self.assertGreater(len(files), 50, "扫描文件数异常，检查路径")

        hits: list[str] = []
        for f in files:
            for path, fn, line, name in scan_undefined_globals(f):
                rel = Path(path).relative_to(BACKEND)
                hits.append(f"{rel}:{line} {fn}() → {name}")

        self.assertEqual(
            hits, [],
            "发现引用了未定义全局名（运行到即 NameError，py_compile 查不出来）：\n  "
            + "\n  ".join(hits),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
