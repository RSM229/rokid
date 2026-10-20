"""台账导出服务 - Excel / PDF / 批量 ZIP"""
import io
import zipfile
from typing import List, Tuple

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

from app.services.doc_generator import FONT, paragraph_text


def export_ledger_bytes(records, ledger_type: str, ledger_name: str, fmt: str) -> Tuple[str, bytes]:
    """导出台账为内存字节，返回 (filename, bytes)。

    fmt: "excel" / "pdf"
    """
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    if fmt == "excel":
        filename = f"{ledger_name}_{timestamp}.xlsx"
        buf = io.BytesIO()
        _write_excel_to_buffer(records, ledger_type, ledger_name, buf)
        return filename, buf.getvalue()
    elif fmt == "pdf":
        filename = f"{ledger_name}_{timestamp}.pdf"
        buf = io.BytesIO()
        _write_pdf_to_buffer(records, ledger_type, ledger_name, buf)
        return filename, buf.getvalue()
    else:
        raise ValueError(f"不支持的格式: {fmt}")


def export_multiple(
    items: List[tuple],
    fmt: str = "excel",
) -> List[Tuple[str, bytes]]:
    """批量导出多份台账。

    Args:
        items: 每项为 (records, ledger_type, ledger_name)
        fmt: excel / pdf

    Returns:
        [(filename, bytes), ...]
    """
    results: List[Tuple[str, bytes]] = []
    for records, ledger_type, ledger_name in items:
        if not records:
            continue
        filename, data = export_ledger_bytes(records, ledger_type, ledger_name, fmt)
        results.append((filename, data))
    return results


def create_zip(files: List[Tuple[str, bytes]]) -> bytes:
    """将多份文件打包成 zip，返回字节流"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, data in files:
            # 防止重名覆盖
            zf.writestr(filename, data)
    return buf.getvalue()


def _write_excel_to_buffer(records, ledger_type: str, ledger_name: str, buf):
    """将 Excel 写入内存 buffer"""
    wb = Workbook()
    ws = wb.active
    ws.title = ledger_name[:31]  # Excel sheet 名长度限制

    header_font, header_fill, body_font, thin_border = _get_excel_styles()

    # 标题行
    ws.merge_cells("A1:J1")
    title_cell = ws["A1"]
    title_cell.value = ledger_name
    title_cell.font = Font(name="微软雅黑", size=14, bold=True)
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    # 表头
    if ledger_type == "image":
        headers = ["序号", "来源", "拍摄时间", "位置", "部位", "天气", "关联隐患", "备注", "原图路径", "水印图路径"]
    elif ledger_type == "hazard":
        headers = ["序号", "状态", "风险状态", "风险类别", "主要风险", "部位", "位置", "影像路径", "整改影像", "创建时间"]
    else:
        headers = ["序号", "文书编号", "标题", "状态", "部位", "位置", "创建时间", "PDF路径"]

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
    ws.row_dimensions[2].height = 25

    # 数据
    for row_idx, r in enumerate(records, 3):
        if ledger_type == "image":
            row_data = [
                r.id, r.source, r.taken_at.strftime("%Y-%m-%d %H:%M") if r.taken_at else "",
                r.location or "", r.section or "", r.weather or "",
                r.hazard_id or "", r.remark or "",
                r.original_path or "", r.watermarked_path or "",
            ]
        elif ledger_type == "hazard":
            row_data = [
                r.id, r.status, r.risk_status or "", r.risk_category or "",
                r.top1_risk or "", r.section or "", r.location or "",
                r.image_path or "", r.rectification_image_path or "",
                r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            ]
        else:
            row_data = [
                r.id,
                r.content.get("doc_number", "") if r.content else "",
                r.title, r.status, r.section or "", r.location or "",
                r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                r.pdf_path or "",
            ]
        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = body_font
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            cell.border = thin_border

    wb.save(buf)


def _write_pdf_to_buffer(records, ledger_type: str, ledger_name: str, buf):
    """将 PDF 写入内存 buffer"""
    styles = getSampleStyleSheet()
    styles["Normal"].fontName = FONT
    styles["Title"].fontName = FONT
    styles.add(ParagraphStyle(
        name="LedgerTitle",
        parent=styles["Title"],
        fontSize=16,
        spaceAfter=20,
        alignment=TA_CENTER,
    ))

    pdf_doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    story = []
    story.append(Paragraph(ledger_name, styles["LedgerTitle"]))
    story.append(Spacer(1, 5 * mm))

    if ledger_type == "image":
        headers = ["序号", "来源", "拍摄时间", "位置", "部位", "天气", "关联隐患", "备注"]
        col_widths = [20 * mm, 20 * mm, 35 * mm, 40 * mm, 30 * mm, 25 * mm, 25 * mm, 50 * mm]
    elif ledger_type == "hazard":
        headers = ["序号", "状态", "风险状态", "主要风险", "部位", "位置", "创建时间"]
        col_widths = [20 * mm, 30 * mm, 30 * mm, 50 * mm, 35 * mm, 40 * mm, 40 * mm]
    else:
        headers = ["序号", "文书编号", "标题", "状态", "部位", "创建时间"]
        col_widths = [20 * mm, 40 * mm, 60 * mm, 30 * mm, 35 * mm, 40 * mm]

    table_data = [headers]
    for r in records:
        if ledger_type == "image":
            row = [
                str(r.id), r.source or "",
                r.taken_at.strftime("%Y-%m-%d %H:%M") if r.taken_at else "",
                r.location or "", r.section or "", r.weather or "",
                str(r.hazard_id) if r.hazard_id else "", r.remark or "",
            ]
        elif ledger_type == "hazard":
            row = [
                str(r.id), r.status or "", r.risk_status or "",
                r.top1_risk or "", r.section or "", r.location or "",
                r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            ]
        else:
            row = [
                str(r.id),
                r.content.get("doc_number", "") if r.content else "",
                r.title, r.status, r.section or "",
                r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            ]
        table_data.append([Paragraph(paragraph_text(c), styles["Normal"]) for c in row])

    # splitInRow：某条记录字段特别长时允许行内分页，否则整行高过一页会 LayoutError
    table = Table(table_data, colWidths=col_widths, repeatRows=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
    ]))
    story.append(table)

    pdf_doc.build(story)


def _get_excel_styles():
    """获取 Excel 样式"""
    header_font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    body_font = Font(name="微软雅黑", size=10)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    return header_font, header_fill, body_font, thin_border


def export_ledger_excel(records, ledger_type: str, ledger_name: str, output_path: str):
    """导出台账为 Excel"""
    wb = Workbook()
    ws = wb.active
    ws.title = ledger_name

    header_font, header_fill, body_font, thin_border = _get_excel_styles()

    # 标题行
    ws.merge_cells("A1:J1")
    title_cell = ws["A1"]
    title_cell.value = ledger_name
    title_cell.font = Font(name="微软雅黑", size=14, bold=True)
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    # 表头与数据列定义
    if ledger_type == "image":
        headers = ["序号", "来源", "拍摄时间", "位置", "部位", "天气", "关联隐患", "备注", "原图路径", "水印图路径"]
        for r in records:
            r_data = [
                r.id, r.source, r.taken_at.strftime("%Y-%m-%d %H:%M") if r.taken_at else "",
                r.location or "", r.section or "", r.weather or "",
                r.hazard_id or "", r.remark or "",
                r.original_path or "", r.watermarked_path or "",
            ]
    elif ledger_type == "hazard":
        headers = ["序号", "状态", "风险状态", "风险类别", "主要风险", "部位", "位置", "影像路径", "整改影像", "创建时间"]
        for r in records:
            r_data = [
                r.id, r.status, r.risk_status or "", r.risk_category or "",
                r.top1_risk or "", r.section or "", r.location or "",
                r.image_path or "", r.rectification_image_path or "",
                r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            ]
    else:
        headers = ["序号", "文书编号", "标题", "状态", "部位", "位置", "创建时间", "PDF路径"]
        for r in records:
            r_data = [
                r.id,
                r.content.get("doc_number", "") if r.content else "",
                r.title, r.status, r.section or "", r.location or "",
                r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                r.pdf_path or "",
            ]

    # 写表头
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
    ws.row_dimensions[2].height = 25

    # 写数据
    for row_idx, r in enumerate(records, 3):
        if ledger_type == "image":
            row_data = [
                r.id, r.source, r.taken_at.strftime("%Y-%m-%d %H:%M") if r.taken_at else "",
                r.location or "", r.section or "", r.weather or "",
                r.hazard_id or "", r.remark or "",
                r.original_path or "", r.watermarked_path or "",
            ]
        elif ledger_type == "hazard":
            row_data = [
                r.id, r.status, r.risk_status or "", r.risk_category or "",
                r.top1_risk or "", r.section or "", r.location or "",
                r.image_path or "", r.rectification_image_path or "",
                r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            ]
        else:
            row_data = [
                r.id,
                r.content.get("doc_number", "") if r.content else "",
                r.title, r.status, r.section or "", r.location or "",
                r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                r.pdf_path or "",
            ]
        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = body_font
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            cell.border = thin_border

    # 自动列宽（跳过标题行合并单元格）
    from openpyxl.cell.cell import MergedCell
    for col in ws.columns:
        max_len = 0
        col_letter = None
        for cell in col:
            if isinstance(cell, MergedCell):
                continue
            if col_letter is None:
                col_letter = cell.column_letter
            try:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            except Exception:
                pass
        if col_letter:
            ws.column_dimensions[col_letter].width = min(max_len + 4, 40)

    wb.save(output_path)


def export_ledger_pdf(records, ledger_type: str, ledger_name: str, output_path: str):
    """导出台账为 PDF（写盘）。实现与 export_ledger_bytes(fmt="pdf") 共用，
    避免两份拷贝各自漂移。"""
    with open(output_path, "wb") as fh:
        _write_pdf_to_buffer(records, ledger_type, ledger_name, fh)
