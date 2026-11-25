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

from sqlalchemy import delete, func, select, text as sql_text, update
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
from app.services import agent_reply_variants
from app.services import agent_request_plan, form_contracts, model_gateway, provform_schema, record_field_spec
from app.services import multi_image_split, split_record_confirm
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
    ("jianli_riji", "监理日记"),
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
    # 随消息发送的素材文本（图片交给视觉模型/水印 OCR 后的识别结果）。
    # 含水印原文，验收子类型等关键字段可从中按词命中，不必再追问用户。
    material: str = ""
    # 随消息上传后的服务端媒体地址（相对路径，如 /uploads/...）。
    # 模型看不到图片本身，无法自行填「举牌验收照片」等照片栏——
    # 这里把用户发的图交给工具层，由服务端映射进记录照片字段，
    # 否则照片必填项永远缺失、验收等记录永远建不出
    # （2026-11-09/11-10 生产实测：用户明明发了图仍卡在缺照片）。
    images: list[str] = field(default_factory=list)
    # 逐图结构化视觉信号（与 images 等长）：是否举牌/封样/危大/施工中…等，字段同
    # image_classification.ImageSignal。多图按图自动分类分流时使用；旧入口不传则退化单记录。
    image_signals: list[dict] = field(default_factory=list)


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
            "need_more": [], "options": {}, "undo": None, "data": {}, "citations": []}
    base.update(kw)
    return base


def _cite(index: int, kind: str, title: str, *, sub: str = "", snippet: str = "",
          target: str | None = None, ref_id: Any = None) -> dict:
    """统一的引用条目：前端据此渲染行内角标与文末引用列表。"""
    return {"index": index, "kind": kind, "title": title, "sub": sub,
            "snippet": snippet, "target": target, "ref_id": ref_id}


def _draft_context_version(args: dict | None, orig_args: dict | None = None) -> int:
    """从草稿 _context 取轮次种子（每次 stash_pending 递增，天然单调）。

    2026-11-24「工作台交互太僵硬」：追问/确认句变体轮换的轮次种子统一取这里。
    读取失败一律退 0（首次轮），绝不抛错阻断业务。
    """
    for source in (args, orig_args):
        ctx = (source or {}).get("_context") if isinstance(source, dict) else None
        if isinstance(ctx, dict):
            try:
                return int(ctx.get("version") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _need_more(missing: list[str], reply: str = "",
               options: dict[str, list[str]] | None = None) -> dict:
    """参数不足：回纯文字追问话术。

    2026-11-06 用户明确「我不要这些选择，我要完全交互」：无论调用方传什么
    options，一律不再下发可点选候选 chips / 表单控件；缺项只用自然语言
    一次问一栏，用户自己打字回答。options 参数保留仅为兼容既有调用签名。
    2026-11-24「工作台交互太僵硬」：兜底句也走变体轮换，同一句不再逐字复读
    （调用方传了 reply 的优先用调用方的，例如最终确认门的轮换文案）。
    """
    if not reply:
        labels = [str(x) for x in (missing or [])]
        if labels:
            reply = agent_reply_variants.missing_field_ask(labels[0], 0)
        else:
            reply = "请补充缺失信息。"
    return {"ok": False, "reply": reply, "card": None, "cards": [], "actions": [],
            "need_more": missing, "options": {}, "undo": None, "data": {}, "citations": []}


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


def _backfill_no_issue(message: str) -> str:
    """用户原话命中「无问题」暗示时返回该暗示词，否则返回空串。

    用途：建巡视/旁站记录时，模型常把用户说的「符合要求/无异常」理解成
    "没有问题"而省略 problems / station_conclusion 参数 → 服务端追问 → 死循环
    （2026-10-15 真机踩坑）。这里做确定性兜底：原话命中即回填。

    🔴 仅对「用户原话明确说无问题」生效；以下情况一律返回空串、**不兜底**：
       - 用户没说（静默上传照片 / 纯文字没提问题）；
       - 带了照片素材但模型没归结出问题。
    这两种情况交给模型从各张照片的【图片内容观察】去判断并填写 problems；
    **服务端严禁盲填「符合要求」**，否则会造假的合规记录（多张照片尤其危险——
    可能其中某张其实有问题只是用户没说）。2026-11-13 多图口径新增此约束。
    """
    text = (message or "").strip()
    if not text:
        return ""
    for h in _NO_ISSUE_HINTS:
        if h in text:
            return h
    return ""


_ACCEPTANCE_OK_RE = re.compile(r"验收[^，。；,;\n]{0,10}(?:符合|合格|通过)")
_ACCEPTANCE_NEG_RE = re.compile(
    r"验收[^，。；,;\n]{0,10}(?:不|未|没)[^，。；,;\n]{0,6}(?:符合|合格|通过)")


def _acceptance_confirmed(message: str) -> bool:
    """用户在确认轮明确说「验收符合/合格/通过」时返回 True。

    只认明说：没提验收、或说的是「巡视问题及处理情况符合要求」都不算，
    避免把巡视的「符合」替监理写进验收结论（2026-11-18 真机：用户回
    「巡视问题及处理情况符合要求，验收符合」，验收记录结论栏却是空）。
    「验收不合格/未通过」一律不算。
    """
    text = str(message or "")
    if _ACCEPTANCE_NEG_RE.search(text):
        return False
    return bool(_ACCEPTANCE_OK_RE.search(text))


def _wants_skip(message: str) -> bool:
    text = (message or "").strip()
    return any(h in text for h in _SKIP_HINTS)


# 🔴 显式「写入正式记录」授权（2026-11-16 真机录屏诊断）：
# 用户连说「写入正式记录」「请写入验收记录」「其余栏位先留空」，服务端却始终
# 回「还没写入正式记录，还差…」反复追问。原因有三：确认词白名单只认「确认生成」；
# 草稿仍有必填缺项时 awaiting=False，白名单根本不生效；必填字段合同不接受
# 「留空」。这里把「明确要求写入 + 允许留空」提升为一次性授权，与
# _wants_skip（只能跳过可选栏）严格区分：write_through 允许跳过必填栏。
_WRITE_THROUGH_HINTS = (
    "写入正式记录", "写入验收记录", "写入工作记录", "请写入", "直接写入",
    "写入记录", "写进记录", "正式写入", "按现在已确认", "按已确认内容",
    "其余栏位先留空", "其他栏位留空", "剩下的留空", "不做臆测填写",
    "直接建档", "直接保存记录", "就按现在提交", "按当前内容提交",
    # 「把验收要点补充到我前面创建的验收记录里」也是一条明确写入指令
    "补充到", "写进", "加进", "填进", "补进",
)
_SHORT_CONCLUSIONS = ("合格", "不合格", "待整改")


def wants_write_through(message: str) -> bool:
    """用户明确要求「按已确认内容写入正式记录（缺的先留空）」→ True。

    只认指令性措辞，不认疑问句/字段值，避免把「验收要点呢」「合格」误判成
    写入授权。命中后由路由层注入 ``_write_through``，工具层据此放行必填缺项
    与「一起询问」拦截，真正落库（而不是又开一份待确认草稿）。
    """
    text = (message or "").strip()
    if not text or any(q in text for q in ("？", "?", "吗", "呢")):
        return False
    return any(h in text for h in _WRITE_THROUGH_HINTS)


def short_conclusion(message: str) -> str:
    """用户只回「合格 / 不合格 / 待整改」→ 返回该结论词，供验收结论栏吸收。"""
    text = (message or "").strip()
    return text if text in _SHORT_CONCLUSIONS else ""


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
# 验收类型：显示枚举（chips 选项）。顺序即展示顺序；「分项分部」为历史别名，保留兼容旧数据。
_ACCEPTANCE_TYPES = (
    "隐蔽工程", "检验批", "进场验收",
    "分项工程验收", "分部工程验收", "单位工程验收",
    "竣工验收", "预验收", "分项分部",
)


# 从素材/原话保守推断验收类型：只认明确出现的类型词，按顺序命中第一个即返回。
# 「分项工程」须在「分部工程」前，避免把「分项工程质量验收」误判为分部；
# 「竣工预验收」须在「竣工验收」前，避免把预验收误判为竣工。
_ACCEPTANCE_INFER = [
    ("隐蔽工程", "隐蔽工程"),
    ("检验批", "检验批"),
    ("进场验收", "进场验收"),
    ("材料进场", "进场验收"),
    ("材料验收", "进场验收"),
    ("构配件", "进场验收"),
    ("分项工程", "分项工程验收"),
    ("子分部", "分部工程验收"),
    ("分部工程", "分部工程验收"),
    ("单位工程", "单位工程验收"),
    ("竣工预验收", "预验收"),
    ("预验收", "预验收"),
    ("竣工验收", "竣工验收"),
    # 2026-11-18 用户「还有这么多空吗」：分组建档时照片水印往往只识别出工序名
    # （钢筋/模板/防水…），没有「隐蔽工程」这类字样，验收类型就一直空着。
    # 按施工惯例给草稿默认值（隐蔽工序→隐蔽工程，安装/浇筑类→检验批），
    # 用户可在详情页改；只影响草稿，不改变正式验收结论。
    ("钢筋", "隐蔽工程"),
    ("预埋", "隐蔽工程"),
    ("防水", "隐蔽工程"),
    ("回填", "隐蔽工程"),
    ("垫层", "隐蔽工程"),
    ("桩基", "隐蔽工程"),
    ("管道", "隐蔽工程"),
    ("模板", "检验批"),
    ("混凝土", "检验批"),
    ("砌体", "检验批"),
    ("抹灰", "检验批"),
    ("路基", "检验批"),
    ("基层", "检验批"),
]


def _infer_acceptance_type(text: str) -> str:
    """从素材/正文里保守推断验收类型（只认明确出现的类型词，取命中第一个）。

    「隐蔽工程验收公示牌」→ 隐蔽工程；「检验批质量验收」→ 检验批；
    「分项工程质量验收记录」→ 分项工程验收；「单位工程竣工验收」→ 单位工程验收。
    只用于文本里*明确出现*类型词的情形——整体仍保留用户可说、可改。
    """
    t = str(text or "")
    if not t:
        return ""
    for kw, kind in _ACCEPTANCE_INFER:
        if kw in t:
            return kind
    return ""


# ---------------------------------------------------------------------------
# 验收「一起询问」模式辅助（2026-11-05）
# 数据源：app.services.acceptance_ask_together（基于 acceptance_checklists.ITEMS 建索引）。
# 流程：系统按 分部工程+关键工序 命中要点库 → 一次性带出全部「询问式」→ 用户一并回复
#       → 系统据全部回复逐条归集 → 生成「验收结论」。
# ---------------------------------------------------------------------------
def _aat_find_entry(division: str, keyproc: str):
    """按 分部工程 + 关键工序_关键部位 精确/模糊匹配条目（房建/市政通用）。"""
    from app.services import acceptance_ask_together as _aat
    return _aat.find_entry(division, keyproc)


def _aat_already_asked(args: dict | None, match: dict | None) -> bool:
    """同一 (分部,工序) 此前已「一起询问」过 → 不再重复带出询问块（防重问）。

    `_acceptance_asked` 是持久化进草稿的标记（非空 dict，不会被 _draft_clean_args 剔除），
    跨轮保留；仅记录已询问过的 (div,item)，不阻断其它工序或新会话。
    """
    asked = (args or {}).get("_acceptance_asked")
    if not (asked and isinstance(asked, dict) and match):
        return False
    return (str(asked.get("div") or "") == str(match.get("div") or "")
            and str(asked.get("item") or "") == str(match.get("item") or ""))


async def _aat_aggregate_reply(items, user_reply):
    from app.services import acceptance_ask_together as _aat
    return await _aat.aggregate_reply(items, user_reply)


def _aat_generate_conclusion(aggregated):
    from app.services import acceptance_ask_together as _aat
    return _aat.generate_conclusion(aggregated)


def _aat_generate_self_check_conclusion(aggregated):
    from app.services import acceptance_ask_together as _aat
    return _aat.generate_self_check_conclusion(aggregated)


def _set_field_persist(orig_args: dict, args: dict, field: dict, value: str) -> dict:
    """写字段值到本地 args，并镜像到 orig_args（路由器将暂存的对象），保证跨轮不丢。"""
    args = _set_field_value(args, field, value)
    arg = field.get("arg")
    if arg:
        orig_args[arg] = value
    else:
        extra = dict(orig_args.get(_EXTRA_ARG) or {})
        extra[_field_slot(field)] = value
        orig_args[_EXTRA_ARG] = extra
    return args


def _looks_like_reply(text: str) -> bool:
    """在「已带出询问块、等待整体回复」状态下，判断用户这句是否是对全部询问项的回复。

    处于待回复态时默认当作回复；仅当用户明显另下了新指令（记一条/新建…）才返回 False。
    """
    if not text:
        return False
    cues = ("符合", "不符合", "不适用", "合格", "不合格", "无异常", "无问题",
            "没问题", "已", "都", "全部", "均", "通过", "正常", "一致", "满足",
            "达到", "ok", "OK", "可以", "验收合格")
    if any(c in text for c in cues):
        return True
    if re.search(r"[①②③④⑤⑥⑦⑧⑨⑩]", text):
        return True
    request_cues = ("记一条", "新建", "创建", "帮我记", "我要记", "记录一条", "再记一条")
    if any(r in text for r in request_cues):
        return False
    return True


def _acceptance_type_from_entry(entry: dict) -> str:
    """由命中条目的 分部/工序 名启发式推断验收类型（内定，仍可被用户改）。

    仅映射到 acceptance_record 表单受控枚举（_ACCEPTANCE_TYPES）内的值；映射不到则返回空。
    """
    s = f"{entry.get('分部工程', '')} {entry.get('关键工序_关键部位', '')}"
    if any(k in s for k in ("进场", "材料", "设备", "构配件")):
        return "进场验收"
    if "隐蔽" in s:
        return "隐蔽工程"
    # 质量验收/危大验收不在表单受控枚举内，不强行内定，留待用户点选
    return ""


_INQUIRY_CONTENT_MARKERS = ("是否", "这项符合吗", "符合吗？", "符合吗?", "吗？", "吗?")
_ACCEPTANCE_CONCLUSION_MARKERS = ("—— 符合", "—— 不符合", "—— 不适用")


def _is_inquiry_style_content(text) -> bool:
    """验收内容栏是否被误填成「询问句清单」。

    正式验收内容应是逐项判断结论：要点原文 + 「—— 符合/不符合/不适用」。
    模型偶尔把「一起询问」的要点改写成「…是否一致／是否合格」预填进该栏，
    合同只校验非空便放行，最终确认卡上就会出现一份问卷（2026-11-09 真机截图）。
    已有结论标记时一律视为正式内容，避免误伤要点原文里含「是否」二字的情形。
    """
    s = str(text or "").strip()
    if not s:
        return False
    if any(m in s for m in _ACCEPTANCE_CONCLUSION_MARKERS):
        return False
    return any(m in s for m in _INQUIRY_CONTENT_MARKERS)


def _rule_aggregate_acceptance(items: list[str], reply_text: str) -> list[dict] | None:
    """LLM 逐条归集失败时的规则兜底：只在监理人员明确说「全部符合」时下结论。

    涉及否定、局部不符合、原因说明时一律不猜，交回用户重新说明，避免替监理
    人员臆断验收结论。
    """
    s = str(reply_text or "").strip()
    if not s or not items:
        return None
    if re.search(r"不符合|不合格|不通过|有问题|异常|整改", s):
        return None
    if re.search(r"都符合|全部符合|均符合|全都符合|都合格|全部合格|都行|没问题|"
                 r"无异常|符合要求|可以|通过|OK|ok", s):
        return [{"item_index": i, "conclusion": "符合", "note": ""}
                for i in range(1, len(items) + 1)]
    return None


def _build_acceptance_content(entry: dict, aggregated: list[dict]) -> str:
    """把逐条归集结果汇成「验收内容」文本：要点原文 + 各条结论。"""
    originals = entry.get("验收要点_原文") or []
    n = len(aggregated)
    head = (f"【{entry.get('分部工程', '')} — {entry.get('关键工序_关键部位', '')}】"
            f"验收内容（共 {n} 项，系统按询问项逐项归集）：")
    lines = [head]
    for a in aggregated:
        idx = a.get("item_index")
        orig = (originals[idx - 1]
                if isinstance(idx, int) and 1 <= idx <= len(originals) else f"第{idx}项")
        note = (f"（{a.get('note') or ''}）" if a.get("note") else "")
        lines.append(f"{idx}、{orig} —— {a.get('conclusion')}{note}")
    return "\n".join(lines)


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


def _kind_from_text(text: str) -> str | None:
    """项目对象推断不出类别时的文本兜底：用水印 OCR/用户消息猜市政或房建。

    场景（2026-11-05 真机实测）：多项目视角/项目档案缺失时 ctx.project 推断
    不出，沙盛四路（市政道路）的公示牌照片却推出一排 [房建] 隐蔽工序 chips。
    """
    t = str(text or "")
    if not t.strip():
        return None
    score: dict[str, int] = {}
    for kind, words in _PROJECT_KIND_WORDS.items():
        score[kind] = sum(1 for w in words if w in t)
    if score.get("市政", 0) > score.get("房建", 0):
        return "市政"
    if score.get("房建", 0) > score.get("市政", 0):
        return "房建"
    return None


def _acceptance_prefer_kind(ctx) -> str | None:
    """验收候选排序用的项目类别：项目档案优先，水印/消息文本兜底。"""
    return _project_kind_from(ctx.project) or _kind_from_text(
        " ".join(x for x in (str(ctx.message or ""), str(ctx.material or "")) if x)
    )


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
    # 🔧 整句是「指令词」（生成记录/确认生成/帮我记录…）时，不是任何字段的值，
    # 绝不能兜底填进缺的字段（2026-11-09 生产实测：缺「举牌验收照片」时
    # 路由层把用户那句「生成记录」原话填进了 photo 栏，合同层 is_instruction_only
    # 判定为指令 → 照片仍缺、还污染了草稿，形成「指令被塞进照片栏又反复追问」）。
    from app.services.record_fields import is_instruction_only
    if is_instruction_only(text):
        return None
    # 「一起询问」态：本句是对全部验收询问项的整体回复，交由 aggregate_reply 逐条归集，
    # 绝不能当某个缺失字段的值兜底填进去（否则会污染验收内容栏并绕过归集）。
    if args.get("_acceptance_ask_state"):
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
        # 🔧 照片/附件类字段只能吃「真实图片」，不吃整句兜底：用户回「生成记录」
        # 这类指令时，若把原话原文填进 photo 栏，合同校验仍会把纯文本判为无效照片
        # （value 为空 → missing 恒真），就会变成「指令被塞进照片栏又反复追问缺照片」
        # 的死循环（2026-11-09 生产实测：草稿 id=40 photo='生成记录'）。
        if (str(field.get("canonical") or "").find("photo") >= 0
                or str(field.get("key") or "").find("photo") >= 0
                or "照片" in str(field.get("label") or "")):
            continue
        if _field_value(args, field):
            continue
        return _set_field_value(args, field, text[:120])
    return None


# ---------------------------------------------------------------------------
# 写工具
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------- 多图分类分流（B 方案）

def _reconstruct_signals_from_material(material: str, n_images: int) -> list[dict]:
    """从聊天素材重建逐图 image_signals（前端视觉结果未填 image_signals 时的兜底）。

    见 ``app.services.agent_vision_signals``。返回空列表表示无法重建（material 不含
    逐图标记），调用方应回退到模型分组或单记录流程。
    """
    from app.services.agent_vision_signals import parse_material_signals
    try:
        return parse_material_signals(material, n_images)
    except Exception as exc:  # noqa: BLE001 - 解析异常不应阻断建档，降级为无信号
        import logging
        logging.getLogger(__name__).warning("material→signals 重建失败（降级为无信号）: %s", exc)
        return []


async def _maybe_handle_multi_image_split(ctx: "ToolContext", args: dict, orig_args: dict) -> dict | None:
    """多图 + 逐图视觉信号时，按图分类、分组、先确认后建；无信号则退化为单记录流程（返回 None）。

    短路在单记录逻辑之前：避免「类型只能取一个 / 多图全挂一条」把多类照片误建为一条。
    """
    # 🔴 A 层零必填（jl 零必填）：只有「你先记着/先存着」这类用户明确要求先存着的请求
    # 才跳过分组，所有照片直接挂一条「待定」记录，避免多问一轮「确认分组」。
    # 普通「有照片但没说类型」的路由也会打 _auto_tbd，那是**不追问记录类型**，
    # 不等于不分组——否则跨类型的多张现场照片会被硬塞进同一条记录
    # （2026-11-13 真机反馈「多图分组没了」）。分组与「类型填什么」是两件事。
    if args.get("_no_multi_split"):
        return None
    # 先消费已存的分组方案：确认轮用户只回肯定词、不带新图，必须在此短路之前判定，
    # 否则「if not images: return None」会把确认轮直接吞掉，分组方案永不消费。
    pending = args.get("_agent_pending_split")
    logger.info("[多图分组调试] 工具层入口: has_pending_split=%s pending_groups=%d args.record_type=%s images=%d is_confirm=%s",
                 bool(pending and pending.get("groups")),
                 len((pending or {}).get("groups") or []),
                 args.get("record_type"),
                 len(ctx.images or []),
                 multi_image_split.is_confirm(ctx.message))
    if isinstance(pending, dict) and pending.get("groups"):
        groups = [_group_from_dict(g) for g in pending["groups"]]
        if multi_image_split.is_confirm(ctx.message):
            logger.info("[多图分组调试] ✅ 确认轮 → 走 _create_split_records, %d groups", len(groups))
            return await _create_split_records(ctx, groups)
        logger.info("[多图分组调试] ⏳ 分组方案已带过来但本轮没说确认词(%r), 保持 need_more", ctx.message)
        return _split_need_more(groups)

    images = [str(u).strip() for u in (ctx.images or []) if str(u).strip()]
    if not images:
        return None
    signals = list(getattr(ctx, "image_signals", None) or [])

    # Path 2：前端把视觉结果放进了 material 但没填 image_signals（设计上的信号通道
    # 一直空着）→ 后端从「水印字段 + 画面观察」重建逐图信号，激活 group_images。
    # 仅多图时做（单图无需分流），且只在请求未直接给信号时补。
    if not signals and len(images) >= 2 and getattr(ctx, "material", None):
        recon = _reconstruct_signals_from_material(ctx.material, len(images))
        if recon:
            signals = recon
            try:
                ctx.image_signals = signals
            except Exception:
                pass

    if signals:
        groups = multi_image_split.group_images(images, signals)
        if len(groups) > 1:
            plan = {"groups": [_group_to_dict(g) for g in groups], "source": "vision"}
            args["_agent_pending_split"] = plan
            orig_args["_agent_pending_split"] = plan
            logger.info("[多图分组调试] vision分组写入 _agent_pending_split: %d groups, args has key=%s",
                         len(groups), "_agent_pending_split" in args)
            return _split_need_more(groups, getattr(ctx, "material", None), len(images))
        # vision 只分出单组（含全部待定）→ 退化为模型按水印分组（Path 1）

    # 无视觉信号 / vision 未分出多组：接受模型按水印文字分组（Path 1）。
    model_groups = _parse_model_image_groups(args.get("image_groups"), images, signals)
    if model_groups:
        if len(model_groups) <= 1:
            return None  # 单一类型 → 普通单记录流程
        plan = {"groups": [_group_to_dict(g) for g in model_groups], "source": "model_watermark"}
        args["_agent_pending_split"] = plan
        orig_args["_agent_pending_split"] = plan
        logger.info("[多图分组调试] 首次分组写入 _agent_pending_split: %d groups, args has key after write=%s",
                     len(model_groups), "_agent_pending_split" in args)
        return _split_need_more(model_groups, getattr(ctx, "material", None), len(images))

    return None  # 无逐图信号且未分组 → 普通单记录流程（多图挂同一条）


def _norm_split_record_type(value) -> str | None:
    """把模型给的分组类型归一为合法记录类型；识别不出归「待定」。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    canonical = _norm_record_type(raw)
    if canonical:
        return canonical
    alias = {
        "巡查": "巡视", "巡视检查": "巡视",
        "材料": "见证", "材料单据": "见证", "取样": "见证", "送检": "见证",
        "举牌": "验收", "隐蔽": "验收",
        "平行": "平行检验",
    }
    for k, v in alias.items():
        if k in raw:
            return v
    return "待定"


def _parse_model_image_groups(raw, images: list, signals: list | None = None) -> list | None:
    """模型按水印文字分组：image_groups=[{record_type, image_indices|images}] → ImageGroup 列表。

    image_indices 为 1-based（对应消息里的「【图片 N】」），映射到 ctx.images 的 URL；
    也接受直接给 URL。仅做结构解析与类型归一，不访问视觉。
    """
    if not isinstance(raw, list) or not raw:
        return None
    buckets: dict[str, "multi_image_split.ImageGroup"] = {}
    order: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rtype = _norm_split_record_type(item.get("record_type"))
        if not rtype:
            continue
        idxs = item.get("image_indices") or item.get("images") or []
        urls: list[str] = []
        for ix in idxs:
            if isinstance(ix, str) and ix.strip().lower().startswith("http"):
                u = ix.strip()
                if u:
                    urls.append(u)
                continue
            try:
                i = int(ix) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(images):
                urls.append(images[i])
        urls = [u for u in urls if u]
        if not urls:
            continue
        grp = buckets.get(rtype)
        if grp is None:
            grp = multi_image_split.ImageGroup(record_type=rtype, images=[], hints=[], wm=[])
            buckets[rtype] = grp
            order.append(rtype)
        for u in urls:
            if u in grp.images:
                continue
            grp.images.append(u)
            wm = {}
            sig = None
            # 尽量按图片序号对齐视觉信号，带出部位 / 拍摄时间 / 天气 / 人员
            try:
                i = images.index(u)
                sig = (signals or [])[i] if i < len(signals or []) else None
            except (ValueError, IndexError):
                sig = None
            if isinstance(sig, dict):
                sig = dict(sig)
                # 信号里的内容可能带水印尾巴：切出来补到信号字段（时间/天气/人员），
                # 工作内容只留干净部分 —— 信息共存，不因清洗而丢
                tail: dict = {}
                raw_content = str(sig.get("content") or "")
                if raw_content and multi_image_split.looks_like_watermark_tail(raw_content):
                    cleaned, tail = multi_image_split.split_hint(raw_content)
                    if cleaned:
                        sig["content"] = cleaned
                for key, value in tail.items():
                    if not str(sig.get(key) or "").strip():
                        sig[key] = value
                wm = {
                    "section": str(sig.get("section") or "").strip(),
                    "taken_at": str(sig.get("taken_at") or "").strip(),
                    "project_name": str(sig.get("project_name") or "").strip(),
                    "weather": str(sig.get("weather") or "").strip(),
                    "person": str(sig.get("person") or sig.get("inspector") or "").strip(),
                    # 水印模板落款：切出工作内容后原样留档
                    "contact": str(sig.get("contact") or "").strip(),
                    "location": str(sig.get("location") or "").strip(),
                    "org": str(sig.get("org") or "").strip(),
                }
                # 模型给的分组本身不带 content：从信号里补，否则工作内容只剩
                # 「验收（由N张现场照片自动归类，待补全内容）」这种占位。
                hint = multi_image_split.clean_hint(str(sig.get("content") or ""))
                if hint and hint not in grp.hints:
                    grp.hints.append(hint)
            grp.wm.append(wm)
    if not buckets:
        return None
    result = [buckets[r] for r in order if r != "待定"]
    if buckets.get("待定"):
        result.append(buckets["待定"])
    return result


def _group_to_dict(g: "multi_image_split.ImageGroup") -> dict:
    return {
        "record_type": g.record_type,
        "images": list(g.images),
        "hints": list(g.hints),
        "wm": [dict(x) for x in (g.wm or [])],
    }


def _group_from_dict(d: dict) -> "multi_image_split.ImageGroup":
    return multi_image_split.ImageGroup(
        record_type=str(d.get("record_type") or ""),
        images=[str(x) for x in (d.get("images") or [])],
        hints=[str(x) for x in (d.get("hints") or [])],
        wm=[dict(x) for x in (d.get("wm") or [])],
    )


def _split_need_more(groups, material: str | None = None, n_images: int = 0) -> dict:
    summary = multi_image_split.summarize(groups)
    # 🔴 2026-11-16 用户口径「识别的内容只保留到思考里，不需要写出来，保留其他就行」：
    # 这里曾把 watermark_lines() 生成的「图片1：拍摄时间…／地点…／施工内容…」逐张
    # 拼进 reply（服务端硬编码回执，改 _AGENT_SYSTEM 完全管不到，真机截图仍在念水印）。
    # 识别值只用于分组、字段回填、验收要点匹配，不再写进任何对话回执。
    # material / n_images 参数保留以兼容调用方；ask_block 的 watermark_lines
    # 也置空，避免前端将来把它渲染出来。分组汇总、逐类待填/要点确认全部照旧。
    lines: list[str] = []
    # 兼容未发版的旧小程序：客户端靠 reply 里的「自动填入」判断服务端已回执过，
    # 否则会把本地 formatWatermark() 再拼一遍（又是一行水印具体值）。这句只说
    # 栏目名，不含任何识别值；新版客户端认「已读到」也会跳过。
    reply = ("已读到照片水印，日期、地点、部位等已自动填入记录栏位"
             "（具体值可在草稿中核对）。\n" + summary)
    # 分类完就按类型带出「这一类要填什么 + 对应内容/要点」，让用户在点
    # 「确认生成」之前能核对是否符合现场（2026-11-16 真机录屏需求）。
    # 提示是增强，失败不阻断分组确认；ask_block 使路由层强制用本 reply，
    # 不会被模型自然接话盖掉。
    try:
        _confirm = split_record_confirm.build_type_confirmation(
            groups, display=_split_record_display_name, stage="plan")
    except Exception:  # noqa: BLE001 - 展示增强，失败按旧回执走
        logger.debug("split type confirmation failed", exc_info=True)
        _confirm = ""
    if _confirm:
        reply += "\n\n" + _confirm
    return {
        "ok": False,
        "reply": reply,
        "card": None, "cards": [], "actions": [],
        "need_more": ["分组确认"],
        # 🎯 与普通记录「最终确认」对齐（2026-11-12）：多图分组确认同样下发「确认生成」
        # 快捷键，点击即以该词发送（multi_image_split.is_confirm 命中→直接建多条），
        # 免打字。路由层不会覆盖已有 chips（它只在「最终确认」态才额外注入）。
        "chips": ["确认生成"],
        # ask_block 触发路由把完整记录表单置空（否则 need_more 会让 create_work_record
        # 渲染一整张空表单，与「只回一句分组汇总、等用户确认」的交互冲突）。
        "ask_block": {"type": "multi_image_split", "summary": summary,
                      "watermark_lines": lines,
                      "type_confirmation": _confirm},
        "options": {}, "undo": None, "data": {},
    }


def _split_watermark_fields(wms: list) -> tuple:
    """从分组逐图水印聚合「施工部位 / 业务日期 / 天气 / 人员」，供分组建单回填。

    - 部位：合并组内各图非空的 单位工程/施工区域/部位，去重（同组多图可能分属不同单位工程）。
    - 日期：取各图拍摄时间中最早的一个；无拍摄时间则回退今天（与单图无信号时一致）。
    - 天气：取首个非空值。
    - 人员：去重留档（记录表无独立栏，落 content_json，避免清洗工作内容后被丢掉）。
    """
    _DATE_PREFIX = re.compile(r"(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})")
    # 2026-11-17 用户截图「日期和位置呢」：分组建单此前只把水印抽成日期写
    # record_date，详情里「巡视开始时间（年月日 时）」读的是 inspection_start_time
    # 一直空着。这里把组内最早拍摄**时刻**（含时分）一并按日期取出来回填。
    _TIME_SUFFIX = re.compile(r"(\d{1,2})\s*[:：时]\s*(\d{2})")
    sections: list[str] = []
    dates: list = []
    start_dts: list[datetime] = []
    weather = ""
    persons: list[str] = []
    # 水印模板的企业落款（调度电话/公司传真/地址）：从工作内容里切出来后
    # 不能凭空消失，聚合起来落 content_json（2026-11-13「要共存」）
    contacts: list[str] = []
    locations: list[str] = []
    orgs: list[str] = []
    for w in wms or []:
        if not isinstance(w, dict):
            continue
        s = str(w.get("section") or "").strip()
        if s and s not in sections:
            sections.append(s)
        t = str(w.get("taken_at") or "").strip()
        if t:
            # 拍摄时间常带「 09:32」等时刻后缀，先抽出日期部分再解析
            dm = _DATE_PREFIX.search(t)
            p = _parse_date(dm.group(0) if dm else t)
            if p and p not in dates:
                dates.append(p)
            # 同一张水印取日期+时分组成完整时刻（最早一张），供开始时间栏回填。
            tm = _TIME_SUFFIX.search(t)
            if p and tm:
                hour, minute = int(tm.group(1)), int(tm.group(2))
                if hour <= 23 and minute <= 59:
                    try:
                        start_dts.append(datetime(p.year, p.month, p.day, hour, minute))
                    except ValueError:
                        pass
        wt = str(w.get("weather") or "").strip()
        if wt and not weather:
            weather = wt
        ps = str(w.get("person") or "").strip()
        if ps and ps not in persons:
            persons.append(ps)
        for bucket, key in ((contacts, "contact"), (locations, "location"), (orgs, "org")):
            val = str(w.get(key) or "").strip()
            # 具体位置最后一闸：表单 OCR 整段（公示牌/发货单）不写进记录地址栏。
            # 真机 2026-11-25 #231：结构化地点是「天头路」，原文兜底把 100+ 字
            # 勾选/质检文本塞进 location，导致详情地点栏不可读。
            if key == "location":
                val = multi_image_split.clean_location(val)
            if val and val not in bucket:
                bucket.append(val)
    section = " / ".join(sections) if sections else None
    rec_date = min(dates) if dates else datetime.now().date()
    return {
        "section": section,
        "date": rec_date,
        # 最早拍摄时刻（含时分）；无时刻水印则为 None，绝不臆造开始时间。
        "start_dt": min(start_dts) if start_dts else None,
        "weather": weather or None,
        "persons": persons,
        "contacts": contacts,
        "locations": locations,
        "orgs": orgs,
    }


# 分组建单的回执/卡片标题用省统表正式表名（与 form_registry 一致），
# 不用「巡视记录 / 旁站记录」这种短称（2026-11-12 用户要求用正式表名）。
_SPLIT_RECORD_DISPLAY = {
    "巡视": "巡视检查记录",
    "旁站": "旁站监理记录",
    "验收": "验收记录",
    "见证": "见证取样记录",
    "平行检验": "平行检验记录",
}


def _split_record_display_name(rtype: str) -> str:
    return _SPLIT_RECORD_DISPLAY.get(rtype) or f"{rtype}记录"


# 水印只印岗位不印姓名时（真机：「监理」），不能当成人名填进「巡视人」栏。
_ROLE_ONLY_NAMES = {
    "监理", "监理员", "监理工程师", "专业监理工程师", "总监理工程师", "总监",
    "总监代表", "项目经理", "施工员", "安全员", "质检员", "取样员", "见证员",
    "甲方", "业主", "建设单位",
}


def _inspector_name(persons, fallback: str) -> str:
    """巡视人/旁站监理人员：优先水印里的真实姓名，岗位名或空则回落到记录人。"""
    for person in persons or ():
        name = str(person or "").strip()
        if name and name not in _ROLE_ONLY_NAMES and 2 <= len(name) <= 6:
            return name
    return str(fallback or "").strip()


async def _create_split_records(ctx: "ToolContext", groups: list) -> dict:
    """确认后循环建多条记录：每组一条，同类多图挂该条照片列表；待定组建极简草稿。

    不走完整字段合同（resolve_event_form）：照片是主要产物，类型字段留待用户在
    工作记录详情页补全。记录均为草稿状态。
    """
    created: list[tuple[str, WorkRecord, list[str]]] = []
    # 确认轮顺带交代的问题（如有）：落到 problems 列为了不丢信息。
    # 2026-11-17 用户截图「这个要跟用户交互的」：分组建单的确认回执会带出
    # 「问题及处理情况」待填栏，用户可能在确认时一并说了问题/无异常。
    _mk = (ctx.message or "").strip()
    # 水印 OCR 常把项目名/监理单位一起识别进「内容」，用它剔除，避免污染工作内容
    _project_name = str(getattr(getattr(ctx, "project", None), "name", "") or "").strip()
    # 记录人（当前登录的监理人员）：水印没印姓名时用作「巡视人」默认值
    _author_name = str(
        getattr(ctx.user, "real_name", "") or getattr(ctx.user, "username", "") or ""
    ).strip()
    for g in groups:
        rtype = g.record_type
        photos = [str(u).strip() for u in g.images if str(u).strip()]
        if not photos:
            continue
        is_witness = (rtype == "见证")
        # 按组回填水印字段：拍摄时间→业务日期，单位工程/施工区域→施工部位。
        # 分组确认可能跨轮，wm 随分组方案持久化，故不依赖 ctx 的临时 image_signals。
        wms = list(getattr(g, "wm", None) or [])
        wm_fields = _split_watermark_fields(wms)
        content = _split_content(
            g, rtype, project_name=_project_name, orgs=wm_fields["orgs"])
        # 验收要点必须真的进记录（2026-11-16 用户截图「工作内容填的不是要点吗」）：
        # work_content 保留水印识别的施工内容（工序名），要点单独落
        # content_json.acceptance_points 作为待确认草案；用户回「都符合 /
        # 写入正式记录」后再由路由层转成正式 acceptance_content。两者共存，不丢工序名。
        acc_points = None
        acc_type = ""
        if rtype == "验收":
            try:
                acc_points = split_record_confirm.acceptance_points_for([g])
            except Exception:  # noqa: BLE001 - 要点是增强，失败不阻断建档
                logger.debug("acceptance points for split record failed", exc_info=True)
            # 2026-11-18 用户「还有这么多空吗」：分组建档时顺手把能判断的验收类型填上，
            # 别让详情页整栏空着等用户手填。只写草稿默认值，用户可改。
            acc_src = " ".join(
                str(x or "") for x in (
                    (acc_points or {}).get("identified"),
                    (acc_points or {}).get("procedure"),
                    content,
                )
            )
            acc_type = _infer_acceptance_type(acc_src)
        # 用户这轮已明确「验收符合/合格」＝要点确认，直接落正式验收内容；
        # 否则回完「验收符合」还要再回一次「写入正式记录」，而回执又没说写没写，
        # 用户不知道到底有没有写入（2026-11-18 真机）。
        _acc_ok = bool(rtype == "验收" and _acceptance_confirmed(_mk))
        acc_formal = ""
        if _acc_ok and acc_points:
            acc_formal = split_record_confirm.build_acceptance_content_from_points(acc_points)
            if acc_formal:
                acc_points["confirmed"] = True
        # 按组回填水印字段：拍摄时间→业务日期，单位工程/施工区域→施工部位
        # （wm_fields 已在循环开头算好，工作内容清洗要用到其中的单位落款）。
        split_date = wm_fields["date"]
        rec = WorkRecord(
            record_type=rtype,
            status=WorkRecordStatus.DRAFT.value,
            creator_id=ctx.user.id,
            project_id=ctx.project_id,
            record_date=split_date,
            section=(wm_fields["section"][:128] if wm_fields["section"] else None),
            # 🔴 2026-11-19「工作台生成的记录每条都缺位置」：水印里的位置（拍照时
            # GPS 取的可读地址，如「四会市大沙镇马房工业开发区内」）落到「具体位置」
            # 栏。此前只存 content_json.watermark_location 留档、该栏恒空，详情页
            # 「具体位置」全靠用户手填。多图地址去重后与 section 同口径 join，
            # 列宽 256 截断；无水印位置照旧为 NULL（不臆造）。
            location=((" / ".join(wm_fields["locations"]))[:256] if wm_fields["locations"] else None),
            weather=(wm_fields["weather"][:16] if wm_fields["weather"] else None),
            inspection_photos=(None if is_witness else photos),
            witness_photos=(photos if is_witness else None),
            work_content=(content[:2000] if content else None),
            content_json={
                "source": "agent_multi_image_split",
                "photo_count": len(photos),
                "auto_classified": True,
                # 水印里的拍摄时间/天气/人员原样留档：工作内容不再拼接它们，
                # 但信息必须可追溯（2026-11-13「不能改一样丢一样」）
                "watermark": [w for w in wms if isinstance(w, dict)],
                **({"watermark_person": wm_fields["persons"]} if wm_fields["persons"] else {}),
                # 水印模板的企业落款（调度电话/传真/地址）：同属被切下来的信息
                **({"watermark_contact": wm_fields["contacts"]} if wm_fields["contacts"] else {}),
                **({"watermark_location": wm_fields["locations"]} if wm_fields["locations"] else {}),
                **({"watermark_org": wm_fields["orgs"]} if wm_fields["orgs"] else {}),
                # 验收要点草案（未经确认，不写成正式验收内容；匹配不到就不写）
                **({"acceptance_points": acc_points} if acc_points else {}),
                # 本轮用户已确认「验收符合」→ 要点直接转成正式验收内容
                **({"acceptance_content": acc_formal} if acc_formal else {}),
                # 验收类型草稿默认（工序可判断时）：减少详情页空栏；用户可改
                **({"acceptance_type": acc_type} if acc_type else {}),
                # 施工单位自检结论：施工单位送检前本就自检合格，草稿先给标准说法；
                # 监理验收结论仍留空，必须依据用户确认结果再落，不能替监理下结论。
                **({"self_check_conclusion": "施工单位自检合格"} if rtype == "验收" else {}),
                # 用户确认轮明确说「验收符合/合格」→ 监理验收结论直接落，
                # 否则（没说、只说巡视符合）保持空，等用户补（2026-11-18 真机）。
                **({"supervision_conclusion": "验收合格，同意进入下道工序。"}
                   if _acc_ok else {}),
                # 巡视人/旁站监理人员：水印通常只印「监理」这种岗位名，没有姓名可用；
                # 记录由当前登录的监理人员创建，默认落本人姓名，避免文书该栏空白
                # （2026-11-18 用户截图「巡视人呢」）。
                **({"inspector": _inspector_name(wm_fields["persons"], _author_name)}
                   if rtype in ("巡视", "旁站")
                   and _inspector_name(wm_fields["persons"], _author_name) else {}),
                # 旁站两个必填枚举（到岗情况/自查自检）：旁站本身即监理在现场核对过，
                # 草稿先给「是」，详情页不再出现「未填写 / -」（2026-11-18 真机）。
                **({"qa_onsite": "是", "self_check": "是"} if rtype == "旁站" else {}),
            },
        )
        # 水印拍摄时刻 → 开始时间栏（与单图 apply_prefill 同规则：巡视落
        # inspection_start_time，旁站落 station_start_time）。只填开始、不臆造
        # 结束时间；没有带时分的水印就保持 None，交给用户补。
        start_dt = wm_fields.get("start_dt")
        if isinstance(start_dt, datetime):
            if rtype == "旁站":
                rec.station_start_time = start_dt
            else:
                rec.inspection_start_time = start_dt
        # 巡视/旁站的「问题及处理情况」栏：确认轮用户明确说了问题就直接落，
        # 说「无异常/符合要求」由 _backfill_no_issue 落；什么都没说不臆造。
        # 2026-11-17 用户截图「这个要跟用户交互的」（分组确认回执已带出待填栏）。
        if rtype in ("巡视", "旁站"):
            prob = _backfill_no_issue(_mk)
            if prob and prob != str(getattr(rec, "problems", "") or "").strip():
                rec.problems = prob[:2000]
        ctx.db.add(rec)
        created.append((rtype, rec, photos))

    if not created:
        return _ok("没有可建的记录。")

    await ctx.db.flush()
    for rtype, rec, photos in created:
        await _audit(ctx.db, ctx.user, "agent_write", {
            "tool": "create_work_record", "entity": "work_record", "id": rec.id,
            "split": True, "record_type": rtype,
        })
    await ctx.db.commit()

    cards = [{
        "kind": "work_record", "id": rec.id, "title": _split_record_display_name(rtype),
        "sub": f"{len(photos)}张照片", "status": rec.status,
        "time": str(rec.record_date),
    } for rtype, rec, photos in created]

    lines = ["已按分组创建以下记录："]
    for rtype, rec, photos in created:
        lines.append(f"· {_split_record_display_name(rtype)} #{rec.id}（{len(photos)}张照片）")
    # 回执总结行同步口径（2026-11-19：水印有位置时具体位置栏也自动填了）
    _loc_filled = sum(1 for _t, rec, _p in created if rec.location)
    lines.append(
        "施工部位、业务日期已按各张照片水印自动填入。"
        + (f"具体位置也已按水印自动填入（{_loc_filled}/{len(created)}条）。" if _loc_filled else "")
    )
    # 回执必须说清「写了什么」：用户回完「巡视符合、验收符合」后，如果只列记录
    # 编号、还把已写入的栏位挂在「待填」里，用户根本不知道到底有没有写进去
    # （2026-11-18 真机截图）。filled_by_type 同时用于剔除「待填」。
    filled_by_type: dict[str, set[str]] = {}
    written_lines: list[str] = []
    for rtype, rec, _photos in created:
        cj = rec.content_json or {}
        filled = filled_by_type.setdefault(rtype, set())
        if rec.section:
            filled.add("section")
        if rec.location:
            filled.add("location")
        if rec.work_content:
            filled.update({"content", "work_content"})
        if rec.problems:
            filled.add("problems")
        if rec.treatment:
            filled.add("treatment")
        if rec.inspection_start_time or rec.station_start_time:
            filled.add("start_time")
        if rec.inspection_end_time or rec.station_end_time:
            filled.add("end_time")
        if rec.inspection_photos or rec.witness_photos:
            filled.add("photo")
        for key in ("acceptance_type", "acceptance_content",
                    "self_check_conclusion", "supervision_conclusion"):
            if str(cj.get(key) or "").strip():
                filled.add(key)

        parts: list[str] = []
        if rtype in ("巡视", "旁站") and str(rec.problems or "").strip():
            parts.append(f"问题及处理情况：{rec.problems}")
        if rtype == "验收":
            if str(cj.get("acceptance_type") or "").strip():
                parts.append(f"验收类型：{cj['acceptance_type']}")
            if str(cj.get("acceptance_content") or "").strip():
                parts.append("验收内容：已按要点逐项写入")
            if str(cj.get("self_check_conclusion") or "").strip():
                parts.append(f"施工单位自检结论：{cj['self_check_conclusion']}")
            if str(cj.get("supervision_conclusion") or "").strip():
                parts.append(f"监理验收结论：{cj['supervision_conclusion']}")
        if parts:
            written_lines.append(f"· #{rec.id} " + "；".join(parts))
    if written_lines:
        lines.append("已写入：")
        lines.extend(written_lines)
    ids_by_type: dict[str, list[int]] = {}
    for rtype, rec, _photos in created:
        ids_by_type.setdefault(rtype, []).append(int(rec.id))
    # 验收要点仍是「待确认草案」时才需要用户再回一句「写入正式记录」；
    # 本轮已确认并落成正式内容的，结尾不再要求重复确认。
    _points_pending = any(
        rtype == "验收"
        and (rec.content_json or {}).get("acceptance_points")
        and not str((rec.content_json or {}).get("acceptance_content") or "").strip()
        for rtype, rec, _ in created
    )
    try:
        type_confirmation = split_record_confirm.build_type_confirmation(
            groups, display=_split_record_display_name,
            created_ids=ids_by_type, stage="created",
            filled_by_type=filled_by_type,
            closing=(
                "以上按类型带出，请确认是否符合现场情况；不符合直接说要改哪项，"
                "要补的栏位一并告诉我。都符合就回「写入正式记录」。"
                if _points_pending
                else "以上内容已写入记录；要改哪项直接说。"
            ),
        )
    except Exception:  # noqa: BLE001 - 展示增强，失败按旧回执走
        logger.debug("split type confirmation failed", exc_info=True)
        type_confirmation = ""
    if type_confirmation:
        lines.append("")
        lines.append(type_confirmation)
    # 缺项提示必须按「真实写库的记录」算：多图分组的 args 里没有逐组内容，
    # 直接拿 args 校验会误报「还差 1 项：记录内容」，而三条记录其实都有内容
    # （2026-11-18 真机截图）。
    missing_all: list[dict] = []
    try:
        from app.services.doc_quality import missing_fields as _missing_fields

        seen_keys: set[str] = set()
        for rtype, rec, _photos in created:
            values = {
                "work_content": rec.work_content,
                "content": rec.work_content,
                "section": rec.section,
                "location": rec.location,
                "problems": rec.problems,
                "treatment": rec.treatment,
                "start_time": rec.inspection_start_time or rec.station_start_time,
                # 验收「举牌验收照片」：照片落在 inspection_photos，键名与省统表
                # canonical=photo 不同，不带上就会把已挂照片的验收误报成缺照片
                "inspection_photos": rec.inspection_photos or [],
                **(rec.content_json or {}),
            }
            for item in _missing_fields(rtype, values):
                key = str(item.get("key") or item.get("label") or "")
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    missing_all.append(item)
    except Exception:  # noqa: BLE001 - 提示增强，失败退回原回执
        logger.debug("split missing fields failed", exc_info=True)
        missing_all = []
    return _ok(
        "\n".join(lines),
        cards=cards,
        actions=[],
        undo=None,
        data={"record_ids": [rec.id for _, rec, _ in created], "split": True,
              "type_confirmation": type_confirmation,
              "missing_fields": missing_all},
    )


def _split_content(g, rtype: str, project_name: str = "", orgs=()) -> str:
    """分组记录的 content 草稿：优先用逐图内容提示拼接，否则给类型占位（待用户补全）。"""
    # clean_hints：切掉水印尾巴（摄时间/天气/监理/品牌防伪码）并去重保序，
    # 顺带剔除混进内容的项目名与监理单位落款。
    # 不清洗会把「…验收 摄时间：2026.10.27 15:18 气：多云31°C 监理」或
    # 「沙盛四路等4条道路工 广东建设监理；沟槽开挖 日」直接写进记录的工作内容。
    hints = multi_image_split.clean_hints(g.hints, project_name=project_name, orgs=orgs)
    if hints:
        # 表盘刻度别当参数值：「水泥搅拌桩水灰比 800」（真机 2026-11-18）
        joined = multi_image_split.scrub_reading_noise("；".join(hints))
        return joined[:2000]
    if rtype == "待定":
        return "待确认归类（由现场照片自动分组，待补充类型与内容）"
    return f"{rtype}（由{len(g.images)}张现场照片自动归类，待补全内容）"


async def _create_work_record(ctx: ToolContext, args: dict) -> dict:
    content = str(args.get("work_content") or args.get("content") or args.get("description") or "").strip()
    # 工作内容里若被写进了水印尾巴（「摄时间：… 气：多云31°C 监理」），
    # 清洗只切「工作内容」，切下来的时间/天气/人员回填到对应字段并存档（不丢信息）。
    _content_raw = content
    _wm_tail: dict = {}
    if content and multi_image_split.looks_like_watermark_tail(content):
        _cleaned, _wm_tail = multi_image_split.split_hint(content)
        if _cleaned:
            content = _cleaned
        for _k, _v in _wm_tail.items():
            if not str(args.get(_k) or "").strip():
                args[_k] = _v
    # 水印相机印刷的 App 品牌/防伪码/拍摄日期/气温不该进工作内容
    # （真机：「沙盛四路等4条道路工 广东建设监理；沟槽开挖 日」）。
    if content:
        content = multi_image_split.scrub_watermark_noise(content)
        content = multi_image_split.scrub_reading_noise(content)
    section = str(args.get("section") or "").strip()
    location = str(args.get("location") or "").strip()
    # 跨轮持久化锚点：路由器最终会暂存它自己持有的 args 对象（即本参数），
    # 本函数内对 apply_canonical_form_fields 之后的本地副本做的字段写入不会自动回到该对象，
    # 故凡需跨轮保留的状态（验收内容/结论/类型/询问态）都通过 _set_field_persist 显式镜像回 orig_args。
    orig_args = args
    # 🔴 显式写入授权（2026-11-16 真机录屏）：用户已明确「按已确认内容写入正式
    # 记录、其余栏位留空」时，必填缺项不再追问，缺的栏位留空（不臆测填写），
    # 直接落库；否则「写入正式记录」会被当成又一轮草稿整理，无限循环。
    write_through = bool(args.get("_write_through")) or wants_write_through(ctx.message or "")
    # 🔀 多图逐张分类分流（B 方案）：本轮多张图且带逐图视觉信号时，先按图分类、同类合并；
    # 多组或含待定组先回执确认，确认后循环建多条。该分支在单记录逻辑之前短路返回，
    # 避免「类型只能取一个 / 多图全挂一条」把多类照片误建为一条。
    _split_result = await _maybe_handle_multi_image_split(ctx, args, orig_args)
    if _split_result is not None:
        return _split_result
    # 🔧 槽位键归一：模型可能用表字段 key（10_14）写 extra_fields，服务端读 canonical（pm_onsite）。
    # 不改会误判「仍缺字段」→ 死循环追问（2026-10-16 真机：危大巡视「到岗」反复被问）。
    raw_type = _norm_record_type(str(args.get("record_type") or "")) or ""
    _weida = _is_weida(raw_type, args, ctx.message or "")
    _form = record_field_spec.form_for_record_type(raw_type, _weida) or ""
    if _form:
        args = apply_canonical_form_fields(args, _form)
        args = normalize_extra_fields(args, _form)
    # 🔴 用户随消息发来的现场照片：模型看不到图片，无法自行填「举牌验收照片」等照片栏，
    # 不映射则照片必填项永远缺失、验收等记录永远建不出
    # （2026-11-09/11-10 生产实测：用户明明发了图，仍卡在缺照片）。
    # 照片来源=本轮消息带的图，或草稿持久化的图（确认轮/补类型轮 ctx.images 已空，
    # 必须靠草稿里的 _agent_images 才能落到记录）。全部图片暂存到隐藏键 _agent_images，
    # 供建记录时挂到 record.inspection_photos 渲染。
    _pending_images = (
        [str(u).strip() for u in (ctx.images or []) if str(u).strip()]
        or [str(u).strip() for u in (args.get("_agent_images") or []) if str(u).strip()]
    )
    if _pending_images:
        _photo_field = next(
            (f for f in record_field_spec.fields_of(_form)
             if str(f.get("canonical") or "") == "photo"
             or str(f.get("key") or "").find("photo") >= 0
             or str(f.get("kind") or "") == "image"),
            None,
        )
        if _photo_field and not _field_value(args, _photo_field):
            # 合同只认一个照片栏，填第一张即可满足必填；其余图片走 _agent_images 渲染。
            args = _set_field_persist(orig_args, args, _photo_field, str(_pending_images[0]))
        if not args.get("_agent_images"):
            args["_agent_images"] = list(_pending_images)
            orig_args["_agent_images"] = list(_pending_images)
    # 🔧 项目名误当部位兜底：「巡视XX项目…工程部位为YY」常被模型把项目名填进 section、
    # 部位清单进了 work_content。两类信号：① section≈本项目名；② section 以「项目/项目部」
    # 结尾（真部位如「3号楼二层临边防护」不会这样结尾）——命中且有内容时，用内容作部位
    if section and content:
        pname = str(getattr(ctx.project, "name", "") or "").strip() if ctx.project is not None else ""
        looks_like_project = bool(pname and (section == pname or pname in section or section in pname))
        looks_like_project = looks_like_project or section.endswith(("项目", "项目部"))
        if looks_like_project:
            args["section"] = section = content
    # 🔴 验收记录不受此守卫拦截：验收的「内容/部位」由要点询问块（一起询问）
    # 汇总生成，先拦内容会把流程串行成「问类型→问内容→问工序→询问块」
    # 四五轮填表（2026-11-05 15:07 用户实测「怎么还是填表」）。
    # 待定记录同样放行（A 层·零必填）：类型已明确「待定」时允许空内容落草稿，
    # 后续在 work 页补内容/贴标签（眼镜端创建待定记录本就不强制字段）。
    if (not content and not section and not location) and raw_type not in ("验收", "待定"):
        return _need_more(["记录内容或施工区域"], "请说明记录内容与位置，例如「3号楼二层，钢筋绑扎，天气晴」。")

    # 记录类型识别（A 层·jj 零必填 → 2026-11-24 改版）：识别不出时不再
    # 静默落「待定」。有内容/照片时先回执问一句类型（用户答了再建）；
    # 「待定/不确定」字样与路由层直达分支（_auto_tbd）仍落待定。
    # 确认轮上用户明确说了「待定/不确定」同样落待定。
    raw_type = str(args.get("record_type") or "").strip()
    record_type = _norm_record_type(raw_type)
    if record_type is None:
        # 照片在确认轮已离开 ctx.images，只看 _pending_images（含草稿持久化的 _agent_images）
        has_entity = bool(content or section or location or _pending_images)
        if raw_type and ("待定" in raw_type or "不确定" in raw_type):
            record_type = "待定"
        elif has_entity and (
            args.get("_auto_tbd")
            or agent_request_plan.wants_zero_required_stash(
                ctx.message or "", bool(_pending_images), raw_type or None)
        ):
            # A 层直达分支（「你先记着」）与多图分组内部仍需落待定：
            # 路由层 _auto_tbd 直达，或消息文本明确说「先记着」且带图
            # （路由层漏打标时执行层兜底，如直调工具/测试链路）。
            record_type = "待定"
        elif has_entity:
            # 🔎 2026-11-24 用户反馈「照片类型没判断出来就建成待定」：
            # 有现场内容/照片但类型不明 → 先问一句类型，不再直接落「待定」。
            return _need_more(
                ["记录类型"],
                "照片和现场信息我先记着。这条要整理成巡视、旁站、验收还是见证记录？直接说一声就行。",
            )
        else:
            return _need_more(
                ["记录类型"],
                "这条要记成哪一类？直接回复记录类型就行。",
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
                    f"{conflict['label']}我这边读到几个不同的说法"
                    f"（{'、'.join(candidates) if candidates else '以你说的为准'}），你说一下按哪个算就行。",
                )
            field = next((item for item in resolved["fields"] if item.get("missing") or item.get("validation_error")), None)
            if field:
                # 2026-11-06「完全交互」：不再把字段表 question_for 的
                # 「还差一项：X（可直接填写…）」模板或候选 options 下发到对话，
                # 具体校验错误仍照实说，其余统一成一句自然语言。
                # 2026-11-24：校验兜底句也走变体轮换（同栏同轮稳定）。
                return _need_more(
                    [field["label"]],
                    field.get("validation_error")
                    or agent_reply_variants.missing_field_ask(
                        str(field["label"]), _draft_context_version(args)),
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

    # 巡视「问题及处理」：用户原话说「符合要求/无异常」时兜底回填（见 _backfill_no_issue 注释）。
    # 🔴 静默/带照片但未说无问题的情况不兜底——交给模型从素材归结，禁止盲填「符合要求」。
    if record_type == "巡视" and not str(args.get("problems") or "").strip():
        hit = _backfill_no_issue(ctx.message)
        if hit:
            args["problems"] = hit
    # 旁站「结论」：同样兜底（GD-B-212「旁站的问题及处理情况」栏）
    if record_type == "旁站" and not str(args.get("station_conclusion") or "").strip():
        hit = _backfill_no_issue(ctx.message)
        if hit:
            args["station_conclusion"] = hit
    # 旁站「施工情况」：LLM 把叙述放进 work_content 时回落复用，保证 GD-B-212 正文栏不空
    if record_type == "旁站" and not str(args.get("station_content") or "").strip() and content:
        args["station_content"] = content

    # 验收分支 ·「一起询问」模式（2026-11-05）：系统按 分部工程+关键工序 命中要点库 →
    # 一次性带出全部「询问式」→ 用户一并回复 → 系统据全部回复逐条归集 → 生成「验收结论」。
    matched_entry = None
    if record_type == "验收":
        acc_field = _acceptance_content_field()
        acc_value = _field_value(args, acc_field) if acc_field else ""
        ask_state = args.get("_acceptance_ask_state")

        # --- 已带出询问块、等待用户整体回复：按全部回复逐条归集 ---
        # 不看 acc_value：第二轮模型/refill 可能把整句回复误填进验收内容栏，
        # 只要询问态仍在，就以本轮 ctx.message 为「整体回复」归集并覆盖该栏。
        if ask_state and isinstance(ask_state, dict):
            reply_text = (ctx.message or "").strip()
            if _wants_skip(reply_text) or any(h in reply_text for h in ("？", "?")):
                # 用户明确「跳过，直接创建」或仍在提问 → 消费询问态，
                # 不把这句当整体回复去归集，也不再重复呈现询问块打扰用户。
                orig_args.pop("_acceptance_ask_state", None)
                args.pop("_acceptance_ask_state", None)
            else:
                entry = _aat_find_entry(str(ask_state.get("div") or ""),
                                        str(ask_state.get("item") or ""))
                if entry and entry.get("验收要点_询问式"):
                    aggregated = None
                    if _looks_like_reply(reply_text):
                        try:
                            aggregated = await _aat_aggregate_reply(
                                entry["验收要点_询问式"], reply_text)
                        except Exception:  # noqa: BLE001
                            aggregated = None
                    if aggregated:
                        built = _build_acceptance_content(entry, aggregated)
                        args = _set_field_persist(orig_args, args, acc_field, built)
                        concl_field = next(
                            (f for f in record_field_spec.ask_fields("acceptance_record")
                             if f.get("canonical") == "supervision_conclusion"), None)
                        if concl_field:
                            # 无条件覆盖：归集结论依据用户「全部回复」算出，
                            # 权威于首轮可能预填的「合格」兜底值，否则不合格会被静默盖成合格。
                            args = _set_field_persist(
                                orig_args, args, concl_field,
                                _aat_generate_conclusion(aggregated))
                        # 🔴 2026-11-17 新增：同步生成施工单位自检结论（省统表 YS 栏位⑥-自检）
                        _ask_fields = record_field_spec.ask_fields("acceptance_record")
                        sc_field = next(
                            (f for f in _ask_fields
                             if f.get("canonical") == "self_check_conclusion"), None)
                        if sc_field:
                            args = _set_field_persist(
                                orig_args, args, sc_field,
                                _aat_generate_self_check_conclusion(aggregated))
                        orig_args.pop("_acceptance_ask_state", None)
                        args.pop("_acceptance_ask_state", None)
                    else:
                        # 归集失败/不像回复：若同一 (分部,工序) 此前已询问过，不再重复呈现
                        # 询问块（避免「答非所问→又问一遍」死循环），改走缺失项补全流程。
                        if not _aat_already_asked(args, ask_state):
                            matched_entry = entry

        # --- 首次 / 非回复态：识别工序、带出询问块（不直接填 raw points） ---
        # 不看 acc_value：模型可能自作主张把水印信息/旧要点全文预填进验收内容栏，
        # 若因此跳过询问块，用户发图后只会得到 chips 补全（2026-11-05 用户实测反馈
        # 「发了图就给我这么一个回复吗」）。询问块归集成功后无条件覆盖该栏；
        # 用户明确「跳过」时消费询问态、沿用已有值直接创建。
        if not (ask_state and isinstance(ask_state, dict)):
            acc_query = _acceptance_query(section, content,
                                          str(args.get("location") or ""), ctx.message or "")
            prefer = _acceptance_prefer_kind(ctx)
            strong = acceptance_checklists.strong_match(acc_query, prefer_kind=prefer) if acc_query else None
            if strong:
                entry = _aat_find_entry(str(strong.get("div") or ""),
                                        str(strong.get("item") or ""))
                if entry and entry.get("验收要点_询问式"):
                    if _aat_already_asked(args, strong):
                        # 该 (分部,工序) 此前已「一起询问」过，不再重复带出询问块；
                        # 沿用已有值进入正常归集/缺失补全流程，避免「答完又问一遍」。
                        pass
                    else:
                        # 首次命中且有结构化询问式 → 先「一起带出全部询问项」等用户一并回复，
                        # 不把用户下指令的首句误当整体回复（避免行为不一致、白调一次 LLM）。
                        _st = {"div": str(strong.get("div") or ""),
                               "item": str(strong.get("item") or "")}
                        args["_acceptance_ask_state"] = _st
                        orig_args["_acceptance_ask_state"] = _st
                        args["_acceptance_asked"] = _st
                        orig_args["_acceptance_asked"] = _st
                        matched_entry = entry
                else:
                    args = _set_field_value(args, acc_field, str(strong["points"]))
            else:
                direct = acceptance_checklists.strong_match(ctx.message or "", prefer_kind=prefer)
                if direct:
                    entry = _aat_find_entry(str(direct.get("div") or ""),
                                            str(direct.get("item") or ""))
                    if entry and entry.get("验收要点_询问式"):
                        if _aat_already_asked(args, direct):
                            # 已询问过，不再重复带出询问块（防重问）
                            pass
                        else:
                            _st = {"div": str(direct.get("div") or ""),
                                   "item": str(direct.get("item") or "")}
                            args["_acceptance_ask_state"] = _st
                            orig_args["_acceptance_ask_state"] = _st
                            args["_acceptance_asked"] = _st
                            orig_args["_acceptance_asked"] = _st
                            matched_entry = entry
                    else:
                        args = _set_field_value(args, acc_field, str(direct["points"]))

        # 验收类型上下文预填（保留原行为；并持久化到 orig_args 以便跨轮，避免二轮被整句兜底污染）
        acc_type_field = _acceptance_type_field()
        if acc_type_field and not _field_value(args, acc_type_field):
            # 素材（照片水印/公示牌/OCR 文本）往往已写明验收子类型（如「检验批质量验收记录」
            # 「隐蔽工程验收记录」），把它一并纳入命中，避免用户还要在 chips 里再点一次。
            acc_src = " ".join(x for x in (
                str(ctx.message or ""), content, section,
                str(args.get("location") or ""), str(ctx.material or "")
            ) if str(x or "").strip())
            inferred = _infer_acceptance_type(acc_src)
            if not inferred and matched_entry:
                inferred = _acceptance_type_from_entry(matched_entry)
            if inferred:
                args = _set_field_persist(orig_args, args, acc_type_field, inferred)
        # 验收结论兜底回填：模型把「合格/不合格」收进 acceptance_result，
        # 而验收表对应栏是「监理验收结论」（2026-11-04 用户反馈「答了怎么还问」——
        # 验收表没有 acceptance_result 栏，不映射就会漏掉用户已经给的结论）。
        # 「一起询问」态下不预填：结论必须依据用户对全部询问项的回复归集生成。
        acc_concl_field = next(
            (f for f in record_field_spec.ask_fields("acceptance_record")
             if f.get("canonical") == "supervision_conclusion"), None)
        if acc_concl_field and not matched_entry and not _field_value(args, acc_concl_field):
            concl = str(args.get("acceptance_result") or "").strip()
            if not concl:
                concl = next(
                    (h for h in _NO_ISSUE_HINTS if h in (ctx.message or "")), "") or ""
            if concl:
                args = _set_field_persist(orig_args, args, acc_concl_field, concl)

    # 字段表驱动的追问：表上「由系统生成=否」且用户还没说的字段，只用纯文字
    # **一次问当前最该补的一栏**（2026-11-06 用户「我不要这些选择，我要完全交互」：
    # 不再下发任何候选 chips / 可点选项 /「还有几项」字段清单，全部自然语言一问一答）。
    # “跳过”只允许跳过非必填栏；合同声明的必填字段、冲突和格式错误不能被跳过。
    assist = assist_fields(record_type, is_weida)
    missing = [
        f for f in assist
        if not (_time_filled(args, f)
                if f.get("canonical") in ("start_time", "end_time")
                else _field_value(args, f))
    ]
    required_missing = [f for f in missing if bool(f.get("required"))]
    # 显式写入授权：必填缺项也放行（留空、不臆测填写），不再反复追问。
    if missing and not write_through and (required_missing or not _wants_skip(ctx.message)):
        labels = [str(f.get("label") or f.get("canonical") or "") for f in missing]
        current = labels[0] if labels else "信息"
        # 2026-11-24「工作台交互太僵硬」：逐栏追问句变体轮换。轮次种子取草稿
        # _context.version（每轮 stash 递增）——同栏同轮稳定、跨轮换说法，
        # 不再每轮逐字复读「还差「X」，你说一下就行」。
        _ask_turn = _draft_context_version(args, orig_args)
        if record_type == "验收" and not matched_entry:
            # 工序未知：只问这一件事，不给候选清单。用户自己说出工序名后，
            # 由 strong_match/_acceptance_query 在下一轮触发「一起询问」。
            reply = agent_reply_variants.acceptance_procedure_ask(_ask_turn)
        else:
            reply = agent_reply_variants.missing_field_ask(current, _ask_turn)
        result = _need_more(labels, reply)
        if matched_entry:
            # 「一起询问」：把该工序全部要点一次性带出（纯文字、无复选框），
            # 用户就全部要点一并回复；系统再据全部回复逐条归集生成结论。
            from app.services import acceptance_ask_together as _aat
            block = _aat.assemble_ask_block(matched_entry)
            result["reply"] = block + "\n\n" + (
                f"另外「{current}」你要是顺手有，也一起说给我就行。")
            result["ask_block"] = block
            result["ask_mode"] = "together"
            # 询问态随草稿跨轮保留（已镜像进 orig_args），第二轮据此触发归集
        return result

    # 「一起询问」拦截：即使其它字段已齐（missing 为空，不进上方补全分支），
    # 验收记录也必须先让用户对全部要点一并确认、归集生成验收内容与结论，
    # 不能拿模型预填的验收内容静默创建（2026-11-05 用户实测反馈）。
    # 用户明确「跳过，直接创建」时消费询问态、沿用已有值放行。
    if record_type == "验收" and matched_entry and not write_through:
        if _wants_skip(ctx.message):
            orig_args.pop("_acceptance_ask_state", None)
            args.pop("_acceptance_ask_state", None)
        else:
            from app.services import acceptance_ask_together as _aat
            block = _aat.assemble_ask_block(matched_entry)
            result = _need_more(
                [str((acc_field or {}).get("label") or "验收内容（验收要点）")],
                block + "\n\n你照实际情况说一句就行，我来整理成验收内容和结论。",
            )
            result["ask_block"] = block
            result["ask_mode"] = "together"
            return result

    # 🔴 验收内容不得是「询问式」清单（2026-11-09 真机截图）：模型会把
    # 「一起询问」的要点改写成「…是否一致／是否合格」预填进该栏，合同只判非空
    # 就放行，最终确认卡上便出现一份问卷。优先按用户明确的整体结论重建，
    # 没有明确结论时停下来问一句，绝不把问卷写进正式记录。
    if record_type == "验收":
        _acc_check = _acceptance_content_field()
        if _acc_check and _is_inquiry_style_content(_field_value(args, _acc_check)):
            asked = args.get("_acceptance_asked")
            entry = (_aat_find_entry(str(asked.get("div") or ""), str(asked.get("item") or ""))
                     if isinstance(asked, dict) else None)
            items = (entry.get("验收要点_询问式") or []) if entry else []
            aggregated = _rule_aggregate_acceptance(items, ctx.message or "")
            if entry and aggregated:
                rebuilt = _build_acceptance_content(entry, aggregated)
                args = _set_field_persist(orig_args, args, _acc_check, rebuilt)
                concl_field = next(
                    (f for f in record_field_spec.ask_fields("acceptance_record")
                     if f.get("canonical") == "supervision_conclusion"), None)
                if concl_field:
                    args = _set_field_persist(
                        orig_args, args, concl_field, _aat_generate_conclusion(aggregated))
                # 🔴 同步填自检结论（见上一处注释）
                _af = record_field_spec.ask_fields("acceptance_record")
                sc_field2 = next(
                    (f for f in _af if f.get("canonical") == "self_check_conclusion"), None)
                if sc_field2:
                    args = _set_field_persist(
                        orig_args, args, sc_field2,
                        _aat_generate_self_check_conclusion(aggregated))
            elif write_through:
                # 授权留空：问卷式内容绝不能写进正式记录，清空该栏后按已确认内容放行。
                args = _set_field_persist(orig_args, args, _acc_check, "")
            else:
                return _need_more(
                    ["验收内容（验收要点）"],
                    "验收内容现在还是逐项询问句，不能当正式记录用。请照实际情况说一句结论"
                    "（例如「4 项都符合」或「第 2 项不符合，原因是…」），我来逐条整理成验收内容和结论。",
                )

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
        # 显式写入授权只放行「缺项」（留空），冲突/格式错误仍必须拦，
        # 否则会把互相矛盾或非法的值写进正式记录。
        blocked = (
            (not write_through and bool(resolved_form.get("missing_fields")))
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
                    f"{conflict.get('label') or conflict.get('canonical')}我这边读到几个不同的说法"
                    f"（{'、'.join(candidates) if candidates else '以你说的为准'}），你说一下按哪个算就行。",
                )
            invalid = next(iter(resolved_form.get("invalid_fields") or []), None)
            field = next((item for item in resolved_form.get("fields") or [] if item.get("missing") or item.get("validation_error")), None)
            if invalid or field:
                item = invalid or field or {}
                label = item.get("label") or item.get("canonical") or "必填项"
                # 同上：具体错误照实说，字段表模板/候选一律不下发到对话。
                # 2026-11-24：校验兜底句也走变体轮换（同栏同轮稳定）。
                return _need_more(
                    [label],
                    item.get("message") or item.get("validation_error")
                    or agent_reply_variants.missing_field_ask(
                        str(label), _draft_context_version(args)),
                )
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
    # 2026-11-24「工作台交互太僵硬」：引导句变体轮换（「尚未写入正式工作记录」
    # 的真实状态提示每变体都保留——假成功红线不破）。轮次种子同追问句。
    if (args.get("_require_confirmation") and not args.get("_execution_confirmed")
            and not write_through):
        return _need_more(
            ["最终确认"],
            agent_reply_variants.final_confirm_ask(_draft_context_version(args, orig_args)),
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

    # 隐藏键清洗：候选/编号解析、一起询问态用，不落库、不进 content_json
    args.pop(_ACCEPT_CANDS_KEY, None)
    args.pop(_ACCEPT_CANDS_ORDER, None)
    args.pop("_acceptance_ask_state", None)
    # 路由层内部标记，绝不落库、不进 content_json
    args.pop("_write_through", None)
    _agent_images = [str(u).strip() for u in (args.pop("_agent_images", None) or []) if str(u).strip()]
    inspection_photos = None
    witness_photos = None
    if _agent_images:
        # 用户随消息发的现场照片：挂到记录的照片列表，详情/省统表即可渲染
        # （验收/巡视/旁站/其他走 inspection_photos，见证走 witness_photos）。
        if record_type == "见证":
            witness_photos = list(_agent_images)
        else:
            inspection_photos = list(_agent_images)

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
        # 用户随消息发的现场照片：详情/省统表渲染用（验收/巡视/旁站/其他走 inspection_photos）
        inspection_photos=inspection_photos,
        witness_photos=witness_photos,
        acceptance_participants=str(args.get("acceptance_participants") or "").strip() or None,
        acceptance_basis=str(args.get("acceptance_basis") or ""),
        witness_material_name=str(args.get("witness_material_name") or "")[:128] or None,
        witness_material_spec=str(args.get("witness_material_spec") or "")[:256] or None,
        witness_quantity=str(args.get("witness_quantity") or "")[:32],
        witness_unit=str(args.get("witness_unit") or "")[:16],
        witness_cert_no=str(args.get("witness_cert_no") or "")[:64],
        content_json={
            "source": "agent",
            # 原话保留（含用户自己写的时间等），工作内容用清洗后的版本
            "voice_text": _content_raw,
            **({"watermark_tail": _wm_tail} if _wm_tail else {}),
            **contract_snapshot,
            **({"prov_fields": extra_values(args)} if extra_values(args) else {}),
            **({"hazard_type": hazard_type} if (is_weida and hazard_type) else {}),
            **({"wind": str(args.get("wind"))[:16]} if args.get("wind") else {}),
            **({"subcontract_unit": str(args.get("subcontract_unit"))[:128]}
               if args.get("subcontract_unit") else {}),
            # 巡视人/旁站监理人员：模型没给姓名就用创建人，避免文书该栏空白
            **({"inspector": _inspector_name(
                [_wm_tail.get("person")], str(getattr(ctx.user, "real_name", "") or ""))}
               if record_type in ("巡视", "旁站") else {}),
            # 旁站到岗情况/自查自检：草稿先给「是」，避免详情页「未填写 / -」
            **({"qa_onsite": "是", "self_check": "是"} if record_type == "旁站" else {}),
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
    # 2026-11-24「工作台交互太僵硬」：成功尾句变体轮换，不再每次建单都复读
    # 同一串「可在工作记录页查看/生成监理日志」口号。轮次种子取记录 id——
    # 同一次会话内连续建单自然换说法，幂等可测。
    reply += "\n" + agent_reply_variants.created_tail(int(record.id or 0))
    if record_type == "待定":
        # 2026-11-13 修正：旧文案承诺「可在工作记录页改成巡视/旁站/验收/见证」，
        # 但那个改类型入口在小程序/Web 后台都没接出来（只有裸 API），属误导。
        # 现用纯后端对话快捷指令「改成巡视」兜底，现网所有版本立刻可用。
        # 2026-11-24：尾句同样变体轮换（快捷指令口径不变）。
        reply += "\n" + agent_reply_variants.tbd_tail(int(record.id or 0))
    if is_weida:
        reply += ("\n这是危大工程巡视：生成文书时选《危险性较大的分部分项工程监理巡视检查记录表》"
                  f"（GDAQ30306），检查项会按「{hazard_type or '危大类型'}」自动带出。")
    # 2026-11-13：本轮消息带的照片已在上面挂到 inspection_photos/witness_photos，
    # 再引导「在下方表单里添加」会让用户误以为照片没存上、需要重传一次。
    if _agent_images:
        reply += f"\n本轮 {len(_agent_images)} 张现场照片已挂到这条记录，无需重复上传。"
    else:
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
        # photo_count 供路由层判断本轮是否已带图（已带图则不再下发「补充现场照片」卡）
        data={"record_id": record.id, "photo_count": len(_agent_images)},
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


async def _dispatch_task(ctx: ToolContext, args: dict) -> dict:
    """总监/总代在聊天里对 AI 说「让某某去…」即派单（2026-11-18 方案②）。

    与 task.py create_task 同口径复用（成员表判据 + task_receive 校验 +
    通知推送），不走 ORM 裸写，保证业务规则不漂移。
    """
    title = str(args.get("title") or "").strip()
    if not title:
        return _need_more(["任务标题"], "请说一下任务标题，例如「检查 3 号楼二层临边防护」。")
    # 被指派人：姓名（模糊匹配本项目可派人）或 user_id
    from app.models.task_assignment import TaskAssignment, TaskCategory, TaskPriority
    from app.routers.task import CreateTaskRequest
    assignee_id: int | None = None
    assignee_name = str(args.get("assignee_name") or args.get("assignee") or "").strip()
    raw_id = str(args.get("assignee_id") or "").strip()
    if raw_id.isdigit():
        assignee_id = int(raw_id)
    elif assignee_name:
        from app.services.permissions import project_members_with_permission
        rows = await project_members_with_permission(ctx.db, ctx.project_id, "task_receive")
        for member, user_row in rows:
            names = {user_row.real_name or "", user_row.username or ""}
            if assignee_name in names or assignee_name in (user_row.real_name or ""):
                assignee_id = user_row.id
                break
        if assignee_id is None:
            candidates = "、".join(
                f"{u.real_name}" for _m, u in rows[:8]
            ) or "（本项目暂无可派人成员）"
            return _need_more(
                ["被指派人"],
                f"我在本项目没找到叫「{assignee_name}」的可派人员。本项目可指派：{candidates}。请确认姓名后再说一次。",
            )
    if assignee_id is None:
        from app.services.permissions import project_members_with_permission
        rows = await project_members_with_permission(ctx.db, ctx.project_id, "task_receive")
        candidates = "、".join(f"{u.real_name}" for _m, u in rows[:8]) or "（本项目暂无可派人成员）"
        return _need_more(
            ["被指派人"],
            f"要指派给谁？本项目可指派：{candidates}。请说姓名。",
        )

    category = str(args.get("category") or "").strip()
    valid_cats = [c.value for c in TaskCategory]
    if category not in valid_cats:
        category = "其他事务" if "其他事务" in valid_cats else valid_cats[0]
    priority = str(args.get("priority") or "").strip()
    valid_pris = [p.value for p in TaskPriority]
    if priority not in valid_pris:
        priority = "中" if "中" in valid_pris else valid_pris[0]

    req = CreateTaskRequest(
        title=title[:200],
        description=str(args.get("description") or "").strip()[:2000] or None,
        assignee_id=assignee_id,
        category=category,
        priority=priority,
        notify_glasses=True,
    )
    from app.routers.task import create_task
    result = await create_task(req=req, user=ctx.user, db=ctx.db)

    from app.models.user import User as _User
    assignee = await ctx.db.get(_User, assignee_id)
    assignee_label = (assignee.real_name if assignee else None) or f"用户#{assignee_id}"
    await _audit(ctx.db, ctx.user, "agent_write", {
        "tool": "dispatch_task", "entity": "task_assignment", "id": result.get("task_id"),
    })
    await ctx.db.commit()

    task = await ctx.db.get(TaskAssignment, result.get("task_id"))
    return _ok(
        f"已派单给 {assignee_label}：{title}（任务 #{result.get('task_id')}，{category}，优先级「{priority}」）。对方会收到眼镜端与微信通知。",
        card={
            "kind": "task", "id": result.get("task_id"), "title": title[:60],
            "sub": f"已派给 {assignee_label}", "status": task.status if task else "待接单",
            "time": str(datetime.now().date()),
        },
        actions=[{"type": "navigate", "label": "查看我的派单", "url": "/pages/todo/index"}],
        undo=None,
        data={"task_id": result.get("task_id"), "assignee_id": assignee_id},
    )


def _provform_name_index() -> list[tuple[str, str]]:
    """表式名 → form_code 的候选索引（含 CHOICES 友好名、registry 官方名、表号）。"""
    from app.services.form_registry import FORM_BY_CODE

    # 常用简称补充（registry/CHOICES 里没有的口语说法）：
    # 「安全日志」→ anquan_jianli_rizhi（D06：prompt 指引用了这个简称，解析不出来
    # 会静默退化成普通监理日志）。
    extra_aliases: dict[str, tuple[str, ...]] = {
        "anquan_jianli_rizhi": ("安全日志", "安全监理日记"),
        "jianli_riji": ("我的日记", "个人日记"),
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
    if period in period_map or form_code == "jianli_riji":
        from app.services.period_report_service import generate_aggregate_doc, generate_supervision_log, now_cn
        # 🔴 个人监理日记：用户说「生成我的监理日记 / 今天的监理日记」时，
        # form_code 已归一为 jianli_riji。日记=个人维度，必须走 generate_supervision_log
        # 并限定 author_id=ctx.user.id（只聚合本人当日记录，落款本人），
        # 不能走 generate_aggregate_doc 的项目级聚合（会把全项目记录混进来）。
        if form_code == "jianli_riji":
            diary_date = str(args.get("date") or "").strip()
            try:
                target = (
                    datetime.strptime(diary_date, "%Y-%m-%d").date()
                    if diary_date else now_cn().date()
                )
            except ValueError:
                return _need_more(["date"], "日期格式不对，请给 YYYY-MM-DD 或直接说「生成我的监理日记」。")
            raw_extra = args.get("extra_fields")
            extra_fields = {}
            if isinstance(raw_extra, dict):
                extra_fields = (
                    normalize_extra_fields({"extra_fields": raw_extra}, "jianli_riji").get("extra_fields") or {}
                )
            narrative = ctx.message if len(ctx.message or "") > 40 else None
            try:
                result = await generate_supervision_log(
                    ctx.db, ctx.project_id, target,
                    supervisor=ctx.user, force=True,
                    extra_fields=extra_fields or None,
                    narrative=narrative,
                    author_id=ctx.user.id,
                )
            except (ValueError, RuntimeError, OSError) as exc:
                return _ok(f"监理日记生成失败：{exc}。可到「省统表录入」核对记录后重试。")
            doc_id = result.get("doc_id")
            await _audit(ctx.db, ctx.user, "agent_write", {
                "tool": "generate_document", "entity": "document", "id": doc_id,
            })
            await ctx.db.commit()
            return _ok(
                f"已生成你的「监理日记」#{doc_id}（{result.get('doc_number') or '编号待生成'}），"
                f"已归档到资料库「工作记录与表单/监理日记」，可在「文书」页导出。",
                card={"kind": "document", "id": doc_id, "title": result.get("title") or "",
                      "sub": "监理日记", "status": "草稿", "time": str(datetime.now().date())},
                cards=[{"kind": "document", "id": doc_id, "title": result.get("title") or "",
                        "sub": "监理日记", "status": "草稿", "time": str(datetime.now().date())}],
                actions=[{"type": "navigate", "label": "查看文书", "url": "/pages/documents/index"}],
                undo={"tool": "generate_document", "entity": "document", "id": doc_id},
                data=result,
            )
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
    from app.services.project_archive import sync_document_to_project_archive
    await sync_document_to_project_archive(ctx.db, doc, ctx.user)
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


async def _prepare_business_object_delete(
    db, object_type: str, object_id: int, actor_id: int
) -> None:
    """Retire collaboration work and release archive FKs before deletion."""
    from app.services.collaboration import cancel_action_items

    await cancel_action_items(
        db,
        object_type=object_type,
        object_id=object_id,
        actor_id=actor_id,
        detail="业务对象已由智能体撤销",
    )
    if object_type == "document":
        from app.services.project_archive import detach_project_archive_copy

        await detach_project_archive_copy(db, object_id)


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
        await _prepare_business_object_delete(
            ctx.db, "work_record", obj_id, ctx.user.id
        )
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
                await _prepare_business_object_delete(
                    ctx.db, "document", doc.id, ctx.user.id
                )
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
        await _prepare_business_object_delete(
            ctx.db, "document", obj_id, ctx.user.id
        )
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
        await _prepare_business_object_delete(
            ctx.db, "work_record", obj_id, ctx.user.id
        )
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
                await _prepare_business_object_delete(
                    ctx.db, "document", doc.id, ctx.user.id
                )
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
        await _prepare_business_object_delete(
            ctx.db, "document", obj_id, ctx.user.id
        )
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


# 🔧 表式/项目业务文书判定（query_regulation 的检索过滤口径）：
# 归到「其他资料」的验收记录/填写示例/操作指引同样是表式文件，按名称+正文特征剔除。
# 2026-11-25 生产实测（user 1 消息 717-718）：用户问「查一下规范」，返回的却是
# 本项目自己生成的《安全隐患整改通知单》全文（GDAQ30308，从隐患创建的项目文书
# 存进知识库、被 RAG 索引收录）。这类「用户项目生成的业务文书」不是规范条文：
# 补「整改通知/回复单/隐患整改/复工」等词，最后一道防线拦住。
_FORM_LIKE_HINTS = (
    "验收记录", "检查记录", "旁站记录", "记录表", "表格模板", "表式",
    "填写示例", "操作指引", "台账", "本表一式", "工程项目名称",
    "验收部位", "编号：", "填表", "监理通知单", "监理日志", "举牌验收照片",
    "整改通知", "整改回复", "回复单", "隐患整改", "整改通知单",
    "GDAQ30308", "GDAQ20309", "复工",
)


def is_form_like_doc(hit: dict) -> bool:
    """知识库命中是否属于表式/项目业务文书（不是规范条文）。"""
    blob = " ".join(str(hit.get(k) or "") for k in ("name", "filename", "snippet"))
    return any(hint in blob for hint in _FORM_LIKE_HINTS)


async def _query_regulation(ctx: ToolContext, args: dict) -> dict:
    keyword = str(args.get("keyword") or args.get("question") or ctx.message or "").strip()
    if not keyword:
        return _need_more(["检索关键词"], "想查哪条规范？例如「模板支撑验收要求」。")

    # 「XX 规范怎么要求」的权威来源是内置验收指引，不是知识库文件。
    # 知识库收录的是省统表/安全监理用表/验收记录/填写示例/操作指引等表式资料，
    # 把它们当「规范依据」必然答非所问（现场实测：问模板支撑验收，回了一堆表式字段）。
    from app.services import knowledge_service
    guide = None
    try:
        guide = knowledge_service.get_acceptance_guide(keyword)
    except Exception as exc:  # noqa: BLE001
        logger.warning("验收指引获取失败: %s", type(exc).__name__)

    FORM_CATEGORIES = {"省统表", "安全监理用表"}
    try:
        hits = knowledge_service.search_by_content(keyword) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("规范检索失败: %s", type(exc).__name__)
        hits = []
    regulation_hits = [
        h for h in hits
        if h.get("category") not in FORM_CATEGORIES and not is_form_like_doc(h)
    ]
    form_hits = [
        h for h in hits
        if h.get("category") in FORM_CATEGORIES or is_form_like_doc(h)
    ]

    topic_hit = bool(
        guide
        and guide.get("regulation_basis")
        and not str(guide["regulation_basis"][0]).startswith("请查询")
    )
    lines: list[str] = []
    citations: list[dict] = []
    if topic_hit:
        lines.append(f"「{keyword}」依据：{'；'.join(guide['regulation_basis'])}")
        if guide.get("key_points"):
            lines.append(f"验收要点：{'；'.join(guide['key_points'])}")
        if guide.get("steps"):
            lines.append("实施步骤：" + " → ".join(guide["steps"]))
        if guide.get("common_violations"):
            lines.append("常见问题：" + "、".join(guide["common_violations"]))
        for m in guide.get("matched_docs", [])[:3]:
            citations.append(_cite(
                len(citations) + 1, "regulation", m,
                sub="关联表式", target="/knowledge-base",
            ))
    # 命中内置指引时不再追加知识库「规范依据」：库内并无真规范条文，
    # 追加只会把表式字段/示例重新混进答案。
    if regulation_hits and not topic_hit:
        lines.append(f"与「{keyword}」相关的规范依据（{len(regulation_hits)} 条）：")
        for i, hit in enumerate(regulation_hits[:3], 1):
            name = hit.get("name") or hit.get("filename") or "规范"
            snippet = (hit.get("snippet") or "").strip().replace("\n", " ")
            lines.append(f"{i}. {name}：{snippet[:150]} [[{len(citations) + i}]]")
            citations.append(_cite(
                len(citations) + i, "regulation", str(name),
                sub="规范条文", snippet=snippet[:200], target="/knowledge-base",
                ref_id=hit.get("source_id") or hit.get("filename") or hit.get("name"),
            ))
    if not topic_hit and not regulation_hits:
        if form_hits:
            return _ok(
                f"资料库中与「{keyword}」相关的是表式/示例文件（{len(form_hits)} 份），不是规范条文。"
                "内置规范指引覆盖：模板、混凝土、钢筋、脚手架、深基坑、防水、电气、"
                "安全帽、临边洞口、临时用电；换个主题词再问，或到「资料库」查看表式。"
            )
        return _ok(
            f"没有检索到与「{keyword}」相关的规范条文。内置规范指引覆盖：模板、混凝土、"
            "钢筋、脚手架、深基坑、防水、电气、安全帽、临边洞口、临时用电；"
            "也可到「资料库」手动检索原文。"
        )
    lines.append("完整条文请到「资料库」检索原文。")
    return _ok("\n".join(lines), data={"count": len(regulation_hits)}, citations=citations)


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
    # 🔴 2026-11-25 生产真实卡点（user 32，无项目 inspector）：用户说「我要拍照
    # 记录工序」→ 前端 pickImages 对无项目用户先 guideJoinProject 拦死（点不出
    # 拍照选择）→ 用户改问对话 → navigate 回「已为你打开拍照识别」+一个可点
    # 按钮，但拍完上传撞 require_project_id，3 轮全失败。无项目时对写业务类
    # 页面直接引导加入项目，不再把用户导向必撞墙的入口。
    _NO_PROJECT_BLOCKED_PAGES = {
        "/pages/capture/index",
        "/pages/work/index",
        "/pages/hazards/index",
        "/pages/record-form/choose",
        "/pages/parallel/index",
    }
    if url in _NO_PROJECT_BLOCKED_PAGES and not getattr(ctx, "project_id", None):
        return _ok(
            "拍照和记录工序要先加入一个项目（记录必须挂在项目下）。"
            "点左上角「加入项目」填邀请码，加入后就能拍照记录了。"
        )
    # 🔴 2026-11-25 文案对齐真实行为：回执只插入一个可点的「打开」按钮，
    # 并不会自动切页面（前端 agentReplyToMessage 把 actions 映射成
    # navigate command，等用户点击才跳）。原文案「已为你打开」让用户以为
    # 页面已切走、原地干等（user 32 现场：等不到拍照界面又重新描述需求，
    # 进入「还没收到照片」死循环）。
    return _ok(f"点下面「{raw}」按钮即可进入{raw}页面。", actions=[{"type": "navigate", "label": raw, "url": url}])


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
def _project_condition(model, project_id: int | None):
    """项目隔离谓词：None 表示「无项目上下文」，只匹配历史 NULL 行。"""
    if project_id is None:
        return model.project_id.is_(None)
    return model.project_id == int(project_id)


async def record_conversation_message(db: AsyncSession, user_id: Any,
                                      conversation_id: str | None,
                                      message: str,
                                      images: list[str] | None = None,
                                      *,
                                      project_id: int | None = None,
                                      ) -> list[dict[str, str]]:
    """Persist a display turn and keep a bounded raw trail for model context.

    ``images``（可选）是随消息上传后的服务端媒体地址（相对路径即可），
    只进展示历史（content_json），不进模型上下文——模型读的是素材文字转述，
    不需要图片 URL。

    ``project_id`` 是项目隔离键：读取方（列表/详情/注入）必须按同一项目过滤，
    否则用户切换项目后会看到上一个项目的聊天与上下文。
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
        _project_condition(AgentConversationMemory, project_id),
    )
    row = (await db.execute(stmt)).scalars().first()
    if row is None and project_id is not None:
        # 升级前的老会话 project_id 为 NULL：没有项目归属。首次在当前项目续聊时
        # 把整条会话（原话 + 展示历史）认领到当前项目，避免老用户被 409 挡住。
        legacy = (await db.execute(select(AgentConversationMemory).where(
            AgentConversationMemory.user_id == int(user_id),
            AgentConversationMemory.conversation_id == conversation_id,
            AgentConversationMemory.project_id.is_(None),
        ))).scalars().first()
        if legacy is not None:
            legacy.project_id = int(project_id)
            row = legacy
            await db.execute(update(AgentConversationMessage).where(
                AgentConversationMessage.user_id == int(user_id),
                AgentConversationMessage.conversation_id == conversation_id,
                AgentConversationMessage.project_id.is_(None),
            ).values(project_id=int(project_id)))
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
            project_id=int(project_id) if project_id is not None else None,
        )
        db.add(row)
    row.messages_json = json.dumps(history, ensure_ascii=False)
    row.updated_at = datetime.now()
    db.add(AgentConversationMessage(
        user_id=int(user_id), conversation_id=conversation_id,
        project_id=int(project_id) if project_id is not None else None,
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
        history = history[:-1]
    # 🔴 2026-11-23 上下文修复（Phase 0 配套）：此前返回值只含 AgentConversationMemory
    # 的 user 原话 trail——assistant 轮全丢，调用方 render_conversation_history 渲染
    # 出的引用块里模型看不到自己说过什么（「听不懂/答非所问」主根因）。返回前把
    # 展示历史（AgentConversationMessage 全表，含 assistant 轮）读出来一起返回：
    # 结构 [{role, text}]，调用方 render_conversation_history 按新契约处理。
    try:
        display_rows = (await db.execute(select(AgentConversationMessage).where(
            AgentConversationMessage.user_id == int(user_id),
            AgentConversationMessage.conversation_id == conversation_id,
            _project_condition(AgentConversationMessage, project_id),
        ).order_by(AgentConversationMessage.id.asc()))).scalars().all()
        display = [
            {"role": row.role, "text": _conversation_content(row).get("text", "")}
            for row in display_rows
            if _conversation_content(row).get("text")
        ]
    except Exception:  # noqa: BLE001 —— 展示历史读取失败不阻断聊天主流程
        display = []
    if display:
        # 展示历史存在（新链路）：返回含 assistant 轮的完整对话；
        # 恰好本轮 user 消息已由上面落库，剔除末尾避免调用方重复拼接。
        if display and display[-1].get("role") == "user" and display[-1].get("text") == current:
            display = display[:-1]
        return display
    return history


async def append_conversation_response(db: AsyncSession, user_id: Any,
                                       conversation_id: str | None,
                                       response: dict[str, Any],
                                       *,
                                       project_id: int | None = None) -> None:
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
        project_id=int(project_id) if project_id is not None else None,
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
                                      limit: int = 30,
                                      *,
                                      project_id: int | None = None,
                                      ) -> list[dict[str, Any]]:
    """List only the current user's conversations in one project, newest first."""
    if not user_id:
        return []
    capped_limit = max(1, min(int(limit or 30), 100))
    latest_ids = select(
        func.max(AgentConversationMessage.id).label("max_id")
    ).where(
        AgentConversationMessage.user_id == int(user_id),
        _project_condition(AgentConversationMessage, project_id),
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
            _project_condition(AgentConversationMessage, project_id),
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
        _project_condition(AgentConversationMemory, project_id),
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
                                  conversation_id: str,
                                  *,
                                  project_id: int | None = None,
                                  ) -> dict[str, Any] | None:
    """Return a conversation only when it belongs to ``user_id`` and the project."""
    if not user_id or not conversation_id:
        return None
    rows = (await db.execute(select(AgentConversationMessage).where(
        AgentConversationMessage.user_id == int(user_id),
        _project_condition(AgentConversationMessage, project_id),
        AgentConversationMessage.conversation_id == conversation_id,
    ).order_by(AgentConversationMessage.id.asc()))).scalars().all()
    messages = [_conversation_content(row) for row in rows]
    if not messages:
        # Compatibility for conversations recorded before display history existed.
        memory = (await db.execute(select(AgentConversationMemory).where(
            AgentConversationMemory.user_id == int(user_id),
            _project_condition(AgentConversationMemory, project_id),
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
                              conversation_id: str,
                              *,
                              project_id: int | None = None) -> bool:
    """Delete display history and model context for one owned conversation."""
    if not user_id or not conversation_id:
        return False
    result = await db.execute(delete(AgentConversationMessage).where(
        AgentConversationMessage.user_id == int(user_id),
        _project_condition(AgentConversationMessage, project_id),
        AgentConversationMessage.conversation_id == conversation_id,
    ))
    await db.execute(delete(AgentConversationMemory).where(
        AgentConversationMemory.user_id == int(user_id),
        _project_condition(AgentConversationMemory, project_id),
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
                     conversation_id: str | None = None,
                     project_id: int | None = None,
                     ) -> AgentDraft | AgentConversationDraft | None:
    model = AgentConversationDraft if conversation_id else AgentDraft
    stmt = select(model).where(model.user_id == int(user_id), model.tool == tool)
    if conversation_id:
        stmt = stmt.where(model.conversation_id == conversation_id)
        if project_id is not None:
            stmt = stmt.where(model.project_id == int(project_id))
    else:
        stmt = stmt.where(_project_condition(model, project_id))
    return (await db.execute(stmt)).scalars().first()


async def _drop_stale_legacy_drafts(db: AsyncSession, user_id: Any, tool: str,
                                    project_id: int | None) -> None:
    """删除属于其他项目的旧版草稿，避免切换项目后把 A 项目字段带进 B 项目。

    旧版 ``agent_drafts`` 的唯一索引是 (user_id, tool)，不按项目区分；新草稿
    写入前清掉不匹配项目的行，既保证隔离也避免唯一键冲突。草稿本身是
    24 小时临时态，用户切换项目后重新补字段是可接受的。
    """
    conditions = [AgentDraft.user_id == int(user_id), AgentDraft.tool == tool]
    if project_id is None:
        conditions.append(AgentDraft.project_id.is_not(None))
    else:
        conditions.append(AgentDraft.project_id != int(project_id))
    rows = (await db.execute(select(AgentDraft).where(*conditions))).scalars().all()
    for row in rows:
        await db.delete(row)
    if rows:
        await db.flush()


async def stash_pending(db: AsyncSession, user_id: Any, tool: str,
                        args: dict | None, missing: list[str] | None,
                        *, message: str = "",
                        fact_args: dict | None = None,
                        conversation_id: str | None = None,
                        project_id: int | None = None) -> None:
    """need_more 时暂存「已收集参数 + 仍缺字段」，按会话与工具隔离。"""
    if not user_id or not tool:
        return
    row = await _draft_row(db, user_id, tool, conversation_id, project_id)
    if row is None:
        if conversation_id:
            row = AgentConversationDraft(
                user_id=int(user_id), conversation_id=conversation_id, tool=tool,
                project_id=int(project_id) if project_id is not None else None,
            )
        else:
            await _drop_stale_legacy_drafts(db, user_id, tool, project_id)
            row = AgentDraft(
                user_id=int(user_id), tool=tool,
                project_id=int(project_id) if project_id is not None else None,
            )
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
                      conversation_id: str | None = None,
                      *,
                      project_id: int | None = None) -> tuple[str, dict] | None:
    """取未过期草稿：(工具名, {args, missing})；tool 给定时只匹配该工具。过期即删。

    草稿也按项目隔离：切到其他项目后，旧项目未填完的草稿不会进入当前上下文。
    """
    if not user_id:
        return None
    model = AgentConversationDraft if conversation_id else AgentDraft
    stmt = select(model).where(model.user_id == int(user_id))
    if tool:
        stmt = stmt.where(model.tool == tool)
    if conversation_id:
        stmt = stmt.where(model.conversation_id == conversation_id)
    stmt = stmt.where(_project_condition(model, project_id))
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
                        conversation_id: str | None = None,
                        *,
                        project_id: int | None = None) -> None:
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
        stmt = stmt.where(_project_condition(model, project_id))
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
                       conversation_id: str | None = None,
                       *,
                       project_id: int | None = None) -> list[dict]:
    """返回该用户当前项目下所有未过期草稿（按 updated_at desc）。过期就地删除。"""
    if not user_id:
        return []
    models = [AgentConversationDraft] if conversation_id else [AgentConversationDraft, AgentDraft]
    rows = []
    for model in models:
        stmt = select(model).where(model.user_id == int(user_id))
        if conversation_id:
            stmt = stmt.where(model.conversation_id == conversation_id)
        stmt = stmt.where(_project_condition(model, project_id))
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
         "problems": "巡视的「问题及处理情况」，**必填、严禁留空**。用户原话说了问题/符合要求就原样传入；用户没说时，必须从各张照片素材的【图片内容观察】里归结：任一张看出问题→按「图N：…问题…，已要求整改」汇总；全部照片都看不出问题→填「经巡视，各部位未发现质量、安全问题（符合要求）」。绝不许因为用户没说就空着，也绝不许没依据硬写「符合要求」。",
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
         "extra_fields": "字段表栏目（对象，键=字段 key 如 1_13、10_14；上面没单列参数的栏目都放这里，值填用户原话）",
         "image_groups": "多类照片分组（列表，仅当这批照片确实跨多种记录类型才传）：每项 {record_type: 巡视/旁站/验收/见证/平行检验/待定, image_indices: [图在消息里的 1-based 序号]}；单一类型多图不要传（直接建一条、多图挂同一条）。确认轮不必重传，系统已记住分组。"},
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
        "dispatch_task",
        "派单：把一项任务指派给本项目的监理人员（总监/总代口述即可）。"
        "用户说「让某某去…」「派某某…」「给某某派个单/任务」时用。"
        "被指派人必须是本项目持 task_receive 岗位的有效成员，说姓名即可（或 user id）。"
        "类别默认「其他事务」，用户说了类别词（巡视检查/验收管理/见证取样/旁站监督/隐患处理/文书处理/整改跟踪）时映射。"
        "优先级默认「中」。派单成功会通知对方（眼镜端+微信）。",
        {"*title": "任务标题（用户口述的任务内容，动词开头，如「检查3号楼二层临边防护」）",
         "*assignee_name": "被指派人姓名（本项目成员名，用户原话里的名字）",
         "assignee_id": "被指派人 user id（知道 id 时优先）",
         "description": "任务描述/要求细节",
         "category": "类别：巡视检查/验收管理/见证取样/旁站监督/隐患处理/文书处理/整改跟踪/其他事务",
         "priority": "优先级：低/中/高/紧急"},
        "task_assign", "write", _dispatch_task,
        ("让张三去检查3号楼二层的临边防护", "给李四派个单：复查昨天那条隐患的整改情况"),
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
        "③用户给的是**时间范围**（「今天的」「本周」「本月」「10月的」）→ 只传 period（+可选 date），"
        "按日/周/月聚合当天/当周/当月的全部记录成监理日志、周报或月报。"
        "🔴 判据是「有没有指代到单条记录」，不是文书名叫什么：「把这条记录生成监理日志」走①传 record_id；"
        "「生成今天的监理日志」走③传 period=day。两者不要同时传。"
        "🔴 **监理日志 vs 监理日记**：用户说「监理日记 / 我的日记 / 个人日记」= 个人维度，"
        "period=day 且 form_code=jianli_riji（只聚合该用户当天自己的记录，落款本人）；"
        "说「监理日志」= 项目维度，不带 form_code。两者不要混。"
        "🔴 路径③里「安全监理日志」是**独立表式**（form_code=anquan_jianli_rizhi），"
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
        "打开系统页面（工作记录/巡视记录/验收记录/旁站记录/见证记录/隐患/隐患识别/文书/监理文书/台账/规范查询/拍照识别/待办/看板/资料库/知识库/省统表录入/平行检验/我的）。"
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
