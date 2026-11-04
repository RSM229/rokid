"""智能体（「聊天即操作」）工具注册表 —— P1 核心闭环。

设计要点
--------
1. **目录即契约**：LLM 只负责「选工具 + 抽参数」，权限校验、参数校验、落库、审计
   全部在服务端完成。前端不做任何业务判断。
2. **权限同口径**：每个工具声明 ``permission``，校验走 ``User.has_permission``，
   与页面端点完全一致，不存在第二套权限逻辑。
3. **风险分级**：``read`` 直接执行；``write``（创建类）直接执行并回执卡片 +
   可撤销；``destructive`` 仅 `undo_last_action` 使用，且只回滚本用户经聊天创建的
   对象（24 小时内），不做任意删除。
4. **可撤销**：每次写操作落 ``SecurityAudit(action='agent_write')``，记录
   ``{entity, id, tool}``；``undo_last_action`` 据此回滚，并写 ``agent_undo`` 审计。
5. **不确定就问**：参数不足返回 ``need_more``（缺失字段中文标签），由聊天追问。

返回结构（统一的 ToolResult 字典）
---------------------------------
``{"ok": bool, "reply": str, "card": dict|None, "cards": list, "actions": list,
   "need_more": list[str], "undo": dict|None, "data": dict}``
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import delete, func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_draft import (
    AgentConversationDraft,
    AgentConversationMemory,
    AgentConversationMessage,
    AgentDraft,
)
from app.models.document import Document, DocStatus
from app.models.hazard import Hazard, HazardStatus
from app.models.parallel import ParallelInspection
from app.models.security_audit import SecurityAudit
from app.models.user import Project, User
from app.models.work_record import WorkRecord, WorkRecordStatus
from app.services import agent_request_plan, form_contracts, model_gateway, provform_schema, record_field_spec
from app.services.agent_context import (
    append_context_message,
    extract_explicit_message_facts,
    extract_user_facts,
    merge_user_facts,
)
from app.services import acceptance_checklists
from app.services.period_report_service import doc_type_for
from app.services.record_fields import project_fields
from app.services.time_normalization import parse_local_datetime

logger = logging.getLogger(__name__)

# 记录类型来自统一业务目录；「待定」只作为仍待用户确认的草稿状态。
RECORD_TYPES = tuple(agent_request_plan.RECORD_TYPES) + ("待定",)

# 省统表 11 类记录 → 友好名（供 LLM 选表；文案与 form_registry 一致）
PROVFORM_CHOICES = (
    ("gd_b_212_pangzhan", "旁站监理记录"),
    ("gd_b_213_pingxing", "平行检验记录"),
    ("gd_b_214_xunshi", "巡视检查记录"),
    ("gd_b_215_tongzhi", "监理工程师通知单"),
    ("acceptance_record", "验收记录"),
    ("gd_b_220_rizhi", "监理日志"),
    ("gd_b_221_yuebao", "监理月报"),
    ("gd_b_219_cailiao", "材料台账"),
    ("gdaq_30306_weida", "危大工程巡视记录"),
    ("anquan_jianli_rizhi", "安全监理日志"),
)

# 页面别名词典：navigate 工具据此把「打开XX」映射到小程序页面。
# 🔴 2026-10-26：与前端工作台指令表对齐（前端 matchCommand 的自由文本路由将退役，
# 自然语言「打开某页」统一由本词典决定）。新增页面时两边同步。
_PAGE_ALIASES = {
    "工作记录": "/pages/work/index",
    "记录": "/pages/work/index",
    "巡视记录": "/pages/work/index",
    "验收记录": "/pages/work/index",
    "旁站记录": "/pages/work/index",
    "见证记录": "/pages/work/index",
    "隐患": "/pages/hazards/index",
    "隐患列表": "/pages/hazards/index",
    "隐患识别": "/pages/hazards/index",
    "文书": "/pages/documents/index",
    "文书列表": "/pages/documents/index",
    "监理文书": "/pages/documents/index",
    "文书审批": "/pages/documents/index",
    "台账": "/pages/documents/index?tab=ledger",
    "查看台账": "/pages/documents/index?tab=ledger",
    "工作台": "/pages/agent/index",
    "待办": "/pages/todo/index",
    "待办任务": "/pages/todo/index",
    "任务": "/pages/todo/index",
    "看板": "/pages/dashboard/index",
    "资料库": "/pages/kb/index",
    "知识库": "/pages/kb/index",
    "我的知识库": "/pages/kb/index",
    "我的资料库": "/pages/kb/index",
    "知识库管理": "/pages/kb/index",
    "资料库管理": "/pages/kb/index",
    "规范查询": "/pages/knowledge/index",
    # 🔴 只收「AI巡检」这个精确说法，**不收「巡检」**：
    # RECORD_TYPES["巡视"].aliases 里「巡检」是巡视记录的同义词，而 _navigate 用
    # 子串包含匹配，收了就会把「巡检3号楼二层，记一条」劫持到 AI巡检页。
    # 前端旧指令表当年也特意删过这条（见 git 历史注释），两边口径保持一致。
    "AI巡检": "/pages/inspection/index",
    "拍照识别": "/pages/capture/index",
    "拍照": "/pages/capture/index",
    "AI隐患识别": "/pages/capture/index?mode=hazard",
    "我的": "/pages/mine/index",
    "省统表": "/pages/record-form/choose",
    "平行检验": "/pages/parallel/index",
}


@dataclass
class ToolContext:
    """工具执行上下文（由路由层构造，工具内不做权限判断）。"""

    db: AsyncSession
    user: User
    project_id: int | None = None
    project: Project | None = None
    message: str = ""


Handler = Callable[[ToolContext, dict], Awaitable[dict]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    desc: str
    params: dict[str, str]  # 参数名 -> 说明；必填用「*」前缀标注
    permission: str | None
    risk: str  # read | write | destructive
    handler: Handler
    examples: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _ok(reply: str, **kw) -> dict:
    base = {"ok": True, "reply": reply, "card": None, "cards": [], "actions": [],
            "need_more": [], "options": {}, "undo": None, "data": {}}
    base.update(kw)
    return base


def _need_more(missing: list[str], reply: str = "",
               options: dict[str, list[str]] | None = None) -> dict:
    """参数不足：回追问话术；options 提供可点选的候选值（前端渲染成 chips）。"""
    if not reply:
        labels = [str(x) for x in (missing or [])]
        if len(labels) == 1:
            reply = (f"还差一项：请补充「{labels[0]}」；不用填也没关系，直接说跳过。")
        elif labels:
            reply = ("还有几项待补充（" + "、".join(labels)
                     + "）；可以一次说完，也可以说「跳过」继续。")
        else:
            reply = "请补充缺失信息。"
    return {"ok": False, "reply": reply, "card": None, "cards": [], "actions": [],
            "need_more": missing, "options": options or {}, "undo": None, "data": {}}


def _parse_json_object(raw: str) -> dict | None:
    """从模型输出里尽力取出一个 JSON 对象（兼容 ```json 包裹与前后废话）。"""
    if not raw:
        return None
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
    try:
        data = json.loads(txt)
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError):
        pass
    start, end = txt.find("{"), txt.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(txt[start:end + 1])
            return data if isinstance(data, dict) else None
        except (TypeError, ValueError):
            return None
    return None


async def _llm_json(prompt: str, system: str, temperature: float = 0.2) -> dict | None:
    """调用文本模型并解析 JSON；失败返回 None（调用方走兜底）。"""
    try:
        raw, _cfg = await model_gateway.chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001 - 网关异常一律降级处理
        logger.warning("agent LLM 调用失败: %s", type(exc).__name__)
        return None
    return _parse_json_object(raw)


async def _audit(db: AsyncSession, user: User, action: str, detail: dict) -> None:
    db.add(SecurityAudit(
        user_id=user.id,
        username=user.username,
        action=action,
        detail=json.dumps(detail, ensure_ascii=False),
    ))
    await db.flush()


def _norm_record_type(value: Any) -> str | None:
    """归一化记录类型；识别不出返回 None（创建时须追问，不静默兜底「待定」）。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    canonical = agent_request_plan.canonical_record_type(raw)
    if canonical:
        return canonical
    for t in RECORD_TYPES:
        if t in raw:
            return t
    return None


def _parse_date(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m-%d", "%m/%d"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            if fmt in ("%m-%d", "%m/%d"):
                parsed = parsed.replace(year=datetime.now().year)
            return parsed
        except ValueError:
            continue
    return None


# 中文时间归一化：「九点到五点半」「五点半」「三点一刻」→ 数字时刻，
# 服务端兜底解析，不依赖 LLM 自觉换算（2026-10-15 真机踩坑：中文时间解析不出 → 落库为空）
_CN_DIGIT_MAP = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9}
_CN_TIME_RE = re.compile(
    r"([零一二两三四五六七八九十]{1,3})点(半|一刻|三刻|([零一二三四五六七八九十]{1,3})分?)?"
)
_PM_HINTS = ("下午", "晚上", "傍晚", "晚")


def _cn_num_to_int(text: str) -> int | None:
    """「十」=10、「十五」=15、「二十」=20、「二十三」=23；不合法返回 None。"""
    if not text:
        return None
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        if (left and left not in _CN_DIGIT_MAP) or (right and right not in _CN_DIGIT_MAP):
            return None
        return ( _CN_DIGIT_MAP.get(left, 1) if left else 1) * 10 + (_CN_DIGIT_MAP.get(right, 0) if right else 0)
    if len(text) == 1 and text in _CN_DIGIT_MAP:
        return _CN_DIGIT_MAP[text]
    return None


def _cn_time_sub(match: "re.Match[str]") -> str:
    hour = _cn_num_to_int(match.group(1))
    if hour is None or hour > 24:
        return match.group(0)
    tail = match.group(2) or ""
    if tail == "半":
        minute = 30
    elif tail == "一刻":
        minute = 15
    elif tail == "三刻":
        minute = 45
    else:
        minute = (_cn_num_to_int(match.group(3)) if match.group(3) else 0) or 0
    return f"{hour}:{minute:02d}"


def _normalize_time_text(raw: str) -> str:
    """把中文时间表述归一成数字 HH:MM 文本，供后面的数字正则统一解析。"""
    text = _CN_TIME_RE.sub(_cn_time_sub, raw)
    text = re.sub(r"(\d{1,2})点(\d{1,2})分?", lambda m: f"{m.group(1)}:{m.group(2)}", text)
    text = re.sub(r"(\d{1,2})点半", lambda m: f"{m.group(1)}:30", text)
    text = re.sub(r"(\d{1,2})点(?![:：\d])", lambda m: f"{m.group(1)}:00", text)
    return text


def _parse_time(value: Any, base: date | None = None) -> datetime | None:
    """Parse time values with the same China-local rules used by form contracts."""
    anchor = base or datetime.now().date()
    return parse_local_datetime(value, anchor)


def _parse_time_range(raw: Any, base: date | None = None) -> tuple[datetime | None, datetime | None]:
    """解析「9:00 到 11:00」「九点到五点半」类时间段 → (开始, 结束)；单个时刻 → (t, None)。

    结束时刻 ≤ 开始时刻时自动 +12h（「九点到五点半」→ 9:00~17:30，而不是凌晨 5:30）。
    """
    if raw in (None, ""):
        return None, None
    text = str(raw).strip()
    parts = re.split(r"\s*(?:到|至|~|～|—|-)\s*", text)
    if len(parts) == 2:
        start = _parse_time(parts[0], base)
        end = _parse_time(parts[1], base)
        if start and end:
            if end <= start:
                end += timedelta(hours=12)
            return start, end
    norm = _normalize_time_text(text)
    matches = re.findall(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", norm)
    if len(matches) >= 2:
        start = _parse_time(f"{matches[0][0]}:{matches[0][1]}", base)
        end = _parse_time(f"{matches[1][0]}:{matches[1][1]}", base)
        if start and end:
            if end <= start:
                end += timedelta(hours=12)
            return start, end
    single = _parse_time(text, base)
    return single, None


# 用户表示「不再补充」的词：命中则跳过可选字段追问，直接落库
_SKIP_HINTS = ("跳过", "不用补", "不必", "不用了", "略过", "直接创建", "先这样", "算了",
               "不补充", "都填了", "不缺", "没有补充", "无照片", "不拍照")


# 用户表示「无问题」的说法：巡视追问「问题及处理」时，模型常把「符合要求」
# 理解成"没有问题"而省略 problems 参数 → 服务端再追问 → 死循环（2026-10-15 真机踩坑）。
# 这里做确定性兜底：原话命中即回填 problems，不依赖模型自觉。
_NO_ISSUE_HINTS = ("符合要求", "无异常", "没有发现问题", "没有问题", "无问题",
                   "一切正常", "无隐患", "正常")


def _wants_skip(message: str) -> bool:
    text = (message or "").strip()
    return any(h in text for h in _SKIP_HINTS)


# ---------------------------------------------------------------------------
# 追问口径：唯一数据源 = ``record_field_spec``（由「字段表」生成，勿在此硬编码）
#
# 规则（与用户确认过的口径一致）：
#   「由系统生成=是」→ 系统带出，不追问；
#   「由系统生成=否」→ 用户没说就逐项追问补充（按表栏目顺序，一次只问一栏，可跳过）；
#   签名/盖章类一律排除，走签章服务；
#   天气虽在表上标「是」，但气象接口未接（见 record_field_spec.ASK_ANYWAY）→ 仍追问。
# ---------------------------------------------------------------------------
_EXTRA_ARG = "extra_fields"        # 表上有、但工具没单列参数的字段，统一收在这个对象里


def assist_fields(record_type: str, is_weida: bool = False) -> list[dict]:
    """该记录类型「用户没说就要补」的字段，按字段表栏目顺序。"""
    form = record_field_spec.form_for_record_type(record_type or "", is_weida)
    return record_field_spec.ask_fields(form) if form else []


def _field_slot(field: dict) -> str:
    """extra_fields 的槽位名：优先用 canonical（文书渲染层认得），否则退回表字段 key。"""
    return str(field.get("canonical") or field.get("key") or "")


def _key_to_canonical(form: str) -> dict[str, str]:
    """表字段 key（如 10_14）→ canonical（如 pm_onsite）的映射，供 extra_fields 键归一。"""
    fields = record_field_spec.FORMS.get(form, {}).get("fields") or []
    mapping: dict[str, str] = {}
    for f in fields:
        key, canon = f.get("key"), f.get("canonical")
        if key and canon and str(key) != str(canon):
            mapping[str(key)] = str(canon)
    return mapping


def normalize_extra_fields(args: dict, form: str | None) -> dict:
    """把 extra_fields 统一为 canonical 键。

    提示词允许模型用表字段 key（如 extra_fields["10_14"]）写槽位，
    服务端读值/落库却按 canonical（pm_onsite）——键不一致会误判「仍缺字段」，
    造成「用户明明说了到岗还反复追问」死循环（2026-10-16 真机复现）。
    这里把 key 形式的旧键迁移到 canonical，已有 canonical 值的不覆盖。
    """
    extra = args.get(_EXTRA_ARG)
    if not isinstance(extra, dict) or not extra:
        return args
    mapping = _key_to_canonical(form or "")
    out: dict[str, Any] = {}
    for k, v in extra.items():
        if not str(v if v is not None else "").strip():
            continue
        canon = mapping.get(str(k), str(k))
        if canon not in out:
            out[canon] = v
    new_args = dict(args)
    new_args[_EXTRA_ARG] = out
    return new_args


def _field_value(args: dict, field: dict) -> str:
    """取该字段在当前 args 里的值（独立参数 或 extra_fields 槽位）。

    容错：extra_fields 里既认 canonical 也认表字段 key（如 10_14），
    模型按哪个写的都能读到，避免误判「仍缺字段」反复追问。
    """
    arg = field.get("arg")
    if arg:
        return str(args.get(arg) or "").strip()
    extra = args.get(_EXTRA_ARG)
    if isinstance(extra, dict):
        slot = _field_slot(field)
        v = extra.get(slot)
        if not str(v if v is not None else "").strip():
            key = field.get("key")
            if key:
                v = extra.get(str(key))
        return str(v if v is not None else "").strip()
    return ""


def _set_field_value(args: dict, field: dict, value: str) -> dict:
    """把值写回该字段（独立参数 或 extra_fields 槽位，统一用 canonical 存）。"""
    out = dict(args)
    arg = field.get("arg")
    if arg:
        out[arg] = value
        return out
    extra = dict(out.get(_EXTRA_ARG) or {})
    slot = _field_slot(field)
    # 若草稿里已有 key 形式的旧值（如 10_14），一并清掉，避免落库两键并存
    key = field.get("key")
    if key and str(key) != slot:
        extra.pop(str(key), None)
    extra[slot] = value
    out[_EXTRA_ARG] = extra
    return out


def apply_canonical_form_fields(args: dict, form: str | None) -> dict:
    """Map cross-cutting canonical facts to the selected form's actual args."""
    if not form:
        return args
    out = dict(args or {})
    for field in record_field_spec.fields_of(form):
        canonical = str(field.get("canonical") or "")
        if not canonical or canonical not in out:
            continue
        value = str(out.get(canonical) if out.get(canonical) is not None else "").strip()
        if value and not _field_value(out, field):
            out = _set_field_value(out, field, value)
    return out


def apply_explicit_user_facts(
    args: dict,
    facts: list[dict[str, Any]] | None,
    form: str | None,
) -> dict:
    """Give deterministic facts from the current user's words precedence over model args."""
    out = dict(args or {})
    fields = record_field_spec.fields_of(form) if form else []
    for fact in facts or []:
        if not isinstance(fact, dict) or fact.get("source") != "user_message_deterministic":
            continue
        canonical = str(fact.get("field") or "").strip()
        value = str(fact.get("value") if fact.get("value") is not None else "").strip()
        if not canonical or not value:
            continue
        out[canonical] = value
        field = next((item for item in fields if (
            str(item.get("canonical") or "") == canonical
            or str(item.get("arg") or "") == canonical
        )), None)
        if field:
            out = _set_field_value(out, field, value)
    return out


def extra_values(args: dict) -> dict:
    """extra_fields 里的非空值（落 content_json.prov_fields 用，统一 canonical 键）。"""
    extra = args.get(_EXTRA_ARG)
    if not isinstance(extra, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in extra.items():
        if str(v if v is not None else "").strip():
            out[str(k)] = v
    return out


def _control_for_field(field: dict) -> str:
    """字段表 → 前端控件类型（不硬编码具体字段，按表类型和枚举推）。"""
    table_type = str(field.get("table_type") or "")
    canonical = str(field.get("canonical") or "")
    if record_field_spec.options_for(field):
        return "select"
    if "日期时间" in table_type or ("时间" in table_type and "日期" not in table_type):
        return "datetime"
    if "日期" in table_type:
        return "date"
    if "数字" in table_type or "数量" in table_type:
        return "number"
    if canonical in ("problems", "treatment", "content", "station_content", "station_process",
                     "acceptance_basis", "acceptance_content", "remark", "work_content"):
        return "textarea"
    if "长" in table_type:
        return "textarea"
    return "text"


def field_view(field: dict, value: str = "") -> dict:
    canonical = str(field.get("canonical") or field.get("key") or "")
    label = str(field.get("label") or canonical)
    control = _control_for_field(field)
    return {
        "name": canonical,
        "key": str(field.get("key") or canonical),
        "canonical": canonical,
        "definition_source": "record_field_spec",
        "label": label,
        "control": control,
        "required": bool(field.get("required")),
        "value": value,
        "options": record_field_spec.options_for(field),
        "placeholder": f"请输入{label}" if control in ("text", "number", "datetime") else "",
        "hint": record_field_spec.QUESTION_OVERRIDES.get(canonical, ""),
        "section": str(field.get("section") or ""),
    }


def missing_field_views(args: dict, form: str | None) -> list[dict]:
    """字段表里还没填的栏目（必填在前，组内保持表栏目顺序）。"""
    if not form:
        return []
    views: list[dict] = []
    for field in record_field_spec.ask_fields(form):
        if field.get("canonical") in ("start_time", "end_time"):
            filled = _time_filled(args, field)
        else:
            filled = bool(_field_value(args, field))
        if not filled:
            views.append(field_view(field))
    views.sort(key=lambda item: 0 if item["required"] else 1)
    return views


def filled_field_views(args: dict, form: str | None) -> list[dict]:
    """已经填好的栏目，用于表单上方回显（让用户知道系统读到了什么）。"""
    if not form:
        return []
    out: list[dict] = []
    for field in record_field_spec.ask_fields(form):
        value = _field_value(args, field)
        if not value and field.get("canonical") in ("start_time", "end_time"):
            value = str(args.get("inspection_time") or "")
        if value:
            out.append({"label": str(field.get("label") or field.get("canonical") or ""), "value": value})
    return out


def apply_form_values(args: dict, form: str | None, values: dict | None) -> dict:
    """把前端表单值按字段表写回参数（键可为 canonical/key/arg/label）。"""
    out = dict(args or {})
    if not isinstance(values, dict):
        return out
    lookup: dict[str, dict] = {}
    for field in record_field_spec.fields_of(form) if form else []:
        for key in (field.get("canonical"), field.get("key"), field.get("arg"),
                    field.get("label"), field.get("raw_label")):
            if key:
                lookup.setdefault(str(key), field)
    for name, raw in values.items():
        value = str(raw if raw is not None else "").strip()
        if not value:
            continue
        key = str(name)
        if key == "record_type":
            out["record_type"] = value
            continue
        field = lookup.get(key)
        if field:
            out = _set_field_value(out, field, value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# 验收要点：按「房建举牌验收内容」表自动带出 / 候选追问（2026-10-16）
#   A) 用户给部位/工序（如「钢筋笼」）→ strong_match 唯一命中 → 直接填入验收要点全文
#   B) 命中多条 → 追问给候选（要点名）可点选；用户回编号/要点名/原文都能解析回全文
# ---------------------------------------------------------------------------
_ACCEPT_CANDS_KEY = "_accept_cands"        # 隐藏键：候选 item→points（存草稿，落库前清洗）
_ACCEPT_CANDS_ORDER = "_accept_cands_order"
_ACCEPT_FORM_CACHE: dict | None = None
_ACCEPTANCE_TYPES = ("隐蔽工程", "检验批", "进场验收", "分项分部")


def _infer_acceptance_type(text: str) -> str:
    """从素材/正文里保守推断验收类型（只认写死的四类词，取命中第一个）。

    「隐蔽工程验收公示牌」「钢筋隐蔽验收」→ 隐蔽工程；「检验批质量验收」→ 检验批。
    只用于文本里*明确出现*类型词的情形——整体仍优先保留用户可说、可改。
    """
    t = str(text or "")
    if not t:
        return ""
    for kind in _ACCEPTANCE_TYPES:
        if kind in t:
            return kind
    return ""


def _acceptance_content_field() -> dict | None:
    """acceptance_record 里 canonical=acceptance_content 的字段定义（缓存）。"""
    global _ACCEPT_FORM_CACHE
    if _ACCEPT_FORM_CACHE is None:
        form = record_field_spec.FORMS.get("acceptance_record") or {}
        _ACCEPT_FORM_CACHE = form.get("fields") or []
    for f in _ACCEPT_FORM_CACHE:
        if f.get("canonical") == "acceptance_content":
            return f
    return None


def _acceptance_type_field() -> dict | None:
    """acceptance_record 里 canonical=acceptance_type 的字段定义（缓存）。"""
    global _ACCEPT_FORM_CACHE
    if _ACCEPT_FORM_CACHE is None:
        form = record_field_spec.FORMS.get("acceptance_record") or {}
        _ACCEPT_FORM_CACHE = form.get("fields") or []
    for f in _ACCEPT_FORM_CACHE:
        if f.get("canonical") == "acceptance_type":
            return f
    return None


def _acceptance_candidates(query: str, limit: int = 4,
                           prefer_kind: str | None = None) -> list[dict]:
    """按施工部位/工序/要点匹配房建/市政举牌验收条目（弱命中候选，供追问点选）。

    prefer_kind ∈ {房建, 市政}：项目名特征推断出的类型，给该类型条目加权，消解歧义词。
    无 prefer（推断不出）时按 房建/市政 各取 top，保证候选跨 kind 可见、用户能点选。
    """
    if prefer_kind:
        return acceptance_checklists.search(query or "", limit=limit, prefer_kind=prefer_kind)
    hits = acceptance_checklists.search(query or "", limit=100)
    if not hits:
        return []
    tail = limit // 2
    fang = [h for h in hits if h["kind"] == "房建"][: max(tail, 1)]
    shi = [h for h in hits if h["kind"] == "市政"][: max(tail, 1)]
    out: list[dict] = []
    for i in range(max(len(fang), len(shi))):
        if i < len(fang):
            out.append(fang[i])
        if i < len(shi):
            out.append(shi[i])
    return out[:limit]


def _acceptance_query(section: str, content: str, location: str, message: str) -> str:
    """构造验收要点匹配用的查询字符串。

    优先用干净的 section/content/location（部位、工序）；都没有时才从原话里
    剥掉「记一条/验收/我要」这类引导词、再去掉前导泛词与分隔符（如「验收：」
    「检验：」），保留真实关键词（如「钢筋」），避免「验收：钢筋」这类查询串
    因含前导词而变成 0 分、候选为空。
    """
    pieces = [x for x in (section, content, location) if str(x or "").strip()]
    if pieces:
        return " ".join(str(p).strip() for p in pieces).strip()
    text = str(message or "").strip()
    for lead in ("记一条", "新建一条", "帮我记", "我要记", "记录一条"):
        if text.startswith(lead):
            text = text[len(lead):].strip()
            break
    # 剥前导泛词 + 分隔符（「验收：」「检验，」「检查：」等），保留其后真实关键词
    lead_re = re.compile(r"^(?:验收|检验|检查|巡查|巡视|隐蔽|关键工序|工序|部位)(?:[：:，,、\s]*)?")
    for _ in range(3):
        text = lead_re.sub("", text, count=1).strip()
        if not text or not lead_re.match(text):
            break
    return text[:80]


def _acceptance_resolve(value: Any, cands: list[dict] | None = None) -> dict | None:
    """把用户的回复（编号 / 要点名 / 要点全文 / 点选文本）解析回房建/市政举牌验收条目。

    返回 {kind, no, unit, div, item, points}；解析不了返回 None（走原话术兜底）。
    编号语义：候选显示带「[房建]/[市政]」kind 前缀 + **候选内 1-based 序号（1./2./3.）**，
    用户回「2」→ 第 2 个候选；回「[市政]1」→ 市政第 1 个；回「要点名」也行。
    不依赖表内 no（房建/市政序号各自起，跨表撞车），排序怎么变都不受影响。
    """
    v = str(value if value is not None else "").strip()
    if not v:
        return None
    cands = list(cands or [])
    # 0) 候选文本带 kind 前缀（如「[房建]2. 钢筋笼…」或「市政1. …」）→ 剥掉
    m0 = re.match(r"^\s*[\[\(（]?\s*(房建|市政)\s*[\]\)）]?\s*", v)
    v_pref = m0.group(1) if m0 else None
    if m0:
        v = v[m0.end():].strip()
    # 1) 纯编号（含「第N条/项」/「N、」）：按 1-based 候选序号解析；带 kind 前缀取该 kind 的第 idx 个
    m = re.fullmatch(r"(?:第)?(\d{1,3})(?:条|项)?", v)
    if m:
        idx = int(m.group(1))
        if v_pref:
            sub = [c for c in cands if c.get("kind") == v_pref]
            if 1 <= idx <= len(sub):
                return sub[idx - 1]
            return None
        if 1 <= idx <= len(cands):
            return cands[idx - 1]
        return None                      # 编号匹配失败就停，不误落入全文匹配
    # 2) 点选文本：「2. 基坑支护…（地基与基础 · 土方）」或「14、基坑支护…」
    m2 = re.match(r"^\s*(\d{1,3})\s*[.、．)\]]?\s*(.*)$", v)
    if m2:
        idx, rest = int(m2.group(1)), m2.group(2).strip()
        if 1 <= idx <= len(cands):
            cand = cands[idx - 1]
            # 带 kind 前缀时：找候选里该 kind 的第 idx 个
            if v_pref and cand.get("kind") != v_pref:
                sub = [c for c in cands if c.get("kind") == v_pref]
                if 1 <= idx <= len(sub):
                    return sub[idx - 1]
            return cand or None
        return None
    # 3) 要点名/要点原文（不分 kind，房建/市政都能命中）
    direct = acceptance_checklists.strong_match(v)
    if direct:
        return direct
    hits = acceptance_checklists.search(v, limit=5)
    for h in hits:
        if h["item"] == v or v in h["points"] or h["points"] == v:
            return h
    if len(hits) == 1:
        return hits[0]
    return None


def _acceptance_resolve_candidates(value: Any) -> dict | None:
    """按候选文本（含分组后缀）解析；供追问 options 点选后回填用。"""
    v = str(value if value is not None else "").strip()
    if not v:
        return None
    return _acceptance_resolve(v.split("（")[0])


def _acceptance_hide_candidates(args: dict, cands: list[dict]) -> dict:
    """把候选存进 args 隐藏键（草稿可带，落库前清洗）。

    结构：{item: {"kind","no","unit","div","points"}}，order 保序。
    用户回复编号（如「2」）/ 要点名都能用它解析回条目。
    """
    out = dict(args)
    order = [str(c.get("item") or "") for c in cands]
    out[_ACCEPT_CANDS_KEY] = {
        str(c.get("item") or ""): {
            "kind": str(c.get("kind") or ""),
            "no": str(c.get("no") or ""),
            "unit": str(c.get("unit") or ""),
            "div": str(c.get("div") or ""),
            "points": str(c.get("points") or ""),
        }
        for c in cands
    }
    out[_ACCEPT_CANDS_ORDER] = order
    return out


def _acceptance_cands_from_args(args: dict) -> list[dict]:
    """从草稿 args 的隐藏键还原候选列表（供编号解析）。"""
    meta = args.get(_ACCEPT_CANDS_KEY) or {}
    order = args.get(_ACCEPT_CANDS_ORDER) or []
    if not isinstance(meta, dict) or not isinstance(order, list):
        return []
    out: list[dict] = []
    for item in order:
        m = meta.get(str(item))
        if isinstance(m, dict) and m.get("points"):
            out.append({
                "no": str(m.get("no") or ""),
                "div": str(m.get("div") or ""),
                "item": str(item),
                "points": str(m.get("points") or ""),
            })
    return out


#: 工程类型特征词：项目名/描述命中即推断为对应类型（2026-10-16 项目名特征判定）
_PROJECT_KIND_WORDS = {
    "市政": ("市政", "道路", "桥梁", "隧道", "盾构", "管廊", "轨道", "道桥",
             "管网", "管线", "排水", "供水", "燃气", "路灯", "污水处理", "水厂",
             "绿化带", "人行道", "广场", "园建", "雨污", "海绵城市", "交通"),
    "房建": ("住宅", "小区", "大厦", "楼", "厂房", "公寓", "宿舍", "学校",
             "医院", "商场", "酒店", "公建", "写字楼", "塔楼", "裙房", "地下车库",
             "幼儿园", "图书馆", "体育馆", "养老"),
}


def _project_kind_from(project) -> str | None:
    """确定项目工程类型；推断不出返回 None（歧义词仍走候选点选）。

    优先取项目显式字段 ``eng_type``（房建/市政），为空再按项目名/描述关键词推断。
    """
    if project is not None:
        explicit = str(getattr(project, "eng_type", "") or "").strip()
        if explicit in ("房建", "市政"):
            return explicit
    pname = ""
    if project is not None:
        pname = " ".join(x for x in (
            str(getattr(project, "name", "") or ""),
            str(getattr(project, "description", "") or ""),
        ) if x)
    if not pname.strip():
        return None
    score: dict[str, int] = {}
    for kind, words in _PROJECT_KIND_WORDS.items():
        score[kind] = sum(1 for w in words if w in pname)
    if score.get("市政", 0) > 0 and score["市政"] > score.get("房建", 0):
        return "市政"
    if score.get("房建", 0) > 0 and score["房建"] > score.get("市政", 0):
        return "房建"
    # 两边命中数相等 → 推断不出（如项目名叫「市政配套楼」），返回 None 走候选
    return None


def _time_filled(args: dict, field: dict) -> bool:
    """起止时间：本字段有值，或用户给了「9:00 到 11:30」这类时间段原文，都算已答。"""
    arg = field.get("arg")
    if arg and str(args.get(arg) or "").strip():
        return True
    return bool(str(args.get("inspection_time") or "").strip())


def _is_weida(record_type: str, args: dict, message: str) -> bool:
    """是否按危大巡视（GDAQ30306）口径追问。"""
    if str(args.get("hazard_type") or args.get("hazard_name") or "").strip():
        return True
    return (record_type == "巡视"
            and any(k in (message or "") for k in ("危大", "危险性较大")))


def ask_hint(draft_args: dict | None, missing: list | None) -> str:
    """把「仍缺字段」翻成「该填到哪个参数/槽位」的提示，供二次抽参 prompt 用。"""
    labels = [str(x) for x in (missing or [])]
    if not labels:
        return "无"
    record_type = _norm_record_type(str((draft_args or {}).get("record_type") or "")) or ""
    form = record_field_spec.form_for_record_type(
        record_type, _is_weida(record_type, draft_args or {}, ""))
    if not form:
        return "、".join(labels)
    lookup = record_field_spec.label_to_field(form)
    parts: list[str] = []
    for label in labels:
        field = lookup.get(label)
        if not field:
            parts.append(label)
        elif field.get("arg"):
            parts.append(f"{label} → 参数 {field['arg']}")
        else:
            slot = field.get("canonical") or field.get("key")
            parts.append(f'{label} → extra_fields["{slot}"]')
    return "；".join(parts)


def supplement_hits(extra: dict, draft_args: dict | None, missing: list | None) -> bool:
    """模型抽出的「补充参数」是否命中当前草稿的仍缺字段。

    命中情形：键就是仍缺字段的中文标签；键是 extra_fields（字段表槽位）；
    键是某个仍缺字段对应的工具参数名。
    """
    labels = [str(x) for x in (missing or [])]
    if not labels or not extra:
        return False
    if any(str(k) in labels for k in extra):
        return True
    if _EXTRA_ARG in extra:
        return True
    record_type = _norm_record_type(str((draft_args or {}).get("record_type") or "")) or ""
    form = record_field_spec.form_for_record_type(
        record_type, _is_weida(record_type, draft_args or {}, ""))
    if not form:
        return False
    for f in record_field_spec.ask_fields(form):
        arg = f.get("arg")
        if arg and arg in extra and str(f.get("label")) in labels:
            return True
    return False


def refill_missing_from_message(args: dict, missing_labels: list | None,
                                message: str) -> dict | None:
    """字段已被追问过、用户这句实质性回复仍未落进该字段 → 原话直接填入该字段。

    返回补齐后的新 args；不适用（无匹配字段/该字段已有值/是跳过/是疑问句）返回 None。
    目标字段由字段表反查（追问标签 → 字段），不依赖模型自觉。
    """
    text = (message or "").strip()
    if not text or _wants_skip(text) or any(h in text for h in ("？", "?")):
        return None
    record_type = _norm_record_type(str(args.get("record_type") or "")) or ""
    form = record_field_spec.form_for_record_type(
        record_type, _is_weida(record_type, args, message))
    if not form:
        return None
    lookup = record_field_spec.label_to_field(form)
    for label in missing_labels or []:
        field = lookup.get(str(label))
        if not field:
            continue
        if field.get("canonical") in ("start_time", "end_time"):
            continue                      # 时间要按区间解析，不吃整句兜底
        if _field_value(args, field):
            continue
        return _set_field_value(args, field, text[:120])
    return None


# ---------------------------------------------------------------------------
# 写工具
# ---------------------------------------------------------------------------

async def _create_work_record(ctx: ToolContext, args: dict) -> dict:
    content = str(args.get("work_content") or args.get("content") or args.get("description") or "").strip()
    section = str(args.get("section") or "").strip()
    location = str(args.get("location") or "").strip()
    # 🔧 槽位键归一：模型可能用表字段 key（10_14）写 extra_fields，服务端读 canonical（pm_onsite）。
    # 不改会误判「仍缺字段」→ 死循环追问（2026-10-16 真机：危大巡视「到岗」反复被问）。
    raw_type = _norm_record_type(str(args.get("record_type") or "")) or ""
    _weida = _is_weida(raw_type, args, ctx.message or "")
    _form = record_field_spec.form_for_record_type(raw_type, _weida) or ""
    if _form:
        args = apply_canonical_form_fields(args, _form)
        args = normalize_extra_fields(args, _form)
    # 🔧 项目名误当部位兜底：「巡视XX项目…工程部位为YY」常被模型把项目名填进 section、
    # 部位清单进了 work_content。两类信号：① section≈本项目名；② section 以「项目/项目部」
    # 结尾（真部位如「3号楼二层临边防护」不会这样结尾）——命中且有内容时，用内容作部位
    if section and content:
        pname = str(getattr(ctx.project, "name", "") or "").strip() if ctx.project is not None else ""
        looks_like_project = bool(pname and (section == pname or pname in section or section in pname))
        looks_like_project = looks_like_project or section.endswith(("项目", "项目部"))
        if looks_like_project:
            args["section"] = section = content
    if not content and not section and not location:
        return _need_more(["记录内容或施工区域"], "请说明记录内容与位置，例如「3号楼二层，钢筋绑扎，天气晴」。")

    # 记录类型必须明确：识别不出就追问，不静默建「待定」。
    # 仅当用户原话里明确说了「待定/不确定」才允许落为待定。
    raw_type = str(args.get("record_type") or "").strip()
    record_type = _norm_record_type(raw_type)
    if record_type is None:
        if raw_type and ("待定" in raw_type or "不确定" in raw_type):
            record_type = "待定"
        else:
            return _need_more(
                ["记录类型"],
                "这条要记成哪一类？点下面选一个，或直接回复记录类型。",
                options={"记录类型": ["巡视", "旁站", "验收", "见证", "平行检验", "起重机械安拆检查"]},
            )
    if record_type == "待定":
        return _need_more(
            ["记录类型"],
            "记录类型还不能是“待定”。请确认是巡视、旁站、验收、见证、平行检验还是起重机械安拆检查。",
            options={"记录类型": ["巡视", "旁站", "验收", "见证", "平行检验", "起重机械安拆检查"]},
        )
    record_date = _parse_date(args.get("record_date")) or datetime.now().date()

    if record_type == "平行检验":
        from app.services.agent_form_requirements import resolve_event_form

        fields = {key: value for key, value in args.items() if value not in (None, "")}
        resolved = resolve_event_form({"evidence": []}, {
            "record_type": "平行检验",
            "record_type_confirmed": True,
            "date": record_date.isoformat(),
            "section": section,
            "location": location,
            "content": content,
            "forms": ["gd_b_213_pingxing"],
            "entity_fields": project_fields(ctx.project),
            "fields": {"gd_b_213_pingxing": fields},
        })
        if resolved["missing_fields"] or resolved["invalid_fields"] or resolved["conflicts"]:
            conflict = next(iter(resolved["conflicts"]), None)
            if conflict:
                candidates = list(dict.fromkeys(
                    str(item.get("value") or "").strip()
                    for item in conflict.get("candidates", []) if str(item.get("value") or "").strip()
                ))
                return _need_more(
                    [conflict["label"]],
                    f"{conflict['label']}识别到多个候选值，请选择正确内容或直接输入：",
                    options={conflict["label"]: candidates} if candidates else None,
                )
            field = next((item for item in resolved["fields"] if item.get("missing") or item.get("validation_error")), None)
            if field:
                options = {field["label"]: field["options"]} if field.get("options") else None
                return _need_more(
                    [field["label"]],
                    field.get("validation_error") or field.get("question") or f"请补充{field['label']}：",
                    options=options,
                )
        if args.get("_require_confirmation") and not args.get("_execution_confirmed"):
            return _need_more(
                ["最终确认"],
                "平行检验字段已按当前表式合同补齐。请核对后回复“确认生成”；现在尚未写入正式记录。",
                options={"最终确认": ["确认生成", "继续修改"]},
            )
        values = resolved["values"]
        permission = "inspection"
        if not ctx.user.has_permission(permission):
            return _ok(f"当前角色无权创建{permission}类记录", ok=False)
        detail_parts = [
            str(values.get("record") or "").strip(),
            f"使用仪器设备名称及编号：{values['instrument']}" if values.get("instrument") else "",
            f"实测数据明细：{values['measure_data']}" if values.get("measure_data") else "",
        ]
        parallel = ParallelInspection(
            user_id=ctx.user.id,
            project_id=ctx.project_id,
            inspect_date=record_date.isoformat(),
            part=str(values.get("section") or section).strip(),
            item=str(values.get("acceptance_content") or "").strip(),
            method=str(values.get("method") or "").strip(),
            basis=str(values.get("basis") or "").strip(),
            result_detail="\n".join(part for part in detail_parts if part),
            conclusion=str(values.get("conclusion") or "").strip(),
            treatment=str(values.get("handling") or "").strip(),
        )
        ctx.db.add(parallel)
        await ctx.db.flush()
        await _audit(ctx.db, ctx.user, "agent_write", {
            "tool": "create_work_record", "entity": "parallel_inspection", "id": parallel.id,
        })
        await ctx.db.commit()
        return _ok(
            f"已创建平行检验记录 #{parallel.id}｜{parallel.part}｜{parallel.item}",
            card={"kind": "parallel_inspection", "id": parallel.id, "title": "平行检验记录", "sub": parallel.part, "time": parallel.inspect_date},
            cards=[{"kind": "parallel_inspection", "id": parallel.id, "title": "平行检验记录", "sub": parallel.part, "time": parallel.inspect_date}],
            actions=[{"type": "navigate", "label": "查看平行检验", "url": "/pages/parallel/index"}],
            undo={"tool": "create_work_record", "entity": "parallel_inspection", "id": parallel.id},
            data={"parallel_inspection_id": parallel.id, "record_type": "平行检验"},
        )

    # 危大工程巡视（GDAQ30306）：表上多「危大工程名称/类型」「风力」「专业分包单位」，
    # 用户提到「危大/危险性较大」时改按危大表追问
    hazard_type = str(args.get("hazard_type") or args.get("hazard_name") or "").strip()
    is_weida = _is_weida(record_type, args, ctx.message)

    # 巡视「问题及处理」：用户原话说「符合要求/无异常」时兜底回填（见 _NO_ISSUE_HINTS 注释）
    if record_type == "巡视" and not str(args.get("problems") or "").strip():
        hit = next((h for h in _NO_ISSUE_HINTS if h in (ctx.message or "")), None)
        if hit:
            args["problems"] = hit
    # 旁站「结论」：同样兜底（GD-B-212「旁站的问题及处理情况」栏）
    if record_type == "旁站" and not str(args.get("station_conclusion") or "").strip():
        hit = next((h for h in _NO_ISSUE_HINTS if h in (ctx.message or "")), None)
        if hit:
            args["station_conclusion"] = hit
    # 旁站「施工情况」：LLM 把叙述放进 work_content 时回落复用，保证 GD-B-212 正文栏不空
    if record_type == "旁站" and not str(args.get("station_content") or "").strip() and content:
        args["station_content"] = content

    # 验收类型：验收要点按「房建举牌验收内容」表自动带出（A，2026-10-16）
    # 用户给了部位/工序（如「钢筋笼」「地基基底」）→ strong_match 唯一命中 → 要点全文直接填入
    if record_type == "验收":
        acc_field = _acceptance_content_field()
        if acc_field and not _field_value(args, acc_field):
            acc_query = _acceptance_query(section, content,
                                          str(args.get("location") or ""), ctx.message or "")
            prefer = _project_kind_from(ctx.project)   # 项目名特征 → 房建/市政，消歧义词
            strong = acceptance_checklists.strong_match(acc_query, prefer_kind=prefer) if acc_query else None
            if strong:
                args = _set_field_value(args, acc_field, str(strong["points"]))
            else:
                # 也支持用户本轮直接说要点名（如「地基基底验收」）→ 同样唯一命中即填
                direct = acceptance_checklists.strong_match(ctx.message or "", prefer_kind=prefer)
                if direct:
                    args = _set_field_value(args, acc_field, str(direct["points"]))
        # 验收类型上下文预填：素材/原话里已出现明确类型词（如「隐蔽工程验收公示牌」
        # 「检验批质量验收」）时直接填入，不再重复追问（2026-11-04 用户反馈「一点也不智能体」）。
        # 只预填写死枚举里明确出现的词；推断不出仍走逐栏追问。
        acc_type_field = _acceptance_type_field()
        if acc_type_field and not _field_value(args, acc_type_field):
            acc_src = " ".join(x for x in (
                str(ctx.message or ""), content, section, str(args.get("location") or "")
            ) if str(x or "").strip())
            inferred = _infer_acceptance_type(acc_src)
            if inferred:
                args = _set_field_value(args, acc_type_field, inferred)
        # 验收结论兜底回填：模型把「合格/不合格」收进 acceptance_result，
        # 而验收表对应栏是「监理验收结论」（2026-11-04 用户反馈「答了怎么还问」——
        # 验收表没有 acceptance_result 栏，不映射就会漏掉用户已经给的结论）。
        acc_concl_field = next(
            (f for f in record_field_spec.ask_fields("acceptance_record")
             if f.get("canonical") == "supervision_conclusion"), None)
        if acc_concl_field and not _field_value(args, acc_concl_field):
            concl = str(args.get("acceptance_result") or "").strip()
            if not concl:
                concl = next(
                    (h for h in _NO_ISSUE_HINTS if h in (ctx.message or "")), "") or ""
            if concl:
                args = _set_field_value(args, acc_concl_field, concl)

    # 字段表驱动的追问：表上「由系统生成=否」且用户还没说的字段，按栏目顺序**一次只问一栏**。
    # “跳过”只允许跳过非必填栏；合同声明的必填字段、冲突和格式错误不能被跳过。
    assist = assist_fields(record_type, is_weida)
    missing = [
        f for f in assist
        if not (_time_filled(args, f)
                if f.get("canonical") in ("start_time", "end_time")
                else _field_value(args, f))
    ]
    required_missing = [f for f in missing if bool(f.get("required"))]
    if missing and (required_missing or not _wants_skip(ctx.message)):
        field = missing[0]
        label = str(field.get("label") or field.get("canonical") or "")
        options: dict[str, list[str]] = {}
        enum = record_field_spec.options_for(field)
        # 验收要点：命中多条 → 候选追问（B，2026-10-16），可点选或直接回复要点名/编号
        if record_type == "验收" and field.get("canonical") == "acceptance_content":
            acc_query = _acceptance_query(section, content,
                                          str(args.get("location") or ""), ctx.message or "")
            prefer = _project_kind_from(ctx.project)
            cands = _acceptance_candidates(acc_query, prefer_kind=prefer) if acc_query else []
            if cands:
                args = _acceptance_hide_candidates(args, cands)
                options[label] = [
                    f"{'[房建]' if c.get('kind') == '房建' else '[市政]'}"
                    f"{i}. {c['item']}（{c['div']}）" for i, c in enumerate(cands, 1)
                ]
                options["可跳过"] = ["跳过，直接创建"]
                return _need_more(
                    [label],
                    "验收要点不唯一，选一个（或直接回复要点名）：",
                    options=options,
                )
        if enum:
            options[label] = enum
        options["可跳过"] = ["跳过，直接创建"]
        return _need_more([label], record_field_spec.question_for(field), options=options)

    # 旧聊天入口也必须经过同一份版本化合同。这里不让可选栏阻断旧有
    # “跳过”体验，但必填、冲突、格式错误与新整理计划保持完全相同的门槛。
    from app.services.agent_form_requirements import resolve_event_form
    project_values = project_fields(ctx.project)
    resolved_form = resolve_event_form({"evidence": [], "system_project": project_values}, {
        "record_type": record_type,
        "record_type_confirmed": True,
        "date": record_date.isoformat(),
        "section": section,
        "location": location,
        "content": content,
        "forms": [_form] if _form else [],
        "entity_fields": project_values,
        "fields": {_form: args} if _form else args,
    }) if _form else None
    contract_snapshot: dict[str, Any] = {}
    if resolved_form:
        blocked = (
            resolved_form.get("missing_fields")
            or resolved_form.get("conflicts")
            or resolved_form.get("invalid_fields")
            or resolved_form.get("materialization_error")
        )
        if blocked:
            conflict = next(iter(resolved_form.get("conflicts") or []), None)
            if conflict:
                candidates = list(dict.fromkeys(
                    str(item.get("value") or "").strip()
                    for item in conflict.get("candidates", []) if str(item.get("value") or "").strip()
                ))
                return _need_more(
                    [conflict.get("label") or conflict.get("canonical")],
                    f"{conflict.get('label') or conflict.get('canonical')}识别到多个候选值，请选择正确内容或直接输入：",
                    options={conflict.get("label") or conflict.get("canonical"): candidates} if candidates else None,
                )
            invalid = next(iter(resolved_form.get("invalid_fields") or []), None)
            field = next((item for item in resolved_form.get("fields") or [] if item.get("missing") or item.get("validation_error")), None)
            if invalid or field:
                item = invalid or field or {}
                label = item.get("label") or item.get("canonical") or "必填项"
                options = {label: item.get("options")} if item.get("options") else None
                return _need_more([label], item.get("message") or item.get("validation_error") or item.get("question") or f"请补充{label}：", options=options)
            return _need_more(["表式字段"], str(resolved_form.get("materialization_error") or "请补全该表式字段"))
        # 合同归一后的值优先于模型参数，确保记录列、快照和后续文书看到同一份数据。
        for key, value in (resolved_form.get("values") or {}).items():
            if value not in (None, ""):
                args[key] = value
        contract_snapshot = {
            "form_code": _form,
            "form_contract_version": resolved_form.get("contract_version") or "",
            "confirmed_fields": dict(resolved_form.get("values") or {}),
            "field_sources": dict(resolved_form.get("sources") or {}),
            "form_requirements": resolved_form,
        }

    # 普通聊天入口也必须先停在“待最终确认”状态；路由层会在首次尝试时注入
    # _require_confirmation。直接调用工具的旧接口/测试不带该标记，保持兼容。
    if args.get("_require_confirmation") and not args.get("_execution_confirmed"):
        return _need_more(
            ["最终确认"],
            "字段已按当前表式合同补齐。请核对下面内容后回复“确认生成”，或继续修改任意字段；现在尚未写入正式工作记录。",
            options={"最终确认": ["确认生成", "继续修改"]},
        )

    worker_count = args.get("worker_count")
    try:
        worker_count = int(worker_count) if worker_count not in (None, "") else None
    except (TypeError, ValueError):
        # 追问兜底回填的是原话（如「12人」「约 8 个」）→ 抽出数字再试一次，不丢信息
        digits = re.findall(r"\d+", str(worker_count or ""))
        worker_count = int(digits[0]) if digits else None

    # 巡视时间（GD-B-214 3_05/3_06 必填）：支持「9:00 到 11:00」区间或开始/结束分开给
    inspection_start = inspection_end = None
    if record_type == "巡视":
        inspection_start, inspection_end = _parse_time_range(args.get("inspection_time"), record_date)
        start_single = _parse_time(args.get("inspection_start_time"), record_date)
        end_single = _parse_time(args.get("inspection_end_time"), record_date)
        inspection_start = start_single or inspection_start
        inspection_end = end_single or inspection_end

    # 验收要点：用户本轮回复了编号/要点名 → 解析回要点全文（B 候选追问的闭环）
    if record_type == "验收":
        acc_field = _acceptance_content_field()
        if acc_field and not _field_value(args, acc_field):
            cands = _acceptance_cands_from_args(args)
            probe = _field_value(args, acc_field)   # 兼容 canonical/key 槽位
            if not probe:
                probe = ctx.message or ""
            resolved = _acceptance_resolve(probe, cands or None)
            if resolved:
                args = _set_field_value(args, acc_field, str(resolved["points"]))

    # 隐藏键清洗：候选/编号解析用，不落库、不进 content_json
    args.pop(_ACCEPT_CANDS_KEY, None)
    args.pop(_ACCEPT_CANDS_ORDER, None)

    record = WorkRecord(
        record_type=record_type,
        status=WorkRecordStatus.DRAFT.value,
        creator_id=ctx.user.id,
        project_id=ctx.project_id,
        section=section or None,
        location=location or None,
        work_content=content or None,
        weather=str(args.get("weather") or "")[:16] or None,
        problems=str(args.get("problems") or ""),
        treatment=str(args.get("treatment") or ""),
        worker_count=worker_count,
        record_date=record_date,
        # 巡视时间（GD-B-214 巡视开始/结束时间栏）
        inspection_start_time=inspection_start,
        inspection_end_time=inspection_end,
        # 类型专属字段（下游省统表渲染用）
        station_process=str(args.get("station_process") or "")[:256] or None,
        station_location=str(args.get("station_part") or args.get("station_location") or "")[:256] or None,
        station_content=str(args.get("station_content") or "")[:2000] or None,
        station_conclusion=str(args.get("station_conclusion") or "")[:256] or None,
        station_start_time=_parse_time(args.get("station_start_time"), record_date),
        station_end_time=_parse_time(args.get("station_end_time"), record_date),
        acceptance_result=str(args.get("acceptance_result") or "")[:32] or None,
        acceptance_participants=str(args.get("acceptance_participants") or "").strip() or None,
        acceptance_basis=str(args.get("acceptance_basis") or ""),
        witness_material_name=str(args.get("witness_material_name") or "")[:128] or None,
        witness_material_spec=str(args.get("witness_material_spec") or "")[:256] or None,
        witness_quantity=str(args.get("witness_quantity") or "")[:32],
        witness_unit=str(args.get("witness_unit") or "")[:16],
        witness_cert_no=str(args.get("witness_cert_no") or "")[:64],
        content_json={
            "source": "agent",
            "voice_text": content,
            **contract_snapshot,
            **({"prov_fields": extra_values(args)} if extra_values(args) else {}),
            **({"hazard_type": hazard_type} if (is_weida and hazard_type) else {}),
            **({"wind": str(args.get("wind"))[:16]} if args.get("wind") else {}),
            **({"subcontract_unit": str(args.get("subcontract_unit"))[:128]}
               if args.get("subcontract_unit") else {}),
        },
    )
    ctx.db.add(record)
    await ctx.db.flush()
    await _audit(ctx.db, ctx.user, "agent_write", {
        "tool": "create_work_record", "entity": "work_record", "id": record.id,
    })
    await ctx.db.commit()

    where = "、".join([v for v in (section, location) if v]) or "未填写区域"
    if len(where) > 26:
        where = where[:26] + "…"
    reply = f"已记录：{record_type}｜{where}"
    if content:
        brief = re.sub(r"\s+", " ", str(content)).strip()
        # 模型偶尔会把整串检查项/长文本塞进 content：此处限长，避免回执糊成一行
        if len(brief) > 40:
            brief = brief[:40] + "…"
        reply += f"\n内容：{brief}"
    reply += "\n可在「工作记录」页查看，或直接说「生成监理日志」。"
    if is_weida:
        reply += ("\n这是危大工程巡视：生成文书时选《危险性较大的分部分项工程监理巡视检查记录表》"
                  f"（GDAQ30306），检查项会按「{hazard_type or '危大类型'}」自动带出。")
    reply += "\n现场照片可以直接在下方表单里添加（可选，自动加水印挂到这条记录）。"
    return _ok(
        reply,
        card={
            "kind": "work_record",
            "id": record.id,
            "title": f"{record_type}记录",
            "sub": f"{where}",
            "status": record.status,
            "time": str(record_date),
            "extra": content[:80],
        },
        cards=[{
            "kind": "work_record", "id": record.id, "title": f"{record_type}记录",
            "sub": where, "status": record.status, "time": str(record_date),
        }],
        actions=[],
        undo={"tool": "create_work_record", "entity": "work_record", "id": record.id},
        data={"record_id": record.id},
    )


async def _create_hazard(ctx: ToolContext, args: dict) -> dict:
    desc = str(args.get("description") or args.get("content") or args.get("risk") or ctx.message or "").strip()
    if not desc:
        return _need_more(["隐患描述"], "请描述现场情况，例如「楼面积水还有碎石，有滑倒风险」。")

    payload = await _llm_json(
        "请把下面这段监理现场描述整理成隐患识别结果 JSON，字段：\n"
        '{"has_hazard": 布尔, "risk_status": "存在隐患"或"未发现隐患", '
        '"risk_category": "类别（如临边防护/临时用电/高处作业/文明施工等，8 字内）", '
        '"top1_risk": "最主要风险（20 字内）", "evidence": ["现场证据，2~4 条"], '
        '"recheck_suggestion": ["复查建议，2~4 条"]}\n'
        "只输出 JSON。\n\n描述：" + desc,
        "你是建筑工程安全隐患识别专家。",
    ) or {}
    risk_status = str(payload.get("risk_status") or "").strip()
    has_hazard = payload.get("has_hazard")
    if has_hazard is None:
        has_hazard = risk_status != "未发现隐患"
    if not risk_status:
        risk_status = "存在隐患" if has_hazard else "未发现隐患"
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    recheck = payload.get("recheck_suggestion") if isinstance(payload.get("recheck_suggestion"), list) else []
    top1 = str(payload.get("top1_risk") or desc)[:200]

    if not has_hazard:
        return _ok(
            f"按描述判断未发现明显隐患：{desc[:60]}。如确有隐患，请补充区域与具体现象。",
            data={"has_hazard": False},
        )

    hazard = Hazard(
        project_id=ctx.project_id,
        reporter_id=ctx.user.id,
        status=HazardStatus.REPORTED.value,
        risk_status=risk_status,
        risk_category=str(payload.get("risk_category") or "")[:32] or None,
        top1_risk=top1,
        evidence=[str(x)[:200] for x in evidence][:6],
        recheck_suggestion=[str(x)[:200] for x in recheck][:6],
        section=str(args.get("section") or "").strip() or None,
        location=str(args.get("location") or "").strip() or None,
        remark=f"[聊天录入] {desc[:400]}",
    )
    ctx.db.add(hazard)
    await ctx.db.flush()
    await _audit(ctx.db, ctx.user, "agent_write", {
        "tool": "create_hazard", "entity": "hazard", "id": hazard.id,
    })
    await ctx.db.commit()

    lines = [f"已创建隐患记录 #{hazard.id}｜{hazard.risk_status}"]
    if hazard.risk_category:
        lines.append(f"类别：{hazard.risk_category}")
    lines.append(f"主要风险：{top1}")
    if evidence:
        lines.append("证据：" + "；".join(str(x) for x in evidence[:3]))
    if recheck:
        lines.append("复查建议：" + "；".join(str(x) for x in recheck[:3]))
    lines.append("提示：文字录入不含取证影像。要补取证照片，请到「隐患识别」页拍一张（自动识别并挂到本条）；也可稍后在隐患详情里补录。")
    return _ok(
        "\n".join(lines),
        card={
            "kind": "hazard", "id": hazard.id, "title": top1,
            "sub": f"{hazard.section or ''} {hazard.location or ''}".strip() or "未填写位置",
            "status": hazard.risk_status, "time": str(datetime.now().date()),
        },
        cards=[{
            "kind": "hazard", "id": hazard.id, "title": top1,
            "sub": (hazard.section or hazard.location or "未填写位置"),
            "status": hazard.risk_status, "time": str(datetime.now().date()),
        }],
        actions=[{"type": "navigate", "label": "查看隐患", "url": "/pages/hazards/index"}],
        undo={"tool": "create_hazard", "entity": "hazard", "id": hazard.id},
        data={"hazard_id": hazard.id},
    )


async def _create_hazard_rectification_doc(ctx: ToolContext, args: dict) -> dict:
    """Create GDAQ30308 only through its owning Hazard business record."""
    try:
        hazard_id = int(args.get("hazard_id"))
    except (TypeError, ValueError):
        return _need_more(["隐患编号"], "请提供要生成整改通知的隐患编号；先查询隐患列表也可以。")
    if hazard_id <= 0:
        return _need_more(["隐患编号"], "隐患编号必须是正整数，请重新提供。")

    from app.routers.hazard import create_rectification_doc

    result = await create_rectification_doc(
        hazard_id,
        deadline=str(args.get("deadline") or "").strip() or None,
        user=ctx.user,
        db=ctx.db,
    )
    doc_id = int(result["doc_id"])
    hazard = await ctx.db.get(Hazard, hazard_id)
    title = f"安全隐患整改通知单_{hazard.top1_risk if hazard else hazard_id}"
    return _ok(
        f"已从隐患 #{hazard_id} 生成并关联 GDAQ30308 整改通知单 #{doc_id}。文书保持草稿状态，签发/签收信息需按实际业务完成。",
        card={"kind": "document", "id": doc_id, "title": title,
              "sub": "GDAQ30308 安全隐患整改通知", "status": result.get("doc_status", "草稿")},
        cards=[{"kind": "document", "id": doc_id, "title": title,
                "sub": "GDAQ30308 安全隐患整改通知", "status": result.get("doc_status", "草稿")}],
        actions=[{"type": "navigate", "label": "查看整改通知", "url": "/pages/documents/index"},
                 {"type": "navigate", "label": "查看隐患闭环", "url": f"/pages/hazards/index?id={hazard_id}"}],
        data={"doc_id": doc_id, "hazard_id": hazard_id,
              "form_code": "gdaq_30308_yinhuan", "hazard_status": result.get("hazard_status")},
    )


async def _create_hazard_reply_doc(ctx: ToolContext, args: dict) -> dict:
    """Generate GDAQ30309 only after an actual rectification submission."""
    try:
        hazard_id = int(args.get("hazard_id"))
    except (TypeError, ValueError):
        return _need_more(["隐患编号"], "请提供要生成整改回复单的隐患编号。")
    from app.routers.hazard import create_reply_doc
    result = await create_reply_doc(
        hazard_id,
        req=__import__("app.routers.hazard", fromlist=["HazardReplyDocRequest"]).HazardReplyDocRequest(
            rectify_desc=str(args.get("rectify_desc") or "").strip() or None,
            rechecker=str(args.get("rechecker") or "").strip() or None,
        ),
        user=ctx.user,
        db=ctx.db,
    )
    return _ok(
        f"已生成并关联隐患 #{hazard_id} 的 GDAQ20309 整改回复单 #{result['doc_id']}；复查尚未发生时不会填入复查人。",
        card={"kind": "document", "id": result["doc_id"], "title": "安全隐患整改回复单", "status": result.get("doc_status", "草稿")},
        data={"doc_id": result["doc_id"], "hazard_id": hazard_id, "form_code": "gdaq_30309_huifu", "hazard_status": result.get("hazard_status")},
    )


async def _recheck_hazard(ctx: ToolContext, args: dict) -> dict:
    try:
        hazard_id = int(args.get("hazard_id"))
    except (TypeError, ValueError):
        return _need_more(["隐患编号"], "请提供要复查的隐患编号。")
    from app.routers.hazard import recheck_hazard, RecheckRequest
    result = await recheck_hazard(
        hazard_id, RecheckRequest(passed=bool(args.get("passed")), remark=str(args.get("remark") or "").strip() or None),
        user=ctx.user, db=ctx.db,
    )
    return _ok(
        f"隐患 #{hazard_id} 已完成复查：{('合格，已闭环' if result.get('passed') else '不合格，已驳回整改')}。",
        data=result,
    )


def _provform_name_index() -> list[tuple[str, str]]:
    """表式名 → form_code 的候选索引（含 CHOICES 友好名、registry 官方名、表号）。"""
    from app.services.form_registry import FORM_BY_CODE

    # 常用简称补充（registry/CHOICES 里没有的口语说法）：
    # 「安全日志」→ anquan_jianli_rizhi（D06：prompt 指引用了这个简称，解析不出来
    # 会静默退化成普通监理日志）。
    extra_aliases: dict[str, tuple[str, ...]] = {
        "anquan_jianli_rizhi": ("安全日志", "安全监理日记"),
    }
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for code, name in PROVFORM_CHOICES:
        for alias in (name, getattr(FORM_BY_CODE.get(code), "name", ""),
                      getattr(FORM_BY_CODE.get(code), "form_number", ""),
                      *extra_aliases.get(code, ())):
            alias = str(alias or "").strip()
            if alias and (alias, code) not in seen:
                seen.add((alias, code))
                pairs.append((alias, code))
    return pairs


def resolve_provform_form_code(raw: str) -> str | None:
    """把模型传来的表式说法解析成 form_code；解析不出返回 None。

    🔴 2026-10-27 修：原实现是「按 PROVFORM_CHOICES 顺序做子串匹配 + break」，
    会把**长名误配到短名**，静默建错文书（不是追问，是建错！）：
        '安全监理日志'      → gd_b_220_rizhi（监理日志）  ✗ 应为 anquan_jianli_rizhi
        '危险性较大的…巡视检查记录表' → gd_b_214_xunshi（巡视记录）✗ 应为 gdaq_30306_weida
    因为 gd_b_220_rizhi 在清单里排在 anquan_jianli_rizhi 之前，
    而「监理日志」是「安全监理日志」的子串 → 先命中就 break。
    同时 registry 官方名/常用简称（监理通知单、巡视记录、旁站记录）**全部未识别**。

    现在三级匹配，且**不再依赖清单顺序**：
      1) 精确等于 code / CHOICES 名 / registry 名 / 表号；
      2) 候选名是用户话的子串 → 取**最长**候选（「安全监理日志」6字 > 「监理日志」4字，长的赢）；
      3) 用户话是候选名的子串 → 取**最短**候选（说「监理通知单」命中「监理通知单（质量类）」，
         而说「监理日志」不会被「安全监理日志」抢走）。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    codes = {code for code, _name in PROVFORM_CHOICES}
    if text in codes:
        return text

    index = _provform_name_index()

    # 1) 精确
    for alias, code in index:
        if alias == text:
            return code

    # 2) 候选名 ⊂ 用户话：最长优先
    contained = [(len(alias), code) for alias, code in index if alias in text]
    if contained:
        return max(contained)[1]

    # 3) 用户话 ⊂ 候选名：最短优先（最贴近用户所说的范围）
    containing = [(len(alias), code) for alias, code in index if text in alias]
    if containing:
        return min(containing)[1]

    return None


# 星期中文名：与 form_renderer._WEEKDAY_CN、period_report_service.WEEKDAY_CN 口径一致
_PROVFORM_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")


def _provform_canon_to_key(form_code: str) -> dict[str, str]:
    """省统表 schema：canonical content key → schema 字段 key 的反向映射。

    CONTENT_MAP 是「schema key → canonical」，这里反过来，供预填函数按语义
    （date/weekday/weather）定位到该表实际用的 schema key（如 11_02）。
    同一 canonical 只保留第一个字段。
    """
    from app.services.provform_schema import canonical_key, get_schema

    schema = get_schema(form_code)
    if not schema:
        return {}
    rev: dict[str, str] = {}
    for sec in schema.get("sections", []) or []:
        for f in sec.get("fields", []) or []:
            k = f.get("key")
            if k:
                rev.setdefault(canonical_key(form_code, k), k)
    return rev


def prefill_provform_date_weekday(form_code: str, fields: dict) -> dict:
    """省统表聊天建表：日期/星期是系统可推导字段，不该逼用户口述。

    🔴 2026-10-27：安全监理日志（anquan_jianli_rizhi）等表把「日期」「星期」
    列为必填，用户即使说全了施工内容/安全检查/处理措施等安全栏目，仍会被
    validate_values 打回要求补日期、星期（实测）。这两项系统本可自填。
    这里在 validate 之前，对 canonical=date / weekday 的空栏目预填：
      - 日期：用户没给 → 填今天（东八区）；给了 → 尊重用户值；
      - 星期：用户没给 → 按上面确定的日期算中文星期（用户给错日期也不覆盖星期逻辑）。

    通用：按 canonical key（date / weekday）识别字段，不硬编码 11_02/11_03，
    故凡把日期/星期规范映射为 canonical date/weekday 的省统表都受益（实测：
    canonical=date 的 9 张表、canonical=weekday 的 2 张表 = 监理日志 + 安全监理日志）。
    ⚠️ 巡视记录 gd_b_214_xunshi 的日期字段 canonical 是 start_time/end_time（含时分），
    需用户口述具体时刻，**不**在本函数自动预填范围（否则会把「今天 00:00」错当巡视时间）。

    **不**在此自动填的两项（各有原因）：
      - 工程名称：属 PROJECT_SYSTEM_KEYS，由 build_content_from_fields 用项目上下文覆盖；
      - 天气：另见 prefill_provform_weather —— 它需联网，单独拆出、尽力而为。
    """
    from app.services.period_report_service import now_cn

    canon_to_key = _provform_canon_to_key(form_code)
    if not canon_to_key:
        return dict(fields or {})

    out = dict(fields or {})
    date_key = canon_to_key.get("date")
    weekday_key = canon_to_key.get("weekday")

    target_date = now_cn().date()
    if date_key:
        given = str(out.get(date_key) or "").strip()
        if not given:
            out[date_key] = target_date.isoformat()
        else:
            parsed = _parse_date(given)
            if parsed:
                target_date = parsed   # 星期按用户实际给的日期算
    if weekday_key and not str(out.get(weekday_key) or "").strip():
        out[weekday_key] = _PROVFORM_WEEKDAY_CN[target_date.weekday()]
    return out


async def prefill_provform_weather(ctx: ToolContext, form_code: str, fields: dict) -> dict:
    """省统表聊天建表：天气尽力预填（联网，尽力而为，不阻塞建表）。

    方案 A（用户 2026-10-27 选定）要求日期/星期/天气都系统预填，把安全监理日志
    等表从「用户口述 8 个必填字段」降到「只口述真正的安全栏目」。

    与日期/星期不同，天气要联网取实况，故单独拆出并遵循三条底线：
      1) **只在项目配了经纬度时才尝试**（daily_weather 内部逻辑：无坐标直接返回
         空 weather + source=待补填），没坐标的项目不产生无谓网络调用；
      2) **取不到就留空**，绝不塞「预报值/默认值」冒充实况 —— 安全监理日志是
         法定文书，天气错填有合规风险，宁可让用户补；
      3) **联网失败/超时不抛**：daily_weather 自带 timeout=8s + 异常降级，这里再
         包一层 try 兜底，任何异常都退回「不预填天气」，绝不让建表因天气挂掉。
    用户已在话里说了天气（模型带进 fields[weather_key]）时，尊重用户值不覆盖。
    """
    canon_to_key = _provform_canon_to_key(form_code)
    weather_key = canon_to_key.get("weather")
    out = dict(fields or {})
    if not weather_key or str(out.get(weather_key) or "").strip():
        return out   # 无天气字段，或用户已给 → 不动

    # 日期沿用预填后的结果（可能来自用户口述，也可能是今天），保证天气与日期同一天
    from app.services.period_report_service import now_cn

    date_key = canon_to_key.get("date")
    target_date = now_cn().date()
    if date_key:
        parsed = _parse_date(str(out.get(date_key) or ""))
        if parsed:
            target_date = parsed

    try:
        from app.services.weather import daily_weather

        ex = await daily_weather(ctx.project, target_date)
        weather = str((ex or {}).get("weather") or "").strip()
        # 底线 2：只在拿到真实天气值时才填；空值（含降级/无坐标）保持空白让用户补
        if weather and str((ex or {}).get("weather_source")) != "待补填":
            out[weather_key] = weather
    except Exception as exc:  # noqa: BLE001 - 底线 3：天气失败绝不阻断建表
        logger.warning("省统表天气预填失败（不影响建表）：%s", exc)
    return out


async def _create_provform_doc(ctx: ToolContext, args: dict) -> dict:
    form_code = str(args.get("form_code") or "").strip()
    if "30308" in form_code or "安全隐患整改通知" in form_code:
        return _ok(
            "安全隐患整改通知必须关联真实隐患记录，不能独立新建。请先创建/查询隐患，再要求我从该隐患生成 GDAQ30308。",
            ok=False,
        )
    code_map = {code: name for code, name in PROVFORM_CHOICES}
    if not form_code:
        names = "、".join(name for _c, name in PROVFORM_CHOICES[:6])
        # 🔴 类数必须动态取 len(PROVFORM_CHOICES)，不能硬编码：
        # 此处原写「等 11 类」，但 30308 被移出清单后实际只有 10 类，
        # 数字与清单脱钩 → 直接给用户报错数量。
        return _need_more(
            ["表式"],
            f"请说明要建哪张省统表（如：{names} 等 {len(PROVFORM_CHOICES)} 类）。",
        )
    resolved = resolve_provform_form_code(form_code)
    if resolved is None:
        return _need_more(["表式"], f"未识别表式「{form_code}」，可选：" + "、".join(code_map.values()))
    form_code = resolved

    fields = args.get("fields") if isinstance(args.get("fields"), dict) else {}
    schema = provform_schema.get_schema(form_code)
    if not schema:
        return _ok("该表式暂不支持聊天创建，请到「工作记录 → +新建 → 省统表录入」操作。")

    # 🔴 日期/星期是系统可推导字段，在校验前自动预填，别逼用户口述
    # （见 prefill_provform_date_weekday docstring）。
    fields = prefill_provform_date_weekday(form_code, fields)
    # 天气尽力预填（联网、取不到留空、失败不阻断建表，见其 docstring）。
    fields = await prefill_provform_weather(ctx, form_code, fields)

    check = provform_schema.validate_values(form_code, fields)
    if not check.get("ok", False):
        errs = check.get("errors") or []
        labels = [str(e.get("label") or e.get("key") or "") for e in errs]
        labels = [x for x in labels if x]
        detail = "；".join(
            f"{e.get('label') or e.get('key')}（{e.get('message')}）" for e in errs[:6]
        )
        # 🔴 换脑子：抽不出必填项不再「该表还不能生成」硬报错，改为协作式追问——
        # 告诉用户还差哪些栏，并邀请把现场/叙述文字直接发来由系统代填（WorkBuddy 式对话）。
        missing_summary = "、".join(labels[:8]) if labels else "必填项"
        return _need_more(
            labels or ["必填项"],
            f"「{schema.get('name')}」还差这几栏：{missing_summary}。"
            f"你可以直接把现场情况或记录文字发给我，我帮你填进去；或者逐条告诉我补上。",
        )

    content = provform_schema.build_content_from_fields(
        form_code, fields, project_fields(ctx.project)
    )
    title = str(args.get("title") or "").strip() or f"{schema.get('name')}"
    doc = Document(
        doc_type=doc_type_for(form_code),
        doc_number=content.get("doc_number"),
        project_id=ctx.project_id,
        creator_id=ctx.user.id,
        status=DocStatus.DRAFT.value,
        title=title,
        content=content,
        form_code=form_code,
        section=str(content.get("section") or "") or None,
    )
    ctx.db.add(doc)
    await ctx.db.flush()
    await _audit(ctx.db, ctx.user, "agent_write", {
        "tool": "create_provform_doc", "entity": "document", "id": doc.id,
    })
    await ctx.db.commit()

    return _ok(
        f"已创建省统表文书「{title}」#{doc.id}（{schema.get('name')}），编号 {doc.doc_number or '待生成'}。可在「文书」页导出 docx。",
        card={
            "kind": "document", "id": doc.id, "title": title,
            "sub": str(schema.get("name") or ""), "status": doc.status,
            "time": str(datetime.now().date()),
        },
        cards=[{
            "kind": "document", "id": doc.id, "title": title,
            "sub": str(schema.get("name") or ""), "status": doc.status,
            "time": str(datetime.now().date()),
        }],
        actions=[{"type": "navigate", "label": "查看文书", "url": "/pages/documents/index"}],
        undo={"tool": "create_provform_doc", "entity": "document", "id": doc.id},
        data={"doc_id": doc.id},
    )


async def _generate_document(ctx: ToolContext, args: dict) -> dict:
    """把工作记录渲染成省统表文书；或按 日/周/月 聚合生成监理日志/周报/月报。"""
    period = str(args.get("period") or "").strip()
    period_map = {"day": "day", "日": "day", "日志": "day", "监理日志": "day",
                  "week": "week", "周": "week", "周报": "week",
                  "month": "month", "月": "month", "月报": "month"}
    # form_code 在「聚合生成」与「单条记录生成」两个分支都要用，提到顶部统一定义。
    # 🔴 归一成 code：模型常传中文表名（「安全监理日志」），而下游 FORM_BY_CODE 与
    # generate_aggregate_doc 的 code == "anquan_jianli_rizhi" 判断都只认 code，
    # 不归一会静默退化成普通监理日志（与 2026-10-27 建错文书同类问题）。
    form_code = str(args.get("form_code") or "").strip() or None
    if form_code:
        form_code = resolve_provform_form_code(form_code) or form_code
    if period in period_map:
        from app.services.period_report_service import generate_aggregate_doc
        # extra_fields（天气/上级检查/其他事项等）透传到聚合文书，先归一到 canonical 键
        raw_extra = args.get("extra_fields")
        extra_fields = {}
        if isinstance(raw_extra, dict):
            ef_form = form_code or "gd_b_220_rizhi"
            extra_fields = (
                normalize_extra_fields({"extra_fields": raw_extra}, ef_form).get("extra_fields") or {}
            )
        # 🔴 叙述性监理日报/日记：把用户原文作为起草素材透传给日报生成服务，
        # 当天无结构化记录时也能落成带正文的监理日志，而不是空日志。
        # 仅在「监理日志/日记类表单」且「原文为较长叙述（非短指令）」时生效，
        # 避免污染正常「生成今天的监理日志」等指令或周/月报聚合路径。
        _code_for_narrative = form_code or "gd_b_220_rizhi"
        _is_rizhi = (
            "日志" in _code_for_narrative
            or "日记" in _code_for_narrative
            or _code_for_narrative in ("gd_b_220_rizhi", "anquan_jianli_rizhi")
        )
        narrative = ctx.message if (_is_rizhi and len(ctx.message or "") > 40) else None
        try:
            result = await generate_aggregate_doc(
                ctx.db, ctx.project_id, period_map[period],
                str(args.get("date") or "") or None,
                form_code=form_code or None,
                extra_fields=extra_fields or None,
                use_llm=True, supervisor=ctx.user, force=True,
                narrative=narrative,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            # OSError 覆盖 TemplateNotFoundError(FileNotFoundError)：模板缺失时给可读提示，
            # 而不是让异常冒到 dispatch 被报成「执行…时出错：TemplateNotFoundError」。
            return _ok(f"聚合生成失败：{exc}。可到「省统表录入」核对记录后重试。")
        doc_id = result.get("doc_id")
        await _audit(ctx.db, ctx.user, "agent_write", {
            "tool": "generate_document", "entity": "document", "id": doc_id,
        })
        await ctx.db.commit()
        return _ok(
            f"已生成「{result.get('form_name') or '监理文书'}」#{doc_id}（{result.get('doc_number') or '编号待生成'}），可在「文书」页导出 docx。",
            card={"kind": "document", "id": doc_id, "title": result.get("title") or "",
                  "sub": result.get("form_name") or "", "status": "草稿",
                  "time": str(datetime.now().date())},
            cards=[{"kind": "document", "id": doc_id, "title": result.get("title") or "",
                    "sub": result.get("form_name") or "", "status": "草稿",
                    "time": str(datetime.now().date())}],
            actions=[{"type": "navigate", "label": "查看文书", "url": "/pages/documents/index"}],
            undo={"tool": "generate_document", "entity": "document", "id": doc_id},
            data=result,
        )

    # 单条记录 → 省统表文书
    record_id = args.get("record_id")
    stmt = select(WorkRecord).where(WorkRecord.project_id == ctx.project_id)
    if record_id not in (None, ""):
        try:
            stmt = stmt.where(WorkRecord.id == int(record_id))
        except (TypeError, ValueError):
            return _need_more(["记录编号"], f"没看懂记录编号「{record_id}」，请换成数字或说「生成监理日志」。")
    else:
        stmt = stmt.order_by(WorkRecord.id.desc())
    record = (await ctx.db.execute(stmt.limit(1))).scalar_one_or_none()
    if record is None:
        return _ok("当前项目还没有工作记录，先在「工作记录 → +新建」建一条，再说「生成监理日志」。")

    from app.routers.form_doc import generate_form_from_record
    try:
        doc, spec, content = await generate_form_from_record(
            ctx.db, ctx.user, ctx.project, record, form_code, None
        )
    except Exception as exc:  # noqa: BLE001 - 渲染失败给可读提示
        logger.warning("聊天生成文书失败: %s", type(exc).__name__)
        return _ok(f"生成失败：{getattr(exc, 'detail', None) or exc}。可到「工作记录」页选中记录后手动生成。")
    await _audit(ctx.db, ctx.user, "agent_write", {
        "tool": "generate_document", "entity": "document", "id": doc.id,
    })
    await ctx.db.commit()

    return _ok(
        f"已基于记录 #{record.id}（{record.record_type}）生成「{spec.name}」#{doc.id}，编号 {content.get('doc_number')}。",
        card={"kind": "document", "id": doc.id, "title": doc.title,
              "sub": spec.name, "status": doc.status, "time": str(datetime.now().date())},
        cards=[{"kind": "document", "id": doc.id, "title": doc.title,
                "sub": spec.name, "status": doc.status, "time": str(datetime.now().date())}],
        actions=[{"type": "navigate", "label": "查看文书", "url": "/pages/documents/index"}],
        undo={"tool": "generate_document", "entity": "document", "id": doc.id},
        data={"doc_id": doc.id, "record_id": record.id},
    )


async def undo_entity(ctx: ToolContext, entity: str, obj_id: int) -> dict:
    """按卡片上的「撤销」按钮精确回滚一个对象（仅限本人创建、24 小时内）。

    与 ``undo_last_action`` 共用同一套删除规则，避免两条路径漂移。
    """
    entity = (entity or "").strip()
    label = {"work_record": "工作记录", "parallel_inspection": "平行检验记录", "hazard": "隐患记录", "document": "文书"}.get(entity, entity)

    if entity == "work_record":
        obj = await ctx.db.get(WorkRecord, obj_id)
        if obj is None or obj.creator_id != ctx.user.id:
            return _ok("这条记录已不存在或不属于你，无需撤销。")
        if obj.created_at and datetime.now() - obj.created_at > timedelta(hours=24):
            return _ok("这条记录创建已超过 24 小时，为安全起见不再自动撤销，请到页面手动处理。")
        await ctx.db.execute(sql_text(
            "UPDATE task_assignments SET work_record_id = NULL WHERE work_record_id = :rid"
        ), {"rid": obj_id})
        await ctx.db.delete(obj)
    elif entity == "parallel_inspection":
        obj = await ctx.db.get(ParallelInspection, obj_id)
        if obj is None or obj.user_id != ctx.user.id:
            return _ok("这条平行检验记录已不存在或不属于你，无需撤销。")
        if obj.created_at and datetime.now() - obj.created_at > timedelta(hours=24):
            return _ok("这条记录创建已超过 24 小时，为安全起见不再自动撤销，请到页面手动处理。")
        if obj.generated_doc_id:
            doc = await ctx.db.get(Document, obj.generated_doc_id)
            if doc and doc.status != DocStatus.DRAFT.value:
                return _ok("这条记录已进入文书流程，不能撤销。")
            if doc:
                obj.generated_doc_id = None
                await ctx.db.flush()
                await ctx.db.delete(doc)
        await ctx.db.delete(obj)
        await _audit(ctx.db, ctx.user, "agent_undo", {"tool": "undo_entity", "entity": entity, "id": obj_id})
        await ctx.db.commit()
        return _ok(f"已撤销刚才创建的{label}（#{obj_id}）。", data={"entity": entity, "id": obj_id})
    if entity == "hazard":
        obj = await ctx.db.get(Hazard, obj_id)
        if obj is None or obj.reporter_id != ctx.user.id:
            return _ok("这条隐患已不存在或不属于你，无需撤销。")
        if obj.created_at and datetime.now() - obj.created_at > timedelta(hours=24):
            return _ok("这条隐患创建已超过 24 小时，为安全起见不再自动撤销，请到页面手动处理。")
        await ctx.db.execute(sql_text(
            "UPDATE image_records SET hazard_id = NULL WHERE hazard_id = :hid"
        ), {"hid": obj_id})
        await ctx.db.execute(sql_text(
            "UPDATE rectifications SET hazard_id = NULL WHERE hazard_id = :hid"
        ), {"hid": obj_id})
        await ctx.db.delete(obj)
        await _audit(ctx.db, ctx.user, "agent_undo", {"tool": "undo_entity", "entity": entity, "id": obj_id})
        await ctx.db.commit()
        return _ok(f"已撤销刚才创建的{label}（#{obj_id}）。", data={"entity": entity, "id": obj_id})
    if entity == "document":
        obj = await ctx.db.get(Document, obj_id)
        if obj is None or obj.creator_id != ctx.user.id:
            return _ok("这份文书已不存在或不属于你，无需撤销。")
        if obj.status != DocStatus.DRAFT.value:
            return _ok(f"这份文书当前状态为「{obj.status}」，已进入流程不能撤销。")
        await ctx.db.execute(sql_text(
            "UPDATE work_records SET generated_doc_id = NULL WHERE generated_doc_id = :did"
        ), {"did": obj_id})
        await ctx.db.delete(obj)
        await _audit(ctx.db, ctx.user, "agent_undo", {"tool": "undo_entity", "entity": entity, "id": obj_id})
        await ctx.db.commit()
        return _ok(f"已撤销刚才创建的{label}（#{obj_id}）。", data={"entity": entity, "id": obj_id})
    return _ok(f"「{label}」类型暂不支持撤销。")


async def _undo_last_action(ctx: ToolContext, args: dict) -> dict:
    """撤销本用户最近一次经聊天创建的记录/隐患/文书（24 小时内）。"""
    audits = (await ctx.db.execute(
        select(SecurityAudit)
        .where(SecurityAudit.user_id == ctx.user.id, SecurityAudit.action == "agent_write")
        .order_by(SecurityAudit.id.desc())
        .limit(10)
    )).scalars().all()
    undone = {
        (json.loads(a.detail or "{}").get("entity"), json.loads(a.detail or "{}").get("id"))
        for a in (await ctx.db.execute(
            select(SecurityAudit).where(
                SecurityAudit.user_id == ctx.user.id, SecurityAudit.action == "agent_undo"
            ).order_by(SecurityAudit.id.desc()).limit(10)
        )).scalars().all()
    }
    target = None
    for a in audits:
        try:
            detail = json.loads(a.detail or "{}")
        except ValueError:
            continue
        key = (detail.get("entity"), detail.get("id"))
        if key in undone or not key[0] or key[1] is None:
            continue
        if a.created_at and datetime.now() - a.created_at > timedelta(hours=24):
            continue
        target = detail
        break
    if target is None:
        return _ok("没有找到可撤销的聊天创建记录（只支持撤销 24 小时内由聊天新建的记录/隐患/文书）。")

    entity, obj_id = target["entity"], int(target["id"])
    label = {"work_record": "工作记录", "parallel_inspection": "平行检验记录", "hazard": "隐患记录", "document": "文书"}.get(entity, entity)
    if entity == "work_record":
        obj = await ctx.db.get(WorkRecord, obj_id)
        if obj is None or obj.creator_id != ctx.user.id:
            return _ok(f"这条{label}已不存在或不属于你，无需撤销。")
        # 无级联外键：先解绑引用，再删本体
        await ctx.db.execute(sql_text(
            "UPDATE task_assignments SET work_record_id = NULL WHERE work_record_id = :rid"
        ), {"rid": obj_id})
        await ctx.db.delete(obj)
    elif entity == "parallel_inspection":
        obj = await ctx.db.get(ParallelInspection, obj_id)
        if obj is None or obj.user_id != ctx.user.id:
            return _ok(f"这条{label}已不存在或不属于你，无需撤销。")
        if obj.created_at and datetime.now() - obj.created_at > timedelta(hours=24):
            return _ok(f"这条{label}创建已超过 24 小时，为安全起见不再自动撤销，请到页面手动处理。")
        if obj.generated_doc_id:
            doc = await ctx.db.get(Document, obj.generated_doc_id)
            if doc and doc.status != DocStatus.DRAFT.value:
                return _ok(f"这条{label}已进入文书流程，不能撤销。")
            if doc:
                obj.generated_doc_id = None
                await ctx.db.flush()
                await ctx.db.delete(doc)
        await ctx.db.delete(obj)
    elif entity == "hazard":
        obj = await ctx.db.get(Hazard, obj_id)
        if obj is None or obj.reporter_id != ctx.user.id:
            return _ok(f"这条{label}已不存在或不属于你，无需撤销。")
        await ctx.db.execute(sql_text(
            "UPDATE image_records SET hazard_id = NULL WHERE hazard_id = :hid"
        ), {"hid": obj_id})
        await ctx.db.execute(sql_text(
            "UPDATE rectifications SET hazard_id = NULL WHERE hazard_id = :hid"
        ), {"hid": obj_id})
        await ctx.db.delete(obj)
    elif entity == "document":
        obj = await ctx.db.get(Document, obj_id)
        if obj is None or obj.creator_id != ctx.user.id or obj.status != DocStatus.DRAFT.value:
            return _ok(f"这份{label}已不存在、不属于你，或已进入审批流程，不能撤销。")
        await ctx.db.execute(sql_text(
            "UPDATE work_records SET generated_doc_id = NULL WHERE generated_doc_id = :did"
        ), {"did": obj_id})
        await ctx.db.delete(obj)
    else:
        return _ok("这条记录类型暂不支持撤销。")

    await _audit(ctx.db, ctx.user, "agent_undo", {"tool": "undo_last_action", "entity": entity, "id": obj_id})
    await ctx.db.commit()
    return _ok(f"已撤销刚才创建的{label}（#{obj_id}）。", data={"entity": entity, "id": obj_id})


# ---------------------------------------------------------------------------
# 读工具
# ---------------------------------------------------------------------------

async def _query_work_records(ctx: ToolContext, args: dict) -> dict:
    stmt = select(WorkRecord).where(WorkRecord.project_id == ctx.project_id)
    section = str(args.get("section") or "").strip()
    if section:
        stmt = stmt.where(WorkRecord.section.like(f"%{section}%"))
    keyword = str(args.get("keyword") or "").strip()
    if keyword:
        stmt = stmt.where(WorkRecord.work_content.like(f"%{keyword}%"))
    rtype = str(args.get("record_type") or "").strip()
    if rtype:
        norm = _norm_record_type(rtype)
        if norm:
            stmt = stmt.where(WorkRecord.record_type == norm)
        elif "待定" in rtype:
            stmt = stmt.where(WorkRecord.record_type == "待定")
    total = len((await ctx.db.execute(stmt)).scalars().all())
    rows = (await ctx.db.execute(stmt.order_by(WorkRecord.id.desc()).limit(10))).scalars().all()
    if not rows:
        return _ok("当前项目没有符合条件的工作记录。可以说「记一条巡视：3号楼二层钢筋绑扎，天气晴」。")
    lines = [f"共 {total} 条工作记录，最近 {len(rows)} 条："]
    for r in rows:
        where = " ".join(x for x in (r.section, r.location) if x) or "未填写区域"
        lines.append(f"{r.id}. [{r.record_type}] {where} {r.status}｜{(r.work_content or '')[:40]}")
    return _ok("\n".join(lines), cards=[{
        "kind": "work_record", "id": r.id, "title": f"{r.record_type}记录",
        "sub": " ".join(x for x in (r.section, r.location) if x) or "未填写区域",
        "status": r.status, "time": str(r.record_date or ""),
    } for r in rows], actions=[{"type": "navigate", "label": "查看工作记录", "url": "/pages/work/index"}])


async def _query_hazards(ctx: ToolContext, args: dict) -> dict:
    stmt = select(Hazard).where(Hazard.project_id == ctx.project_id)
    status = str(args.get("status") or "").strip()
    if status:
        stmt = stmt.where(Hazard.status.like(f"%{status}%"))
    section = str(args.get("section") or "").strip()
    if section:
        stmt = stmt.where(Hazard.section.like(f"%{section}%"))
    rows = (await ctx.db.execute(stmt.order_by(Hazard.id.desc()).limit(10))).scalars().all()
    total = len((await ctx.db.execute(stmt)).scalars().all())
    if not rows:
        return _ok("当前项目没有符合条件的隐患记录。")
    lines = [f"共 {total} 条隐患，最近 {len(rows)} 条："]
    for h in rows:
        lines.append(f"{h.id}. {h.risk_status or ''}｜{(h.top1_risk or '')[:40]}｜{h.section or ''} {h.location or ''}｜{h.status}｜闭环阶段：{('已回复' if h.reply_doc_id else '已发整改通知' if (h.rectification_doc_id or h.rectify_doc_id) else '未发整改通知')}")
    return _ok("\n".join(lines), cards=[{
        "kind": "hazard", "id": h.id, "title": h.top1_risk or "隐患",
        "sub": " ".join(x for x in (h.section, h.location) if x) or "未填写位置",
        "status": h.status, "time": str(h.created_at.date() if h.created_at else ""),
    } for h in rows], actions=[{"type": "navigate", "label": "查看隐患", "url": "/pages/hazards/index"}])


async def _query_documents(ctx: ToolContext, args: dict) -> dict:
    stmt = select(Document).where(Document.project_id == ctx.project_id)
    doc_type = str(args.get("doc_type") or args.get("keyword") or "").strip()
    if doc_type:
        stmt = stmt.where(Document.doc_type.like(f"%{doc_type}%"))
    rows = (await ctx.db.execute(stmt.order_by(Document.id.desc()).limit(10))).scalars().all()
    total = len((await ctx.db.execute(stmt)).scalars().all())
    if not rows:
        return _ok("当前项目没有符合条件的文书。可以说「把最后一条记录生成监理日志」。")
    lines = [f"共 {total} 篇文书，最近 {len(rows)} 篇："]
    for d in rows:
        lines.append(f"{d.id}. {d.title}｜{d.doc_type}｜{d.status}｜{d.doc_number or '无编号'}")
    return _ok("\n".join(lines), cards=[{
        "kind": "document", "id": d.id, "title": d.title, "sub": d.doc_type,
        "status": d.status, "time": str(d.created_at.date() if d.created_at else ""),
    } for d in rows], actions=[{"type": "navigate", "label": "查看文书", "url": "/pages/documents/index"}])


async def _query_regulation(ctx: ToolContext, args: dict) -> dict:
    keyword = str(args.get("keyword") or args.get("question") or ctx.message or "").strip()
    if not keyword:
        return _need_more(["检索关键词"], "想查哪条规范？例如「模板支撑验收要求」。")
    try:
        from app.services import knowledge_service
        hits = knowledge_service.search_by_content(keyword) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("规范检索失败: %s", type(exc).__name__)
        hits = []
    if not hits:
        return _ok(f"没有检索到与「{keyword}」相关的规范条目，换个说法或到「资料库」手动检索。")
    lines = [f"与「{keyword}」相关的规范依据（{len(hits)} 条）："]
    for i, hit in enumerate(hits[:3], 1):
        name = hit.get("name") or hit.get("filename") or "规范"
        snippet = (hit.get("snippet") or "").strip().replace("\n", " ")
        lines.append(f"{i}. {name}：{snippet[:150]}")
    lines.append("完整条文请到「资料库」检索原文。")
    return _ok("\n".join(lines), data={"count": len(hits)})


async def _review_plan_doc(ctx: ToolContext, args: dict) -> dict:
    """依据「严重缺陷清单」审查专项施工方案（建办质〔2024〕63 号）。"""
    from app.services import severe_defect_review

    text = str(args.get("content") or args.get("plan_text") or ctx.message or "").strip()
    if not text:
        return _need_more(
            ["方案内容"], "请把专项施工方案正文粘贴给我（或上传方案文件），我按 80 条严重缺陷清单逐条帮你核对。"
        )
    work_type = str(args.get("work_type") or "").strip()
    try:
        report = await severe_defect_review.review_plan(text, work_type=work_type)
    except Exception as exc:  # noqa: BLE001
        logger.exception("方案审查失败")
        return _ok(f"方案审查暂时不可用（{type(exc).__name__}），请稍后重试。")

    if not report.get("ok"):
        return _ok(report.get("error") or "方案审查未能完成。")

    failed_items = [i for i in report["items"] if i.get("passed") is False]
    lines = [
        f"已按严重缺陷清单对照 {report['total_check']} 条适用条款审查，"
        f"发现严重缺陷 {report['failed']} 条，结论：{report['conclusion']}。",
        report.get("summary", ""),
    ]
    for i, item in enumerate(failed_items[:8], 1):
        problem = (item.get("problem") or "").strip()
        lines.append(
            f"{i}. 【{item.get('category')}】{item.get('clause')}"
            + (f"\n   → {problem}" if problem else "")
        )
    if len(failed_items) > 8:
        lines.append(f"……其余 {len(failed_items) - 8} 条未通过，见报告详情。")
    return _ok(
        "\n".join(line for line in lines if line.strip()),
        data=report,
        card={
            "type": "plan_review",
            "title": "专项施工方案审查报告",
            "conclusion": report["conclusion"],
            "failed": report["failed"],
            "total": report["total_check"],
            "items": report["items"],
        },
    )


async def _query_weather(ctx: ToolContext, args: dict) -> dict:
    target = _parse_date(args.get("date")) or datetime.now().date()
    try:
        from app.services import weather as weather_service
        data = await weather_service.daily_weather(ctx.project, target)
    except Exception as exc:  # noqa: BLE001
        logger.warning("天气查询失败: %s", type(exc).__name__)
        return _ok("天气服务暂时不可用，可手动填写天气。")
    if not data.get("weather"):
        return _ok(f"{target} 暂无气象数据（项目未配置坐标或供应商未开通），可手动填写天气。")
    return _ok(
        f"{target} 天气：{data.get('weather')}，{data.get('temp_low')}~{data.get('temp_high')}℃（来源：{data.get('weather_source')}）。",
        data=data,
    )


async def _navigate(ctx: ToolContext, args: dict) -> dict:
    raw = str(args.get("page") or args.get("target") or "").strip()
    url = _PAGE_ALIASES.get(raw)
    if url is None:
        for key, val in _PAGE_ALIASES.items():
            if key in raw:
                url, raw = val, key
                break
    if not url:
        return _ok("要打开哪个页面？可以说「打开工作记录」「打开隐患识别」「打开文书」「打开台账」「打开省统表录入」。")
    return _ok(f"已为你打开「{raw}」。", actions=[{"type": "navigate", "label": raw, "url": url}])


# ---------------------------------------------------------------------------
# 追问暂存（pending draft）—— 解决「无状态追问」丢参
# ---------------------------------------------------------------------------
# 背景：need_more 之后，服务端原本靠「最近 6 轮文本历史」让模型重新拼参数。
# 隐患：历史窗口滑出（中途夹闲聊）、模型每轮抽参不一致 → 反复追问、丢字段。
# 做法：need_more 时把已收集参数落库（按 用户+工具 唯一），下一轮把新抽参数
# 合并到草稿上再执行；成功即清空，超 TTL 视为过期。
# 存储：``agent_drafts`` 表（见 models/agent_draft.py）。早期版本放在进程内存，
# 因 reload/重启即丢、多进程不安全，已改为落库；重载不再影响用户已填内容。
_PENDING_TTL = timedelta(hours=24)
async def record_conversation_message(db: AsyncSession, user_id: Any,
                                      conversation_id: str | None,
                                      message: str,
                                      images: list[str] | None = None) -> list[dict[str, str]]:
    """Persist a display turn and keep a bounded raw trail for model context.

    ``images``（可选）是随消息上传后的服务端媒体地址（相对路径即可），
    只进展示历史（content_json），不进模型上下文——模型读的是素材文字转述，
    不需要图片 URL。
    """
    if not user_id or not conversation_id:
        return []
    display_images = [
        str(url).strip() for url in (images or [])
        if isinstance(url, str) and str(url).strip()
    ][:9]
    stmt = select(AgentConversationMemory).where(
        AgentConversationMemory.user_id == int(user_id),
        AgentConversationMemory.conversation_id == conversation_id,
    )
    row = (await db.execute(stmt)).scalars().first()
    try:
        history = json.loads(row.messages_json or "[]") if row else []
    except (TypeError, ValueError):
        history = []
    history = history if isinstance(history, list) else []
    previous = [item for item in history if isinstance(item, dict) and item.get("text")]
    history = append_context_message({"messages": previous}, message).get("messages", [])
    if row is None:
        row = AgentConversationMemory(
            user_id=int(user_id), conversation_id=conversation_id,
        )
        db.add(row)
    row.messages_json = json.dumps(history, ensure_ascii=False)
    row.updated_at = datetime.now()
    db.add(AgentConversationMessage(
        user_id=int(user_id), conversation_id=conversation_id,
        role="user", created_at=datetime.now(),
        content_json=json.dumps({
            "role": "user", "text": str(message or "").strip(),
            **({"images": display_images} if display_images else {}),
            "at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False),
    ))
    await db.commit()
    current = str(message or "").strip()
    if current and history and history[-1].get("text") == current:
        return history[:-1]
    return history


async def append_conversation_response(db: AsyncSession, user_id: Any,
                                       conversation_id: str | None,
                                       response: dict[str, Any]) -> None:
    """Persist the exact structured assistant result shown to the user."""
    if not user_id or not conversation_id:
        return
    now = datetime.now()
    content = {
        "role": "assistant",
        "text": str(response.get("reply") or ""),
        "response": response,
        "at": now.isoformat(timespec="seconds"),
    }
    db.add(AgentConversationMessage(
        user_id=int(user_id), conversation_id=conversation_id,
        role="assistant", content_json=json.dumps(content, ensure_ascii=False),
        created_at=now,
    ))
    await db.commit()


def _conversation_content(row: AgentConversationMessage) -> dict[str, Any]:
    try:
        content = json.loads(row.content_json or "{}")
    except (TypeError, ValueError):
        content = {}
    if not isinstance(content, dict):
        content = {}
    content.setdefault("role", row.role)
    content.setdefault("text", "")
    content["at"] = row.created_at.isoformat(timespec="seconds") if row.created_at else ""
    return content


async def list_conversation_summaries(db: AsyncSession, user_id: Any,
                                      limit: int = 30) -> list[dict[str, Any]]:
    """List only the current user's conversations, newest first."""
    if not user_id:
        return []
    capped_limit = max(1, min(int(limit or 30), 100))
    latest_ids = select(
        func.max(AgentConversationMessage.id).label("max_id")
    ).where(
        AgentConversationMessage.user_id == int(user_id)
    ).group_by(
        AgentConversationMessage.conversation_id
    ).subquery()
    latest_rows = (await db.execute(select(AgentConversationMessage).join(
        latest_ids, AgentConversationMessage.id == latest_ids.c.max_id,
    ).order_by(AgentConversationMessage.created_at.desc()).limit(capped_limit))).scalars().all()
    conversation_ids = [row.conversation_id for row in latest_rows]
    rows = []
    if conversation_ids:
        rows = (await db.execute(select(AgentConversationMessage).where(
            AgentConversationMessage.user_id == int(user_id),
            AgentConversationMessage.conversation_id.in_(conversation_ids),
        ).order_by(AgentConversationMessage.id.asc()))).scalars().all()
    buckets: dict[str, list[AgentConversationMessage]] = {}
    for row in rows:
        buckets.setdefault(row.conversation_id, []).append(row)
    summaries: list[dict[str, Any]] = []
    for latest in latest_rows:
        messages = buckets.get(latest.conversation_id, [])
        first_text = next((
            _conversation_content(item).get("text", "") for item in messages
            if item.role == "user" and _conversation_content(item).get("text")
        ), "")
        title = str(first_text or "新对话").strip().replace("\n", " ")[:40]
        summaries.append({
            "conversation_id": latest.conversation_id,
            "title": title,
            "message_count": len(messages),
            "updated_at": latest.created_at.isoformat(timespec="seconds"),
            "updatedAt": latest.created_at.strftime("%m/%d %H:%M"),
        })
    # Older deployments only kept the bounded model-context trail. Surface
    # those conversations too, still scoped to the exact same account.
    known_ids = {item["conversation_id"] for item in summaries}
    memory_rows = (await db.execute(select(AgentConversationMemory).where(
        AgentConversationMemory.user_id == int(user_id),
    ).order_by(AgentConversationMemory.updated_at.desc()).limit(capped_limit))).scalars().all()
    for memory in memory_rows:
        if memory.conversation_id in known_ids:
            continue
        try:
            legacy = json.loads(memory.messages_json or "[]")
        except (TypeError, ValueError):
            legacy = []
        legacy_messages = [
            item for item in legacy
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        if not legacy_messages:
            continue
        summaries.append({
            "conversation_id": memory.conversation_id,
            "title": str(legacy_messages[0].get("text") or "新对话").replace("\n", " ")[:40],
            "message_count": len(legacy_messages),
            "updated_at": memory.updated_at.isoformat(timespec="seconds"),
            "updatedAt": memory.updated_at.strftime("%m/%d %H:%M"),
        })
    summaries.sort(key=lambda item: item["updated_at"], reverse=True)
    return summaries[:capped_limit]


async def get_conversation_detail(db: AsyncSession, user_id: Any,
                                  conversation_id: str) -> dict[str, Any] | None:
    """Return a conversation only when it belongs to ``user_id``."""
    if not user_id or not conversation_id:
        return None
    rows = (await db.execute(select(AgentConversationMessage).where(
        AgentConversationMessage.user_id == int(user_id),
        AgentConversationMessage.conversation_id == conversation_id,
    ).order_by(AgentConversationMessage.id.asc()))).scalars().all()
    messages = [_conversation_content(row) for row in rows]
    if not messages:
        # Compatibility for conversations recorded before display history existed.
        memory = (await db.execute(select(AgentConversationMemory).where(
            AgentConversationMemory.user_id == int(user_id),
            AgentConversationMemory.conversation_id == conversation_id,
        ))).scalars().first()
        if memory:
            try:
                legacy = json.loads(memory.messages_json or "[]")
            except (TypeError, ValueError):
                legacy = []
            messages = [
                {"role": "user", "text": str(item.get("text") or ""), "at": str(item.get("at") or "")}
                for item in legacy if isinstance(item, dict) and item.get("text")
            ]
    if not messages:
        return None
    last = messages[-1]
    first_user = next((item for item in messages if item.get("role") == "user" and item.get("text")), {})
    updated_at = str(last.get("at") or "")
    return {
        "conversation_id": conversation_id,
        "title": str(first_user.get("text") or "新对话").replace("\n", " ")[:40],
        "message_count": len(messages),
        "updated_at": updated_at,
        "messages": messages,
    }


async def delete_conversation(db: AsyncSession, user_id: Any,
                              conversation_id: str) -> bool:
    """Delete display history and model context for one owned conversation."""
    if not user_id or not conversation_id:
        return False
    result = await db.execute(delete(AgentConversationMessage).where(
        AgentConversationMessage.user_id == int(user_id),
        AgentConversationMessage.conversation_id == conversation_id,
    ))
    await db.execute(delete(AgentConversationMemory).where(
        AgentConversationMemory.user_id == int(user_id),
        AgentConversationMemory.conversation_id == conversation_id,
    ))
    await db.commit()
    return bool(result.rowcount)


def merge_args(pending_args: dict | None, new_args: dict | None) -> dict:
    """草稿参数打底，本轮新值非空则覆盖（模型新抽的优先）。"""
    merged: dict[str, Any] = dict(pending_args or {})
    for key, value in (new_args or {}).items():
        if str(value if value is not None else "").strip():
            merged[key] = value
    # extra_fields 是嵌套对象：必须合并而不是整体覆盖，否则新一轮会丢掉上一轮追问到的字段
    old_extra = (pending_args or {}).get(_EXTRA_ARG)
    new_extra = (new_args or {}).get(_EXTRA_ARG)
    if isinstance(old_extra, dict) or isinstance(new_extra, dict):
        merged[_EXTRA_ARG] = {**(old_extra or {}), **(new_extra or {})}
    return merged


def _draft_clean_args(args: dict | None) -> dict:
    """空值不入草稿（模型常把没抽到的参数写成空串，落进去会覆盖后续真实值）。"""
    return {k: v for k, v in (args or {}).items()
            if str(v if v is not None else "").strip()}


async def _draft_row(db: AsyncSession, user_id: Any, tool: str,
                     conversation_id: str | None = None) -> AgentDraft | AgentConversationDraft | None:
    model = AgentConversationDraft if conversation_id else AgentDraft
    stmt = select(model).where(model.user_id == int(user_id), model.tool == tool)
    if conversation_id:
        stmt = stmt.where(model.conversation_id == conversation_id)
    return (await db.execute(stmt)).scalars().first()


async def stash_pending(db: AsyncSession, user_id: Any, tool: str,
                        args: dict | None, missing: list[str] | None,
                        *, message: str = "",
                        fact_args: dict | None = None,
                        conversation_id: str | None = None) -> None:
    """need_more 时暂存「已收集参数 + 仍缺字段」，按会话与工具隔离。"""
    if not user_id or not tool:
        return
    row = await _draft_row(db, user_id, tool, conversation_id)
    if row is None:
        if conversation_id:
            row = AgentConversationDraft(
                user_id=int(user_id), conversation_id=conversation_id, tool=tool,
            )
        else:
            row = AgentDraft(user_id=int(user_id), tool=tool)
        db.add(row)
    clean_args = _draft_clean_args(args)
    try:
        old_args = json.loads(row.args_json or "{}")
    except (TypeError, ValueError):
        old_args = {}
    old_context = old_args.pop("_context", {}) if isinstance(old_args, dict) else {}
    if not isinstance(old_context, dict):
        old_context = {}
    old_facts = old_context.get("facts") if isinstance(old_context.get("facts"), list) else []
    # Merge model extraction first, then the deterministic user-language pass.
    # The latter is intentionally last so a model omission cannot overwrite a
    # date or location that the user stated explicitly.
    model_facts = extract_user_facts(
        _draft_clean_args(fact_args) if fact_args is not None else clean_args,
        message_ref=message,
    )
    explicit_facts = extract_explicit_message_facts(message, message_ref=message)
    facts = merge_user_facts(old_facts, model_facts + explicit_facts)
    context = {
        "facts": facts,
        "version": int(old_context.get("version") or 0) + 1,
        "last_message": message[:2000] if message else old_context.get("last_message", ""),
    }
    context = append_context_message(context | {"messages": old_context.get("messages", [])}, message)
    clean_args["_context"] = context
    row.args_json = json.dumps(clean_args, ensure_ascii=False)
    row.missing_json = json.dumps([str(x) for x in (missing or [])], ensure_ascii=False)
    row.updated_at = datetime.now()
    await db.commit()


async def get_pending(db: AsyncSession, user_id: Any,
                      tool: str | None = None,
                      conversation_id: str | None = None) -> tuple[str, dict] | None:
    """取未过期草稿：(工具名, {args, missing})；tool 给定时只匹配该工具。过期即删。"""
    if not user_id:
        return None
    model = AgentConversationDraft if conversation_id else AgentDraft
    stmt = select(model).where(model.user_id == int(user_id))
    if tool:
        stmt = stmt.where(model.tool == tool)
    if conversation_id:
        stmt = stmt.where(model.conversation_id == conversation_id)
    row = (await db.execute(stmt.order_by(model.updated_at.desc()))).scalars().first()
    if row is None:
        return None
    if datetime.now() - row.updated_at > _PENDING_TTL:
        await db.delete(row)
        await db.commit()
        return None
    try:
        args = json.loads(row.args_json or "{}")
    except (TypeError, ValueError):
        args = {}
    try:
        missing = json.loads(row.missing_json or "[]")
    except (TypeError, ValueError):
        missing = []
    context = args.pop("_context", {}) if isinstance(args, dict) else {}
    if not isinstance(context, dict):
        context = {}
    return row.tool, {
        "args": args if isinstance(args, dict) else {},
        "missing": missing if isinstance(missing, list) else [],
        "facts": context.get("facts") if isinstance(context.get("facts"), list) else [],
        "version": int(context.get("version") or 1),
        "last_message": str(context.get("last_message") or ""),
        "messages": context.get("messages") if isinstance(context.get("messages"), list) else [],
    }


async def clear_pending(db: AsyncSession, user_id: Any, tool: str | None = None,
                        conversation_id: str | None = None) -> None:
    """清空草稿：成功落库后、或用户切换了意图时调用。"""
    if not user_id:
        return
    models = [AgentConversationDraft] if conversation_id else [AgentConversationDraft, AgentDraft]
    deleted = False
    for model in models:
        stmt = select(model).where(model.user_id == int(user_id))
        if tool:
            stmt = stmt.where(model.tool == tool)
        if conversation_id:
            stmt = stmt.where(model.conversation_id == conversation_id)
        for row in (await db.execute(stmt)).scalars().all():
            await db.delete(row)
            deleted = True
    if deleted:
        await db.commit()


# 草稿对应的中文名（前端「待补全」展示用；未知工具回退原名）
_TOOL_LABELS = {
    "create_work_record": "工作记录",
    "create_hazard": "安全隐患",
    "create_hazard_rectification_doc": "隐患整改通知",
    "create_provform_doc": "省统表文书",
    "generate_document": "文书",
    "undo_last_action": "撤销操作",
}


def tool_label(tool: str) -> str:
    return _TOOL_LABELS.get(tool, tool)


def _draft_form_code(tool: str, args: dict) -> str | None:
    """草稿 → 省统表 form_code（供小程序「用表单填写」直接跳分步表单）。

    推断不出来就返回 None，前端按原逻辑回聊天里补全，功能不退化。
    """
    if tool == "create_hazard":
        return "gdaq_30308_yinhuan"
    if tool == "create_work_record":
        record_type = str(args.get("record_type") or "").strip()
        if not record_type:
            return None
        is_weida = bool(args.get("is_weida")) or record_type == "危大"
        return record_field_spec.form_for_record_type(record_type, is_weida)
    return None


async def list_pending(db: AsyncSession, user_id: Any,
                       conversation_id: str | None = None) -> list[dict]:
    """返回该用户所有未过期草稿（按 updated_at desc）。过期的就地删除并回收。"""
    if not user_id:
        return []
    models = [AgentConversationDraft] if conversation_id else [AgentConversationDraft, AgentDraft]
    rows = []
    for model in models:
        stmt = select(model).where(model.user_id == int(user_id))
        if conversation_id:
            stmt = stmt.where(model.conversation_id == conversation_id)
        rows.extend((model, row) for row in (await db.execute(
            stmt.order_by(model.updated_at.desc())
        )).scalars().all())
    out: list[dict] = []
    now = datetime.now()
    expired: list[tuple[type, int]] = []
    for model, row in rows:
        if now - row.updated_at > _PENDING_TTL:
            expired.append((model, row.id))
            continue
        try:
            args = json.loads(row.args_json or "{}")
        except (TypeError, ValueError):
            args = {}
        try:
            missing = json.loads(row.missing_json or "[]")
        except (TypeError, ValueError):
            missing = []
        context = args.pop("_context", {}) if isinstance(args, dict) else {}
        if not isinstance(context, dict):
            context = {}
        out.append({
            "id": row.id,
            "conversation_id": getattr(row, "conversation_id", None),
            "tool": row.tool,
            "label": tool_label(row.tool),
            "form_code": _draft_form_code(row.tool, args if isinstance(args, dict) else {}),
            "args": args if isinstance(args, dict) else {},
            "missing": missing if isinstance(missing, list) else [],
            "facts": context.get("facts") if isinstance(context.get("facts"), list) else [],
            "version": int(context.get("version") or 1),
            "messages": context.get("messages") if isinstance(context.get("messages"), list) else [],
            "updated_at": row.updated_at.isoformat(),
        })
    if expired:
        for model, rid in expired:
            obj = await db.get(model, rid)
            if obj:
                await db.delete(obj)
        await db.commit()
    return out


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "create_work_record",
        "新建工作记录或平行检验记录（巡视/旁站/验收/见证/平行检验/起重机械安拆检查/待定）。用户描述现场情况、要求「记录一下/记一条」时用。"
        "record_type 必须优先使用用户明确说出的类型；图片/水印素材本身不能推断记录类型，类型不明时先澄清。",
        {"record_type": "记录类型：巡视/旁站/验收/见证/平行检验/起重机械安拆检查（用户未明确时必须先追问，不能因有图片默认巡视）",
         "*work_content": "施工内容或现场情况",
         "section": "施工区域，如 3号楼二层", "location": "具体位置", "weather": "天气",
         "problems": "巡视发现的问题及处理情况原文；用户说「符合要求/无异常」时也必须原样传入（如 problems=符合要求），不要省略",
         "treatment": "处理意见及措施", "worker_count": "施工人数（整数）",
         "record_date": "业务日期 YYYY-MM-DD（默认今天）",
         "inspection_start_time": "巡视开始时间 HH:MM（巡视记录，如 9:00）",
         "inspection_end_time": "巡视结束时间 HH:MM（巡视记录，如 11:00；用户给时间段也可只填 inspection_time）",
         "inspection_time": "巡视时间段原文（如「9:00 到 17:30」）；中文时间如「九点到五点半」也原样传入，服务端会换算",
         "station_process": "旁站关键工序（旁站记录）",
         "station_part": "旁站关键部位/施工部位（旁站记录，如 西楼A区十层墙柱、十一层梁板）",
         "station_content": "旁站的关键部位、关键工序施工情况原文（人员/机械/材料/方量等，保留用户原话要点）",
         "station_conclusion": "旁站结论：用户说「符合要求/无异常」时必须原样传入（旁站记录）",
         "station_start_time": "旁站开始时间 HH:MM", "station_end_time": "旁站结束时间 HH:MM",
         "acceptance_result": "验收结果：合格/不合格/待整改（验收记录）",
         "acceptance_participants": "参验单位及人员（验收记录）", "acceptance_basis": "检验依据（验收记录）",
         "witness_material_name": "材料名称（见证记录）：名称与规格分开填，这里只填名称本身，如「钢筋」",
         "witness_material_spec": "规格型号（见证记录）：型号/牌号/直径/等级，如「HRB400 Φ25」；用户说了就拆出来单独填，别并进材料名",
         "witness_quantity": "取样数量（见证记录）", "witness_unit": "计量单位（见证记录）",
         "witness_cert_no": "合格证/检测报告编号（见证记录）",
         "hazard_type": "危大工程名称/类型（危大巡视，如「落地式钢管脚手架工程-一般危大工程」；含类型名会据此带出检查表）",
         "wind": "风力（危大巡视表，如 无风/微风/和风）",
         "subcontract_unit": "专业分包单位（危大巡视表）",
         "extra_fields": "字段表栏目（对象，键=字段 key 如 1_13、10_14；上面没单列参数的栏目都放这里，值填用户原话）"},
        "work_record", "write", _create_work_record,
        ("3号楼二层临边防护，9:00 到 11:00，符合要求，记一条巡视",
         "记一条旁站：屋面防水卷材铺贴，9:00 到 12:00，10人"),
    ),
    ToolSpec(
        "create_hazard",
        "新建隐患记录（文字描述即可，含风险类别/主要风险/证据/复查建议）。",
        {"*description": "现场情况描述", "section": "施工区域", "location": "具体位置"},
        "hazard_ai", "write", _create_hazard,
        ("楼面积水还有碎石，有滑倒风险",),
    ),
    ToolSpec(
        "create_hazard_rectification_doc",
        "从当前项目已有的隐患主记录生成并关联 GDAQ30308 安全隐患整改通知；绝不能独立创建孤立通知单。",
        {"*hazard_id": "当前项目的隐患记录编号", "deadline": "整改期限 YYYY-MM-DD（可省略）"},
        "hazard_ai", "write", _create_hazard_rectification_doc,
        ("给隐患 123 生成安全整改通知",),
    ),
    ToolSpec(
        "create_hazard_reply_doc",
        "从已有隐患整改提交生成 GDAQ20309 整改回复单；必须已有整改通知和真实整改影像/完成时间。",
        {"*hazard_id": "当前项目的隐患记录编号", "rectify_desc": "整改完成情况（可省略，按真实整改记录生成）", "rechecker": "实际复查人（只有已完成复查时填写）"},
        "hazard_ai", "write", _create_hazard_reply_doc,
        ("给隐患 123 生成整改回复单",),
    ),
    ToolSpec(
        "recheck_hazard",
        "对已提交整改影像的隐患执行复查；必须处于待复查状态，结果合格才闭环。",
        {"*hazard_id": "当前项目的隐患记录编号", "*passed": "是否合格 true/false", "remark": "复查意见"},
        "hazard_ai", "write", _recheck_hazard,
        ("复查隐患 123，合格",),
    ),
    ToolSpec(
        "create_provform_doc",
        # 🔴 表式清单用 f-string 从 PROVFORM_CHOICES 动态生成，不再手写 —— 原来手写
        # 一串表名，清单增删后文案就漂移（曾长期写「11 类」而实际只有 10 类）。
        f"凭空新建一张省统表空表，内容由用户**当场口述**的字段决定"
        f"（{'、'.join(n for _c, n in PROVFORM_CHOICES)}）。"
        "🔴 与 generate_document 的分工：本工具用于「没有现成记录/时间范围可依据、"
        "要从零口述填一张表」的场景（如「建一份监理工程师通知单：要求整改脚手架搭设」）；"
        "若用户指代**某条已有记录**（「把这条记录…」）或给了**时间范围**（「今天的/本月」），"
        "应改用 generate_document（它从工作记录或按日/周/月聚合取数，不用用户口述）。"
        "🔴 日期/星期/天气由系统自动预填（日期取今天、星期随之推算、天气按项目坐标联网取实况，"
        "取不到则留空），**不要因为缺这几项就追问用户**；用户若主动说了日期或天气则以其为准。"
        "所以像安全监理日志这类表，只需向用户收齐施工内容、安全检查情况、处理措施、"
        "隐患整改情况、危大工程状况等**业务栏目**即可。"
        "安全隐患整改通知 GDAQ30308 必须通过 create_hazard_rectification_doc 从隐患主记录生成。",
        {"*form_code": f"表式代码或表名，如 gd_b_215_tongzhi 或「监理工程师通知单」"
                       f"（可选：{'、'.join(n for _c, n in PROVFORM_CHOICES)}）",
         "fields": "字段对象，键用表式字段 key（如 1_01/1_15）", "title": "文书标题（可省略）"},
        "work_record", "write", _create_provform_doc,
        ("建一份监理工程师通知单：要求整改脚手架搭设",),
    ),
    ToolSpec(
        "generate_document",
        "生成监理文书，有两条互斥路径，按用户指的是「某一条记录」还是「一段时间」来选："
        "①用户指代**某条具体记录**（「这条记录」「刚才那条」「上面那单」，通常前文刚建过记录、"
        "话里带得到它的 ID）→ 只传 record_id，把那条记录渲染成对应的省统表；"
        "②用户给的是**时间范围**（「今天的」「本周」「本月」「10月的」）→ 只传 period（+可选 date），"
        "按日/周/月聚合当天/当周/当月的全部记录成监理日志、周报或月报。"
        "🔴 判据是「有没有指代到单条记录」，不是文书名叫什么：「把这条记录生成监理日志」走①传 record_id；"
        "「生成今天的监理日志」走②传 period=day。两者不要同时传。"
        "🔴 路径②里「安全监理日志」是**独立表式**（form_code=anquan_jianli_rizhi），"
        "不要退化成普通监理日志：用户说「生成今天的安全监理日志 / 安全日志」时，"
        "period=day 且必须同时传 form_code（表名或 code 都可，服务端会归一）。"
        "🔴 extra_fields 只放**系统取不到、用户当场补充**的字段（如上级/质监站检查情况、"
        "停水停电等其他有关事项）；日期/星期/天气/记录内容由聚合自动取，**不要**塞进 extra_fields，"
        "更不要因为缺这些就去追问用户。",
        {"record_id": "路径①：工作记录 id（用户指代某条记录时必填，从前文里取出那个 ID）",
         "form_code": "指定表式代码或表名（可省略，默认按记录类型/周期选表）；"
                      "路径②生成「安全监理日志」时必填 anquan_jianli_rizhi",
         "period": "路径②：聚合周期 day/week/month 或 日/周/月（用户给时间范围时用）",
         "date": "路径②：聚合基准日期 YYYY-MM-DD（可省略，默认今天）",
         "extra_fields": "路径②可选：用户当场补充、系统取不到的字段，键用表字段 key 或 canonical"
                         "（如 7_12 上级/质监站检查情况、7_13 其他有关事项、weather 天气）"},
        "work_record", "write", _generate_document,
        ("把这条记录生成监理日志", "生成今天的监理日志", "生成本月监理月报",
         "生成今天的安全监理日志"),
    ),
    ToolSpec(
        "undo_last_action",
        "撤销最近一次由聊天新建的记录/隐患/文书（24 小时内）。用户说「撤销」「删掉刚才那条」「说错了」时用。",
        {},
        None, "destructive", _undo_last_action,
        ("撤销", "刚才那条删掉"),
    ),
    ToolSpec(
        "query_work_records",
        "查询工作记录列表（可按区域/类型/关键词筛选，也可问「今天记了几条」）。",
        {"section": "施工区域关键词", "record_type": "记录类型", "keyword": "内容关键词"},
        None, "read", _query_work_records,
        ("我今天记了几条", "3号楼有哪些记录"),
    ),
    ToolSpec(
        "query_hazards",
        "查询隐患记录列表（可按状态/区域筛选，如「未整改的隐患」）。",
        {"status": "状态关键词，如 已提报/整改中/已闭环", "section": "施工区域关键词"},
        None, "read", _query_hazards,
        ("未整改的隐患有哪些",),
    ),
    ToolSpec(
        "query_documents",
        "查询监理文书列表（可按文书类型筛选，如「有没有监理月报」）。",
        {"doc_type": "文书类型关键词，如 监理月报/通知单", "keyword": "标题关键词"},
        None, "read", _query_documents,
        ("有没有监理月报",),
    ),
    ToolSpec(
        "query_regulation",
        "检索规范依据（规范/标准/条文问答）。问「规范怎么要求」「依据是哪条」时用。",
        {"*keyword": "检索关键词", "question": "用户原始问法（可省略）"},
        None, "read", _query_regulation,
        ("模板支撑验收规范怎么要求",),
    ),
    ToolSpec(
        "review_plan_doc",
        "依据住建部「严重缺陷清单」（建办质〔2024〕63 号，80 条红线）审查危大工程专项施工方案，"
        "逐条对照给出通过/不通过与整改建议。用户说「审查方案/审方案/按80条/核对严重缺陷/查方案缺什么」"
        "且提供了方案正文时用。content 放方案全文（用户直接粘贴的文字，或已解析的文档内容）。",
        {"*content": "方案全文/正文（用户粘贴或文档已解析的文本）",
         "work_type": "工程类型（基坑/模板/起重/脚手架/拆除/暗挖/幕墙/人工挖孔桩/钢结构，可省略，服务端自动推断）"},
        "work_record", "read", _review_plan_doc,
        ("审查这份基坑专项施工方案", "帮我按严重缺陷清单查一下这个方案"),
    ),
    ToolSpec(
        "query_weather",
        "查询指定日期（默认今天）项目所在地天气。",
        {"date": "日期 YYYY-MM-DD（可省略）"},
        None, "read", _query_weather,
        ("今天天气怎么样",),
    ),
    ToolSpec(
        "navigate",
        "打开系统页面（工作记录/巡视记录/验收记录/旁站记录/见证记录/隐患/隐患识别/文书/监理文书/台账/AI巡检/规范查询/拍照识别/待办/看板/资料库/知识库/省统表录入/平行检验/我的）。"
        "用户说「打开XX」「查看XX」「XX页面」或只报一个页面名/功能名时用；不要追问用户想干什么，也不要当成知识问答。",
        {"*page": "页面名，如 工作记录/台账/规范查询"},
        None, "read", _navigate,
        ("打开工作记录", "打开台账", "规范查询"),
    ),
)

TOOL_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}


def catalog_for_prompt() -> str:
    """把工具目录压成提示词文本（精简，省 token）。"""
    blocks = []
    for t in TOOLS:
        params = "、".join(f"{k}={v}" for k, v in t.params.items()) or "（无参数）"
        block = f"- {t.name}：{t.desc}\n  参数：{params}"
        if t.examples:
            block += "\n  示例说法：" + " / ".join(t.examples)
        blocks.append(block)
    return "\n".join(blocks)


async def dispatch(ctx: ToolContext, name: str, args: dict) -> dict:
    """按工具名分发执行，统一做权限校验（返回结构一律为 ToolResult）。"""
    spec = TOOL_BY_NAME.get(name)
    if spec is None:
        return _ok("我不太确定要执行哪个操作，你可以换个说法，或到对应页面手动操作。")
    if spec.permission and not ctx.user.has_permission(spec.permission):
        return _ok(f"你没有「{spec.permission}」权限，无法执行该操作，请联系项目负责人开通。")
    try:
        return await spec.handler(ctx, args or {})
    except Exception as exc:  # noqa: BLE001 - 工具内部异常不外泄堆栈
        logger.exception("agent 工具 %s 执行失败", name)
        return _ok(f"执行「{spec.desc.split('（')[0]}」时出错：{getattr(exc, 'detail', None) or type(exc).__name__}。")
