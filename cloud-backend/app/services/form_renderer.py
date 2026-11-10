"""省统表 docx 模板填充引擎。

以 知识库/省统表word版/*.docx 与安全监理用表为模板，把工作记录/AI 汇总后的
结构化内容按「标签单元格 → 值单元格」规则回填，得到与标准版式一致的
docx 文件；在有 Word(win32com)/LibreOffice 的环境中可再转 PDF。

模板结构约定（本项目模板均为标准表格表单）：
  - 头部段落：一行“工程项目名称：\t编号：”（可空值，按 \t 分列回填）
  - 主表：若干行；每行含“标签单元格”和待填的空白/占位单元格
  - 内容型单元格：整行合并或大段空行，需要在开头“标签：”后追加正文
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph

from app.config import settings

logger = logging.getLogger(__name__)

# 头部段落中 工程名称/编号 的分隔与替换目标（出现在段落的文字中）
_HEADER_PROJECT = "工程项目名称："
_HEADER_NO = "编号："
_HEADER_ALT_PROJECT = "工程名称："


class TemplateNotFoundError(FileNotFoundError):
    pass


# ---------------------------------------------------------------------------
# 单元格/段落基础工具
# ---------------------------------------------------------------------------

def _cell_text(cell: _Cell) -> str:
    return cell.text.replace("\t", " ").replace("\n", "").strip()


def _apply_run_font(run: Any, font_name: str, font_size: Any = None) -> None:
    """显式设置 run 字体（ascii/hAnsi/eastAsia + 字号）；eastAsia 需手动设 rFonts。"""
    try:
        run.font.name = font_name
        if font_size is not None:
            run.font.size = font_size
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = rpr.makeelement(qn("w:rFonts"), {})
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), font_name)
    except Exception:  # noqa: BLE001
        pass


def _set_cell_text(cell: _Cell, text: str, font_name: str | None = None, font_size: Any = None) -> None:
    """清空单元格（保留首段）后写入文本；多行文本按段落展开。

    font_name/font_size 传入时对写入的所有 run 显式设置字体（如官方样表的宋体 10pt），
    避免新 run 无 rPr 回退到文档默认字体导致与标签格式不一致。
    """
    lines = [ln for ln in str(text).split("\n")]
    first = cell.paragraphs[0]
    # 删除首段之后的所有段落
    for p in cell.paragraphs[1:]:
        p._element.getparent().remove(p._element)
    # 清空首段所有 run
    for r in list(first.runs):
        r._element.getparent().remove(r._element)
    if lines:
        first.text = lines[0]
        for ln in lines[1:]:
            p = cell.add_paragraph(ln)
            _copy_font(first, p)
    # 若源内容本身是空行（如空字符串），保留一个可见占位以避免空段影响布局
    if not lines or lines == [""]:
        first.text = ""
    if font_name:
        for para in cell.paragraphs:
            for run in para.runs:
                _apply_run_font(run, font_name, font_size)


def _copy_font(src: Paragraph, dst: Paragraph) -> None:
    """把 src 段落的字体拷贝到 dst（新建段落时保持统一字号）。"""
    try:
        if src.runs:
            src_font = src.runs[0].font
            for run in dst.runs:
                try:
                    if src_font.size:
                        run.font.size = src_font.size
                    if src_font.name:
                        run.font.name = src_font.name
                except Exception:
                    pass
    except Exception:
        pass


def _find_label_cell(table: Table, label: str) -> _Cell | None:
    """在表格中按标签文字找单元格（模糊匹配：忽略空白）。

    先精确匹配（归一化后相等），再退化为“以标签开头”。
    """
    norm = re.sub(r"\s+", "", label)
    for row in table.rows:
        seen: set[int] = set()
        for cell in row.cells:
            key = id(cell._tc)
            if key in seen:
                continue
            seen.add(key)
            text = _cell_text(cell)
            compact = re.sub(r"\s+", "", text)
            if compact == norm:
                return cell
    for row in table.rows:
        seen = set()
        for cell in row.cells:
            key = id(cell._tc)
            if key in seen:
                continue
            seen.add(key)
            text = _cell_text(cell)
            compact = re.sub(r"\s+", "", text)
            if compact.startswith(norm):
                return cell
    return None


def _same_cell(a: _Cell, b: _Cell) -> bool:
    return id(a._tc) == id(b._tc)


def _right_neighbor(table: Table, cell: _Cell) -> _Cell | None:
    """向右找第一个不同 tc 的兄弟单元格（合并单元格的后续网格列）。"""
    for row in table.rows:
        cells = row.cells
        for i, c in enumerate(cells):
            if _same_cell(c, cell):
                # 同一行中该 tc 之后首个不同 tc 的 cell
                for j in range(i + 1, len(cells)):
                    if not _same_cell(cells[j], cell):
                        return cells[j]
                return None
    return None


def _append_after_label(cell: _Cell, label: str, text: str) -> bool:
    """在含 label 的段落后插入正文（每行一段）。

    内容大单元格通常是「标签：…\n空行…\n签名行」结构，正文必须紧跟标签段
    插入，而不是追加到单元格末尾（末尾一般是签名/日期）。
    """
    text = str(text).strip()
    if not text:
        return False
    paras = cell.paragraphs
    anchor_idx = None
    for idx, p in enumerate(paras):
        if label in p.text:
            anchor_idx = idx
            break
    if anchor_idx is not None:
        # 在锚点段之后插入：等价于向“锚点下一段”连续 insert_paragraph_before
        anchor = paras[anchor_idx]
        insert_before = paras[anchor_idx + 1] if anchor_idx + 1 < len(paras) else None
        for ln in text.split("\n"):
            if not ln.strip():
                continue
            if insert_before is not None:
                new_p = insert_before.insert_paragraph_before(ln)
            else:
                new_p = cell.add_paragraph(ln)
                _copy_font(anchor, new_p)
        return True
    # 标签段没找到：退化为在末尾追加
    for ln in text.split("\n"):
        if not ln.strip():
            continue
        p = cell.add_paragraph(ln)
        if paras:
            _copy_font(paras[0], p)
    return False


def _fill_units_across_row(row_cells: list, value: Any) -> None:
    """“年/月/日/时/分”分散在不同网格列时，逐个单元格回填。"""
    if value is None or value == "":
        return
    import datetime as dt
    value = _coerce_datetime(value)
    if isinstance(value, dt.datetime):
        units = {"年": value.year, "月": value.month, "日": value.day, "时": value.hour, "分": value.minute}
    elif isinstance(value, dt.date):
        units = {"年": value.year, "月": value.month, "日": value.day}
    else:
        return
    # 保留 XML 节点引用；只存 id 会因代理对象回收后 id 复用而误跳过后续签章行。
    seen_tcs: list = []
    for cell in row_cells:
        if any(cell._tc is tc for tc in seen_tcs):
            continue
        seen_tcs.append(cell._tc)
        txt = cell.text
        for unit in ("年", "月", "日", "时", "分"):
            if unit in txt and unit in units:
                _fill_date_cell(cell, {unit: units[unit]})
                units.pop(unit, None)
                break


# ---------------------------------------------------------------------------
# 头部段落（工程名称/编号）
# ---------------------------------------------------------------------------

def _fill_header(doc: DocxDocument, project_name: str, doc_number: str) -> None:
    """按 \t 或固定标签填充头部段落中的项目名称与编号。"""
    pat = re.compile(r"(工程项目名称：|工程名称：)\s*([^\t]*?)(\t|$)(编号：)\s*([^\t]*)")
    pat2 = re.compile(r"(工程项目名称：|工程名称：)\s*(.*)")

    for para in doc.paragraphs:
        text = para.text
        if "项目名称：" not in text and "工程名称：" not in text:
            continue
        if "\t" in text or "编号：" in text:
            m = pat.search(text)
            if m:
                # 重建段落：保留文字标签、把值替换为真实内容
                _rewrite_header_paragraph(para, project_name, doc_number)
                return
        m2 = pat2.match(text)
        if m2 and (project_name or doc_number):
            prefix = "工程项目名称：" if "工程项目名称：" in text else "工程名称："
            _rewrite_header_paragraph(para, project_name, doc_number, prefix=prefix)
            return


def _rewrite_header_paragraph(para: Paragraph, project_name: str, doc_number: str,
                              prefix: str = "") -> None:
    """按“工程项目名称：X  编号：Y”重写一个头部段落。"""
    text = para.text
    # 定位前缀位置
    if "编号：" in text:
        # 形如： 工程项目名称：\t编号：
        left, right = text.split("编号：", 1)
        left_prefix = "工程项目名称：" if "工程项目名称：" in left else ("工程名称：" if "工程名称：" in left else "")
        new_text = f"{left_prefix}{project_name or ''}\t编号：{doc_number or ''}"
    else:
        new_text = f"{prefix or '工程项目名称：'}{project_name or ''}"
    # 重建段落 runs
    for r in list(para.runs):
        r._element.getparent().remove(r._element)
    para.add_run(new_text)


# ---------------------------------------------------------------------------
# 时间占位（年月日时分 回填）
# ---------------------------------------------------------------------------

def _coerce_datetime(value: Any):
    """把 ISO/常见字符串时间转成 datetime/date。"""
    import datetime as dt
    if value is None or isinstance(value, (dt.datetime, dt.date)):
        return value
    if isinstance(value, str):
        s = value.strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
            try:
                parsed = dt.datetime.strptime(s, fmt)
                return parsed.date() if "H" not in fmt else parsed
            except ValueError:
                continue
    return value


def _fill_paragraph_unit(para: Paragraph, unit: str, num: Any) -> bool:
    """把 num 插入到段落中单位字符前（如 ‘年’ → ‘2026年’）。"""
    if num is None or num == "":
        return False
    for run in list(para.runs):
        t = run.text
        if unit in t:
            run.text = t.replace(unit, f"{num}{unit}", 1)
            return True
    # 无 run 匹配（可能是空 run 或已拆开）——整段替换
    full = para.text
    if unit in full:
        for r in list(para.runs):
            r._element.getparent().remove(r._element)
        para.add_run(full.replace(unit, f"{num}{unit}", 1))
        return True
    return False


def _set_cell_text_keep(cell: _Cell, text: str) -> None:
    """在单元格末尾追加一行文字（不清空原有内容，适合合并大格中的子标签）。"""
    text = str(text).strip()
    if not text:
        return
    p = cell.add_paragraph(text)
    if cell.paragraphs:
        _copy_font(cell.paragraphs[0], p)


def _fill_date_cell(cell: _Cell, value: Any) -> None:
    """把时间值写入“年 月 日 时 分”式占位单元格。

    value 支持 datetime/date、ISO 字符串、int/float。
    单元格若含年月日占位则原位回填；否则直接写文本。
    """
    if value is None or value == "":
        return
    import datetime as dt
    # 传入 {单位: 数值}（_fill_units_across_row 用）时直接按单位回填
    if isinstance(value, dict):
        units = {k: v for k, v in value.items() if k in ("年", "月", "日", "时", "分")}
        if not units:
            return
        for p in cell.paragraphs:
            for run in list(p.runs):
                run_text = run.text
                if not run_text:
                    continue
                new_text = run_text
                for unit in ("年", "月", "日", "时", "分"):
                    if unit in units and unit in new_text:
                        new_text = new_text.replace(unit, f"{units[unit]}{unit}", 1)
                        units.pop(unit, None)
                if new_text != run_text:
                    run.text = new_text
        return
    value = _coerce_datetime(value)
    text = cell.text
    has_units = any(u in text for u in ("年", "月", "日"))
    if not has_units:
        _set_cell_text(cell, str(value)[:19] if not isinstance(value, str) else value)
        return
    if isinstance(value, dt.datetime):
        units = {"年": value.year, "月": value.month, "日": value.day, "时": value.hour, "分": value.minute}
    elif isinstance(value, dt.date):
        units = {"年": value.year, "月": value.month, "日": value.day}
    else:
        _set_cell_text(cell, str(value))
        return

    for p in cell.paragraphs:
        for run in list(p.runs):
            run_text = run.text
            if not run_text:
                continue
            new_text = run_text
            for unit in ("年", "月", "日", "时", "分"):
                if unit in units and unit in new_text:
                    new_text = new_text.replace(unit, f"{units[unit]}{unit}", 1)
                    units.pop(unit, None)
            if new_text != run_text:
                run.text = new_text


def _is_date_placeholder_para(para: Paragraph) -> bool:
    """段落是否为「年 月 日」占位段（去掉数字/年月日时分/空白后无残留正文）。"""
    t = para.text
    if not ("年" in t and "月" in t and "日" in t):
        return False
    return re.sub(r"[\d年月日时分\s\u00a0\t:：.·\-]", "", t) == ""


def _fill_date_runs(para: Paragraph, value: Any) -> None:
    """只在指定段落的 run 内按 年/月/日 占位回填（防误伤含「X日内」等正文）。"""
    import datetime as dt
    value = _coerce_datetime(value)
    if isinstance(value, dt.datetime):
        units = {"年": value.year, "月": value.month, "日": value.day, "时": value.hour, "分": value.minute}
    elif isinstance(value, dt.date):
        units = {"年": value.year, "月": value.month, "日": value.day}
    else:
        return
    for run in list(para.runs):
        new_text = run.text
        for unit in ("年", "月", "日", "时", "分"):
            if unit in units and unit in new_text:
                new_text = new_text.replace(unit, f"{units[unit]}{unit}", 1)
                units.pop(unit, None)
        if new_text != run.text:
            run.text = new_text
        if not units:
            break


def _append_signature_text(cell: _Cell, extra: str) -> None:
    """在单元格末尾补一行（通常用于签名日期等）。"""
    if extra and extra.strip():
        cell.add_paragraph(extra)


# ---------------------------------------------------------------------------
# 渲染调度
# ---------------------------------------------------------------------------

#: 内容型大单元格的“标签→追加正文”说明由各 fill 函数直接操作单元格坐标，
#: 因为每个模板的表格结构不同。下面的函数按模板差异实现。

def _fill_gdb_common(doc: DocxDocument, data: dict, skip_body: tuple[str, ...] = ()) -> None:
    """巡视 GD-B-214 / 旁站 GD-B-212 / 平行检查 GD-B-213 等公用头部与整表。"""
    project_name = data.get("project_name", "")
    doc_number = data.get("doc_number", "")
    if project_name or doc_number:
        _fill_header(doc, project_name, doc_number)

    table = doc.tables[0]
    # 通用规则：遇到“标签：”→ 填右侧空格；“XXX时间：”→ 填右侧“年月日”占位
    # 先处理需要明确坐标的模板（在 fill_* 内做），这里只做通用行级匹配。
    label_value_rows: list[tuple[str, str]] = [
        # (标签, 目标字段)
        ("巡视的工程部位", "section"),
        ("巡视开始时间", "start_time"),
        ("巡视结束时间", "end_time"),
        ("巡视时间", "time"),
        ("施工人数", "worker_count"),
        ("旁站的关键部位、关键工序", "section"),
        ("旁站开始时间", "start_time"),
        ("旁站结束时间", "end_time"),
        ("检查时间", "time"),
        ("检查部位", "section"),
        ("检查项目", "acceptance_content"),
        ("检查方法", "method"),
        ("检查依据", "basis"),
        ("施工单位", "construction_unit"),
    ]
    for row in table.rows:
        cells = row.cells
        # 找出该行唯一“非空标签单元格”与其右侧目标
        seen = set()
        label_cells = []
        for ci, c in enumerate(cells):
            key = id(c._tc)
            if key in seen:
                continue
            seen.add(key)
            txt = _cell_text(c)
            if txt and ci == 0 or (txt and not any(_cell_text(x) == txt for x in cells[:ci])):
                label_cells.append((ci, c, txt))
        for ci, cell, txt in label_cells:
            for label, field in label_value_rows:
                if re.sub(r"\s+", "", txt).startswith(re.sub(r"\s+", "", label)):
                    target = _right_neighbor(table, cell)
                    if target is None:
                        continue
                    val = data.get(field)
                    if val is None:
                        continue
                    # 时间类字段：目标单元格含“年月日”字样 → 日期回填
                    if any(u in _cell_text(target) for u in ("年", "月", "日")) and field.endswith("_time"):
                        _fill_date_cell(target, val)
                    else:
                        _set_cell_text(target, str(val))
                    break
    # 大内容区：按行内文本前缀追加正文（巡视内容/问题、旁站问题、检查记录等）
    _fill_body_areas(table, data, skip=skip_body)


_BODY_LABELS = [
    # (行内包含文本, data 字段, 追加前缀是否保留行首)
    ("巡视内容", "content", False),
    ("巡视发现的问题及处理情况", "problems", False),
    ("旁站发现的问题及处理情况", "problems", False),
    ("检查记录", "record", False),
    ("检查结论", "conclusion", False),
    ("处理记录", "handling", False),
]


def _fill_body_areas(table: Table, data: dict, skip: tuple[str, ...] = ()) -> None:
    """在内容型大单元格的“标签：”之后插入正文行。skip 里的标签跳过（保持预印文本不变）。"""
    for row in table.rows:
        seen: set[int] = set()
        for cell in row.cells:
            key = id(cell._tc)
            if key in seen:
                continue
            seen.add(key)
            txt = _cell_text(cell)
            compact = re.sub(r"\s+", "", txt)
            for label, field, _keep in _BODY_LABELS:
                if any(label == s for s in skip):
                    continue
                if compact.startswith(label) or (label in compact and len(compact) < 200):
                    val = data.get(field)
                    if val:
                        # 内容大单元格的标签正文一般紧跟行首标签
                        _append_after_label(cell, label, str(val))
                    break


# ---------------------------------------------------------------------------
# 各模板专用填充器
# ---------------------------------------------------------------------------

def _table0(doc: DocxDocument) -> Table:
    return doc.tables[0]


def _append_missing_fields(doc, data, fields, heading: str = "记录字段补充"):
    # 模板原本无独立栏位时附字段补充，保证数据不被吞掉。
    values = [(label, data.get(key)) for key, label in fields if data.get(key) not in (None, "")]
    if values:
        doc.add_paragraph(heading)
        for label, value in values:
            doc.add_paragraph(f"{label}：{value}")


def fill_xunshi(doc: DocxDocument, data: dict) -> None:
    """GD-B-214 巡视记录（双版式自适应，2026-10-15 修复生产模板错位）。

    版式 A（官方样表/真样例，5 行主表，r0 含「工程名称」）：
      R0 工程名称 | 值 | 编号 | 值
      R1 巡视的工程部位 | 值 | 施工单位 | 值
      R2 巡视时间 | 值（单行「开始 至 结束」）
      R4 巡视的问题及处理情况 + 巡视监理人员（签字）
    版式 B（生产模板 16巡视记录.docx，4 行主表）：
      工程名称/编号是表格外段落「工程项目名称：\\t编号：」→ 用 _fill_header
      R0 巡视的工程部位 | 值 | 施工单位 | 值
      R1 巡视时间 | 值（占位「年月日时至年月日时」）
      R3 巡视发现的问题及处理情况 + 巡视监理人员（签名）+ 年月日
    """
    table = _table0(doc)
    trs = table._tbl.findall(qn("w:tr"))

    def tcs_of(idx: int):
        return trs[idx].findall(qn("w:tc"))

    def put(idx: int, cell_idx: int, value) -> None:
        if value in (None, ""):
            return
        try:
            # 官方样表值格字体=宋体 10pt（五号），显式设置避免回退文档默认字体
            from docx.shared import Pt
            _set_cell_text(_Cell(tcs_of(idx)[cell_idx], table), str(value),
                           font_name="宋体", font_size=Pt(10))
        except IndexError:
            pass

    # 巡视时间：单行「开始 至 结束」（与官方样表 2026-10-09 08:30至2026-10-09 17:30 一致）
    start, end = data.get("start_time"), data.get("end_time")
    if start or end:
        def shown(value):
            parsed = _coerce_datetime(value)
            return parsed.strftime("%Y-%m-%d %H:%M") if hasattr(parsed, "strftime") else str(value)
        span = "至".join(x for x in (shown(start) if start else "", shown(end) if end else "") if x)
    else:
        span = ""
    time_value = span or data.get("time")

    # 版式探测：r0 首格含「工程名称」→ 版式 A，否则版式 B
    r0_text = "".join(t.text or "" for t in trs[0].iter(qn("w:t")))
    if "工程名称" in r0_text:
        put(0, 1, data.get("project_name"))
        put(0, 3, data.get("doc_number"))
        put(1, 1, data.get("section"))
        put(1, 3, data.get("construction_unit"))
        put(2, 1, time_value)
        tail_row = 4
    else:
        if data.get("project_name") or data.get("doc_number"):
            _fill_header(doc, data.get("project_name") or "", data.get("doc_number") or "")
        put(0, 1, data.get("section"))
        put(0, 3, data.get("construction_unit"))
        put(1, 1, time_value)
        tail_row = 3

    # 问题及处理 + 签名：两版式标签措辞不同（「巡视的问题…」vs「巡视发现的问题…」、
    # 「（签字）」vs「（签名）」），用公共锚点 + 多 marker 兼容
    problems = data.get("problems") or data.get("conclusion")
    try:
        tail = _Cell(tcs_of(tail_row)[0], table)
        if problems:
            # 省统表模板写作「巡视发现的问题及处理情况」，房建样例使用
            # 「巡视的问题及处理情况」。从最具体到最宽松依次匹配，避免
            # 正文被插入错误栏目或静默丢失。
            for marker in (
                "发现的问题及处理情况：",
                "发现的问题及处理情况",
                "问题及处理情况：",
                "问题及处理情况",
            ):
                if _insert_after_text(tail.paragraphs, marker, str(problems)):
                    break
        name = str(data.get("inspector_signature") or "")
        if name:
            for marker in ("（签字）：", "（签名）：", "（签名）", "（签字）"):
                if _fill_signature_inline(tail, marker, name):
                    break
    except IndexError:
        pass


def fill_pangzhan(doc: DocxDocument, data: dict) -> None:
    """GD-B-212 旁站记录（房建新版式）。

    主表：工程名称/编号 → 旁站的关键部位、关键工序/施工单位 → 起止时间 →
    「旁站的关键部位、关键工序施工情况」正文 → 「监理情况」检查项表（嵌套表）→
    「旁站的问题及处理情况」+ 旁站监理人员（签字）。
    """
    table = _table0(doc)
    # 工程名称/编号在表外段落「工程项目名称：\t编号：」（非表内），走头部填充
    _fill_header(doc, str(data.get("project_name") or ""), str(data.get("doc_number") or ""))
    trs = table._tbl.findall(qn("w:tr"))

    def tcs_of(idx: int):
        return trs[idx].findall(qn("w:tc"))

    def put(idx: int, cell_idx: int, value) -> None:
        if value in (None, ""):
            return
        try:
            _set_cell_text(_Cell(tcs_of(idx)[cell_idx], table), str(value))
        except IndexError:
            pass

    def shown(value) -> str:
        parsed = _coerce_datetime(value)
        return parsed.strftime("%Y-%m-%d %H:%M") if hasattr(parsed, "strftime") else str(value)

    part = str(data.get("station_part") or data.get("section") or "").strip()
    process = str(data.get("station_process") or "").strip()

    # 14旁站记录.docx 实为 4 行表（不是 6 行含工程名称/检查项嵌套表的旧版）：
    #   R0 旁站的关键部位、关键工序 | 值 | 施工单位 | 值
    #   R1 旁站开始时间 | 值 | 旁站结束时间 | 值
    #   R2 旁站的关键部位、关键工序施工情况（整行正文）
    #   R3 旁站发现的问题及处理情况 + 旁站监理人员（签名）（整行）
    # 工程名称/编号该模板无独立栏（系统带出、不追问），不再误写到部位/施工单位格。
    put(0, 1, "；".join(x for x in (part, process) if x))
    put(0, 3, data.get("construction_unit"))
    if data.get("start_time"):
        put(1, 1, shown(data["start_time"]))
    if data.get("end_time"):
        put(1, 3, shown(data["end_time"]))

    # 施工情况正文：xlsx 追问到的栏目（天气/人数/机械/材料/到岗/自查）模板无独立栏 →
    # 与「检查内容及检查结果」一起聚合进「施工情况」正文，保证不白问、数据不丢
    body_lines: list[str] = []
    primary = str(
        data.get("content") or data.get("work_content") or data.get("station_content") or ""
    ).strip()
    if primary:
        body_lines.append(primary)
    for label, key in (
        ("天气", "weather"),
        ("各专业作业人数", "worker_count"),
        ("机械设备", "equipment"),
        ("材料品牌、规格、数量", "material_info"),
        ("施工管理人员（质检员/安全员/施工员）到岗", "qa_onsite"),
        ("施工单位自查自检", "self_check"),
    ):
        val = str(data.get(key) or "").strip()
        if val:
            body_lines.append(f"{label}：{val}")
    if body_lines:
        try:
            _append_after_label(
                _Cell(tcs_of(2)[0], table),
                "旁站的关键部位、关键工序施工情况",
                "\n".join(body_lines),
            )
        except IndexError:
            pass

    # 问题及处理 + 旁站监理人员签名（R3 整行，模板标签为「旁站发现的问题及处理情况」）
    try:
        tail = _Cell(tcs_of(3)[0], table)
        problems = str(data.get("problems") or data.get("conclusion") or "").strip()
        if problems:
            _append_after_label(tail, "旁站发现的问题及处理情况", problems)
        sig = data.get("station_inspector_signature") or ""
        if not _fill_signature_inline(tail, "（签名）：", sig):
            _fill_signature_inline(tail, "（签字）：", sig)
    except IndexError:
        pass


def _mark_checkbox(cell: _Cell, target: str) -> bool:
    """把单元格里的「□目标」勾成「☑目标」（用于「经检查□是/□否」类选项）。"""
    target = str(target or "").strip()
    if not target:
        return False
    hit = False
    for para in cell.paragraphs:
        for run in para.runs:
            if f"□{target}" in run.text:
                run.text = run.text.replace(f"□{target}", f"☑{target}")
                hit = True
    return hit


def _insert_after_text(paragraphs: Iterable[Paragraph], anchor: str, value: str) -> bool:
    """把值插到段落中 anchor 文字之后（同段，保留原字体）。"""
    value = str(value or "").strip()
    if not value:
        return False
    for para in paragraphs:
        if anchor not in para.text:
            continue
        for run in para.runs:
            if anchor in run.text:
                run.text = run.text.replace(anchor, f"{anchor}{value}", 1)
                return True
        full = para.text.replace(anchor, f"{anchor}{value}", 1)
        fonts = [(r.font.size, r.font.name) for r in para.runs[:1]]
        for r in list(para.runs):
            r._element.getparent().remove(r._element)
        new_run = para.add_run(full)
        try:
            if fonts and fonts[0][0]:
                new_run.font.size = fonts[0][0]
            if fonts and fonts[0][1]:
                new_run.font.name = fonts[0][1]
        except Exception:  # noqa: BLE001
            pass
        return True
    return False


_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")


def fill_pingxing(doc: DocxDocument, data: dict) -> None:
    """GD-B-213 平行检验记录。

    栏目：工程项目名称/编号 / 检查时间 / 检查部位 / 检查项目 / 检查方法 /
    检查依据 / 检查记录（+检查人员签名）/ 检查结论（经检查□是□否）/
    处理记录（+复核人员签名）。
    """
    table = _table0(doc)
    _fill_header(doc, str(data.get("project_name") or ""), str(data.get("doc_number") or ""))
    trs = table._tbl.findall(qn("w:tr"))

    def cell_of(i: int, j: int) -> _Cell:
        return _Cell(trs[i].findall(qn("w:tc"))[j], table)

    # r0 检查时间 / 检查部位
    try:
        _fill_date_cell(cell_of(0, 1), data.get("check_date") or data.get("date") or data.get("time"))
        _set_cell_text(cell_of(0, 3), str(data.get("section") or ""))
    except IndexError:
        pass
    # r1 检查项目 / 检查方法
    try:
        item = (data.get("acceptance_content") or data.get("work_content")
                or data.get("content") or "")
        _set_cell_text(cell_of(1, 1), str(item))
        _set_cell_text(cell_of(1, 3), str(data.get("method") or ""))
    except IndexError:
        pass
    # r2 检查依据
    if data.get("basis"):
        _append_after_label(cell_of(2, 0), "检查依据", str(data["basis"]))
    # r3 检查记录 + 检查人员（签名）
    # xlsx 追问到的「使用仪器设备」「实测数据明细」模板无独立列 → 并入检查记录正文（不白问）
    rec_cell = cell_of(3, 0)
    rec_parts: list[str] = []
    record = str(data.get("record") or data.get("content") or "").strip()
    if record:
        rec_parts.append(record)
    instrument = str(data.get("instrument") or "").strip()
    if instrument:
        rec_parts.append(f"使用仪器设备：{instrument}")
    measure = str(data.get("measure_data") or "").strip()
    if measure:
        rec_parts.append(f"实测数据：{measure}")
    if rec_parts:
        _append_after_label(rec_cell, "检查记录", "\n".join(rec_parts))
    _fill_signature_inline(rec_cell, "（签名）：", data.get("inspector_signature") or "")
    # r4 检查结论（含 □是/□否 勾选）
    con_cell = cell_of(4, 0)
    result = str(data.get("acceptance_result") or data.get("conclusion") or "").strip()
    if result and result not in ("合格", "不合格", "符合", "不符合", "是", "否"):
        _append_after_label(con_cell, "检查结论", result)
    if result in ("合格", "符合", "是"):
        _mark_checkbox(con_cell, "是")
    elif result in ("不合格", "不符合", "否"):
        _mark_checkbox(con_cell, "否")
    # r5 处理记录 + 复核人员（签名）
    deal_cell = cell_of(5, 0)
    handling = data.get("handling") or data.get("treatment")
    if handling:
        _append_after_label(deal_cell, "处理记录", str(handling))
    _fill_signature_inline(deal_cell, "（签名）：", data.get("reviewer") or "")


def fill_anquan_rizhi(doc: DocxDocument, data: dict) -> None:
    """安全监理日志（对齐《安全监理日志表格》）。

    栏目：工程名称 / 日期（含星期）/ 天气（上午、下午、夜间）/ 施工内容 /
    安全检查情况（包括巡视检查情况）/ 处理措施（限期整改、暂时停工、上报建设单位或有关部门）/
    施工单位隐患整改情况 / 危险性较大的分部分项工程安全状况 / 其他 /
    安全监理员（签名）/ 总监理工程师/总监代表（签名）+ 审阅日期。
    """
    table = _table0(doc)
    trs = table._tbl.findall(qn("w:tr"))

    def cell_of(i: int, j: int) -> _Cell:
        return _Cell(trs[i].findall(qn("w:tc"))[j], table)

    # 工程名称（头部段落）
    name = str(data.get("project_name") or "")
    if name:
        _insert_after_text(doc.paragraphs, "工程名称：", name)

    # r0 日期 / 星期 / 天气（上午、下午、夜间）
    date_val = _coerce_datetime(data.get("date") or data.get("record_date"))
    try:
        date_cell = cell_of(0, 0)
        _fill_date_cell(date_cell, date_val)
        if hasattr(date_val, "weekday"):
            _insert_after_text(date_cell.paragraphs, "星期", _WEEKDAY_CN[date_val.weekday()])
        weather_cell = cell_of(0, 2)
        am = data.get("weather_am") or data.get("weather")
        pm = data.get("weather_pm") or data.get("weather")
        night = data.get("weather_night")
        if am:
            _insert_after_text(weather_cell.paragraphs, "上午", str(am))
        if pm:
            _insert_after_text(weather_cell.paragraphs, "下午", str(pm))
        if night:
            _insert_after_text(weather_cell.paragraphs, "夜间", str(night))
    except IndexError:
        pass

    # r1..r6 六个正文栏目
    sections = (
        (1, "施工内容", ("content", "work_content")),
        (2, "安全检查情况", ("safety_check", "inspection_note", "content")),
        (3, "处理措施", ("treatment", "handling")),
        (4, "施工单位隐患整改情况", ("rectify_status", "rectification")),
        (5, "危险性较大的分部分项工程安全状况", ("hazard_status", "weida_status")),
        (6, "其他", ("other", "remark")),
    )
    for idx, label, keys in sections:
        value = next((data.get(k) for k in keys if data.get(k)), None)
        if value:
            try:
                _append_after_label(cell_of(idx, 0), label, str(value))
            except IndexError:
                pass

    # r7 安全监理员（签名）
    try:
        _set_cell_text(cell_of(7, 1), str(data.get("safety_supervisor") or data.get("supervisor") or ""))
    except IndexError:
        pass
    # r8 总监理工程师/总监代表（签名）+ 审阅日期
    try:
        _set_cell_text(cell_of(8, 1), str(data.get("chief_supervisor") or ""))
        review = _coerce_datetime(data.get("review_date") or data.get("date") or data.get("record_date"))
        _fill_date_cell(cell_of(8, 3), review)
    except IndexError:
        pass


def _replace_text(paragraphs: Iterable[Paragraph], old: str, new: str) -> bool:
    """把首次出现的 old 文本替换为 new（跨 run 时按首 run 字体重建该段）。"""
    if not old or new is None:
        return False
    for para in paragraphs:
        if old not in para.text:
            continue
        if any(old in r.text for r in para.runs):
            for run in para.runs:
                if old in run.text:
                    run.text = run.text.replace(old, str(new), 1)
                    break
            return True
        full = para.text.replace(old, str(new), 1)
        fonts = [(r.font.size, r.font.name) for r in para.runs[:1]]
        for r in list(para.runs):
            r._element.getparent().remove(r._element)
        run = para.add_run(full)
        try:
            if fonts and fonts[0][0]:
                run.font.size = fonts[0][0]
            if fonts and fonts[0][1]:
                run.font.name = fonts[0][1]
        except Exception:  # noqa: BLE001
            pass
        return True
    return False


def fill_yuebao(doc: DocxDocument, data: dict) -> None:
    """GD-B-221 监理月报（段落版式）。

    模板无表格：封面是行内文字占位（（工程项目名称）/（第 期）/年 月 至 年 月/
    总监理工程师（签名）：/（监理单位名称）/（项目监理机构名称、盖项目章）/（ ）年（ ）月（ ）日），
    正文为「一、…六、…」六个节标题。按占位替换 + 节标题后插入正文。
    """
    from docx.oxml import OxmlElement
    from docx.text.paragraph import Paragraph

    paras = doc.paragraphs

    def find(anchor: str):
        for p in paras:
            if anchor in p.text.replace(" ", "").replace("\u3000", ""):
                return p
        return None

    def insert_after(para, text: str) -> None:
        text = str(text or "").strip()
        if para is None or not text:
            return
        new_p = OxmlElement("w:p")
        para._p.addnext(new_p)
        new_para = Paragraph(new_p, para._parent)
        run = new_para.add_run(text)
        try:
            if para.runs:
                if para.runs[0].font.size:
                    run.font.size = para.runs[0].font.size
                if para.runs[0].font.name:
                    run.font.name = para.runs[0].font.name
        except Exception:  # noqa: BLE001
            pass

    # ---- 封面 ----
    name = str(data.get("project_name") or "").strip()
    if name:
        _replace_text(paras, "工程项目名称", name)

    period = str(data.get("report_period") or data.get("period") or "").strip()
    year = month = ""
    m = re.search(r"(\d{4})\D{0,3}(\d{1,2})", period)
    if m:
        year, month = m.group(1), m.group(2).zfill(2)
    if not month and hasattr(_coerce_datetime(data.get("submit_date")), "month"):
        dt = _coerce_datetime(data.get("submit_date"))
        year, month = str(dt.year), str(dt.month)

    issue = str(data.get("issue_no") or data.get("period_no") or "").strip()
    p_issue = find("（第")
    if p_issue is not None and issue:
        _insert_after_text([p_issue], "第", issue)

    p_span = find("至")
    if p_span is not None and year and month:
        full = f"{year} 年 {month} 月    至    {year} 年 {month} 月"
        fonts = [(r.font.size, r.font.name) for r in p_span.runs[:1]]
        for r in list(p_span.runs):
            r._element.getparent().remove(r._element)
        run = p_span.add_run(full)
        try:
            if fonts and fonts[0][0]:
                run.font.size = fonts[0][0]
            if fonts and fonts[0][1]:
                run.font.name = fonts[0][1]
        except Exception:  # noqa: BLE001
            pass

    p_sig = find("总监理工程师（签名）")
    if p_sig is not None:
        _insert_after_text([p_sig], "（签名）：", data.get("chief_supervisor") or data.get("supervisor") or "")

    supervision_org = str(data.get("supervision_org") or data.get("supervision_unit") or "").strip()
    if supervision_org:
        _replace_text(paras, "监理单位名称", supervision_org)
        _replace_text(paras, "项目监理机构名称、盖项目章", f"{supervision_org}（盖项目章）")

    submit = _coerce_datetime(data.get("submit_date") or data.get("date"))
    p_date = find("）年（")
    if p_date is not None and hasattr(submit, "year"):
        full = f"（{submit.year}）年（{submit.month}）月（{submit.day}）日"
        fonts = [(r.font.size, r.font.name) for r in p_date.runs[:1]]
        for r in list(p_date.runs):
            r._element.getparent().remove(r._element)
        run = p_date.add_run(full)
        try:
            if fonts and fonts[0][0]:
                run.font.size = fonts[0][0]
            if fonts and fonts[0][1]:
                run.font.name = fonts[0][1]
        except Exception:  # noqa: BLE001
            pass

    # ---- 六节正文（插在节标题之后）----
    sections: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("一、人员到位及工程质量控制情况",
         ("project_overview", "quality_material", "quality_acceptance", "quality_stats", "quality_issues")),
        ("二、工程进度控制情况", ("progress_plan", "progress_deviation")),
        ("三、工程进度款支付情况", ("cost_payment", "cost_change")),
        ("四、合同管理及其他事项的处理情况", ("contract_mgmt",)),
        ("五、现场安全生产与文明施工管理监理情况", ("safety_stats", "safety_critical")),
        ("六、工程项目协调及其他重大事项", ("work_stats", "next_focus")),
    )
    for title, keys in sections:
        anchor = find(title)
        if anchor is None:
            continue
        lines = [str(data[k]).strip() for k in keys if data.get(k) and str(data[k]).strip()]
        if lines:
            insert_after(anchor, "\n".join(lines))


def fill_cailiao(doc: DocxDocument, data: dict) -> None:
    """GD-B-219 工程材料/构配件/设备管理台账。

    模板是一张 13 列台账表（序号/名称/规格型号/数量/生产厂家或品牌/进场日期/
    使用部位/取样见证人签名/取样日期/检测结果/进场验收结论/验收日期/验收人签名），
    按 items 逐行生成（未给 items 时把当前记录当一行）。
    """
    import copy

    table = _table0(doc)
    _fill_header(doc, str(data.get("project_name") or ""), str(data.get("doc_number") or ""))
    trs = table._tbl.findall(qn("w:tr"))
    if len(trs) < 2:
        return
    items = data.get("items")
    if not isinstance(items, list) or not items:
        items = [data]
    tpl_tr = trs[1]
    for tr in trs[1:]:
        tr.getparent().remove(tr)
    for idx, item in enumerate(items, start=1):
        new_tr = copy.deepcopy(tpl_tr)
        table._tbl.append(new_tr)
        tcs = new_tr.findall(qn("w:tc"))

        def put(i: int, value) -> None:
            if value in (None, ""):
                return
            try:
                _set_cell_text(_Cell(tcs[i], table), str(value))
            except IndexError:
                pass

        put(0, idx)
        put(1, item.get("material_name") or item.get("witness_material_name") or item.get("name"))
        put(2, item.get("material_spec") or item.get("witness_material_spec") or item.get("spec"))
        quantity = item.get("quantity") or item.get("witness_quantity")
        unit = item.get("witness_unit") or item.get("unit") or ""
        put(3, f"{quantity}{unit}" if quantity not in (None, "") else "")
        put(4, item.get("manufacturer") or item.get("brand"))
        try:
            _fill_date_cell(_Cell(tcs[5], table), item.get("date") or item.get("record_date"))
        except IndexError:
            pass
        put(6, item.get("section") or item.get("use_part"))
        put(7, item.get("sampler") or item.get("witness_sampler"))
        try:
            _fill_date_cell(_Cell(tcs[8], table), item.get("sample_date"))
        except IndexError:
            pass
        put(9, item.get("test_result") or item.get("detect_result"))
        put(10, item.get("acceptance_result") or item.get("accept_result") or item.get("witness_accept_result"))
        try:
            _fill_date_cell(_Cell(tcs[11], table), item.get("acceptance_date") or item.get("accept_date"))
        except IndexError:
            pass
        put(12, item.get("acceptor") or item.get("witness_acceptor"))


def _fill_rizhi_shengtong(doc: DocxDocument, data: dict) -> None:
    """GD-B-220 监理日志（省统表 21监理日志.docx 版式，15 列网格）。

    表头为 15 网格列：
      日期(0)|年(1)|月(2)|日(3)|气温(4)|最高(5)|最高值(6-7)|天气(8-9)|上午(10-12)|下午(13)|晚上(14)
      日期(0)|星期(1-3)|气温(4)|最低(5)|最低值(6-7)|天气(8-9)|午/下午/晚(10..14)
    行0/行1 垂直合并了 日期、气温、天气 三个标签格。
    """
    table = _table0(doc)
    _fill_gdb_common(doc, data)

    def grid_cells(ri: int):
        """返回该行 [网格列, tc对象] 序列（去重合并）。"""
        row = table.rows[ri]
        out = []
        col = 0
        seen: set[int] = set()
        for cell in row.cells:
            key = id(cell._tc)
            if key in seen:
                continue
            seen.add(key)
            out.append((col, cell))
            tc = cell._tc
            tcpr = tc.find(qn("w:tcPr"))
            gs = tcpr.find(qn("w:gridSpan")) if tcpr is not None else None
            span = int(gs.get(qn("w:val"))) if gs is not None else 1
            col += span
        return out

    def fill_cell_value(cell: _Cell, label: str, value: Any) -> None:
        """给一个（含标签文字或空）单元格追加值；避免清空共享 tc 里其他标签。"""
        if value in (None, ""):
            return
        # 只改单元格内“标签段”后追加一行值
        _set_cell_text_keep(cell, str(value))

    try:
        date_val = _coerce_datetime(data.get("date") or data.get("target_date"))
        if date_val is not None:
            # 行0：年/月/日 各自独立网格格（同 tc 分段）
            seen_tcs: list = []
            for cell in table.rows[0].cells:
                if any(cell._tc is tc for tc in seen_tcs):
                    continue
                seen_tcs.append(cell._tc)
                for p in cell.paragraphs:
                    ptxt = p.text.strip()
                    for unit, key_name in (("年", "year"), ("月", "month"), ("日", "day")):
                        if ptxt == unit or ptxt.endswith(unit):
                            num = getattr(date_val, key_name, None)
                            _fill_paragraph_unit(p, unit, num)
                            break
        # 星期：行1 网格列1（星期）
        wd = str(data.get("weekday") or "")
        if wd:
            wd_text = wd if "星期" in wd else f"星期{wd}"
            for col, cell in grid_cells(1):
                if col == 1 and "星期" in cell.text:
                    # 清除原占位并写
                    for r in list(cell.paragraphs[0].runs):
                        if "星期" in r.text:
                            r.text = wd_text
                    break
        # 温度/天气：行0 最高值（col6），行1 最低值（col6）
        temp_map = {"最高": data.get("temperature_high"), "最低": data.get("temperature_low")}
        for ri, lab in ((0, "最高"), (1, "最低")):
            cells = grid_cells(ri)
            for ci, (col, cell) in enumerate(cells):
                if lab in cell.text:
                    val = temp_map.get(lab)
                    if val not in (None, ""):
                        # 目标为其后的值格（col+1）
                        for col2, cell2 in cells[ci + 1:]:
                            if col2 == col + 1:
                                _set_cell_text(cell2, str(val))
                                break
        # 天气：行0 的 上午/下午/晚上 标签（col10/13/14）
        weather_map = {"上午": data.get("weather_morning"), "下午": data.get("weather_afternoon"), "晚上": data.get("weather_night")}
        cells0 = grid_cells(0)
        for ci, (col, cell) in enumerate(cells0):
            for lab in ("上午", "下午", "晚上"):
                if lab in cell.text and weather_map.get(lab) not in (None, ""):
                    fill_cell_value(cell, lab, weather_map[lab])
                    break
    except IndexError:
        pass

    # 正文大区：插入到对应标题段之后
    def append_after_label(row_idx: int, label: str, value: Any) -> None:
        if not value:
            return
        try:
            cell = table.rows[row_idx].cells[0]
            _append_after_label(cell, label, str(value))
        except Exception:
            pass

    if data.get("construction_progress"):
        append_after_label(3, "当日施工进展情况", data["construction_progress"])
    work_sections = "\n".join(str(data[k]) for k in ("material_section", "supervision_section", "parallel_section", "acceptance_section", "other_work_section") if data.get(k))
    if work_sections:
        append_after_label(5, "工程材料/构配件/设备进场报审", work_sections)
    if data.get("problems_section"):
        append_after_label(7, "当日存在问题", data["problems_section"])
    other_sections = "\n".join(str(data[k]) for k in ("meeting_section", "document_section", "closure_section", "report_section", "supervision_check", "misc_section") if data.get(k)) or data.get("other_section", "")
    if other_sections:
        append_after_label(8, "其他有关事项", other_sections)
    _append_missing_fields(doc, data, [("review_date", "审阅日期")])


_LIST_PREFIX_RE = re.compile(r"^[-–—•·*○●]+\s*")
_ORDER_PREFIX_RE = re.compile(r"^\d+\s*[\.、)）]\s*")
_TAG_PREFIX_RE = re.compile(r"^[\[【][^\]】]{1,10}[\]】]\s*")
_NOTHING_SET = {"无", "无。", "暂无", "暂无。"}


def _clean_section_text(text: Any) -> str:
    """清洗聚合正文：去 markdown 列表符与 [记录类型]/[状态] 前缀，多行重新编号。

    规则汇总（period_report_service）曾用 ``"  - {item}"`` 拼行、用 ``[待定]`` 标类型，
    这些符号会原样写进 Word，很难看；这里统一兜底清洗，对已入库的旧 content 同样生效。
    多行内容统一编为「1、2、…」，单行内容不带序号。
    """
    if text in (None, ""):
        return ""
    out: list[str] = []
    for raw in str(text).split("\n"):
        ln = raw.strip()
        ln = _LIST_PREFIX_RE.sub("", ln)
        ln = _ORDER_PREFIX_RE.sub("", ln)
        ln = _TAG_PREFIX_RE.sub("", ln)
        ln = re.sub(r"\s{2,}", " ", ln).strip()
        if not ln or ln in _NOTHING_SET:
            continue
        out.append(ln)
    if not out:
        return ""
    if len(out) == 1:
        return out[0]
    return "\n".join(f"{i}、{ln}" for i, ln in enumerate(out, 1))


def _rizhi_supervision_body(data: dict) -> str:
    """当日监理工作情况：按新版式小标题分节（材料 / 旁站巡视平行 / 验收 / 其他）。

    每节内容先各自清洗（各自编号），再拼节，避免整段清洗把「当日无…」也编上序号。
    """
    sup_parallel = "\n".join(
        x
        for x in (
            _clean_section_text(data.get("supervision_section")),
            _clean_section_text(data.get("parallel_section")),
        )
        if x
    )
    blocks = [
        ("1.工程材料/构配件/设备进场报审，见证取样送检情况", _clean_section_text(data.get("material_section"))),
        ("2.旁站、巡视、平行检验情况", sup_parallel),
        ("3.验收、工程实体抽测情况", _clean_section_text(data.get("acceptance_section"))),
        ("4.其他", _clean_section_text(data.get("other_work_section"))),
    ]
    parts: list[str] = [f"{title}\n{body}" for title, body in blocks if body]
    return "\n".join(parts)


def _rizhi_other_lines(data: dict) -> str:
    """其他有关事项：按 会议 / 重要发文 / 闭合记录 / 资料上报 / 上级检查 / 其他 归并，空项不写。"""
    groups = [
        ("会议", data.get("meeting_section")),
        ("重要发文", data.get("document_section")),
        ("闭合记录", data.get("closure_section")),
        ("资料上报", data.get("report_section")),
        # 「上级/质监站检查情况」xlsx 有栏、日志模板无独立列 → 并入其他有关事项（不白问）
        ("上级/质监站检查", data.get("supervision_check")),
        ("其他", data.get("misc_section") or data.get("other_section")),
    ]
    parts: list[str] = []
    for title, body in groups:
        cleaned = _clean_section_text(body)
        if cleaned:
            parts.append(f"{title}：{cleaned}")
    return "\n".join(parts) or "暂无"


def _fill_rizhi_fangjian(doc: DocxDocument, table: Table, trs: list, data: dict) -> None:
    """监理日志「房建新版」版式（21监理日志-房建新.docx，9 列网格）。

    R0 工程项目名称|值|编号|值 ；R1 日期(含星期)|气温|天气（各一格）；
    R2/R3 当日施工进展情况，R4/R5 当日监理工作情况，R6/R7 当日存在问题及处理情况，
    R8 其他有关事项，R9 记录人（签名），R10 总监/总监代表 + 审阅日期。
    """
    from docx.shared import Pt

    val_font, val_size = "宋体", Pt(10.5)

    def tcs_of(idx: int):
        return trs[idx].findall(qn("w:tc"))

    def put(idx: int, cell_idx: int, value: Any, keep: bool = False) -> None:
        if value in (None, ""):
            return
        try:
            cell = _Cell(tcs_of(idx)[cell_idx], table)
        except IndexError:
            return
        if keep:
            _set_cell_text_keep(cell, str(value))
        else:
            _set_cell_text(cell, str(value), font_name=val_font, font_size=val_size)

    date_val = _coerce_datetime(data.get("date") or data.get("target_date"))
    date_text = date_val.strftime("%Y年%m月%d日") if hasattr(date_val, "strftime") else ""

    # R0 工程名称 / 编号
    put(0, 1, data.get("project_name"))
    put(0, 3, data.get("doc_number"))

    # R1 日期（含星期）/ 气温 / 天气
    if date_text:
        weekday = str(data.get("weekday") or "")
        wd = weekday if weekday.startswith("星期") else (f"星期{weekday}" if weekday else "")
        put(1, 1, f"{date_text} {wd}".strip())
    high, low = data.get("temperature_high"), data.get("temperature_low")
    if high or low:
        if high and low:
            put(1, 3, f"{low}℃~{high}℃")
        else:
            put(1, 3, f"{high or low}℃")
    put(1, 5, data.get("weather_morning") or data.get("weather") or data.get("weather_afternoon"))

    # 正文三区
    put(3, 0, _clean_section_text(data.get("construction_progress")))
    put(5, 0, _rizhi_supervision_body(data))
    put(7, 0, _clean_section_text(data.get("problems_section")))

    # R8 其他有关事项（保留行内标签，内容插在其后）
    try:
        _append_after_label(_Cell(tcs_of(8)[0], table), "其他有关事项", _rizhi_other_lines(data))
    except IndexError:
        pass

    # R9 记录人（签名）：项目日志留空走签认流程；个人监理日记回填作者本人，
    # 让日记正文带上可辨识的个人标识（2026-11-10 修复「日记与日志肉眼一样」）。
    recorder = str(data.get("recorder_name") or "").strip()
    if recorder:
        try:
            _fill_signature_inline(_Cell(tcs_of(9)[0], table), "（签名）：", recorder)
        except (IndexError, AttributeError):
            pass

    # R10 审阅日期（签名格不回填姓名，走签认流程）
    if date_text:
        try:
            _set_cell_text(
                _Cell(tcs_of(10)[1], table), f"审阅日期：{date_text}",
                font_name=val_font, font_size=val_size,
            )
        except IndexError:
            pass


def fill_rizhi(doc: DocxDocument, data: dict) -> None:
    """GD-B-220 监理日志（聚合生成）。按模板结构自动选版式。

    版式 A（房建新版 21监理日志-房建新.docx，9 列网格）：R0 含「工程项目名称」——
      工程名称/编号在表内首行，日期与星期同格，气温、天气各一格；
    版式 B（省统表 21监理日志.docx，15 列网格）：表头把 年/月/日 拆格，
      工程名称/编号在表格外段落（见 _fill_rizhi_shengtong）。
    """
    table = _table0(doc)
    trs = table._tbl.findall(qn("w:tr"))
    r0_text = "".join(t.text or "" for t in trs[0].iter(qn("w:t"))) if trs else ""
    # 版式 A 的 R0 首格是「工程项目名称」（注意不是「工程名称」的子串）
    if "工程项目名称" in r0_text or "工程名称" in r0_text:
        _fill_rizhi_fangjian(doc, table, trs, data)
    else:
        _fill_rizhi_shengtong(doc, data)


def _fill_signature_inline(cell: _Cell, marker: str, name: str) -> bool:
    """把姓名插到单元格内 marker（如「（签名）：」）之后，保留原字体。"""
    name = str(name or "").strip()
    if not name:
        return False
    for para in cell.paragraphs:
        if marker not in para.text or name in para.text:
            continue
        for run in para.runs:
            if marker in run.text:
                run.text = run.text.replace(marker, f"{marker}{name} ", 1)
                return True
        # marker 被拆分到多个 run：整段重建（沿用首 run 字体）
        full = para.text.replace(marker, f"{marker}{name} ", 1)
        fonts = [(r.font.size, r.font.name) for r in para.runs[:1]]
        for r in list(para.runs):
            r._element.getparent().remove(r._element)
        new_run = para.add_run(full)
        try:
            if fonts and fonts[0][0]:
                new_run.font.size = fonts[0][0]
            if fonts and fonts[0][1]:
                new_run.font.name = fonts[0][1]
        except Exception:  # noqa: BLE001
            pass
        return True
    return False


def embed_signoff_image(docx_path: Path, slot: str, image_url: str, form_code: str | None) -> bool:
    """Embed a signature image only in a known template signoff cell."""
    from app.services.signature_service import _resolve_file_path
    from docx.shared import Inches

    image_path = _resolve_file_path(image_url)
    if not image_path or not image_path.exists():
        raise FileNotFoundError("签名图片不存在")
    doc = DocxDocument(str(docx_path))
    role_labels = {
        "gd_b_220_rizhi": {
            "inspector": ("记录人", "专业监理工程师"),
            "supervisor": "专业监理工程师",
            "chief": ("总监理工程师/总监理工程师代表", "总监理工程师"),
        },
        "gd_b_221_yuebao": {
            "chief": "总监理工程师（签名）",
            "project_seal": "盖项目章",
        },
        "gd_b_215_tongzhi": {
            "supervisor": "专业监理工程师（签名）",
            "chief": "总监理工程师（代表）",
            "project_seal": "项目监理机构",
        },
        "gdaq_30308_yinhuan": {
            "supervisor": "总监理工程师/总监代表（签名）",
            "chief": "总监理工程师/总监代表（签名）",
            "project_seal": "项目监理机构",
        },
        "gd_b_214_xunshi": "巡视监理人员",
        "gd_b_212_pangzhan": "旁站监理人员",
        "gd_b_213_pingxing": "复核人员" if slot in {"reviewer", "chief"} else "检查人员",
        "acceptance_record": "监理验收人" if slot in {"acceptor", "inspector"} else "施工单位质检员",
        "gdaq_30306_weida": "总监理工程师" if slot in {"chief", "reviewer"} else "监理检查人员",
        "gdaq_30305_qizhong": "总监理工程师" if slot in {"chief", "reviewer"} else "监理检查人员",
        "witness_record": "见证取样人",
    }
    role_spec = role_labels.get(form_code or "")
    if isinstance(role_spec, dict):
        role_spec = role_spec.get(slot)
    roles = (role_spec,) if isinstance(role_spec, str) else tuple(role_spec or ())
    if not roles:
        return False
    preferred_row = None
    if form_code == "gd_b_214_xunshi" and doc.tables:
        preferred_row = 4 if len(doc.tables[0].rows) >= 5 else 3
    elif form_code == "gd_b_212_pangzhan":
        preferred_row = 3
    elif form_code == "gd_b_213_pingxing":
        preferred_row = 5 if slot in {"reviewer", "chief"} else 3
    elif form_code == "acceptance_record":
        preferred_row = 7
    elif form_code == "gd_b_220_rizhi":
        preferred_row = 9 if slot == "inspector" else 10 if slot == "chief" else 9
    for table_index, table in enumerate(doc.tables):
        for row_index, row in enumerate(table.rows):
            if preferred_row is not None and table_index == 0 and row_index != preferred_row:
                continue
            for cell in row.cells:
                para = next((p for p in cell.paragraphs if any(role in p.text for role in roles)
                             and ("签名" in p.text or "签字" in p.text or
                                  (form_code == "gd_b_220_rizhi" and slot == "chief") or
                                  (slot == "project_seal" and "章" in p.text))), None)
                if para:
                    para.add_run(" ").add_picture(str(image_path), width=Inches(0.9))
                    doc.save(str(docx_path))
                    return True
    if form_code in {"witness_record", "gd_b_221_yuebao"}:
        for para in doc.paragraphs:
            if (any(role in para.text for role in roles)
                    and ("签名" in para.text or "签字" in para.text or
                         (form_code == "gd_b_221_yuebao" and slot == "project_seal"))):
                para.add_run(" ").add_picture(str(image_path), width=Inches(0.9))
                doc.save(str(docx_path))
                return True

    # 省统表 16巡视记录.docx 实测 0 处「签字/签名/监理人员」锚点（模板本身漏了签名行），
    # 而 GD-B-214 纸面表式要求「巡视监理人员（签字）」。模板缺行时不能直接 422 让用户
    # 无路可走，在末行（问题及处理情况）追加一行右对齐签名栏；只对这张表生效，
    # 其他表式缺锚点仍返回 False，避免签名被静默放到错误位置。
    if form_code == "gd_b_214_xunshi" and doc.tables:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        tail_cell = doc.tables[0].rows[-1].cells[0]
        signature_para = tail_cell.add_paragraph()
        signature_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        signature_para.add_run(f"{roles[0]}（签字）：").add_picture(
            str(image_path), width=Inches(0.9),
        )
        doc.save(str(docx_path))
        return True
    return False


def _fill_weida_checklist(sub_tbl, template_tr, rows: list[dict], parent) -> None:
    """按检查项清单重建嵌套表数据行（模板行克隆，序号/内容/记录/结果逐格写入）。"""
    import copy

    for tr in sub_tbl.findall(qn("w:tr"))[1:]:
        sub_tbl.remove(tr)
    for idx, item in enumerate(rows, start=1):
        new_tr = copy.deepcopy(template_tr)
        sub_tbl.append(new_tr)
        tcs = new_tr.findall(qn("w:tc"))
        values = (str(idx), str(item.get("item") or ""),
                  str(item.get("record") or "符合要求"), str(item.get("result") or "符合"))
        for tc, val in zip(tcs, values):
            _set_cell_text(_Cell(tc, parent), val)


def fill_weida(doc: DocxDocument, data: dict) -> None:
    """GDAQ30306 危大工程监理巡视检查记录表（房建新版式）。

    主表 10 行：工程名称/编号 → 作业日期/天气/风力 → 巡视的工程部位 → 施工单位 →
    专业分包单位 → 巡视时间 → 巡视内容（嵌套「序号/检查内容/检查记录/检查结果」表）→
    巡视的问题及处理情况 → 签名行。
    检查项按「危大工程名称/类型」自动带出（脚手架 17 / 模板支撑 15 / 装配式 10）。
    """
    from app.services.hazard_checklists import checklist_payload, items_for

    table = _table0(doc)
    trs = table._tbl.findall(qn("w:tr"))

    def tcs_of(idx: int):
        return trs[idx].findall(qn("w:tc"))

    def put(idx: int, cell_idx: int, value) -> None:
        if value in (None, ""):
            return
        try:
            _set_cell_text(_Cell(tcs_of(idx)[cell_idx], table), str(value))
        except IndexError:
            pass

    hazard_type = str(data.get("hazard_type") or data.get("hazard_name") or "").strip()
    hazard_category = str(data.get("hazard_category") or "").strip()
    section = str(data.get("section") or "").strip()
    # 「巡视的工程部位」栏：部位 + 危大类型 + 危大类别（样表写法：…装配式建筑…-一般危大工程）
    part_text = " ".join(x for x in (section, hazard_type, hazard_category) if x)

    put(0, 1, data.get("project_name"))
    put(0, 3, data.get("doc_number"))
    date_val = _coerce_datetime(data.get("date") or data.get("record_date"))
    if hasattr(date_val, "strftime"):
        put(1, 1, date_val.strftime("%Y-%m-%d"))
    put(1, 3, data.get("weather"))
    put(1, 5, data.get("wind") or data.get("wind_force"))
    put(2, 1, part_text)
    put(3, 1, data.get("construction_unit"))
    put(4, 1, data.get("subcontract_unit"))
    # 巡视时间：单行「开始 至 结束」（与样表 2026-10-10 08:30至2026-10-10 15:32 一致）
    start, end = data.get("start_time"), data.get("end_time")
    if start or end:
        def shown(v):
            d = _coerce_datetime(v)
            return d.strftime("%Y-%m-%d %H:%M") if hasattr(d, "strftime") else str(v)
        span = "至".join(x for x in (shown(start) if start else "", shown(end) if end else "") if x)
        if span:
            put(5, 1, span)

    # 巡视内容：嵌套表按类型清单重建
    try:
        sub_tbl = tcs_of(6)[0].find(qn("w:tbl"))
        sub_trs = sub_tbl.findall(qn("w:tr"))
        rows = data.get("checklist")
        if not (isinstance(rows, list) and rows):
            results = data.get("checklist_results")
            rows = checklist_payload(hazard_type, results if isinstance(results, dict) else None)
        # xlsx 追问到的「项目负责人到位/专职安全员到位/特种作业持证/监测数据/整改闭环」
        # 模板固定清单未必覆盖 → 作为补充检查项行并入「巡视内容」表（已含同关键词的不重复加）
        if isinstance(rows, list):
            rows = [dict(r) for r in rows]
            # 把用户答的到位/监测/闭环绑定到已有同义检查项；清单没有该关键词则追加一行，
            # 保证追问值一定落到「巡视内容」表（不白问）
            for label, key, kws in (
                ("施工单位项目负责人到位情况", "pm_onsite", ("项目负责人",)),
                ("专职安全生产管理人员到位情况", "safety_onsite", ("安全员", "安全生产管理")),
                ("特种作业人员持证情况", "special_cert", ("特种作业",)),
                ("监测数据记录", "monitoring", ("监测",)),
                ("隐患整改及复查闭环情况", "rectify", ("整改", "闭环")),
            ):
                v = str(data.get(key) or "").strip()
                if not v:
                    continue
                ok = v in ("是", "符合", "符合要求", "合格")
                result = "符合" if ok else "见记录"
                bound = False
                for r in rows:
                    if any(kw in str(r.get("item") or "") for kw in kws):
                        r["record"], r["result"] = v, result
                        bound = True
                        break
                if not bound:
                    rows.append({"item": label, "record": v, "result": result})
        _fill_weida_checklist(sub_tbl, sub_trs[1], rows, table)
    except (IndexError, AttributeError):  # 模板异常时不影响其余栏目
        logger.exception("危大巡视检查项表回填失败")

    # 巡视发现的问题及处理情况（「巡视内容」整段正文无独立栏时一并折入，避免白问）
    prob_lines: list[str] = []
    content_text = str(data.get("content") or data.get("work_content") or "").strip()
    if content_text:
        prob_lines.append(content_text)
    problems = str(data.get("problems") or data.get("conclusion") or "").strip()
    if problems:
        prob_lines.append(problems)
    if prob_lines:
        try:
            _append_after_label(_Cell(tcs_of(7)[0], table), "巡视的问题及处理情况", "\n".join(prob_lines))
        except IndexError:
            pass

    # 签名行：施工/分包单位专职安全员（左侧）、监理检查人员、总监理工程师
    supervisor = data.get("supervisor") or ""
    chief = data.get("chief_supervisor") or data.get("chief")
    try:
        _fill_signature_inline(_Cell(tcs_of(8)[1], table), "（签名）：", supervisor)
        _fill_signature_inline(_Cell(tcs_of(9)[1], table), "（签名）：", chief)
        review = _coerce_datetime(data.get("review_date") or data.get("date") or data.get("record_date"))
        if hasattr(review, "year"):
            for para in _Cell(tcs_of(9)[1], table).paragraphs:
                if "年" in para.text:
                    for unit, num in (("年", review.year), ("月", review.month), ("日", review.day)):
                        if f"{num}{unit}" not in para.text and unit in para.text:
                            _fill_paragraph_unit(para, unit, num)
    except IndexError:
        pass


def fill_acceptance(doc: DocxDocument, data: dict) -> None:
    """验收记录（YS，项目自建表式）。

    行布局：验收日期/验收类型 → 验收部位 → 验收内容（要点）→ 施工单位自检结论 →
    监理验收结论 → 举牌验收照片 → 验收单位及人员 → 双方签名行。
    """
    table = _table0(doc)
    _fill_header(doc, str(data.get("project_name") or ""), str(data.get("doc_number") or ""))
    trs = table._tbl.findall(qn("w:tr"))

    def cell_of(i: int, j: int) -> _Cell:
        return _Cell(trs[i].findall(qn("w:tc"))[j], table)

    # 照片字段现在存的是上传后的媒体 URL（表单拍照/选图），不能把裸 /uploads/...
    # 路径塞进表格单元格；真实照片由 _append_images 在文末「附：现场影像」呈现。
    photo_raw = data.get("photo") or data.get("photos")
    photo_text = str(photo_raw or "").strip()
    if photo_text.startswith("/uploads/") or photo_text.startswith("http"):
        photo_cell: Any = "见附：现场影像" if _collect_image_urls(data) else ""
    else:
        photo_cell = photo_raw

    layout: tuple[tuple[int, int, Any, bool], ...] = (
        (0, 1, data.get("date") or data.get("acceptance_date"), True),
        (0, 3, data.get("acceptance_type"), False),
        (1, 1, data.get("section"), False),
        (2, 1, data.get("acceptance_content") or data.get("content"), False),
        (3, 1, data.get("self_check_conclusion"), False),
        (4, 1, data.get("conclusion") or data.get("supervision_conclusion"), False),
        (5, 1, photo_cell, False),
        (6, 1, data.get("acceptance_participants"), False),
    )
    for i, j, value, as_date in layout:
        if value in (None, "", []):
            continue
        try:
            cell = cell_of(i, j)
            if as_date:
                _fill_date_cell(cell, value)
            elif isinstance(value, (list, tuple)):
                _set_cell_text(cell, "；".join(str(x) for x in value))
            else:
                _set_cell_text(cell, str(value))
        except IndexError:
            pass

    try:
        _fill_signature_inline(cell_of(7, 0), "（签名）：",
                               data.get("inspector_signature") or "")
        _fill_signature_inline(cell_of(7, 2), "（签名）：",
                               data.get("quality_inspector") or "")
    except IndexError:
        pass


#: 按 form_code 分派的填充函数
FILLERS = {
    "gd_b_214_xunshi": fill_xunshi,
    "gd_b_212_pangzhan": fill_pangzhan,
    "gd_b_213_pingxing": fill_pingxing,
    "gd_b_220_rizhi": fill_rizhi,
    # 监理日记复用 GD-B-220 版式，但必须走同一填充器：未注册时会掉进
    # fill_generic，二次导出（/document/{id}/export-pdf）会丢栏目。
    "jianli_riji": fill_rizhi,
    "gdaq_30306_weida": fill_weida,
    "gd_b_219_cailiao": fill_cailiao,
    "anquan_jianli_rizhi": fill_anquan_rizhi,
    "gd_b_221_yuebao": fill_yuebao,
    "acceptance_record": fill_acceptance,
}


#: 复用同一模板、但抬头必须区分的表式。日记不是省统表，不冒用 GD-B-220 编号。
FORM_HEADER_OVERRIDES = {"jianli_riji"}


def _apply_header_override(doc: DocxDocument, form_code: str) -> None:
    """把模板抬头「监理日志 / GD-B-220□□□」改写为日记抬头。

    只改文档级段落（表格内的栏目名不动），保留首个 run 的字体与段落对齐；
    文本被拆成多个 run 时合并到首 run，避免逐 run 替换漏字。
    """
    if form_code not in FORM_HEADER_OVERRIDES:
        return
    for para in doc.paragraphs:
        text = para.text
        if text.strip() == "监理日志":
            new_text = text.replace("监理日志", "监理日记", 1)
        elif "GD-B-220" in text:
            new_text = re.sub(r"GD-B-220[□ \t]*", "GD-JL-RJ（个人监理记录）", text, count=1)
        else:
            continue
        runs = para.runs
        if not runs or new_text == text:
            continue
        runs[0].text = new_text
        for run in runs[1:]:
            run.text = ""


def _fill_business_footer(doc, data):
    """回填第一张业务表的年月日落款，不替未发生的回复/复查填写日期。"""
    value = _coerce_datetime(data.get("date") or data.get("check_date"))
    if not hasattr(value, "year") or not doc.tables:
        return
    seen = set()
    for row in doc.tables[0].rows:
        for cell in row.cells:
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            for paragraph in cell.paragraphs:
                if re.fullmatch(r"\s*年\s*月\s*日\s*", paragraph.text):
                    for unit, number in (("年", value.year), ("月", value.month), ("日", value.day)):
                        _fill_paragraph_unit(paragraph, unit, number)


def _collect_image_urls(data: dict) -> list[str]:
    """汇总记录关联影像：attached_images（列表或单串）+ image_path + rectification_image_path，去重保序"""
    urls: list[str] = []
    raw = data.get("attached_images")
    if isinstance(raw, str):
        raw = [raw]
    for u in raw or []:
        if isinstance(u, str) and u.strip():
            urls.append(u.strip())
    for key in ("image_path", "rectification_image_path"):
        v = str(data.get(key) or "").strip()
        if v:
            urls.append(v)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _resolve_media_path(url: str):
    """把 /uploads/xxx、/exports/xxx 解析为服务器文件系统路径；不可解析返回 None"""
    if not url:
        return None
    try:
        if url.startswith("/uploads/"):
            candidate = (settings.upload_dir / url.removeprefix("/uploads/")).resolve()
            candidate.relative_to(settings.upload_dir.resolve())
            return candidate
        if url.startswith("/exports/"):
            candidate = (settings.export_dir / url.removeprefix("/exports/")).resolve()
            candidate.relative_to(settings.export_dir.resolve())
            return candidate
    except Exception:
        return None
    return None


def _append_images(doc: DocxDocument, data: dict, max_images: int = 12) -> None:
    """把记录关联影像以「附：现场影像」追加到文书末尾（每张居中，宽 15cm 与官方样例一致；失败静默跳过单张）"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm

    paths: list[Any] = []
    for url in _collect_image_urls(data)[:max_images]:
        p = _resolve_media_path(url)
        if p is not None and p.exists():
            paths.append(p)
    if not paths:
        return
    # Photos are evidence, not part of the form body. Starting them on a fresh
    # page prevents image height/width from reflowing the first form page in PDF.
    doc.add_page_break()
    doc.add_paragraph("附：现场影像：")
    for index, p in enumerate(paths):
        if index and index % 2 == 0:
            doc.add_page_break()
        try:
            doc.add_picture(str(p), width=Cm(15))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        except Exception:
            logger.warning("附加现场影像失败：%s", p, exc_info=True)


def fill_tongzhi(doc: DocxDocument, data: dict) -> None:
    """GD-B-215 监理通知单（质量类）。

    模板为「通知单 + 回复单」合并表：只填通知单（TABLE 0），回复单留空待施工单位回填。
    通知单正文为单合并大格（致 / 事由 / 内容 / 项目监理机构（章）+ 总监签名 / 日期）；
    工程名称、编号在表前段落。
    """
    pn = str(data.get("project_name") or "")
    dn = str(data.get("doc_number") or "")
    _replace_text(doc.paragraphs, _HEADER_PROJECT, f"{_HEADER_PROJECT}{pn}")
    _replace_text(doc.paragraphs, _HEADER_NO, f"{_HEADER_NO}{dn}")
    if not doc.tables:
        return
    cell = doc.tables[0].rows[0].cells[0]
    paras = cell.paragraphs
    # 致（施工单位项目经理部）
    to_unit = str(data.get("to_unit") or data.get("construction_unit") or "")
    if to_unit:
        _replace_text(paras, "致：", f"致：{to_unit}")
    # 事由（缺省按部位/问题派生，保证栏目不为空）
    subject = str(data.get("subject") or "").strip()
    if not subject:
        sec = str(data.get("section") or "").strip()
        subject = f"{sec}施工问题整改通知" if sec else "施工质量问题整改通知"
    _append_after_label(cell, "事由：", subject)
    # 内容（存在问题 + 违反条款 + 整改要求 + 时限/附件/抄送，官方表无独立栏 → 折入内容，防吞数据）
    problem = str(data.get("problems") or data.get("problem_desc") or "").strip()
    req = str(data.get("requirement") or data.get("handling") or "").strip()
    violation = str(data.get("violation") or "").strip()
    deadline = str(data.get("deadline") or "").strip()
    attachment = str(data.get("attachment") or "").strip()
    cc_unit = str(data.get("cc_unit") or "").strip()
    content = str(data.get("content") or "").strip()
    if not content:
        lines: list[str] = []
        if problem:
            lines.append(f"经检查，发现下列问题：\n{problem}")
        # 问题区域（xlsx 追问字段）模板无独立栏 → 折入内容，避免白问
        sec_text = str(data.get("section") or "").strip()
        if sec_text:
            lines.append(f"问题区域：{sec_text}")
        if violation:
            lines.append(f"违反条款：{violation}")
        if req:
            lines.append(f"整改要求：{req}")
        if deadline:
            lines.append(f"整改完成时限：{deadline}")
        if attachment:
            lines.append(f"附件：{attachment}")
        if cc_unit:
            lines.append(f"抄送：{cc_unit}")
        content = "\n".join(lines)
    if content:
        _append_after_label(cell, "内容：", content)
    # 项目监理机构（项目章）值（与总监签名同段，内联插到「（项目章）」后）
    org = str(data.get("supervision_org") or "").strip()
    if org:
        _fill_signature_inline(cell, "项目监理机构（项目章）", org)
    # 总监/专业监理工程师签名
    supervisor = str(data.get("supervisor") or "").strip()
    if supervisor:
        _fill_signature_inline(cell, "（签名）：", supervisor)
    # 日期（年 月 日）：只回填真正的占位段——整格扫描会把正文里「限3日内」
    # 这类含「日」的行先命中，既写坏正文又吞掉真占位（历史 bug）
    date = data.get("date") or ""
    if date:
        for para in cell.paragraphs:
            if _is_date_placeholder_para(para):
                _fill_date_runs(para, date)
                break


def _fill_placeholder_date_near(tbl: Table, anchor_label: str, date: Any, search_next: bool) -> None:
    """在 anchor 标签所在行（search_next 时含其下一行）找「年/月/日」占位格回填日期。

    整行用 list(row.cells) 持有引用：lxml 元素代理在无引用时会被 GC，
    其 id 可能被后续代理复用，按 id 去重会把新元素误判为已见过的合并格。
    """
    norm = re.sub(r"\s+", "", anchor_label)
    r_anchor = None
    for r in range(len(tbl.rows)):
        row_cells = list(tbl.rows[r].cells)  # 整行持引用
        for cell in row_cells:
            if norm in re.sub(r"\s+", "", _cell_text(cell)):
                r_anchor = r
                break
        if r_anchor is not None:
            break
    if r_anchor is None:
        return
    candidates = (r_anchor + 1, r_anchor) if search_next else (r_anchor,)
    for rr in candidates:
        if rr < 0 or rr >= len(tbl.rows):
            continue
        row_cells = list(tbl.rows[rr].cells)  # 整行持引用
        seen: list = []
        for cell in row_cells:
            if any(cell._tc is e for e in seen):
                continue
            seen.append(cell._tc)
            text = cell.text
            if "年" in text and "月" in text and "日" in text:
                _fill_date_cell(cell, date)
                return


def _fmt_ymd_cn(value) -> str:
    """任意日期值 → '2026年10月6日'；空 → 可手填的占位串。"""
    blank = "\u3000\u3000\u3000\u3000年\u3000\u3000月\u3000\u3000日"
    if not value:
        return blank
    if isinstance(value, datetime):
        return f"{value.year}年{value.month}月{value.day}日"
    s = str(value).strip()
    if not s:
        return blank
    try:
        d = datetime.fromisoformat(s)
        return f"{d.year}年{d.month}月{d.day}日"
    except ValueError:
        pass
    m = re.search(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})", s)
    if m:
        return f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日"
    return s


_HAZARD_NUM_RE = re.compile(r"^\s*[0-9０-９]{1,3}\s*[、.．)）]\s*")


def _split_hazard_items(text: str) -> list[str]:
    """隐患描述 → 条目列表：多行/分号分隔均拆开，输出时统一编号。"""
    if not text:
        return []
    text = text.strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) == 1:
        for sep in ("；", ";"):
            if sep in lines[0]:
                parts = [x.strip(" ；;。") for x in lines[0].split(sep)]
                lines = [x for x in parts if x]
                break
    return lines


def _para_set_full_text(p: "Paragraph", text: str) -> None:
    """整段重设文本：沿用首 run 的字符格式，清掉其余 runs。"""
    if p.runs:
        p.runs[0].text = text
        for r in p.runs[1:]:
            r.text = ""
    else:
        p.add_run(text)


def _fill_yinhuan_table(doc: DocxDocument, table: Table, ctx: dict, items: list[str]) -> None:
    """官方表格版 GDAQ30308（真样例 `安全隐患整改通知.docx`，29 行主表）坐标式填充。

    行按标签文字定位（不写死行号，模板增删空行也不错位）：
      工程名称：|值|编号：|值  →  R+1 起
      致：|值|（施工单位）
      经检查发现，施工现场存在下列安全隐患：  →  下一行是 vMerge 描述大格
      限于 |年月日| 前完成整改，并向我单位提出整改复查申请。
      项目监理机构（章）：|值  /  总监理工程师/总监代表（签名）：|值
      （总监签名下一行）年月日 = 签发日期
    值格字体统一宋体 10.5pt，与官方标签一致。
    """
    from docx.shared import Pt

    trs = table._tbl.findall(qn("w:tr"))

    def tcs_of(idx: int):
        return trs[idx].findall(qn("w:tc"))

    def put(idx: int, cell_idx: int, value) -> None:
        if value in (None, ""):
            return
        try:
            _set_cell_text(_Cell(tcs_of(idx)[cell_idx], table), str(value),
                           font_name="宋体", font_size=Pt(10.5))
        except IndexError:
            pass

    def row_text(idx: int) -> str:
        try:
            return "".join(t.text or "" for t in trs[idx].iter(qn("w:t")))
        except IndexError:
            return ""

    def labels_of(idx: int) -> list[str]:
        try:
            return [_cell_text(_Cell(tc, table)) for tc in tcs_of(idx)]
        except IndexError:
            return []

    def find_row(label: str, start: int = 0) -> int | None:
        norm = re.sub(r"\s+", "", label)
        for i in range(start, len(trs)):
            if norm in re.sub(r"\s+", "", row_text(i)):
                return i
        return None

    def date_cell_idx(idx: int, default: int = 1) -> int:
        """该行里「年…月…日」占位格的 tc 序号。"""
        labs = labels_of(idx)
        for k, s in enumerate(labs):
            compact = re.sub(r"\s+", "", s)
            if "年" in compact and "月" in compact and "日" in compact and not compact.startswith("限于"):
                return k
        return default

    # 工程名称 / 编号（同一行）
    r_pn = find_row("工程名称")
    if r_pn is not None:
        put(r_pn, 1, ctx.get("project_name"))
        labs = labels_of(r_pn)
        j_dn = next((k for k, s in enumerate(labs)
                     if re.sub(r"\s+", "", s) in ("编号：", "编号:")), None)
        if j_dn is not None and j_dn + 1 < len(labs):
            put(r_pn, j_dn + 1, ctx.get("doc_number"))

    # 致：（施工单位）
    r_to = find_row("致：")
    if r_to is not None:
        put(r_to, 1, ctx.get("to_unit"))

    # 隐患描述大格：标签行的下一行（官方为 vMerge restart 起）
    r_haz = find_row("经检查发现")
    if r_haz is not None and r_haz + 1 < len(trs):
        block: list[str] = []
        subject, section = ctx.get("subject") or "", ctx.get("section") or ""
        extra = "，".join(x for x in ((f"事由：{subject}" if subject else ""),
                                      (f"隐患部位：{section}" if section else "")) if x)
        if extra:
            block.append(extra)
        block.extend(items or ["（无）"])
        contract = ctx.get("contract") or ""
        if contract:
            block.append(f"整改依据：《{contract}》")
        put(r_haz + 1, 0, "\n".join(block))

    # 限于 …… 前完成整改
    r_dl = find_row("限于")
    if r_dl is not None:
        put(r_dl, date_cell_idx(r_dl), ctx.get("deadline"))

    # 项目监理机构（章） / 总监签名
    r_org = find_row("项目监理机构")
    if r_org is not None:
        put(r_org, 1, ctx.get("supervision_org"))
    r_sig = find_row("总监理工程师")
    if r_sig is not None:
        put(r_sig, 1, ctx.get("supervisor"))
        # 签发日期 = 总监签名下一行的年月日格（官方 R24）
        r_issue = r_sig + 1
        if r_issue < len(trs) and not re.sub(r"\s+", "", row_text(r_issue)).startswith(("安全负责人", "签收日期")):
            put(r_issue, date_cell_idx(r_issue), ctx.get("issue_date"))


def fill_yinhuan(doc: DocxDocument, data: dict) -> None:
    """GDAQ30308 安全隐患整改通知（监理对施工单位项目部）。

    🔴 双版式自适应（2026-10-15）：
      版式 A = 官方表格版（真样例 `安全隐患整改通知.docx`，md5 d8d10152，29 行主表，
              标签「限于…前完成整改，并向我单位提出整改复查申请。」）→ 走 _fill_yinhuan_table
              坐标式填充，版式/文案/字体全部与官方一致。
      版式 B = 段落式 {{token}} 版（曾被误判官方版式后重建，md5 33e5e62a）→ 走下方 token 替换，
              仅作历史兼容，不再作为目标版式。
    判据：文档含表格且表内有「限于」+「前完成整改」标签行 → 版式 A。
    """
    pn = str(data.get("project_name") or "")
    dn = str(data.get("doc_number") or "")
    to_unit = str(data.get("to_unit") or data.get("construction_unit") or "")
    subject = str(data.get("subject") or "").strip()
    section = str(data.get("section") or "").strip()
    hazard = str(
        data.get("problems") or data.get("hazard_description") or data.get("content") or ""
    ).strip()
    deadline = _fmt_ymd_cn(data.get("deadline"))
    requirement = str(data.get("requirement") or "").strip()
    attachment = str(data.get("attachment") or "").strip()
    contract = str(data.get("contract_name") or "").strip()
    supervisor = str(data.get("supervisor") or "").strip()
    issue_date = _fmt_ymd_cn(data.get("date"))
    regulation_refs = data.get("regulation_refs") or []
    if not isinstance(regulation_refs, (list, tuple)):
        regulation_refs = []

    # 隐患条目 → 统一编号列表（附整改依据条目，若有）
    items = [f"{i + 1}、{_HAZARD_NUM_RE.sub('', it)}" for i, it in enumerate(_split_hazard_items(hazard))]
    for ref in regulation_refs:
        title = str((ref or {}).get("title") or "").strip() if isinstance(ref, dict) else str(ref).strip()
        if title:
            items.append(f"整改依据：{title}")
    # 整改要求 / 附件（xlsx 追问字段）模板隐患描述大格无独立栏 → 并入隐患描述条目
    if requirement:
        items.append(f"整改要求：{requirement}")
    if attachment:
        items.append(f"附件：{attachment}")

    # 版式 A：官方表格版（表内有「限于」+「前完成整改」标签行）→ 坐标式填充后返回
    for tbl in doc.tables:
        joined = "".join(t.text or "" for t in tbl._tbl.iter(qn("w:t")))
        compact = re.sub(r"\s+", "", joined)
        if "限于" in compact and "前完成整改" in compact:
            _fill_yinhuan_table(doc, tbl, {
                "project_name": pn, "doc_number": dn, "to_unit": to_unit,
                "subject": subject, "section": section, "contract": contract,
                "deadline": deadline, "supervisor": supervisor, "issue_date": issue_date,
                "supervision_org": str(data.get("supervision_org") or "").strip(),
            }, items)
            return

    removed = []
    for p in doc.paragraphs:
        t = p.text
        if "{{" not in t:
            continue
        # 同一段落可能含多个 token（工程名称+编号同行），顺序全部替换
        if "{{project_name}}" in t:
            t = t.replace("{{project_name}}", pn)
        if "{{doc_number}}" in t:
            t = t.replace("{{doc_number}}", dn)
        if "{{to_unit}}" in t:
            t = t.replace("{{to_unit}}", to_unit)
        if "{{subject}}" in t:
            if subject and section:
                t = f"事由：{subject}（隐患部位：{section}）"
            elif subject:
                t = f"事由：{subject}"
            elif section:
                t = f"隐患部位：{section}"
            else:
                removed.append(p)
        if "{{problems_block}}" in t:
            # 用占位段作格式母版，逐条克隆后整块替换
            import copy as _copy

            from docx.text.paragraph import Paragraph as _P

            items_final = items or ["（无）"]
            anchor = p._p
            for item in items_final:
                new_el = _copy.deepcopy(p._p)
                anchor.addnext(new_el)
                anchor = new_el
                _para_set_full_text(_P(new_el, p._parent), item)
            removed.append(p)
            continue
        if "{{deadline_date}}" in t:
            t = t.replace("{{deadline_date}}", deadline)
        if "{{contract_name}}" in t:
            if contract:
                t = t.replace("{{contract_name}}", contract)
            else:
                t = (
                    "通知书签收后，按本工程施工合同内容及本建设工程项目的各项制度、"
                    "规定和管理办法，进行相应处理，并上报抄送监理部门！"
                )
        if "{{supervisor}}" in t:
            t = t.replace("{{supervisor}}", supervisor)
        if "{{issue_date}}" in t:
            t = t.replace("{{issue_date}}", issue_date)
        _para_set_full_text(p, t)
    for p in removed:
        p._p.getparent().remove(p._p)


FILLERS.update({"gd_b_215_tongzhi": fill_tongzhi, "gdaq_30308_yinhuan": fill_yinhuan})


#: 通用 fallback 的标签→字段映射（用于未专门编写填充器的模板）
GENERIC_FIELD_MAP = {
    "工程名称": "project_name",
    "工程项目名称": "project_name",
    "编号": "doc_number",
    "日期": "date",
    "时间": "time",
    "天气": "weather",
    "施工单位": "construction_unit",
    "施工区域": "section",
    "部位": "section",
    "位置": "location",
    "巡视内容": "content",
    "检查内容": "content",
    "内容": "content",
    "致": "to_unit",
    "存在问题": "problems",
    "问题描述": "problem_desc",
    "问题": "problems",
    "处理情况": "handling",
    "整改要求": "requirement",
    "整改期限": "deadline",
    "整改": "handling",
    "结论": "conclusion",
    "依据": "basis",
    "备注": "remark",
    "材料名称": "material_name",
    "规格": "material_spec",
    "见证": "content",
}


def fill_generic(doc: DocxDocument, data: dict) -> None:
    """通用填充器：扫描每个单元格，找到标签文字后把值填到相邻空白格。

    用于未专门写 fill_* 的模板（如通知单/审查表/台帐），保证基本可生成。
    """
    project_name = data.get("project_name", "")
    doc_number = data.get("doc_number", "")
    if project_name or doc_number:
        _fill_header(doc, project_name, doc_number)
    for table in doc.tables:
        for row in table.rows:
            seen: set[int] = set()
            cells = row.cells
            for index, cell in enumerate(cells):
                key = id(cell._tc)
                if key in seen:
                    continue
                seen.add(key)
                txt = _cell_text(cell)
                if not txt:
                    continue
                compact = re.sub(r"[\s：:，。]+", "", txt)
                for label, field in GENERIC_FIELD_MAP.items():
                    label_compact = re.sub(r"[\s：:，。]+", "", label)
                    if compact.startswith(label_compact) or compact == label_compact:
                        val = data.get(field)
                        if val in (None, ""):
                            continue
                        target = _right_neighbor(table, cell)
                        if target is not None and not _cell_text(target):
                            if any(u in _cell_text(target) for u in ("年", "月", "日")) and field == "date":
                                _fill_date_cell(target, val)
                            else:
                                _set_cell_text(target, str(val))
                        break
    # 大段正文（存在“问题：/内容：”等行首标签的全宽格）
    for table in doc.tables:
        _fill_body_areas(table, data)


def _fill_by_schema_labels(doc: DocxDocument, form_code: str, data: dict) -> None:
    """按省统表 schema 的字段中文标签回填模板单元格（兜底，不丢字段）。

    仅对登记在 CONTENT_MAP 的记录表式生效。遍历每个字段，把其值填到
    模板里「标签文字以该字段标签开头/结尾」的空白目标单元格；按标签长度降序优先
    匹配更具体的字段，避免泛化标签（如「内容」）误吞多个字段。
    """
    from app.services import provform_schema

    schema = provform_schema.SCHEMAS.get(form_code)
    if not schema:
        return
    mp = provform_schema.CONTENT_MAP.get(form_code, {})
    label_value: dict[str, str] = {}
    for sec in schema["sections"]:
        for f in sec["fields"]:
            ck = mp.get(f["key"], f["key"])
            v = data.get(ck)
            if v not in (None, ""):
                label_value[f["label"]] = str(v)
    if not label_value:
        return
    # 长标签优先，减少泛化标签误匹配
    ordered = sorted(label_value.items(), key=lambda kv: -len(kv[0]))
    for table in doc.tables:
        for row in table.rows:
            seen: set[int] = set()
            cells = row.cells
            for index, cell in enumerate(cells):
                key = id(cell._tc)
                if key in seen:
                    continue
                seen.add(key)
                txt = _cell_text(cell)
                if not txt:
                    continue
                compact = re.sub(r"[\s：:，。；;]+", "", txt)
                compact = re.sub(r"^\d+[、.．]", "", compact)
                for label, val in ordered:
                    lc = re.sub(r"[\s：:，。；;]+", "", label)
                    if not lc:
                        continue
                    if lc.startswith(compact) or compact.startswith(lc):
                        # 🔴 只认标签右侧「紧邻的那个单元格」，不能一路往后找第一个空单元格：
                        # 紧邻值格已被专用填充器填好时（如监理日志 R1「日期」值格），原实现会
                        # 越界写到下一栏——实测把 date 写进了「气温」格（2026-11-03 用户截图）。
                        target = None
                        for candidate in cells[index + 1:]:
                            if _same_cell(candidate, cell):
                                continue
                            target = candidate
                            break
                        if target is not None and not _cell_text(target).strip():
                            _set_cell_text(target, val)
                        break


def _fix_table_layout(doc: DocxDocument) -> None:
    """把文档内所有表格（含嵌套表）的宽度钉死为 tblGrid 总宽。

    官方样表由 xlsx 转换而来，tblW=0/auto + tblLayout=autofit：桌面 Word 显示正常，
    但微信等移动端预览引擎会按内容把整表收缩成窄条（旁站 GD-B-212 实测塌陷）。
    统一改为 tblW=dxa 总宽 + fixed 布局后，所有渲染器都按 tblGrid 全宽显示。
    """
    for tbl in doc.element.body.iter(qn("w:tbl")):
        grid = tbl.find(qn("w:tblGrid"))
        if grid is None:
            continue
        total = sum(int(gc.get(qn("w:w")) or 0) for gc in grid.findall(qn("w:gridCol")))
        if total <= 0:
            continue
        tblPr = tbl.find(qn("w:tblPr"))
        if tblPr is None:
            tblPr = tbl.makeelement(qn("w:tblPr"), {})
            tbl.insert(0, tblPr)
        el_w = tblPr.find(qn("w:tblW"))
        if el_w is None:
            el_w = tblPr.makeelement(qn("w:tblW"), {})
            tblPr.insert(0, el_w)
        el_w.set(qn("w:w"), str(total))
        el_w.set(qn("w:type"), "dxa")
        el_layout = tblPr.find(qn("w:tblLayout"))
        if el_layout is None:
            el_layout = tblPr.makeelement(qn("w:tblLayout"), {})
            tblPr.append(el_layout)
        el_layout.set(qn("w:type"), "fixed")


def render_form(form_code: str, data: dict, output_dir: Path | None = None) -> Path:
    """按省统表模板渲染一份 docx，返回输出路径。

    Args:
        form_code: FORM_BY_CODE 中的 form_code
        data: 结构化内容（project_name/doc_number/section/…）
        output_dir: 输出目录（默认 settings.export_dir）
    """
    from app.services.form_registry import get_form

    spec = get_form(form_code)
    if spec is None:
        raise ValueError(f"未知表式：{form_code}")
    if not spec.template:
        if form_code == "gdaq_30309_huifu":
            raise ValueError(
                "GDAQ30309 官方可编辑模板尚未取得授权文件；为避免把自制版误认为官方原表，暂不生成该表。"
            )
        # 无模板的表式：降级输出简单 docx（标题 + 字段表）
        return _render_plain_docx(spec, data, output_dir)

    folder = (
        settings.knowledge_base_path / "省统表word版"
        if spec.folder == "sheng_tong_biao"
        else settings.knowledge_base_path / "第三节安全监理用表" / "安全监理用表"
        if spec.folder == "safety"
        else settings.knowledge_base_path
    )
    template_path = folder / spec.template
    if not template_path.exists():
        bundled_root = Path(__file__).resolve().parents[2] / "知识库"
        fallback_folder = (
            bundled_root / "省统表word版"
            if spec.folder == "sheng_tong_biao"
            else bundled_root / "第三节安全监理用表" / "安全监理用表"
            if spec.folder == "safety"
            else bundled_root
        )
        fallback_path = fallback_folder / spec.template
        if fallback_path.exists():
            template_path = fallback_path
        else:
            raise TemplateNotFoundError(f"模板不存在：{template_path}")

    doc = DocxDocument(str(template_path))
    filler = FILLERS.get(form_code)
    if filler:
        filler(doc, data)
    else:
        fill_generic(doc, data)
    # 复用模板的表式（监理日记）改抬头，必须在填充器之后执行
    _apply_header_override(doc, form_code)
    # 兜底：按 schema 字段标签回填（仅 CONTENT_MAP 登记的 11 类记录表式），
    # 保证所有录入字段在 docx 中不丢，且不影响既有专用填充器已填内容。
    try:
        from app.services import provform_schema
        if form_code in provform_schema.CONTENT_MAP:
            _fill_by_schema_labels(doc, form_code, data)
            if form_code == "gdaq_30305_qizhong":
                rendered = "\n".join(
                    paragraph.text for paragraph in doc.paragraphs
                ) + "\n" + "\n".join(
                    cell.text for table in doc.tables for row in table.rows for cell in row.cells
                )
                mapping = provform_schema.CONTENT_MAP[form_code]
                missing = []
                for section in provform_schema.SCHEMAS[form_code]["sections"]:
                    for field in section["fields"]:
                        canonical = mapping.get(field["key"], field["key"])
                        value = str(data.get(canonical) or "").strip()
                        if value and value not in rendered and field.get("type") not in {"date", "datetime", "time"}:
                            missing.append((canonical, field["label"]))
                _append_missing_fields(doc, data, missing, "未定位到模板栏位的已确认字段")
    except Exception:  # noqa: BLE001
        logger.exception("schema 标签兜底回填失败（不影响主流程）")
    _fill_business_footer(doc, data)
    # 表格宽度钉死（auto/autofit 在移动端预览会被收缩成窄条）
    _fix_table_layout(doc)
    # 统一附加现场影像：记录/文书关联的照片（attached_images/image_path 等），
    # 有图才加「附：现场影像」，无图不加床
    _append_images(doc, data)

    out_dir = (output_dir or settings.export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    import uuid
    # 规范命名：巡视记录_GD-B-214-00044.docx（表式中文名 + 编号空格转连字符）；
    # 同编号幂等复用/重生成时同名覆盖，无编号时回退 form_code+短hash
    clean_dn = re.sub(r"\s+", "-", str(data.get("doc_number") or "")).strip("-")
    # 表名可能含「/」等非法路径字符（如「材料/构配件/设备进场台账」），替换避免落盘失败
    safe_name = re.sub(r'[\\/:*?"<>|]', "-", str(getattr(spec, "name", "") or form_code))
    if clean_dn:
        filename = f"{safe_name}_{clean_dn}.docx"
    else:
        filename = f"{form_code}_{uuid.uuid4().hex[:8]}.docx"
    output = out_dir / filename
    doc.save(str(output))
    return output


def _render_plain_docx(spec, data: dict, output_dir: Path | None) -> Path:
    """无固定模板时的降级渲染：标题 + 标签值表。"""
    from docx.shared import Pt

    doc = DocxDocument()
    title = doc.add_paragraph()
    run = title.add_run(spec.name)
    run.bold = True
    run.font.size = Pt(18)
    labels = {"project_name": "工程项目名称", "doc_number": "编号", "date": "业务日期",
              "construction_unit": "施工单位", "supervision_org": "项目监理机构", "section": "部位",
              "notice_number": "安全隐患整改通知编号", "notice_date": "安全隐患整改通知日期",
              "rectify_desc": "整改情况", "rectify_image": "整改影像",
              "contractor_manager": "项目负责人（签名）", "supervision_opinion": "专业监理工程师审查意见",
              "supervision_engineer": "专业监理工程师（签名）", "supervision_seal_label": "项目监理机构（章）",
              "material_name": "材料名称", "material_spec": "规格/型号", "manufacturer": "生产厂家/品牌",
              "batch_no": "批号/炉号", "quantity": "取样数量", "unit": "计量单位",
              "sample_date": "取样日期", "sampler": "取样人", "cert_no": "合格证/报告编号",
              "test_result": "检测结果", "acceptance_result": "见证结论", "remark": "备注",
              "acceptance_type": "验收类型", "acceptance_content": "验收内容", "acceptance_participants": "验收单位及人员",
              "basis": "检查依据", "handling": "处理情况",
              "summary": "内容", "content": "记录内容", "problems": "发现问题及处理情况", "review_date": "审阅日期"}
    aliases = {
        "quantity": ("quantity", "witness_quantity"),
        "unit": ("unit", "witness_unit"),
        "cert_no": ("cert_no", "witness_cert_no"),
        "acceptance_result": ("acceptance_result", "witness_accept_result"),
        "sample_date": ("sample_date", "date"),
        "sampler": ("sampler", "inspector"),
    }
    for key in labels:
        val = next((data.get(candidate) for candidate in aliases.get(key, (key,)) if data.get(candidate)), None)
        if val:
            p = doc.add_paragraph()
            label = {
                "project_name": "工程项目名称",
                "doc_number": "编号",
                "date": "日期",
                "summary": "内容",
                "content": "内容",
                "problems": "问题及处理",
            }.get(key, labels.get(key, key))
            if key == "acceptance_result":
                label = "人工确认的验收结论" if spec.form_code == "acceptance_record" else "见证结论"
            run = p.add_run(f"{label}：")
            run.bold = True
            p.add_run(str(val))
    if spec.form_code == "gdaq_30309_huifu":
        doc.add_paragraph("施工单位（项目部）项目负责人（签名）：________________    回复日期：________________")
        doc.add_paragraph("专业监理工程师审查意见：")
        doc.add_paragraph("项目监理机构（章）：________________    专业监理工程师（签名）：________________")
        doc.add_paragraph("监理审查日期：________________")
    if spec.form_code == "acceptance_record":
        doc.add_paragraph("验收人（本人签认）：________________    签认日期：________________")
    elif spec.form_code == "witness_record":
        doc.add_paragraph("见证取样人（签名）：________________    日期：________________")
    _append_images(doc, data)
    out_dir = (output_dir or settings.export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    import uuid
    filename = f"{spec.form_code}_{uuid.uuid4().hex[:8]}.docx"
    output = out_dir / filename
    doc.save(str(output))
    return output


def docx_to_pdf(docx_path: Path) -> Path | None:
    """把 docx 转 pdf：优先 LibreOffice，其次 Word COM（Windows）。

    返回生成的 pdf 路径；环境不支持时返回 None。
    """
    pdf_path = docx_path.with_suffix(".pdf")
    pdf_path.unlink(missing_ok=True)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        import subprocess
        # 每次转换用独立 profile：并发导出时共享 profile 会因实例锁直接失败。
        profile = tempfile.mkdtemp(prefix="lo-profile-")
        try:
            result = subprocess.run(
                [soffice, f"-env:UserInstallation=file://{profile}", "--headless",
                 "--norestore", "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path)],
                capture_output=True, timeout=120,
            )
            if result.returncode == 0 and pdf_path.exists():
                return pdf_path
        finally:
            shutil.rmtree(profile, ignore_errors=True)
    word = None
    document = None
    try:
        import win32com.client  # type: ignore
        import pythoncom  # type: ignore
        pythoncom.CoInitialize()
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(str(docx_path.resolve()), ReadOnly=True)
        document.SaveAs(str(pdf_path.resolve()), FileFormat=17)  # wdFormatPDF
        return pdf_path if pdf_path.exists() else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("docx 转 PDF 失败（不影响 docx 导出）：%s", exc)
        return None
    finally:
        try:
            if document is not None:
                document.Close(False)
            if word is not None:
                word.Quit()
        except Exception:  # noqa: BLE001
            logger.debug("清理 Word COM 实例失败", exc_info=True)


def list_available_template_files() -> list[dict]:
    """列出模板文件存在性，用于自检。"""
    from app.services.form_registry import FORMS
    result = []
    for spec in FORMS:
        if not spec.template:
            result.append({"form_code": spec.form_code, "exists": False, "reason": "no_template"})
            continue
        folder = (
            settings.knowledge_base_path / "省统表word版"
            if spec.folder == "sheng_tong_biao"
            else settings.knowledge_base_path / "第三节安全监理用表" / "安全监理用表"
            if spec.folder == "safety"
            else settings.knowledge_base_path
        )
        result.append({"form_code": spec.form_code, "exists": (folder / spec.template).exists()})
    return result
