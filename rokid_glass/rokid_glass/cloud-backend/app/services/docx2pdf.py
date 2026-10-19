"""docx → PDF 通用转换（reportlab 复刻版式）。

背景：此前 /document/{id}/export-pdf 走 generate_pdf() 自绘键值列表，
而 /export-docx 走 render_form() 的省统表模板填充 —— 同一份文书两种版式。
本模块让 PDF 与 Word 同源：把省统表模板渲染出的 docx 直接转成 PDF。

支持：正文段落（对齐/字号/加粗）+ 顶层表格（列宽 / gridSpan / vMerge 纵向合并）+ 内嵌图片（add_picture 的 w:drawing，按 wp:extent 定尺寸、居中）。
不支持（省统表模板用不到，静默忽略）：嵌套表格、页眉页脚、脚注。
"""
from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph as RLParagraph
from reportlab.platypus import SimpleDocTemplate, Spacer
from reportlab.platypus import Table as RLTable
from reportlab.platypus import TableStyle

logger = logging.getLogger(__name__)

_FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"  # app/fonts（容器 bind mount）
_FONT_FILES = {
    # 注册名: (文件名候选, 说明)
    "DocHei": ["simhei.ttf"],
    "DocSong": ["simsun.ttc", "simsun.ttf"],   # ttc 取第一个 face（SimSun）
    "DocFang": ["simfang.ttf"],
}
_FALLBACK_DIRS = [
    _FONT_DIR,
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/simhei"),
    Path("/usr/share/fonts"),
]
_FONT_NAME = "DocSong"  # 正文默认：宋体（与真样例/模板默认一致）
_MAP_EXPLICIT = {"hei": "DocHei", "song": "DocSong", "fang": "DocFang", "kai": "DocFang"}

_ALIGN_MAP = {1: TA_CENTER, 2: TA_RIGHT}  # WD_ALIGN_PARAGRAPH: CENTER=1, RIGHT=2，其余左对齐


def _register_fonts() -> None:
    """注册黑体/宋体/仿宋；缺字库时就近回退，保证 <font> 标签不报错。"""
    resolved: dict[str, str] = {}
    for name, files in _FONT_FILES.items():
        for fname in files:
            for d in _FALLBACK_DIRS:
                cand = d / fname
                if cand.exists():
                    try:
                        kw = {"subfontIndex": 0} if fname.endswith(".ttc") else {}
                        pdfmetrics.registerFont(TTFont(name, str(cand), **kw))
                        resolved[name] = name
                        break
                    except Exception:  # noqa: BLE001
                        logger.warning("字体注册失败：%s", cand, exc_info=True)
            if name in resolved:
                break
    if "DocHei" not in resolved:
        raise RuntimeError("未找到中文字体 simhei.ttf")
    # 回退链：宋体缺→黑体，仿宋缺→宋体
    resolved.setdefault("DocSong", resolved["DocHei"])
    resolved.setdefault("DocFang", resolved["DocSong"])
    for name in ("DocHei", "DocSong", "DocFang"):
        target = resolved[name]
        if target == name:
            pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
    # 字体名映射表落到模块级，供 _map_font 使用
    global _FONT_ALIAS
    _FONT_ALIAS = {k: v for k, v in _MAP_EXPLICIT.items()}  # 逻辑名 → 注册名初值
    _FONT_ALIAS = {
        "hei": resolved["DocHei"],
        "song": resolved["DocSong"],
        "fang": resolved["DocFang"],
        "kai": resolved["DocFang"],
    }


_FONT_ALIAS: dict[str, str] = {}


def _map_font(east_asia: str | None) -> str:
    """docx 字体名 → reportlab 注册名；未声明按宋体（真样例/模板默认均为宋体）。"""
    key = (east_asia or "").lower()
    if "黑" in key or "hei" in key:
        return _FONT_ALIAS["hei"]
    if "仿" in key or "fang" in key:
        return _FONT_ALIAS["fang"]
    if "楷" in key or "kai" in key:
        return _FONT_ALIAS["kai"]
    return _FONT_ALIAS["song"]  # 宋体/SimSun/默认


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _para_text(tc_el) -> str:
    """单元格内所有段落文本，多段以换行拼接。"""
    parts = []
    for p_el in tc_el.findall(qn("w:p")):
        text = Paragraph(p_el, None).text or ""
        if text.strip():
            parts.append(text.rstrip())
    return "\n".join(parts)


def _run_font(p_el) -> str | None:
    """取 run 声明的中文字体（w:rFonts eastAsia，退而 ascii）。"""
    rpr = p_el.find(qn("w:rPr"))
    if rpr is None:
        return None
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        return None
    return rf.get(qn("w:eastAsia")) or rf.get(qn("w:ascii"))


def _run_is_bold(p_el) -> bool:
    rpr = p_el.find(qn("w:rPr"))
    if rpr is None:
        return False
    b = rpr.find(qn("w:b"))
    if b is None:
        return False
    return (b.get(qn("w:val")) or "1").lower() not in ("0", "false", "none")


def _para_markup(p_el) -> str:
    """段落 → reportlab 标记：按 run 的实际中文字体包 <font>，粗体包 <b>，换行转 <br/>。"""
    parts: list[str] = []
    for r in p_el.iter(qn("w:r")):
        text = "".join(t.text or "" for t in r.iter(qn("w:t")))
        # w:br / w:cr 换行
        brs = len(r.findall(qn("w:br"))) + len(r.findall(qn("w:cr")))
        if brs and not text:
            parts.append("<br/>" * brs)
            continue
        if not text:
            continue
        seg = _esc(text)
        font = _map_font(_run_font(r))
        if font != _FONT_NAME:
            seg = f'<font name="{font}">{seg}</font>'
        if _run_is_bold(r):
            seg = f"<b>{seg}</b>"
        parts.append(seg)
        if brs:
            parts.append("<br/>" * brs)
    return "".join(parts)


def _cell_markup(tc_el) -> str:
    """单元格：非空段落逐段取 run 级标记，段落间 <br/>。"""
    parts: list[str] = []
    for p_el in tc_el.findall(qn("w:p")):
        text = Paragraph(p_el, None).text or ""
        if not text.strip():
            continue
        parts.append(_para_markup(p_el))
    return "<br/>".join(parts) if parts else ""


def _run_size_pt(el, default: float) -> float:
    """取元素内第一个声明了字号的 run（w:sz 为半磅）。"""
    for sz in el.iter(qn("w:sz")):
        try:
            return int(sz.get(qn("w:val")) or "0") / 2.0
        except (TypeError, ValueError):
            continue
    return default


def _is_bold(p_el) -> bool:
    for rpr in p_el.iter(qn("w:rPr")):
        if rpr.find(qn("w:b")) is not None:
            b = rpr.find(qn("w:b"))
            val = (b.get(qn("w:val")) or "1").lower()
            if val not in ("0", "false", "none"):
                return True
    return False


def _images_in_paragraph(p_el, doc) -> list[tuple[bytes, int, int]]:
    """提取段落内嵌图片（w:drawing/a:blip），返回 (图片字节, 宽EMU, 高EMU)。"""
    out: list[tuple[bytes, int, int]] = []
    for blip in p_el.iter(qn("a:blip")):
        rid = blip.get(qn("r:embed"))
        if not rid:
            continue
        try:
            data = doc.part.related_parts[rid].blob
        except (KeyError, AttributeError):
            continue
        cx = cy = 0
        node = blip
        while node is not None and node.tag not in (qn("wp:inline"), qn("wp:anchor")):
            node = node.getparent()
        if node is not None:
            ext = node.find(qn("wp:extent"))
            if ext is not None:
                try:
                    cx, cy = int(ext.get("cx") or 0), int(ext.get("cy") or 0)
                except (TypeError, ValueError):
                    pass
        out.append((data, cx, cy))
    return out


def _image_flowable(data: bytes, cx: int, cy: int):
    """按 docx 里的显示尺寸（EMU）生成居中图片流对象；失败返回 None 跳过。"""
    try:
        buf = BytesIO(data)
        width = height = None
        if cx > 0 and cy > 0:
            width, height = cx / 914400.0 * 72, cy / 914400.0 * 72  # EMU → pt
            max_w = 165 * mm  # 页宽内
            if width > max_w:
                k = max_w / width
                width, height = width * k, height * k
        img = RLImage(buf, width=width, height=height) if width else RLImage(buf)
        img.hAlign = "CENTER"
        return img
    except Exception:
        logger.warning("docx→PDF 内嵌图片渲染失败，跳过该图", exc_info=True)
        return None


def _para_style(text: str, p_el, base: ParagraphStyle, seq: list) -> ParagraphStyle:
    size = _run_size_pt(p_el, base.fontSize)
    align = TA_LEFT
    ppr = p_el.find(qn("w:pPr"))
    if ppr is not None:
        jc = ppr.find(qn("w:jc"))
        if jc is not None:
            val = (jc.get(qn("w:val")) or "").lower()
            align = TA_CENTER if val in ("center", "both") else TA_RIGHT if val == "right" else TA_LEFT
    seq[0] += 1
    return ParagraphStyle(
        f"p{seq[0]}",
        parent=base,
        fontName=_FONT_NAME,
        fontSize=size,
        leading=size * 1.45,
        alignment=align,
    )


def _build_pdf_table(docx_table: DocxTable, cell_base: ParagraphStyle, seq: list) -> RLTable:
    tbl = docx_table._tbl
    grid = tbl.find(qn("w:tblGrid"))
    col_twips = []
    if grid is not None:
        for gc in grid.findall(qn("w:gridCol")):
            try:
                col_twips.append(int(gc.get(qn("w:w")) or "0"))
            except (TypeError, ValueError):
                col_twips.append(0)
    n_cols = max(len(col_twips), 1)
    rows_xml = tbl.findall(qn("w:tr"))
    n_rows = len(rows_xml)
    if not n_rows:
        return Spacer(1, 1)

    # 列宽：twip → mm（1 twip = 25.4/1440 mm），超宽等比缩到页宽内
    col_widths = [w * 25.4 / 1440.0 * mm for w in col_twips] or [180 * mm / n_cols] * n_cols
    total = sum(col_widths)
    avail = 180 * mm  # A4(210) - 左右各 15mm
    if total > avail > 0:
        k = avail / total
        col_widths = [w * k for w in col_widths]

    # 逐行分配网格坐标，处理 gridSpan（横向）与 vMerge（纵向）
    occupied: set[tuple[int, int]] = set()
    restarts: dict[tuple[int, int], dict] = {}  # (r, c) -> {'colspan','rowspan'}
    layout: list[list[tuple[int, int, str]]] = []  # 每行: (起始列, colspan, 文本)
    for r, tr in enumerate(rows_xml):
        row_cells: list[tuple[int, int, str]] = []
        c = 0
        for tc in tr.findall(qn("w:tc")):
            while (r, c) in occupied:
                c += 1
            tcpr = tc.find(qn("w:tcPr"))
            gs, vm = 1, None
            if tcpr is not None:
                g = tcpr.find(qn("w:gridSpan"))
                if g is not None:
                    try:
                        gs = max(1, int(g.get(qn("w:val")) or "1"))
                    except (TypeError, ValueError):
                        gs = 1
                v = tcpr.find(qn("w:vMerge"))
                if v is not None:
                    vm = (v.get(qn("w:val")) or "continue").lower()
            for cc in range(c, min(c + gs, n_cols + 8)):
                occupied.add((r, cc))
            if vm == "continue":
                # 归属到上方最近的同列 restart，纵向跨度 +1
                for rr in range(r - 1, -1, -1):
                    if (rr, c) in restarts:
                        restarts[(rr, c)]["rowspan"] += 1
                        break
                row_cells.append((c, gs, ""))
            else:
                restarts[(r, c)] = {"colspan": gs, "rowspan": 1}
                row_cells.append((c, gs, _cell_markup(tc)))
            c += gs
        layout.append(row_cells)

    # 组装 reportlab 数据矩阵与 SPAN 指令
    data: list[list] = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    spans = []
    for r, row_cells in enumerate(layout):
        for c, gs, text in row_cells:
            if r >= n_rows or c >= n_cols:
                continue
            info = restarts.get((r, c))
            if text:
                style = ParagraphStyle(
                    f"c{seq[0]}",
                    parent=cell_base,
                    fontSize=10.5,
                    leading=14.5,
                )
                seq[0] += 1
                data[r][c] = RLParagraph(text, style)
            if info:
                if info["colspan"] > 1 or info["rowspan"] > 1:
                    c1 = min(c + info["colspan"] - 1, n_cols - 1)
                    r1 = min(r + info["rowspan"] - 1, n_rows - 1)
                    if c1 > c or r1 > r:
                        spans.append(("SPAN", (c, r), (c1, r1)))

    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        *spans,
    ]
    return RLTable(data, colWidths=col_widths, style=TableStyle(style_cmds), hAlign="CENTER")


def convert_docx_to_pdf(docx_path: str, pdf_path: str) -> None:
    """把（省统表模板渲染出的）docx 转成版式一致的 PDF。

    Raises:
        RuntimeError: 中文字体缺失等致命错误（调用方应回退 generate_pdf）。
    """
    _register_fonts()
    doc = DocxDocument(docx_path)
    seq = [0]
    body_style = ParagraphStyle("body", fontName=_FONT_NAME, fontSize=12, leading=18)
    cell_style = ParagraphStyle("cell", fontName=_FONT_NAME, fontSize=10.5, leading=14.5)

    story = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, doc)
            imgs = _images_in_paragraph(child, doc)
            text = (p.text or "").strip()
            if not text and not imgs:
                story.append(Spacer(1, 1.5 * mm))
                continue
            if text:
                style = _para_style(text, child, body_style, seq)
                # 按 run 的实际字体/粗细生成标记（宋体默认，黑体/仿宋显式映射）
                story.append(RLParagraph(_para_markup(child), style))
                story.append(Spacer(1, 1 * mm))
            for img_data, cx, cy in imgs:
                flow = _image_flowable(img_data, cx, cy)
                if flow is not None:
                    story.append(flow)
                    story.append(Spacer(1, 2 * mm))
        elif child.tag == qn("w:tbl"):
            t = DocxTable(child, doc)
            story.append(_build_pdf_table(t, cell_style, seq))
            story.append(Spacer(1, 2 * mm))

    pdf = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="文书",
    )
    pdf.build(story)
