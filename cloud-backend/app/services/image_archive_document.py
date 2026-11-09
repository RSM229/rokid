"""工程影像归档文档正文生成（纯计算，无 DB / 无 IO）。

截图里的「监理工作记录文档」落在 WorkBuddy space，在本项目里对应的是
「项目资料库 KB」。本模块负责把一次归档批次渲染成两份可归档文书的正文：

1. ``build_work_record`` —— 工程影像归档工作记录（按天一份，改口径后出新版）；
2. ``build_standard_document`` —— 工程影像分类标准（项目口径，含旁站清单排除项
   与修订历史），对应「改口径后四处联动」里的「分类标准」。

两份文书都同时产出：
- ``markdown``：人可读全文（前端预览 / 以后导出 md 用）；
- ``content``：喂给 ``doc_generator.generate_pdf`` 的字段字典，
  其中长文本走独立段落，避免 reportlab 表格整行超页抛 LayoutError。

导出 PDF、写 Document、同步 KB 由 ``image_archive_sync`` / 路由层负责。
"""
from __future__ import annotations

from typing import Optional

from app.services.image_classification import CATEGORIES, X_SUBCATEGORIES


WORK_RECORD_DOC_TYPE = "工程影像归档工作记录"
STANDARD_DOC_TYPE = "工程影像分类标准（项目口径）"


# ---------------------------------------------------------------- 工具


def compact_date(value: str) -> str:
    """2026-11-03 / 20261103 -> 20261103；空值给 00000000。"""
    text = str(value or "").replace("-", "").replace("/", "").strip()
    return (text[:8] if len(text) >= 8 and text[:8].isdigit() else "00000000")


def _cell(value, limit: int = 200) -> str:
    """Markdown 表格单元格：去换行/竖线/控制字符，防表格被撑破。"""
    text = "" if value is None else str(value)
    text = "".join(" " if ord(ch) < 32 else ch for ch in text)
    return text.replace("|", "／").replace("\r", " ").replace("\n", " ").strip()[:limit] or "—"


def _md_table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return "\n".join(lines)


def _text(v) -> str:
    return "" if v is None else str(v)


def _category_stats(batch: dict) -> list[str]:
    counts = (batch.get("summary") or {}).get("by_category") or {}
    rows = []
    for code, (name, directory, _definition) in CATEGORIES.items():
        count = counts.get(code, 0)
        if count:
            rows.append(f"{code} {name}（{directory}）：{count} 张")
    if not rows:
        rows.append("本批次无已归类影像。")
    sub = [r for r in (batch.get("ledger_rows") or []) if r.get("子类")]
    sub_counts: dict[str, int] = {}
    for row in sub:
        key = str(row["子类"])
        sub_counts[key] = sub_counts.get(key, 0) + 1
    for code in X_SUBCATEGORIES:
        if code in sub_counts:
            rows.append(f"　└ {code} {X_SUBCATEGORIES[code][0]}：{sub_counts[code]} 张")
    total = (batch.get("summary") or {}).get("total") or 0
    rows.append(f"合计 {total} 张；待确认 {len(batch.get('confirmations') or [])} 项。")
    return rows


def _gap_lines(batch: dict) -> list[str]:
    gaps = batch.get("gaps") or {}
    missing = gaps.get("missing") or []
    suggestions = gaps.get("suggestions") or {}
    lines: list[str] = []
    if missing:
        lines.append("今日缺失影像类别：" + "、".join(str(c) for c in missing))
        for code in missing:
            lines.append(f"• {code}：{_text(suggestions.get(code) or '按标准补拍')}")
    else:
        lines.append("今日八类影像均有留证。")
    review = batch.get("confirmations") or []
    no_doc = sum(1 for row in (batch.get("ledger_rows") or [])
                 if str(row.get("关联资料编号") or "") == "待补")
    if no_doc:
        lines.append(f"• {no_doc} 张照片暂无对应书面资料，编号栏暂填「待补」；"
                     "实际归档前须补齐编号，否则按分类标准就近对应原则只能作参考影像。")
    if review:
        lines.append(f"• 归类存疑/待人工确认共 {len(review)} 项，见下节。")
    return lines


def _override_lines(overrides: Optional[list[dict]]) -> list[str]:
    lines: list[str] = []
    for ov in overrides or []:
        index = ov.get("序号") or ov.get("index") or "?"
        parts = []
        for key, label in (("category_code", "类别码"), ("subcategory", "二级类"),
                           ("related_doc_no", "关联资料编号"), ("note", "依据")):
            if ov.get(key) not in (None, "", []):
                parts.append(f"{label}={_text(ov[key])}")
        if ov.get("labels"):
            parts.append("标签=" + "、".join(_text(x) for x in ov["labels"]))
        if parts:
            lines.append(f"• 第 {index} 张：" + "；".join(parts))
    return lines or ["本次未作人工口径修正，全部按分类标准自动判定。"]


def _rules_lines(rules: Optional[dict]) -> list[str]:
    data = rules or {}
    excluded = data.get("pangzhan_excluded") or []
    lines = [
        f"口径版本：第 {data.get('revision') or 0} 版"
        f"（更新时间 {_text(data.get('updated_at') or '未记录')}）",
        "本项目旁站清单排除项：" + ("、".join(_text(x) for x in excluded)
                              if excluded else "沿用标准默认口径（沟槽/深基坑开挖不旁站）"),
    ]
    if data.get("note"):
        lines.append("口径说明：" + _text(data["note"]))
    history = data.get("history") or []
    if history:
        lines.append("修订历史：")
        for item in history[-5:]:
            lines.append(f"• R{item.get('revision')} {_text(item.get('at'))} "
                         f"{_text(item.get('actor'))}：{_text(item.get('note') or '调整排除项')}")
    return lines


# ---------------------------------------------------------------- 工作记录文档


def build_work_record(batch: dict, *, project_id: int, project_name: str,
                      business_date: str, actor_name: str = "",
                      revision: int = 1, ledger_path: str = "",
                      rules: Optional[dict] = None,
                      overrides: Optional[list[dict]] = None) -> dict:
    """按天生成「工程影像归档工作记录」；revision 递增表示改口径后的新版。"""
    day = compact_date(business_date)
    title = f"工程影像归档工作记录_{day}"
    # 编号按「项目 + 日期」固定：改口径出 R2/R3 新版时仍是同一份记录文档，
    # 版本由 Document.content_revision 与 KB 副本文件名体现，不另起编号。
    doc_number = f"YX-{project_id}-{day}"
    records = batch.get("records") or []
    stats = _category_stats(batch)
    gaps = _gap_lines(batch)
    confirmations = batch.get("confirmations") or []
    override_lines = _override_lines(overrides)
    rules_lines = _rules_lines(rules)

    md = [f"# {title}", "",
          f"- 工程项目名称：{_text(project_name) or '—'}",
          f"- 编号：{doc_number}",
          f"- 日期：{_text(business_date) or day}",
          f"- 记录人：{_text(actor_name) or '—'}",
          "- 影像来源：水印相机照片（项目 / 部位 / 内容 / 时间标识）",
          f"- 归档根目录：/uploads/现场影像/",
          f"- 影像台账：{_text(ledger_path) or '影像台账_' + day + '.csv'}",
          f"- 版本：第 {revision} 版", "",
          "## 一、归档结果", ""]
    md.append(_md_table(
        ["时间", "单位工程", "部位", "施工内容", "归类", "依据"],
        [[r.get("时间"), r.get("单位工程"), r.get("部位"), r.get("施工内容"),
          r.get("归类"), r.get("依据")] for r in records]))
    md += ["", "## 二、分类统计与归档目录", ""] + [f"- {line}" for line in stats]
    md += ["", "## 三、影像台账", "",
           f"台账文件：{_text(ledger_path) or '影像台账_' + day + '.csv'}",
           f"台账行数：{len(batch.get('ledger_rows') or [])} 条；"
           f"字段：{'、'.join(batch.get('ledger_fields') or [])}。",
           "改口径后重新归档按「存储路径」合并去重并留 .bak 备份，原图始终保留。"]
    md += ["", "## 四、资料缺口与补拍建议", ""] + [f"- {line}" for line in gaps]
    md += ["", "## 五、待确认事项", ""]
    if confirmations:
        md.append(_md_table(["序号", "时间", "部位", "工序/对象", "待办"],
                            [[c.get("序号"), c.get("时间"), c.get("部位"),
                              c.get("工序/对象"), c.get("待办")] for c in confirmations]))
    else:
        md.append("无待确认事项。")
    md += ["", "## 六、人工口径修正记录", ""] + [f"- {line}" for line in override_lines]
    md += ["", "## 七、项目分类标准口径", ""] + [f"- {line}" for line in rules_lines]
    md += ["", "> 本记录由工程影像归档批次自动生成；分类判定依据"
           "《工程影像资料分类标准》及本项目确认口径，口径变更后本记录同步出新版。", ""]
    markdown = "\n".join(md)

    # doc_generator 通用分支的字段：短字段进两列表格，长文本/列表进独立段落。
    result_lines = [
        " ｜ ".join([_cell(r.get(k), 60) for k in
                     ("时间", "单位工程", "部位", "施工内容", "归类")])
        for r in records
    ]
    content = {
        "doc_number": doc_number,
        "date": _text(business_date) or day,
        "project_name": _text(project_name),
        "supervisor": _text(actor_name),
        "summary": f"本日归档工程影像 {len(records)} 张，台账 {len(batch.get('ledger_rows') or [])} 条，"
                   f"待确认 {len(confirmations)} 项；第 {revision} 版。",
        "details": "归档结果（时间｜单位工程｜部位｜施工内容｜归类）\n" + "\n".join(result_lines),
        "evidence": ["【分类统计】" + "；".join(stats),
                     "【资料缺口】" + "；".join(gaps),
                     "【待确认】" + ("；".join(
                         f"{c.get('序号')} {c.get('部位')} {c.get('待办')}" for c in confirmations)
                         or "无"),
                     "【台账】" + (_text(ledger_path) or f"影像台账_{day}.csv")],
        "remark": "【人工口径修正】" + "；".join(override_lines)
                  + "\n【项目分类标准口径】" + "；".join(rules_lines),
        "review_date": "",
    }
    return {
        "doc_type": WORK_RECORD_DOC_TYPE,
        "title": title,
        "doc_number": doc_number,
        "revision": revision,
        "markdown": markdown,
        "content": content,
    }


# ---------------------------------------------------------------- 分类标准文档


def build_standard_document(standard: dict, rules: Optional[dict], *,
                            project_id: int, project_name: str,
                            business_date: str, revision: int = 1) -> dict:
    """生成「工程影像分类标准（项目口径）」：标准全文 + 本项目排除项 + 修订历史。"""
    day = compact_date(business_date)
    title = "工程影像分类标准_项目口径"
    doc_number = f"YX-BZ-{project_id}"
    data = rules or {}
    excluded = [_text(x) for x in (data.get("pangzhan_excluded") or [])]
    standard_default = (standard.get("project_rules") or {}).get("pangzhan_excluded") or []

    categories = standard.get("categories") or []
    x_subs = standard.get("x_subcategories") or []
    pangzhan = standard.get("pangzhan_list") or []

    def _excluded(name: str) -> bool:
        return any(kw and kw in _text(name) for kw in excluded)

    md = [f"# {title}", "",
          f"- 工程项目名称：{_text(project_name) or '—'}",
          f"- 编号：{doc_number}",
          f"- 版本：第 {revision} 版（{_text(business_date) or day}）",
          "- 依据：《工程影像资料分类标准》+ 本项目监理部确认口径", "",
          "## 一、一级类别（8 类，互斥）", ""]
    md.append(_md_table(["类别码", "类别名称", "归档目录", "定义"],
                        [[c.get("code"), c.get("name"), c.get("archive_dir"),
                          c.get("definition")] for c in categories]))
    md += ["", "## 二、巡视 X 二级类别", "",
           _md_table(["类别码", "名称", "典型场景"],
                     [[x.get("code"), x.get("name"), x.get("typical")] for x in x_subs])]
    md += ["", "## 三、旁站清单（本项目适用性）", ""]
    md.append(_md_table(["序号", "工序", "主要控制点", "本项目适用性"],
                        [[p.get("序号"), p.get("工序"), p.get("主要控制点"),
                          "本项目不适用（排除项）" if _excluded(p.get("工序")) else "适用"]
                         for p in pangzhan]))
    md += ["", "> 标准原条目全部保留，仅标注本项目适用性；"
           "本项目旁站清单不含沟槽/深基坑开挖，第 10 项保留「钢板桩支护打拔」。"
           "沟槽/深基坑**开挖**按 X2 危大工程巡视归类；只排除开挖，回填仍属清单第 1 项。", ""]
    md += ["", "## 四、判定与冲突消解", "",
           f"- 冲突优先级：{' > '.join(standard.get('conflict_priority') or [])}",
           f"- 标签体系：{'、'.join(standard.get('labels') or [])}",
           f"- 命名规范：`{_text(standard.get('naming_template'))}`",
           f"- 影像台账字段：{'、'.join(standard.get('ledger_fields') or [])}", ""]
    md += ["", "## 五、本项目口径与修订历史", "",
           f"- 标准默认排除项：{'、'.join(_text(x) for x in standard_default) or '无'}",
           f"- 本项目当前排除项：{'、'.join(excluded) or '沿用标准默认口径'}",
           f"- 口径说明：{_text(data.get('note')) or '沿用标准默认口径'}",
           f"- 口径版本：第 {data.get('revision') or 0} 版"
           f"（更新时间 {_text(data.get('updated_at') or '未记录')}）", ""]
    history = data.get("history") or []
    if history:
        md.append(_md_table(["版本", "时间", "确认人", "说明", "排除项", "当次修正张数"],
                            [[h.get("revision"), h.get("at"), h.get("actor"), h.get("note"),
                              "、".join(_text(x) for x in (h.get("pangzhan_excluded") or [])),
                              h.get("override_count")] for h in history]))
    else:
        md.append("暂无口径修订记录。")
    md += ["", "> 口径确认后，后续归档批次自动按本文件的排除项判定，无需逐张手工修正；"
           "同时联动更新当日工作记录、影像台账与照片归档目录。", ""]
    markdown = "\n".join(md)

    content = {
        "doc_number": doc_number,
        "date": _text(business_date) or day,
        "project_name": _text(project_name),
        "summary": f"工程影像分类标准项目口径第 {revision} 版；"
                   f"本项目旁站清单排除项：{'、'.join(excluded) or '沿用标准默认口径'}。",
        "details": "一级类别\n" + "\n".join(
            f"{c.get('code')} {c.get('name')} → {c.get('archive_dir')}：{c.get('definition')}"
            for c in categories) + "\n\n旁站清单（本项目适用性）\n" + "\n".join(
            f"{p.get('序号')}. {p.get('工序')}"
            + ("【本项目不适用】" if _excluded(p.get("工序")) else "")
            for p in pangzhan),
        "evidence": ["【本项目口径】" + (_text(data.get("note")) or "沿用标准默认口径"),
                     "【当前排除项】" + ("、".join(excluded) or "沿用标准默认口径"),
                     "【修订历史】" + ("；".join(
                         f"R{h.get('revision')} {h.get('at')} {h.get('note')}"
                         for h in history[-5:]) or "暂无")],
        "remark": "标准原条目全部保留，仅标注本项目适用性；本项目旁站清单不含沟槽/深基坑开挖，"
                  "第 10 项保留钢板桩支护打拔。开挖按 X2 危大工程巡视，回填仍属清单第 1 项。",
        "review_date": "",
    }
    return {
        "doc_type": STANDARD_DOC_TYPE,
        "title": title,
        "doc_number": doc_number,
        "revision": revision,
        "markdown": markdown,
        "content": content,
    }
