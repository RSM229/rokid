"""从「监理通各记录关键字（草稿）.xlsx」→「房建」sheet 生成 acceptance_checklists.py。

数据源：附件1 房屋建筑工程关键工序、关键部位隐蔽工程实施举牌验收内容（181 条）。
列：序号 | 分部工程 | 关键工序、关键部位 | 监理验收要点。
生成器只负责输出数据行；search/strong_match 等逻辑是固定代码块，原样写入。
改表后重跑本脚本即可更新。
"""
from __future__ import annotations

import datetime
import hashlib
import os

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
OUT = os.path.join(BACKEND, "app", "services", "acceptance_checklists.py")

# 微信版优先，本地版兜底
XLSX_CANDIDATES = [
    r"D:/微信/xwechat_files/wxid_9o7tmwcr5b5d22_c80f/msg/file/2026-10/监理通各记录关键字（草稿）(1).xlsx",
    r"D:/rokid/rokid/文档/监理通各记录关键字（草稿）(1).xlsx",
    r"D:/rokid/监理通各记录关键字（草稿）(1).xlsx",
]


def _find_xlsx() -> str:
    # 可用环境变量 ACCEPTANCE_XLSX 显式指定源表，便于 CI/其他机器复现
    env = os.environ.get("ACCEPTANCE_XLSX")
    if env and os.path.exists(env):
        return env
    for p in XLSX_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "未找到字段表 xlsx（可用 ACCEPTANCE_XLSX 环境变量指定）："
        + " / ".join(XLSX_CANDIDATES)
    )


def _file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(v) -> str:
    return "" if v is None else str(v).strip()


def build_rows() -> list[dict]:
    """读「房建」(4列) + 「市政」(5列,含单位工程) 两个 sheet → 统一数据行。

    每条目：{kind, no, unit, div, item, points}。kind ∈ {房建, 市政}。
    组标题行（只有分部/单位工程名）不入条目。
    """
    wb = openpyxl.load_workbook(_find_xlsx(), data_only=True, read_only=True)
    rows: list[dict] = []

    # 房建：序号 | 分部工程 | 关键工序、关键部位 | 监理验收要点
    cur_div = ""
    for row in wb["房建"].iter_rows(min_row=3, values_only=True):
        c1, c2, c3, c4 = (list(row) + [None] * 4)[:4]
        no, div, item, points = norm(c1), norm(c2), norm(c3).replace("\n", " "), norm(c4)
        if div:
            cur_div = div
        if item or points:
            rows.append({"kind": "房建", "no": no, "unit": "", "div": cur_div,
                         "item": item, "points": points})

    # 市政：序号 | 单位工程 | 分部工程 | 关键工序、关键部位 | 监理验收要点
    cur_unit = cur_div = ""
    for row in wb["市政"].iter_rows(min_row=3, values_only=True):
        c1, c2, c3, c4, c5 = (list(row) + [None] * 5)[:5]
        no, unit, div, item, points = (norm(c1), norm(c2), norm(c3),
                                       norm(c4).replace("\n", " "), norm(c5))
        if unit:
            cur_unit = unit
        if div:
            cur_div = div
        if item or points:
            rows.append({"kind": "市政", "no": no, "unit": cur_unit, "div": cur_div,
                         "item": item, "points": points})
    return rows


#: 生成文件固定代码块（数据行在其间插入）
_FIXED_TAIL = '''#: 泛词黑名单：查询串本身是这类词时不做要点全文匹配，防「验收」等字全表命中
_NOISE_TEXTS: frozenset[str] = frozenset((
    "验收", "检验", "检查", "巡查", "巡视", "旁站", "复工", "交底", "复查", "复核",
    "施工", "工程", "隐蔽", "关键", "工序", "部位", "过程", "回填", "记录", "报告",
    "情况", "现场", "材料", "设备", "质量", "安全", "整改", "通知", "单", "表",
))


def search(text, limit: int = 5, prefer_kind: str | None = None):
    """按施工部位/工序/要点关键词匹配房建/市政举牌验收条目，返回相关度排序列表。

    评分：查询串命中工序部位名（≥3 字）最高；命中要点全文次之；关键分词最低。
    prefer_kind ∈ {房建, 市政} 时给该类型**已得分条目**加权（+50），用于项目名可判定
    工程类型时消解房建/市政同名歧义；未命中关键词的条目不加分，不会拉平成一片。
    """
    q = (text or "").strip()
    if not q:
        return []
    q_noise = q in _NOISE_TEXTS
    prefer = prefer_kind if prefer_kind in ("房建", "市政") else None
    scored: list[tuple[int, dict]] = []
    for kind, no, unit, div, item, points in ITEMS:
        score = 0
        # 部位名子串命中（≥2 字即可，否则「钢筋」这类 2 字关键词全被要点弱命中淹没）
        # 但查询串本身是泛词（验收/检查等）时不做部位名匹配，防「验收」命中「钢筋笼隐蔽前验收」
        if not q_noise and len(q) >= 2 and q in item:
            score += 120
        if len(item) >= 3 and item in q:
            score += 130
        # 分部/单位工程词级命中（市政复合条目名长，用户常说简称如「桥梁盖梁」「雨水管渠」）
        # 但查询串本身是泛词（验收/检查等）时不做分部级匹配，防全表误命中
        if not q_noise and div and (q in div or div in q):
            score += 100
        if not q_noise and unit and (unit in q or q in unit):
            score += 40
        if not q_noise and q in points:
            score += 80
        for w in _item_words(item):
            if len(w) >= 2 and w in q:
                score += 30
        if score > 0 and prefer and kind == prefer:
            score += 50
        if score > 0:
            scored.append((-score, {
                "kind": kind, "no": no, "unit": unit, "div": div,
                "item": item, "points": points, "score": score,
            }))
    scored.sort(key=lambda x: x[0])
    return [d for _, d in scored[:limit]] if limit else [d for _, d in scored]


def strong_match(text, prefer_kind: str | None = None):
    """强命中：同一条目胜出则直接返回，否则 None（交给候选选择）。

    胜出判定：得分最高条目 ≥130（工序部位名/分部名完整命中）且比第二名高出 ≥50 分，
    视为「用户说的就是这个部位」；否则（如「路基」下多条 80 分）返回 None 走候选。
    prefer_kind 透传给 search（项目名可判定工程类型时消歧义词）。
    """
    hits = [d for d in search(text, limit=100, prefer_kind=prefer_kind) if d["score"] >= 120]
    if not hits:
        return None
    top, second = hits[0], hits[1] if len(hits) > 1 else None
    if top["score"] >= 130:
        if second is None or top["score"] - second["score"] >= 50:
            return top
    if len(hits) == 1:
        return top
    return None


def _item_words(item: str) -> list[str]:
    """把工序部位名切成可匹配的词（去常见噪音后缀）。"""
    raw = item
    for noise in ("工程", "施工", "验收", "检验", "检查", "隐蔽", "关键", "工序",
                  "部位", "过程", "项目", "实施", "材料"):
        raw = raw.replace(noise, " ")
    return [w for w in raw.split() if w]
'''


def gen() -> None:
    src = _find_xlsx()
    rows = build_rows()
    n_fj = sum(1 for r in rows if r["kind"] == "房建")
    n_sz = sum(1 for r in rows if r["kind"] == "市政")
    lines: list[str] = []
    lines.append('"""房屋建筑工程 / 市政基础设施工程关键工序、关键部位隐蔽工程实施举牌验收内容清单')
    lines.append("（自动生成，勿手改）。")
    lines.append("")
    lines.append("数据源：监理通各记录关键字（草稿）.xlsx →「房建」sheet（附件1）+「市政」sheet（附件2）。")
    lines.append(f"源文件：{os.path.basename(src)}  md5={_file_md5(src)}")
    lines.append("kind ∈ (房建, 市政)；unit 为市政的单位工程，房建为空字符串。")
    lines.append(
        f"生成时间：{datetime.datetime.now():%Y-%m-%d %H:%M}，"
        f"总条目数：{len(rows)}（房建 {n_fj}，市政 {n_sz}）。"
    )
    lines.append("改表后重跑 scripts/gen_acceptance_checklists.py 更新；源表 md5 变化即表示需要重新生成。")
    lines.append('"""')
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("#: (kind, 序号, 市政单位工程, 分部工程, 关键工序/关键部位, 监理验收要点全文)")
    lines.append("# kind ∈ {房建, 市政}；unit 仅市政有值，房建为空字符串。")
    lines.append("ITEMS: list[tuple[str, str, str, str, str, str]] = [")
    for r in rows:
        lines.append(
            f"    {repr((r['kind'], r['no'], r['unit'], r['div'], r['item'], r['points']))},"
        )
    lines.append("]")
    lines.append("")
    lines.append(_FIXED_TAIL)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"OK 生成 {OUT}，总条目 {len(rows)}（房建 {n_fj}，市政 {n_sz}）")

    import importlib.util
    spec = importlib.util.spec_from_file_location("acceptance_checklists", OUT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for probe in ("钢筋笼", "路基", "盾构", "雨水管", "验收"):
        hits = m.search(probe, limit=3)
        print(f"  搜索「{probe}」→", [(h['kind'], h['item'], h['score']) for h in hits])
        print(f"      strong_match: {m.strong_match(probe) is not None}")


if __name__ == "__main__":
    gen()