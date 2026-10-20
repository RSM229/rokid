"""智能体路由 —— 「聊天即操作」的统一入口（P1 核心闭环）。

- ``POST /api/agent/chat``   ：一句话 → 意图路由 → 执行 → 结构化回执
- ``POST /api/agent/undo``   ：卡片上的「撤销」按钮精确回滚
- ``GET  /api/agent/tools``  ：当前可用工具清单（调试/自检）

设计要点见 ``app/services/agent_tools.py`` 顶部说明。本文件只做：
组装提示词 → 调模型（JSON 模式）→ 解析 → 分发 → 兜底纯问答。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.user import Project, User
from app.routers.auth import get_current_user
from app.routers.local_ai import _PLAINTEXT_SUFFIX
from app.services import agent_tools, model_gateway
from app.services.text_clean import strip_markdown

logger = logging.getLogger(__name__)
router = APIRouter()


class AgentChatRequest(BaseModel):
    message: str
    # 最近若干轮对话（前端只传 role/text），用于「把这条生成日志」这类指代消解
    history: list[dict] = Field(default_factory=list)
    """前端当前态：hazard=隐患识别工作台（仅作为轻微提示，不作为硬分流）"""
    mode: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    project_id: Optional[int] = None


class AgentUndoRequest(BaseModel):
    entity: str
    id: int


# 🔴 2026-10-16 改版（跑分 75.0% → 97.5%，repeat=3 严格模式）：
# 原 prompt 里「关键参数缺失→先追问不要调工具」与「省略即可（服务端会追问）」
# 两条约束打架，模型把任意字段缺失都当成"关键参数缺失"→ 返回 tool:null 反问用户，
# 表现为「答非所问」。实测 ToolSpec 自带 example「记一条旁站：屋面防水卷材铺贴，
# 9:00 到 12:00，10人」也会被判 null。本版明确：唯一该追问的情况 = 类型不明。
# 回归测试：python tests/agent_eval/runner.py --repeat 3
_AGENT_SYSTEM = """你是「白泽」，广东建筑工程监理智能助手，同时是「监理通」小程序的统一操作入口。

判断用户意图分两类：
1) 用户在**办事**（记记录、建隐患、建文书、生成日志/周报/月报、查记录/隐患/文书、查规范、查天气、打开页面、撤销）→ 必须从工具目录里选一个工具并抽取参数。
2) 用户只是**问知识或闲聊**（规范怎么规定、某句话什么意思、打招呼）→ tool 传 null，在 answer 里正常回答。

可用工具目录：
{catalog}

只输出一个 JSON 对象，不要输出任何其它文字、不要加代码块标记：
{{"tool": "工具名或 null", "args": {{"参数名": "值"}}, "answer": "给用户看的纯文本回答"}}

🔴 最高优先级：办事就必须调工具，缺字段交给服务端追问
----------------------------------------------------
**唯一需要你追问的情况**：用户要新建工作记录，但没说清是 巡视 / 旁站 / 验收 / 见证 哪一类。
此时 tool 传 null，在 answer 里只问这一个问题。

**除此之外，任何字段缺失都不要追问、不要返回 null**——直接调工具，把你已经听到的填进 args，
没听到的一律省略。服务端会自动列出缺失栏目并逐项向用户追问，这是既定机制，你不需要代劳。

错误示范（严禁）：用户说「记一条旁站：屋面防水卷材铺贴，9:00 到 12:00，10人」，
你因为「没有旁站结论」而返回 tool=null 去追问结论 —— 这是错的。
正确做法：调用 create_work_record，args 填 record_type=旁站、station_part=屋面防水卷材铺贴、
station_start_time=9:00、station_end_time=12:00、worker_count=10，结论留给服务端追问。

判断口诀：**能确定是哪一类记录 → 必须调工具；连类型都定不了 → 才追问。**

其他硬约束
----------
- args 只能包含该工具声明的参数；拿不准的参数不要编造，省略即可（服务端会追问缺失项）。
- 🔴 追问必须**一次只问一个字段**（按表格栏目顺序：部位→时间→问题……），严禁一口气列出多个问题；用户答完一栏再问下一栏。
- section（工程部位/施工区域）填**具体施工部位**（如「3号楼二层临边防护」「临边洞口防护」）；用户句中的项目名称（如「巡视XX项目」）不要填进 section。
- 记录类栏目口径以**字段表**为准：标「由系统生成=是」的栏（工程项目名称/编号/日期/星期/施工单位/项目章等）由系统带出，**不要追问、也不用传**；标「否」的栏用户没说才要问。表上有、参数清单里没单列的栏目，放进 **extra_fields**（键用服务端给出的字段 key，如 1_13/10_14），值填用户原话。
- 用户补充回答追问时，除了已有参数，也要把该栏目写进 extra_fields 一并传入。
- 用户回复「跳过 / 直接创建 / 不用补」等表示不再补充时，**必须仍调用 create_work_record**，把历史里已收集到的信息一并填入（不要只回文字）。
- 🔴 用户补充回答追问时（如答了类型、补了时间、补了结论），必须把**历史对话里已收集到的全部信息与本次补充合并成完整 args** 一次性传入，然后**立即调用工具**。合并后信息已足够建单，严禁继续追问下一个字段（只传本次提到的字段、或答完又追问，都会导致用户被反复盘问）。
- 你**没有**写库能力，严禁在 answer 里声称「已保存」「已记录」「已生成」「已删除」——落库由服务端执行后回执。
- 用户说「撤销 / 删掉刚才那条 / 说错了」→ 用 undo_last_action。
- 用户问「今天天气」→ query_weather；问「规范/标准怎么要求」→ query_regulation。
- 🔴 用户只说一个页面名或功能名（如「监理文书」「台账」「查看台账」「待办任务」「巡视记录」）→ 用 navigate 打开该页面，不要追问用户想干什么，也不要当成知识问答。
- 用户说「有没有XX」「查一下XX」「今天记了几条」→ 是**查询**，用 query_* 工具；不是「生成/新建」。
- 输出纯文本，禁止 Markdown 标记（#、*、表格、代码块）。"""


def _history_messages(history: list[dict], limit: int = 6) -> list[dict]:
    """把前端历史压成模型消息（只保留最近 limit 轮，忽略卡片等非文本内容）。"""
    out: list[dict] = []
    for item in (history or [])[-limit:]:
        role = str(item.get("role") or "").strip()
        content = str(item.get("text") or item.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        out.append({"role": role, "content": content[:500]})
    return out


async def _plain_answer(message: str, history: list[dict], provider: str | None, model: str | None,
                        mode: str | None) -> str:
    """兜底：走纯问答（与原 /local/llm/chat 行为一致）。"""
    persona = "你是建筑工程监理智能助手白泽，请给出准确、可执行、简洁的回答。"
    if mode == "hazard":
        persona = "你是AI安全隐患识别专家。若用户描述现场情况，请判断是否存在隐患并给出风险类别、主要风险、证据与复查建议。"
    persona += ("你不具备直接保存或创建记录的能力，不要声称已保存；"
                "需要留痕时引导用户到「工作记录」页点「+新建」，或直接说「记一条XXX」由系统落库。")
    messages = [{"role": "system", "content": persona + _PLAINTEXT_SUFFIX}]
    messages.extend(_history_messages(history))
    messages.append({"role": "user", "content": message})
    answer, _cfg = await model_gateway.chat_completion(
        messages, temperature=0.7, provider=provider, model=model
    )
    return strip_markdown(answer or "")


_SUPPLEMENT_SYSTEM = (
    "用户在补全一条待办理事项的字段。只输出一个 JSON 对象，键为参数名、值为从用户这句话能确定的参数。"
    "优先填补「仍缺字段」：这句话只要能当作某个仍缺字段的值（如部位、内容、问题、描述类字段），"
    "就把整句或提炼后的短语填入该字段；确实与仍缺字段无关的才省略。"
    "不要编造，不要输出其它文字。"
)

# 命中即不把这句话当「补充回答」（提问 / 找页面 / 闲聊）——避免中途问规范被当成补字段
_SUPPLEMENT_SKIP_HINTS = ("？", "?", "吗", "怎么", "如何", "为什么", "是什么", "多少",
                          "打开", "你好", "谢谢")


async def _extract_supplement(message: str, tool: str, draft: dict,
                              provider: str | None, model: str | None) -> dict:
    """把「补充回答」单独抽成参数：路由器没选工具、但存在未完成草稿时用。失败返回 {}。"""
    spec = agent_tools.TOOL_BY_NAME.get(tool)
    if not spec:
        return {}
    params = "、".join(f"{k}={v}" for k, v in spec.params.items())
    prompt = (
        f"工具：{tool}\n参数说明：{params}\n"
        f"已收集：{json.dumps(draft.get('args') or {}, ensure_ascii=False)}\n"
        f"仍缺字段（含该填到哪个参数/槽位）："
        f"{agent_tools.ask_hint(draft.get('args') or {}, draft.get('missing'))}\n"
        f"用户这句话：{message}"
    )
    try:
        raw, _cfg = await model_gateway.chat_completion(
            [{"role": "system", "content": _SUPPLEMENT_SYSTEM},
             {"role": "user", "content": prompt}],
            temperature=0.1, provider=provider, model=model,
            response_format={"type": "json_object"},
        )
    except model_gateway.ModelGatewayError:
        return {}
    data = agent_tools._parse_json_object(raw) or {}
    if isinstance(data.get("args"), dict):  # 兼容 {"args": {...}} 包一层
        data = data["args"]
    return {k: v for k, v in data.items()
            if str(v if v is not None else "").strip()}


@router.post("/chat")
async def agent_chat(
    req: AgentChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """统一入口：意图路由 + 执行 + 回执。识别不到工具时退化为纯问答。"""
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")

    project_id = getattr(user, "project_id", None)
    project = await db.get(Project, project_id) if project_id else None

    system = _AGENT_SYSTEM.format(catalog=agent_tools.catalog_for_prompt())
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(_history_messages(req.history))
    messages.append({"role": "user", "content": message})

    parsed: dict | None = None
    raw = ""
    try:
        raw, config = await model_gateway.chat_completion(
            messages,
            temperature=0.2,
            provider=req.provider,
            model=req.model,
            response_format={"type": "json_object"},
        )
        parsed = agent_tools._parse_json_object(raw)
    except model_gateway.ModelGatewayError as exc:
        # 不支持 JSON 模式的端点：退化为纯问答，聊天不中断
        logger.warning("agent JSON 路由失败，退化纯问答: %s", type(exc).__name__)
        try:
            answer = await _plain_answer(message, req.history, req.provider, req.model, req.mode)
        except model_gateway.ModelGatewayError as exc2:
            raise HTTPException(status_code=503, detail=str(exc2)) from exc2
        return {"kind": "chat", "tool": None, "reply": answer, "card": None,
                "cards": [], "actions": [], "need_more": [], "undo": None,
                "model": None, "degraded": True}

    tool = str((parsed or {}).get("tool") or "").strip()
    if tool in ("null", "None") or tool not in agent_tools.TOOL_BY_NAME:
        tool = ""
    args = (parsed or {}).get("args") if isinstance((parsed or {}).get("args"), dict) else {}

    # 上一轮追问留下的草稿（同用户，已落库）：本轮参数合并到草稿上，避免丢字段
    pend = await agent_tools.get_pending(db, user.id)
    pending_tool = pend[0] if pend else ""
    draft = pend[1] if pend else {}

    # 模型没选工具、但草稿未完成：把这句当「补充回答」单独抽一次参数
    # （明显是提问/找页面的话不抽，避免「用户中途问规范」被当成补字段）
    if not tool and pending_tool and not any(h in message for h in _SUPPLEMENT_SKIP_HINTS):
        extra = await _extract_supplement(message, pending_tool, draft, req.provider, req.model)
        # 命中「仍缺字段」才当补充回答：标签、extra_fields 槽位、或仍缺字段对应的参数名
        if agent_tools.supplement_hits(extra, draft.get("args") or {}, draft.get("missing")):
            tool = pending_tool
            args = {**args, **extra}

    if tool:
        spec = agent_tools.TOOL_BY_NAME[tool]
        if spec.risk != "read" and not project_id:
            return {"kind": "action", "tool": tool, "reply": "你还没有所属项目，先加入项目后再记录。",
                    "card": None, "cards": [], "actions": [{"type": "navigate", "label": "去加入项目",
                    "url": "/pages/join-project/index"}], "need_more": [], "undo": None, "model": config.model_id}
        if tool == pending_tool:
            args = agent_tools.merge_args(draft.get("args"), args)
            # 🔧 extra_fields 键归一（key↔canonical），避免模型按 key 写、服务端读 canonical 误判缺失
            record_type = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
            _form = None
            if record_type:
                _form = agent_tools.record_field_spec.form_for_record_type(
                    record_type,
                    agent_tools._is_weida(record_type, args, message),
                ) or ""
            args = agent_tools.normalize_extra_fields(args, _form)
        elif pending_tool and spec.risk != "read":
            # 用户换成了另一个写操作 → 旧草稿作废（纯查询/问答不清草稿，草稿有 24 小时 TTL）
            await agent_tools.clear_pending(db, user.id, pending_tool)
        ctx = agent_tools.ToolContext(db=db, user=user, project_id=project_id, project=project, message=message)
        result = await agent_tools.dispatch(ctx, tool, args)
        if result.get("need_more") and tool == pending_tool:
            # 🔴 兜底：用户这句是对上一轮追问的实质性回复，但模型没填进该字段 → 原话直接填入重跑，
            # 避免「回复了还反复追问同一字段」死循环（2026-10-15 真机踩坑：巡视部位）
            prev_missing = [str(m) for m in (draft.get("missing") or [])]
            refill_labels = [m for m in result["need_more"] if str(m) in prev_missing]
            refilled = agent_tools.refill_missing_from_message(args, refill_labels, message)
            if refilled is not None:
                args = refilled
                result = await agent_tools.dispatch(ctx, tool, args)
        if result.get("need_more"):
            await agent_tools.stash_pending(db, user.id, tool, args, result.get("need_more"))
        elif result.get("ok"):
            await agent_tools.clear_pending(db, user.id, tool)
        return {"kind": "action", "tool": tool, "model": config.model_id, **result}

    # 无工具 → 纯问答（把模型已生成的 answer 直接用；空则再调一次）
    answer = str((parsed or {}).get("answer") or "").strip()
    if not answer:
        try:
            answer = await _plain_answer(message, req.history, req.provider, req.model, req.mode)
        except model_gateway.ModelGatewayError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"kind": "chat", "tool": None, "reply": strip_markdown(answer), "card": None,
            "cards": [], "actions": [], "need_more": [], "undo": None,
            "model": config.model_id}


@router.post("/undo")
async def agent_undo(
    req: AgentUndoRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """聊天卡片上的「撤销」按钮：精确回滚一个本次聊天创建的对象。"""
    project_id = getattr(user, "project_id", None)
    project = await db.get(Project, project_id) if project_id else None
    ctx = agent_tools.ToolContext(db=db, user=user, project_id=project_id, project=project)
    return await agent_tools.undo_entity(ctx, req.entity, int(req.id))


@router.get("/tools")
async def agent_tools_list(user: User = Depends(get_current_user)):
    """当前可用工具清单（按权限标注可用性，便于自检与调试）。"""
    items: list[dict[str, Any]] = []
    for spec in agent_tools.TOOLS:
        items.append({
            "name": spec.name,
            "desc": spec.desc,
            "params": spec.params,
            "risk": spec.risk,
            "permission": spec.permission,
            "allowed": bool(not spec.permission or user.has_permission(spec.permission)),
        })
    return {"count": len(items), "tools": items}


class AgentClearDraftsRequest(BaseModel):
    """放弃草稿：指定 tool 只清该工具草稿；省略则清空当前用户全部待补全草稿。"""
    tool: Optional[str] = None


@router.get("/drafts")
async def agent_drafts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """当前用户所有未过期草稿（聊天即操作的半成品，落库持久化、跨会话可见）。"""
    drafts = await agent_tools.list_pending(db, user.id)
    return {"count": len(drafts), "drafts": drafts}


@router.post("/drafts/clear")
async def agent_clear_drafts(
    req: AgentClearDraftsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """放弃草稿：清掉当前用户指定工具或全部待补全草稿。"""
    await agent_tools.clear_pending(db, user.id, req.tool)
    return {"ok": True}
