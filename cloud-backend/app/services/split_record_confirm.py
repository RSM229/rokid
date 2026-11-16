"""分类/分组建档后，按记录类型带出「这一类要填什么 + 对应内容/要点」供用户确认。

2026-11-16 真机录屏：分组后只回「已按分组创建以下记录…具体位置等可继续在
「工作记录」页补充」，用户不知道巡视要填巡视内容、验收要填验收要点，也没有
机会确认是否符合现场情况。本模块把「提醒 + 带出内容 + 请确认」集中生成。

数据源单一（勿在此另造第二份字段表）：
- 栏目 / 待填字段 → app.services.record_field_spec.FORMS（生成物，勿手改）
- 验收要点 → acceptance_ask_together.match_points_by_text
  （底层 acceptance_checklists.ITEMS，房建 181 / 市政 139 条规范要点）
- 识别内容（仅内部）→ ImageGroup.hints（multi_image_split 已切掉水印尾巴）：
  用来匹配验收要点、落 content_json，**不写进任何对话回执**（2026-11-16 用户口径）
- 旁站三段栏目 → FORMS 的 field.section，与《监理旁站记录范本大全》
  「施工情况 / 监理情况 / 发现问题及处理意见」一致
"""
from __future__ import annotations

import re
from typing import Any, Callable

from app.services import acceptance_ask_together as _aat
from app.services import multi_image_split, record_field_spec

_TYPE_ORDER = ("巡视", "旁站", "验收", "见证", "平行检验", "待定")

# 水印已回填 / 照片已挂载的字段，不再列进「待填」，避免重复问用户
_SECTION_FROM_WATERMARK = "section"
_PHOTO_ALREADY_ATTACHED = "photo"
_POINTS_ALREADY_SHOWN = "acceptance_content"


def _short(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[:limit] + "…"


def _clean_label(label: Any, limit: int = 16) -> str:
    """字段表里的 label 常带「（年 月 日 时）」等说明，展示只留主干。"""
    s = re.split(r"[（(\n]", str(label or ""))[0].strip().rstrip("：: ")
    return _short(s, limit)


def _pending_labels(rtype: str, *, has_section: bool, has_photo: bool,
                    has_points: bool, limit: int = 4) -> list[str]:
    """该类还需用户补的必填栏目，唯一真源 = record_field_spec.FORMS。"""
    form_key = record_field_spec.RECORD_TYPE_TO_FORM.get(rtype)
    form = record_field_spec.FORMS.get(form_key or "") or {}
    skip: set[str] = set()
    if has_section:
        skip.add(_SECTION_FROM_WATERMARK)
    if has_photo:
        skip.add(_PHOTO_ALREADY_ATTACHED)
    if has_points:
        skip.add(_POINTS_ALREADY_SHOWN)
    out: list[str] = []
    for field in form.get("fields", []):
        if field.get("kind") != "ask" or not field.get("required"):
            continue
        if field.get("canonical") in skip:
            continue
        label = _clean_label(field.get("raw_label"))
        if label and label not in out:
            out.append(label)
        if len(out) >= limit:
            break
    return out


def _xunshi_items(limit: int = 5) -> list[str]:
    """GD-B-214「巡视内容」的 1~6 项，直接解析字段表，不另写一份文案。"""
    form = record_field_spec.FORMS.get("gd_b_214_xunshi") or {}
    raw = next(
        (str(f.get("raw_label") or "") for f in form.get("fields", [])
         if f.get("canonical") == "content"),
        "",
    )
    parts = re.split(r"\s*\d\.\s*", raw)
    items: list[str] = []
    for part in parts[1:]:
        s = part.strip().rstrip("）) ").strip()
        if not s or s == "其他":
            continue
        items.append(_short(s, 44))
        if len(items) >= limit:
            break
    return items


def _pangzhan_sections() -> list[str]:
    """GD-B-212 的三段内容栏目（与旁站范本 PDF 一致），取字段表的 section。"""
    form = record_field_spec.FORMS.get("gd_b_212_pangzhan") or {}
    out: list[str] = []
    for field in form.get("fields", []):
        if field.get("kind") != "ask":
            continue
        sec = str(field.get("section") or "").strip()
        if sec in ("", "基本信息", "签章") or sec in out:
            continue
        out.append(sec)
    return out


def _identified(groups: list[Any]) -> str:
    """组内逐图识别到的施工内容（已清洗水印尾巴），去重保序。"""
    items: list[str] = []
    for g in groups:
        for hint in multi_image_split.clean_hints(getattr(g, "hints", None)):
            h = _short(hint, 30)
            if h and h not in items:
                items.append(h)
    return "／".join(items[:3])


def _group_has_section(groups: list[Any]) -> bool:
    for g in groups:
        for wm in getattr(g, "wm", None) or []:
            if isinstance(wm, dict) and str(wm.get("section") or "").strip():
                return True
    return False


def _group_has_photo(groups: list[Any]) -> bool:
    return any(str(u).strip() for g in groups for u in (getattr(g, "images", None) or []))


def _circled(items: list[str]) -> str:
    return "；".join(f"{chr(0x2460 + i)} {it}" for i, it in enumerate(items))


def build_type_confirmation(
    groups: list[Any],
    *,
    display: Callable[[str], str] | None = None,
    created_ids: dict[str, list[int]] | None = None,
    stage: str = "created",
) -> str:
    """按记录类型聚合，生成「该类内容/要点 + 待填栏目 + 请确认」。

    display 由调用方传入（复用 agent_tools._split_record_display_name），
    避免本模块再复制一份正式表名。stage=plan 时结尾引导点「确认生成」。
    🔴 识别出的施工内容（identified）只作内部依据：用于匹配验收要点、
    落 content_json，不写进标题或正文（2026-11-16 用户「都要」）。
    """
    buckets: dict[str, list[Any]] = {}
    for g in groups or []:
        rtype = str(getattr(g, "record_type", "") or "待定").strip() or "待定"
        buckets.setdefault(rtype, []).append(g)
    if not buckets:
        return ""
    order = [rt for rt in _TYPE_ORDER if rt in buckets]
    order += [rt for rt in buckets if rt not in _TYPE_ORDER]
    name_of = display or (lambda rt: f"{rt}记录")
    ids_by_type = created_ids or {}

    lines: list[str] = []
    for rtype in order:
        gs = buckets[rtype]
        ids = [int(i) for i in (ids_by_type.get(rtype) or []) if str(i).isdigit()]
        tag = " ".join(f"#{i}" for i in ids[:4])
        head = f"【{name_of(rtype)}{(' ' + tag) if tag else ''}】"
        # 识别内容只留内部：曾在这里拼「 现场识别：水泥搅拌桩施工」，
        # 2026-11-16 用户要求一律不写出来；identified 仍用于下方要点匹配。
        identified = _identified(gs)
        lines.append(head)

        has_points = False
        if rtype == "巡视":
            items = _xunshi_items()
            body = f"巡视内容（GD-B-214）：{_circled(items)}" if items else "巡视内容：待补"
        elif rtype == "验收":
            _proc, points = _aat.match_points_by_text(identified)
            if points:
                has_points = True
                # 要点原文自带 ① ② ③，别再套一层序号。
                # 2026-11-16 用户截图「验收要点，括号里的要去掉吧」：匹配到的
                # 规范工序名不再写出来，只留要点正文；proc 仍用于内部追溯。
                body = "验收要点：" + "；".join(_short(p, 52) for p in points)
            else:
                body = "验收要点：暂未匹配到这道工序的规范要点，说下验收的是哪道工序我再带出"
        elif rtype == "旁站":
            secs = _pangzhan_sections()
            body = ("旁站内容（GD-B-212／范本）：" + " / ".join(secs) + " 三段"
                    if secs else "旁站内容：施工情况 / 监理情况 / 问题与处理")
        elif rtype == "见证":
            body = "见证内容：按现场照片整理（材料与取样信息待补）"
        else:
            body = "内容：待确认归类后补全"

        pending = _pending_labels(
            rtype,
            has_section=_group_has_section(gs),
            has_photo=_group_has_photo(gs),
            has_points=has_points,
        )
        if pending:
            body += " ｜ 待填：" + "、".join(pending)
        lines.append("  " + body)

    if stage == "plan":
        lines.append("以上按类型先跟你核对：分类和内容符合就点「确认生成」；要改哪一类直接说。")
    else:
        lines.append("以上按类型带出，请确认是否符合现场情况；不符合直接说要改哪项，"
                     "要补的栏位一并告诉我。都符合就回「写入正式记录」。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 要点落库：回执里带出的验收要点必须真的进记录，不能只在对话里出现过。
# 2026-11-16 用户截图「工作内容填的不是要点吗」：验收记录详情只看到
# 水印识别的工序名，要点既没落库、详情页也没有对应栏目。
# ---------------------------------------------------------------------------

def acceptance_points_for(groups: list[Any]) -> dict | None:
    """验收分组 → 结构化要点草案，供建档写入 ``content_json.acceptance_points``。

    与 ``build_type_confirmation`` 的验收分支同源（同一个 ``_identified`` +
    ``match_points_by_text``），保证「对话里给用户看的」与「落库的」是同一套要点。
    匹配不到返回 None，不臆造要点。
    """
    identified = _identified(groups)
    proc, points = _aat.match_points_by_text(identified)
    items = [str(p).strip() for p in points if str(p).strip()]
    if not items:
        return None
    return {
        # 工序名只留在内部：用于追溯匹配来源，不写进任何回执/记录展示文本
        "procedure": str(proc or ""),
        "identified": identified,
        "points": items,
        "text": "；".join(items),
        "confirmed": False,
    }


# 历史草案曾把匹配到的规范工序名写在要点前（「工序名：① …」）。
# 2026-11-16 用户「验收要点，括号里的要去掉吧」：展示层统一剥掉，
# content_json.acceptance_points.procedure 仍保留，不丢数据。
_LEGACY_PROC_PREFIX = re.compile(r"^省统表相近工序「[^」]*」\s*[:：]\s*")


def _strip_procedure_prefix(text: Any) -> str:
    return _LEGACY_PROC_PREFIX.sub("", str(text or "").strip())


def acceptance_points_text(points: Any) -> str:
    """详情接口用：把要点草案拼成一行可读文本（正式验收内容为空时展示）。

    历史草案里带工序名前缀的也在这里剥掉，旧记录同样不再显示。
    """
    if isinstance(points, dict):
        return _strip_procedure_prefix(points.get("text"))
    return ""


def build_acceptance_content_from_points(points: Any, *, positive: bool = True) -> str:
    """用户确认后，把要点草案转成正式「验收内容」：要点原文 + 逐条结论。

    正式验收文书必须带结论，不能只放要点清单。带否定词时不生成（不能把
    「不符合」写成「符合」），草案原样保留，由后续追问补真实结论。
    """
    if not isinstance(points, dict) or not positive:
        return ""
    items = [str(p).strip() for p in (points.get("points") or []) if str(p).strip()]
    if not items:
        return ""
    return (f"验收内容（逐项核对，共 {len(items)} 项）："
            + "；".join(f"{p} —— 符合" for p in items))
