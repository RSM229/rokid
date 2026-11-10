"""监理文书 PDF 生成服务 - 基于 reportlab"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pathlib import Path
from typing import Any
from datetime import date as date_type
import re
from xml.sax.saxutils import escape as _xml_escape


# 中文字体注册（reportlab 默认 Helvetica 不含中文，且不支持 CFF/PostScript 轮廓，
# 需使用 TrueType 字形的中文字体）
CJK_FONT_CANDIDATES = [
    "simhei.ttf",
    "DroidSansFallbackFull.ttf",
    "wqy-microhei.ttc",
]

# 内置字体目录（随代码分发，不受 exports/ 被 gitignore 影响）
APP_FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# 系统级常见中文字体路径（防兜底）：Windows / Linux
_SYSTEM_CJK_FONT_PATHS = [
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simsun.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
]


def _find_cjk_font() -> Path | None:
    """依次在导出目录字体子目录、内置字体目录、系统常见路径查找中文字体"""
    try:
        from app.config import settings
    except Exception:
        settings = None
    search_roots = []
    if settings is not None:
        search_roots.append(settings.export_dir / "fonts")
    search_roots.append(APP_FONT_DIR)
    for root in search_roots:
        for name in CJK_FONT_CANDIDATES:
            candidate = root / name
            if candidate.exists():
                return candidate
    for p in _SYSTEM_CJK_FONT_PATHS:
        if p.exists():
            return p
    return None


def _register_cjk_fonts() -> bool:
    """注册 TrueType 中文字体，返回是否成功"""
    try:
        font_path = _find_cjk_font()
        if font_path is None:
            return False
        subfont_index = 0
        if str(font_path).lower().endswith(".ttc"):
            subfont_index = 0
        try:
            pdfmetrics.registerFont(TTFont("NotoSC", str(font_path), subfontIndex=subfont_index))
        except pdfmetrics.AlreadyRegistered:
            pass
        try:
            pdfmetrics.registerFont(TTFont("NotoSC-Bold", str(font_path), subfontIndex=subfont_index))
        except pdfmetrics.AlreadyRegistered:
            pass
        return True
    except Exception:
        return False


_CJK_READY = _register_cjk_fonts()
FONT = "NotoSC" if _CJK_READY else "Helvetica"
FONT_BOLD = "NotoSC-Bold" if _CJK_READY else "Helvetica-Bold"


# 文书类型中文映射
DOC_TYPE_TITLES = {
    "旁站记录": "旁站记录",
    "监理日志": "监理日志",
    "监理工程师通知单（质量类）": "监理工程师通知单",
    "安全隐患整改通知单": "安全隐患整改通知单",
    "巡视记录": "巡视记录",
    "见证取样记录": "见证取样记录",
    "监理周报": "监理周报",
    "监理月报": "监理月报",
}


def _get_styles():
    """获取样式"""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="DocTitle",
        parent=styles["Title"],
        fontSize=18,
        fontName=FONT_BOLD,
        spaceAfter=20,
        alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        name="DocHeader",
        parent=styles["Normal"],
        fontSize=10,
        fontName=FONT,
        alignment=TA_CENTER,
        textColor=colors.grey,
        spaceAfter=10,
    ))
    styles.add(ParagraphStyle(
        name="DocBody",
        parent=styles["Normal"],
        fontSize=11,
        fontName=FONT,
        leading=18,
        spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="DocLabel",
        parent=styles["Normal"],
        fontSize=11,
        fontName=FONT,
        leading=18,
        textColor=colors.HexColor("#333333"),
        spaceAfter=4,
    ))
    return styles


def _build_content_table(content: dict, styles) -> list:
    """根据文书内容构建「标签 | 值」表格行（仅短字段）。"""
    return _split_content(content, styles)[0]


# 「标签 | 值」两列表格里只放短字段。整段文字（LLM 生成的周报摘要、月报正文等）
# 必须由 story 里的独立段落承载：reportlab 的表格只能**按行**分页，某一行一旦
# 高过一页纸就直接抛 LayoutError（周报/月报 PDF 500 的真实原因）。
SHORT_VALUE_MAX = 60

# 这些键天然是多段/列表内容，无论长短一律走独立段落
BLOCK_LIST_KEYS = (
    ("evidence", "证据"),
    ("recheck_suggestion", "复查建议"),
    ("regulation_refs", "规范依据"),
)

CONTENT_LABELS = {
    "doc_number": "编号",
    "date": "日期",
    "time": "时间",
    "project_name": "工程项目名称",
    "supervisor": "监理人员",
    "supervisor_level": "监理级别",
    "section": "楼栋/楼层/部位",
    "location": "位置",
    "weather": "天气",
    "hazard_description": "隐患描述",
    "deadline": "整改期限",
    "summary": "施工进展情况",
    "details": "监理工作情况",
    "body": "正文",
    "reason": "事由",
    "construction_unit": "施工单位",
    "material_name": "见证材料名称",
    "material_spec": "规格型号",
    "manufacturer": "生产厂家",
    "batch_no": "批次号",
    "acceptance_type": "验收类型",
    "acceptance_content": "验收内容",
    "remark": "备注",
    "record": "检查记录", "method": "检查方法", "basis": "检查依据",
    "conclusion": "检查结论", "handling": "处理记录", "problems": "发现的问题及处理情况",
    "content": "记录内容", "treatment": "处理措施", "worker_count": "施工人数",
    "start_time": "开始时间", "end_time": "结束时间", "station_process": "旁站关键工序",
    "station_part": "旁站关键部位", "supervision_status": "旁站监理情况",
    "acceptance_result": "验收结论", "acceptance_participants": "验收单位及人员",
    "supervision_org": "项目监理机构", "supervision_unit": "监理单位",
    "review_date": "审阅日期",
}


def paragraph_text(value: Any) -> str:
    """把任意值转成 reportlab Paragraph 可用的标记文本。

    1. 先做 XML 转义 —— 正文里出现裸 `&`/`<`/`>` 会让 Paragraph 解析直接抛异常；
    2. 再把极简 Markdown 转成 reportlab 标签（LLM 生成的周报/月报正文是 Markdown
       风格，不转换的话 PDF 里全是裸 `**`）。
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = _xml_escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)                 # **粗体**
    text = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*(.+?)[ \t]*$", r"<b>\1</b>", text)  # ### 标题
    text = re.sub(r"(?m)^[ \t]*-{3,}[ \t]*$", "", text)                 # --- 分隔线
    text = re.sub(r"(?m)^[ \t]*[-*][ \t]+", "• ", text)                 # - 列表
    text = text.strip()
    return text.replace("\n", "<br/>")


def _flatten(value: Any) -> str:
    """把列表/元组拼成一行文本，其余转 str。"""
    if isinstance(value, (list, tuple)):
        return "；".join(_flatten(v) for v in value if v not in (None, ""))
    return "" if value is None else str(value)


def _is_long_block(text: str) -> bool:
    return len(text) > SHORT_VALUE_MAX or "\n" in text


def _split_content(content: dict, styles) -> tuple[list, list]:
    """把文书内容拆成 (短字段表格行, 长文本段落块)。

    长文本块形如 [(标题, 正文), ...]，由调用方作为独立段落追加进 story，
    这样超长内容可以正常跨页，不会把表格行撑爆。
    """
    short_rows: list = []
    blocks: list = []

    for key, label in CONTENT_LABELS.items():
        raw = content.get(key, "")
        if raw in (None, "", [], {}):
            continue
        text = _flatten(raw).strip()
        if not text:
            continue
        if _is_long_block(text):
            blocks.append((label, text))
        else:
            short_rows.append([
                Paragraph(f"<b>{label}</b>", styles["DocLabel"]),
                Paragraph(paragraph_text(text), styles["DocBody"]),
            ])

    for key, label in BLOCK_LIST_KEYS:
        vals = content.get(key)
        if not vals:
            continue
        if isinstance(vals, (list, tuple)):
            text = "\n".join(f"• {_flatten(v)}" for v in vals if _flatten(v))
        else:
            text = _flatten(vals)
        if text.strip():
            blocks.append((label, text))

    return short_rows, blocks


def generate_pdf(doc, output_path: str):
    """生成监理文书 PDF

    Args:
        doc: Document ORM 对象
        output_path: PDF 输出路径
    """
    if doc.doc_type == "监理日志":
        return generate_daily_log_pdf(doc, output_path)
    styles = _get_styles()
    doc_type = doc.doc_type
    content = doc.content or {}
    title = DOC_TYPE_TITLES.get(doc_type, doc_type)

    pdf_doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    story = []

    # 标题
    story.append(Paragraph(paragraph_text(title), styles["DocTitle"]))
    story.append(Paragraph(f"编号：{paragraph_text(content.get('doc_number', ''))}", styles["DocHeader"]))
    story.append(Spacer(1, 10 * mm))

    # 内容：短字段走两列表格，长文本走独立段落（可跨页，避免整行超页 LayoutError）
    short_rows, blocks = _split_content(content, styles)
    if short_rows:
        table = Table(short_rows, colWidths=[40 * mm, 120 * mm], splitInRow=1)
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(table)

    for label, text in blocks:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(f"<b>{label}</b>", styles["DocLabel"]))
        story.append(Paragraph(paragraph_text(text), styles["DocBody"]))

    story.append(Spacer(1, 10 * mm))

    # 关联影像
    attached_images = doc.attached_images or content.get("attached_images", [])
    if attached_images:
        story.append(Paragraph("<b>关联影像</b>", styles["DocLabel"]))
        story.append(Spacer(1, 5))
        for img_url in attached_images[:4]:  # 最多展示4张
            img_path = _resolve_image_path(img_url)
            if img_path and img_path.exists():
                try:
                    img = RLImage(str(img_path), width=80 * mm, height=60 * mm)
                    story.append(img)
                    story.append(Spacer(1, 5))
                except Exception:
                    pass

    story.append(Spacer(1, 15 * mm))

    # 签名区
    sig_data = [
        [Paragraph("专业监理工程师（签名）：", styles["DocBody"]),
         Paragraph("总监理工程师（签名）：", styles["DocBody"])],
        [Paragraph("&nbsp;", styles["DocBody"]),
         Paragraph("&nbsp;", styles["DocBody"])],
        [Paragraph(f"日期：{paragraph_text(content.get('date', ''))}", styles["DocBody"]),
         Paragraph(paragraph_text(f"审阅日期：{content.get('review_date', '')}"), styles["DocBody"])],
    ]
    sig_table = Table(sig_data, colWidths=[80 * mm, 80 * mm])
    sig_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(sig_table)

    pdf_doc.build(story)


def _resolve_image_path(img_url: str) -> Path | None:
    """将 /uploads/xxx 路径解析为文件系统路径"""
    if not img_url:
        return None
    from app.config import settings
    if img_url.startswith("/uploads/"):
        relative = Path(img_url.removeprefix("/uploads/"))
        candidate = (settings.upload_dir / relative).resolve()
        try:
            candidate.relative_to(settings.upload_dir.resolve())
        except ValueError:
            return None
        return candidate
    if img_url.startswith("/exports/"):
        relative = Path(img_url.removeprefix("/exports/"))
        candidate = (settings.export_dir / relative).resolve()
        try:
            candidate.relative_to(settings.export_dir.resolve())
        except ValueError:
            return None
        return candidate
    return Path(img_url) if Path(img_url).exists() else None


# ===== 监理日志报告 PDF 生成 =====

def _get_log_styles():
    """获取监理日志专用样式"""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="LogTitle",
        parent=styles["Title"],
        fontSize=16,
        fontName=FONT_BOLD,
        spaceAfter=6,
        alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        name="LogHeader",
        parent=styles["Normal"],
        fontSize=10,
        fontName=FONT,
        alignment=TA_CENTER,
        textColor=colors.grey,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="LogSectionTitle",
        parent=styles["Normal"],
        fontSize=11,
        leading=16,
        fontName=FONT_BOLD,
        textColor=colors.HexColor("#1890ff"),
        spaceBefore=8,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="LogSubTitle",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        fontName=FONT_BOLD,
        textColor=colors.HexColor("#333333"),
        spaceBefore=4,
        spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="LogBody",
        parent=styles["Normal"],
        fontSize=10,
        fontName=FONT,
        leading=14,
        spaceAfter=2,
    ))
    return styles


def _log_title_for(doc) -> str:
    """监理日记复用日志自绘版式，但抬头必须区分（回退路径同样不能串味）。"""
    is_diary = (
        getattr(doc, "doc_type", "") == "监理日记"
        or getattr(doc, "form_code", "") == "jianli_riji"
    )
    return "监理日记" if is_diary else "监理日志"


def generate_daily_log_pdf(doc, output_path: str, target_date: date_type = None):
    """生成监理日志报告 PDF

    按照标准监理日志报告模板生成，包含：
    - 表头：工程项目名称、日期、星期、气温、天气、编号
    - 当日施工进展情况
    - 当日监理工作情况（4个子项）
    - 当日存在问题及处理情况
    - 其他有关事项（5个子项）
    - 签名区

    Args:
        doc: Document ORM对象，content中包含日志数据
        output_path: PDF输出路径
        target_date: 目标日期
    """
    styles = _get_log_styles()
    content = doc.content or {}

    pdf_doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    story = []

    # ===== 标题 =====
    # 自绘版式是省统表 docx→PDF 失败时的回退路径，日记同样要抬头正确，
    # 否则回退时又变回「监理日志」（2026-11-10 修复）。
    story.append(Paragraph(_log_title_for(doc), styles["LogTitle"]))

    # ===== 表头信息表格 =====
    header_data = [
        [
            Paragraph(f"<b>工程项目名称</b>", styles["LogBody"]),
            Paragraph(paragraph_text(content.get("project_name", "")), styles["LogBody"]),
            Paragraph(f"<b>编号</b>", styles["LogBody"]),
            Paragraph(paragraph_text(content.get("doc_number", "")), styles["LogBody"]),
        ],
        [
            Paragraph(f"<b>日期</b>", styles["LogBody"]),
            Paragraph(paragraph_text(content.get("date", "")), styles["LogBody"]),
            Paragraph(f"<b>星期</b>", styles["LogBody"]),
            Paragraph(paragraph_text(content.get("weekday", "")), styles["LogBody"]),
        ],
        [
            Paragraph(f"<b>气温</b>", styles["LogBody"]),
            Paragraph(paragraph_text(f"最高：{content.get('temperature_high', '')}℃  最低：{content.get('temperature_low', '')}℃"), styles["LogBody"]),
            Paragraph(f"<b>天气</b>", styles["LogBody"]),
            Paragraph(paragraph_text(f"上午：{content.get('weather_morning', '')}  下午：{content.get('weather_afternoon', '')}  晚上：{content.get('weather_night', '')}"), styles["LogBody"]),
        ],
    ]
    header_table = Table(header_data, colWidths=[25 * mm, 65 * mm, 20 * mm, 60 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f5ff")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f0f5ff")),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 6 * mm))

    # ===== 1. 当日施工进展情况 =====
    story.append(Paragraph("一、当日施工进展情况", styles["LogSectionTitle"]))
    progress_text = content.get("construction_progress", "当日无施工记录。")
    for line in progress_text.split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))
    story.append(Spacer(1, 3 * mm))

    # ===== 2. 当日监理工作情况 =====
    story.append(Paragraph("二、当日监理工作情况", styles["LogSectionTitle"]))

    # 2.1 工程材料/构配件/设备进场报审、见证取样送检情况
    story.append(Paragraph("1. 工程材料/构配件/设备进场报审、见证取样送检情况：", styles["LogSubTitle"]))
    for line in content.get("material_section", "  无。").split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))

    # 2.2 旁站、巡视、平行检验情况
    story.append(Paragraph("2. 旁站、巡视、平行检验情况：", styles["LogSubTitle"]))
    for line in content.get("supervision_section", "  无。").split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))

    # 2.3 验收、工程实体抽测情况
    story.append(Paragraph("平行检查：", styles["LogSubTitle"]))
    for line in content.get("parallel_section", "当日无平行检查记录。").split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))
    story.append(Paragraph("3. 验收、工程实体抽测情况：", styles["LogSubTitle"]))
    for line in content.get("acceptance_section", "  无。").split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))

    # 2.4 其他
    story.append(Paragraph("4. 其他：", styles["LogSubTitle"]))
    for line in content.get("other_work_section", "  无。").split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))

    story.append(Spacer(1, 3 * mm))

    # ===== 3. 当日存在问题及处理情况 =====
    story.append(Paragraph("三、当日存在问题及处理情况", styles["LogSectionTitle"]))
    problems_text = content.get("problems_section", "  当日无安全隐患及质量问题。")
    for line in problems_text.split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))
    story.append(Spacer(1, 3 * mm))

    # ===== 4. 其他有关事项 =====
    story.append(Paragraph("四、其他有关事项", styles["LogSectionTitle"]))

    story.append(Paragraph("1. 会议：", styles["LogSubTitle"]))
    for line in content.get("meeting_section", "  无。").split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))

    story.append(Paragraph("2. 重要发文：", styles["LogSubTitle"]))
    for line in content.get("document_section", "  无。").split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))

    story.append(Paragraph("3. 闭合记录：", styles["LogSubTitle"]))
    for line in content.get("closure_section", "  无。").split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))

    story.append(Paragraph("4. 资料上报：", styles["LogSubTitle"]))
    for line in content.get("report_section", "  无。").split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))

    story.append(Paragraph("5. 其他：", styles["LogSubTitle"]))
    for line in content.get("misc_section", "  无。").split("\n"):
        story.append(Paragraph(paragraph_text(line), styles["LogBody"]))

    story.append(Spacer(1, 10 * mm))

    # ===== 签名区 =====
    sig_data = [
        [Paragraph("专业监理工程师（签名）：", styles["LogBody"]),
         Paragraph("总监理工程师（签名）：", styles["LogBody"])],
        [Paragraph("&nbsp;", styles["LogBody"]),
         Paragraph("&nbsp;", styles["LogBody"])],
        [Paragraph(paragraph_text(f"日期：{content.get('date', '')}"), styles["LogBody"]),
         Paragraph(paragraph_text(f"审阅日期：{content.get('review_date', '')}"), styles["LogBody"])],
    ]
    sig_table = Table(sig_data, colWidths=[80 * mm, 80 * mm])
    sig_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(sig_table)

    pdf_doc.build(story)
