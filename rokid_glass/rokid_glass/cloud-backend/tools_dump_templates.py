"""临时脚本：转储省统表 docx 模板的段落与表格结构（含合并单元格展开）。"""
import sys
from pathlib import Path

import docx

KB = Path(__file__).resolve().parents[1] / "知识库" / "省统表word版"
names = sys.argv[1:] or sorted(p.stem for p in KB.glob("*.docx"))


def dump(doc, label_rows=6, table_rows=30):
    print(f"######## {label_rows}/{table_rows}")

def cellgrid(cell):
    # python-docx 不暴露合并范围；这里用 tc 的 gridSpan/vMerge 推断
    tc = cell._tc
    from docx.oxml.ns import qn
    grid = tc.find(qn("w:tcPr"))
    span = 1
    if grid is not None:
        gs = grid.find(qn("w:gridSpan"))
        if gs is not None:
            span = int(gs.get(qn("w:val"), 1))
    return span


for name in names:
    f = KB / f"{name}.docx"
    if not f.exists():
        print("MISSING:", name)
        continue
    print("=" * 100)
    print("FILE:", name)
    d = docx.Document(str(f))
    for i, p in enumerate(d.paragraphs):
        t = p.text.strip()
        if t:
            print(f"  [para {i}] {t}")
    for ti, tbl in enumerate(d.tables):
        print(f"  -- table {ti}: rows={len(tbl.rows)} gridCols={len(tbl.columns)}")
        for ri, row in enumerate(tbl.rows):
            cells = row.cells
            seen = []
            for ci, c in enumerate(cells):
                txt = c.text.replace("\n", "⏎").strip()
                if txt or ci == 0:
                    # 只打印“新”单元格（合并单元格 python-docx 会重复同一对象）
                    key = id(c._tc)
                    if key not in seen:
                        seen.append(key)
                        print(f"      r{ri}c{ci} span={cellgrid(c)}: {txt[:120]}")
