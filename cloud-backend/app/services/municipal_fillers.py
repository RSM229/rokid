"""市政工程（道路/桥梁等）省统表 / 安全统表 官方版式填充器。

与房建版式（GD-B 系列）不同，市政/安全统表模板结构各异：
  - 头部多为「标签：」与值同格（append 模式），少数为「标签｜值」分列（set 模式）；
  - 危大巡视表（AQSZ-J-2.5-1~7）含「符合/不符合/其他」□ 勾选列；
  - 监理日志/旁站/巡视多为整行大单元格，正文须紧跟行首标签插入。

本模块复用 form_renderer 的基础工具（_Cell/qn/_set_cell_text/_insert_after_text/
_append_after_label/_fill_date_cell/_fill_signature_inline）。form_renderer 在本文件
定义之后（文件末尾）才 import 本模块，故此处顶层 import 不会引发循环依赖。
"""
from __future__ import annotations

from docx.oxml.ns import qn
from docx.table import _Cell

from app.services.form_renderer import (
    _append_after_label,
    _coerce_datetime,
    _fill_date_cell,
    _fill_signature_inline,
    _insert_after_text,
    _set_cell_text,
)


_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")


def _norm_weekday(value) -> str:
    """星期归一：'二'/'2'/'星期二' → '星期二'；空 → ''。"""
    s = str(value or "").strip()
    if not s:
        return ""
    if s.startswith("星期"):
        return s
    if s in _WEEKDAY_CN:
        return f"星期{s}"
    # 数字 1-7
    try:
        n = int(s)
        if 1 <= n <= 7:
            return f"星期{_WEEKDAY_CN[n - 1]}"
    except ValueError:
        pass
    return s


def _shown_dt(value, with_time: bool = False) -> str:
    """datetime/date/ISO → 'YYYY年M月D日' 或带时分。"""
    v = _coerce_datetime(value)
    if not hasattr(v, "strftime"):
        return str(value or "")
    if with_time:
        return v.strftime("%Y年%m月%d日 %H时")
    return v.strftime("%Y年%m月%d日")


def _span_text(start, end, with_time: bool = False) -> str:
    """起止时间 → 一段可读文字。"""
    s = _coerce_datetime(start)
    e = _coerce_datetime(end)
    if s and e:
        if with_time:
            return f"{s.strftime('%Y年%m月%d日 %H:%M')} 至 {e.strftime('%Y年%m月%d日 %H:%M')}"
        return f"{s.strftime('%Y年%m月%d日')} 至 {e.strftime('%Y年%m月%d日')}"
    if s:
        return _shown_dt(s, with_time)
    if e:
        return _shown_dt(e, with_time)
    return ""


def _cell_at(table, ri: int, cj: int) -> _Cell | None:
    """按行号/网格列号取单元格（容错）。"""
    try:
        return _Cell(table._tbl.findall(qn("w:tr"))[ri].findall(qn("w:tc"))[cj], table)
    except (IndexError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# 市政监-24 监理日志（15 行 × 4 列）
# ---------------------------------------------------------------------------

def fill_shijian_24_rizhi(doc, data: dict) -> None:
    """市政监-24 监理日志。

    头部工程名称/施工单位为「标签｜值」分列；日期/星期/天气/气温为 append 模式；
    R4-R6 三行作业面明细；R7-R13 七个整行大内容区（标签后插正文）；
    R14 其它重要事项 + 总监/填表人/日期 同格。
    """
    table = doc.tables[0]

    # 工程名称 / 施工单位（R1：col0 标签、col1 值；col2 标签、col3 值）
    _set_cell_text(table.rows[1].cells[1], str(data.get("project_name") or ""))
    _set_cell_text(table.rows[1].cells[3], str(data.get("construction_unit") or ""))

    # 日期 / 星期 / 天气 / 气温（R2：每列均为「标签：」+ 同格追加值）
    date_val = _coerce_datetime(data.get("date") or data.get("record_date"))
    r2 = table.rows[2]
    if hasattr(date_val, "strftime"):
        _insert_after_text(r2.cells[0].paragraphs, "日期：", _shown_dt(date_val))
        wd = _norm_weekday(data.get("weekday") or _WEEKDAY_CN[date_val.weekday()])
        if wd:
            _insert_after_text(r2.cells[1].paragraphs, "星期：", wd)
    weather = str(data.get("weather") or "").strip()
    if weather:
        _insert_after_text(r2.cells[2].paragraphs, "天气：", weather)
    # 模板 R2 col3 本身已含「℃」占位（最高气温：℃   最低气温：℃），
    # 此处只追加数值，避免写成「28℃℃」。
    high = str(data.get("temperature_high") or "").strip()
    low = str(data.get("temperature_low") or "").strip()
    if high:
        _insert_after_text(r2.cells[3].paragraphs, "最高气温：", high)
    if low:
        _insert_after_text(r2.cells[3].paragraphs, "最低气温：", low)

    # 作业面明细（R4-R6，最多三行）
    surfaces = data.get("work_surfaces") or []
    if isinstance(surfaces, dict):
        surfaces = [surfaces]
    for off in range(3):
        row = table.rows[4 + off]
        item = surfaces[off] if off < len(surfaces) else {}
        if not isinstance(item, dict):
            item = {}
        if item.get("surface") or item.get("progress") or item.get("workers") or item.get("machinery"):
            _set_cell_text(row.cells[0], str(item.get("surface") or ""))
            _set_cell_text(row.cells[1], str(item.get("progress") or ""))
            _set_cell_text(row.cells[2], str(item.get("workers") or ""))
            _set_cell_text(row.cells[3], str(item.get("machinery") or ""))

    # 七个整行大内容区（标签后插正文）
    content_areas = (
        (7, "现场巡视发现问题及处理情况：", "content_inspection"),
        (8, "工程质量情况：", "content_quality"),
        (9, "工程材料、大型机械进退场检查情况、材料试件见证取样送检情况：", "content_material"),
        (10, "安全、文明施工情况：", "content_safety"),
        (11, "重要人员往来、指示或通知：", "content_personnel"),
        (12, "监理主要工作、活动（会议、指令、文件、检查等）、存在问题；", "content_supervision"),
        (13, "设计变更、工程洽商情况：", "content_change"),
    )
    for ri, label, key in content_areas:
        val = data.get(key)
        if val:
            _append_after_label(table.rows[ri].cells[0], label, str(val))

    # 其它重要事项 + 总监/填表人/日期（R14 同格）
    other = str(data.get("content_other") or "").strip()
    r14 = table.rows[14]
    if other:
        _append_after_label(r14.cells[0], "其它重要事项：", other)
    chief = str(data.get("chief_supervisor") or data.get("supervisor") or "").strip()
    if chief:
        _fill_signature_inline(r14.cells[0], "总监理工程师/总监代表：", chief)
    recorder = str(data.get("recorder") or data.get("recorder_name") or "").strip()
    if recorder:
        _fill_signature_inline(r14.cells[0], "填表人：", recorder)
    if hasattr(date_val, "strftime"):
        _fill_date_cell(r14.cells[0], date_val)


# ---------------------------------------------------------------------------
# 市政监-9 旁站监理记录表（10 行 × 13 列）
# ---------------------------------------------------------------------------

def fill_shijian_9_pangzhan(doc, data: dict) -> None:
    """市政监-9 旁站监理记录表。

    R0 单位工程名称（整行合并，append）；R1 旁站日期(年 月 日)/星期/旁站时气候
    三组合并列；R2-R8 整行大单元格；R9 旁站监理人员（签名）+ 年月日。
    """
    table = doc.tables[0]
    trs = table._tbl.findall(qn("w:tr"))

    # R0 单位（子单位）工程名称
    _insert_after_text(table.rows[0].cells[0].paragraphs,
                       "单位（子单位）工程名称：", str(data.get("project_name") or ""))

    # R1：col0 旁站日期：标签 | col1-4 年 月 日占位 | col5-8 星期： | col9-12 旁站时气候：
    date_val = _coerce_datetime(data.get("station_date") or data.get("date"))
    if hasattr(date_val, "strftime"):
        c = _cell_at(table, 1, 1)
        if c is not None:
            _fill_date_cell(c, date_val)
    wd = _norm_weekday(data.get("weekday") or (date_val.weekday() if hasattr(date_val, "weekday") else None))
    if wd:
        c = _cell_at(table, 1, 5)
        if c is not None:
            _insert_after_text(c.paragraphs, "星期：", wd)
    climate = str(data.get("weather") or data.get("climate") or "").strip()
    if climate:
        c = _cell_at(table, 1, 9)
        if c is not None:
            _insert_after_text(c.paragraphs, "旁站时气候：", climate)

    # R2-R8 大单元格（标签后插正文）
    big_rows = (
        (2, "旁站监理的部位或工序：", "station_part"),
        (3, "旁站监理时间：", "station_time"),
        (4, "施工情况：", "content"),
        (5, "监理情况：", "supervision"),
        (6, "发现问题：", "problems"),
        (7, "处理意见：", "handling"),
        (8, "备注：", "remark"),
    )
    # 旁站时间默认由起止时间合成
    if not data.get("station_time"):
        st = _span_text(data.get("start_time"), data.get("end_time"), with_time=True)
        if st:
            data = {**data, "station_time": st}
    for ri, label, key in big_rows:
        val = data.get(key)
        if val:
            _append_after_label(table.rows[ri].cells[0], label, str(val))

    # R9 旁站监理人员（签名）+ 年月日
    r9 = table.rows[9]
    sig = str(data.get("station_inspector_signature") or data.get("supervisor") or "").strip()
    if sig:
        _fill_signature_inline(r9.cells[0], "（签名）：", sig)
    if hasattr(date_val, "strftime"):
        _fill_date_cell(r9.cells[0], date_val)


# ---------------------------------------------------------------------------
# 市政监-11 巡视记录（5 行 × 4 列）
# ---------------------------------------------------------------------------

def fill_shijian_11_xunshi(doc, data: dict) -> None:
    """市政监-11 巡视记录。

    R0 巡视的工程部位（标签｜值）施工单位（标签｜值）；R1 巡视时间（年 月 日 时 至 …）；
    R2 巡视内容；R3 巡视发现的问题及处理情况；R4 问题处理情况。
    """
    table = doc.tables[0]
    _set_cell_text(table.rows[0].cells[1], str(data.get("section") or ""))
    _set_cell_text(table.rows[0].cells[3], str(data.get("construction_unit") or ""))

    # R1 巡视时间占位（合并单元格：年 月 日 时 至 年 月 日 时）
    span = _span_text(data.get("start_time"), data.get("end_time"), with_time=True)
    if not span:
        span = str(data.get("time") or "")
    if span:
        _set_cell_text(table.rows[1].cells[1], span)

    _append_after_label(table.rows[2].cells[0], "巡视内容：", str(data.get("content") or ""))
    _append_after_label(table.rows[3].cells[0], "巡视发现的问题及处理情况：",
                        str(data.get("problems") or ""))
    _append_after_label(table.rows[4].cells[0], "问题处理情况：", str(data.get("handling") or ""))


# ---------------------------------------------------------------------------
# AQSZ-J-2.2 安全监理日志（10 行 × 1 列）
# ---------------------------------------------------------------------------

def fill_aqsz_2_2_anquan_rizhi(doc, data: dict) -> None:
    """AQSZ-J-2.2 安全监理日志。

    工程名称 / 日期(含星期) / 天气(上午下午夜间) / 施工内容 / 安全检查情况 /
    处理措施 / 隐患整改情况 / 危大安全状况 / 其他 / 安全监理员 / 总监(审阅日期)。
    """
    table = doc.tables[0]
    _insert_after_text(table.rows[0].cells[0].paragraphs, "工程名称：",
                       str(data.get("project_name") or ""))

    date_val = _coerce_datetime(data.get("date") or data.get("record_date"))
    r1 = table.rows[1]
    if hasattr(date_val, "strftime"):
        _fill_date_cell(r1.cells[0], date_val)
        wd = _norm_weekday(data.get("weekday") or _WEEKDAY_CN[date_val.weekday()])
        if wd:
            _insert_after_text(r1.cells[0].paragraphs, "星期：", wd)
    am = str(data.get("weather_am") or data.get("weather") or "").strip()
    pm = str(data.get("weather_pm") or data.get("weather") or "").strip()
    night = str(data.get("weather_night") or "").strip()
    if am:
        _insert_after_text(r1.cells[0].paragraphs, "上午：", am)
    if pm:
        _insert_after_text(r1.cells[0].paragraphs, "下午：", pm)
    if night:
        _insert_after_text(r1.cells[0].paragraphs, "夜间：", night)

    sections = (
        (2, "施工内容：", ("content", "work_content")),
        (3, "安全检查情况（包括巡视监理情况）：", ("safety_check", "inspection_note", "content")),
        (4, "处理措施（限期整改、暂时停工、上报建设单位或有关部门）：", ("treatment", "handling")),
        (5, "施工单位隐患整改情况：", ("rectify_status", "rectification")),
        (6, "危险性较大分部分项工程安全状况：", ("hazard_status", "weida_status")),
        (7, "其他：", ("other", "remark")),
    )
    for ri, label, keys in sections:
        val = next((data.get(k) for k in keys if data.get(k)), None)
        if val:
            _append_after_label(table.rows[ri].cells[0], label, str(val))

    # 安全监理员（签名）
    safety = str(data.get("safety_supervisor") or data.get("supervisor") or "").strip()
    if safety:
        _fill_signature_inline(table.rows[8].cells[0], "安全监理员：", safety)
    # 总监/总监代表（签名）+ 审阅日期
    chief = str(data.get("chief_supervisor") or "").strip()
    if chief:
        _fill_signature_inline(table.rows[9].cells[0], "总监/总监代表：", chief)
    review = _coerce_datetime(data.get("review_date") or data.get("date") or data.get("record_date"))
    if hasattr(review, "strftime"):
        _fill_date_cell(table.rows[9].cells[0], review)


# ---------------------------------------------------------------------------
# AQSZ-J-2.5-1~7 危大工程监理巡视检查记录表（通用填充器）
# 7 张模板结构相同，仅检查项清单（来自模板本身）不同；一张填充器服务全部。
# ---------------------------------------------------------------------------

def _mark_cell_checkbox(cell: _Cell) -> bool:
    """把单元格里的首个「□」勾成「☑」。"""
    for p in cell.paragraphs:
        for run in p.runs:
            if "□" in run.text:
                run.text = run.text.replace("□", "☑", 1)
                return True
    return False


def _result_col(result) -> int | None:
    """把结论映射到 □ 勾选列：符合/是→2，不符合/否→3，其他→4。"""
    r = str(result or "").strip()
    if not r:
        return None
    if r.startswith("符合") or r == "是":
        return 2
    if r.startswith("不符合") or r == "否":
        return 3
    if r.startswith("其他"):
        return 4
    return None


def fill_aqsz_weida(doc, data: dict) -> None:
    """AQSZ-J-2.5-1~7 危大巡视表（通用）。

    R0 工程名称/施工单位名称/巡视日期（同格 append）；R1 分部分项工程名称/巡视部位；
    R2 检查表头；其后为检查项行（符合/不符合/其他 三列 □）；
    末段 巡视监理人员意见 →（签名）+日期 → 总监理工程师意见 →（签名）+日期 → 注。
    """
    table = doc.tables[0]
    trs = table._tbl.findall(qn("w:tr"))
    n = len(trs)

    # 找出「巡视监理人员意见」所在行（从后往前定位，跳过注脚）
    opinion_row = None
    for ri in range(n - 1, 1, -1):
        txt = table.rows[ri].cells[0].text
        if "巡视监理人员意见" in txt:
            opinion_row = ri
            break
    if opinion_row is None:
        # 兜底：倒数第 4 行（意见/签名/总监意见/总监签名 之后是注）
        opinion_row = max(3, n - 4)

    # R0 / R1 头部
    _insert_after_text(table.rows[0].cells[0].paragraphs, "工程名称：",
                       str(data.get("project_name") or ""))
    _insert_after_text(table.rows[0].cells[2].paragraphs, "施工单位名称：",
                       str(data.get("construction_unit") or ""))
    insp = _coerce_datetime(data.get("inspection_date") or data.get("date"))
    if hasattr(insp, "strftime"):
        c = _cell_at(table, 0, 3)
        if c is not None:
            _fill_date_cell(c, insp)

    _insert_after_text(table.rows[1].cells[0].paragraphs, "分部分项工程名称：",
                       str(data.get("hazard_type") or data.get("hazard_name") or ""))
    _insert_after_text(table.rows[1].cells[2].paragraphs, "巡视部位：",
                       str(data.get("section") or ""))

    # 检查项结论：支持 checklist=[{item,result}] 或 checklist_results=[result...]
    checklist = data.get("checklist") or []
    results_list = data.get("checklist_results") or []
    if isinstance(checklist, list):
        item_result = {
            str(c.get("item") or "").strip(): str(c.get("result") or "").strip()
            for c in checklist if isinstance(c, dict) and c.get("item")
        }
    else:
        item_result = {}

    item_start = 3
    for off, ri in enumerate(range(item_start, opinion_row)):
        item_text = table.rows[ri].cells[1].text if table.rows[ri].cells[1] is not None else ""
        res = item_result.get(str(item_text).strip())
        if not res and off < len(results_list):
            res = results_list[off]
        tcol = _result_col(res)
        if tcol is None:
            continue
        c = _cell_at(table, ri, tcol)
        if c is not None:
            _mark_cell_checkbox(c)

    # 意见与签名
    inspector_opinion = str(data.get("inspector_opinion") or data.get("opinion") or "").strip()
    if inspector_opinion:
        _append_after_label(table.rows[opinion_row].cells[0], "巡视监理人员意见：", inspector_opinion)
    sig_row = opinion_row + 1
    supervisor = str(data.get("supervisor") or data.get("inspector_signature") or "").strip()
    if supervisor:
        _fill_signature_inline(table.rows[sig_row].cells[0], "（签名）：", supervisor)
    if hasattr(insp, "strftime"):
        _fill_date_cell(table.rows[sig_row].cells[0], insp)

    chief_row = opinion_row + 2
    chief_opinion = str(data.get("chief_opinion") or "").strip()
    if chief_opinion:
        _append_after_label(table.rows[chief_row].cells[0], "总监理工程师意见：", chief_opinion)
    chief_sig_row = opinion_row + 3
    chief = str(data.get("chief_supervisor") or data.get("chief") or "").strip()
    if chief:
        _fill_signature_inline(table.rows[chief_sig_row].cells[0], "（签名）：", chief)
    review = _coerce_datetime(data.get("review_date") or data.get("date") or data.get("inspection_date"))
    if hasattr(review, "strftime"):
        _fill_date_cell(table.rows[chief_sig_row].cells[0], review)
