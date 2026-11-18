"""极简 Markdown → PDF：监理日志（纯 Markdown 产物）需要打印/盖章时按需转换。

不追求排版保真：日志正文本就是纯文本+章节标题，用 reportlab 逐行排即可。
图表/复杂语法不存在于该产物中，遇到未知标记按原样输出。
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

_CN_FONTS = [
    ("Microsoft YaHei", "C:/Windows/Fonts/msyh.ttc"),
    ("SimSun", "C:/Windows/Fonts/simsun.ttc"),
    ("SimHei", "C:/Windows/Fonts/simhei.ttf"),
    ("Noto Sans CJK SC", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    # 生产容器（rokid-backend）实测装有 wqy-zenhei.ttc（16MB 中文字体）
    ("WenQuanYi ZenHei", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ("WenQuanYi MicroHei", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
]

_font_name = "Helvetica"


def _register_font() -> None:
    global _font_name
    for name, path in _CN_FONTS:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                _font_name = name
                return
            except Exception:  # noqa: BLE001 - 换下一个候选字体
                continue


def markdown_to_pdf(markdown_text: str, output_path: str) -> Path:
    """把监理日志 Markdown 正文排成 A4 PDF（标题加粗、条目缩进、正文折行）。"""
    _register_font()
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="监理日志",
    )
    body = ParagraphStyle(
        "body", fontName=_font_name, fontSize=10.5, leading=17, spaceAfter=4
    )
    h1 = ParagraphStyle("h1", parent=body, fontName=_font_name, fontSize=16, leading=22, spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=body, fontName=_font_name, fontSize=12.5, leading=18, spaceBefore=6, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=body, fontName=_font_name, fontSize=11, leading=16, spaceBefore=4)
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=14)

    story: list = []
    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            story.append(Spacer(1, 4))
            continue
        text = (
            line.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        if line.startswith("# "):
            story.append(Paragraph(text[2:].strip(), h1))
        elif line.startswith("## "):
            story.append(Paragraph(text[3:].strip(), h2))
        elif line.startswith("### "):
            story.append(Paragraph(text[4:].strip(), h3))
        elif line.startswith("- ") or line.startswith("* "):
            story.append(Paragraph("• " + text[2:].strip(), bullet))
        else:
            story.append(Paragraph(text, body))
    doc.build(story)
    return Path(output_path)
