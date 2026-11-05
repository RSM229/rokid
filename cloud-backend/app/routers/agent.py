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
import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.user import Project, User
from app.models.agent_draft import AgentFlowInstance, AgentReviewPlan
from app.models.miniprogram import MediaRecord, MediaType
from app.routers.auth import get_current_user
from app.routers.local_ai import _PLAINTEXT_SUFFIX
from app.services import agent_fact_model, agent_request_plan, agent_runtime, agent_tools, model_gateway
from app.services.record_fields import project_context
from app.services.agent_context import (
    extract_explicit_message_facts,
    render_context_summary,
    render_conversation_history,
)
from app.services import agent_review_plan
from app.services.text_clean import strip_markdown

logger = logging.getLogger(__name__)
router = APIRouter()


def _is_mechanical_reply(text: str) -> bool:
    """判断模型 answer 是否机械（只是标签式清单，如「还需要补充：验收类型」）。

    自然话术（含转述与引导，即使以「还差一项」开头）保留；纯标签清单退回工具话术。
    """
    t = str(text or "").strip()
    if not t:
        return True
    stripped = re.sub(r"^(请补充|还需要补充|缺少|缺失|还差|待补充)[：:、 ]*", "", t)
    stripped = re.sub(r"[、，,。;；\s「」『』（）()·\-—]", "", stripped)
    return len(stripped) <= 6


class AgentChatRequest(BaseModel):
    message: str
    request_id: Optional[str] = None
    conversation_id: Optional[str] = None
    # 最近若干轮对话（前端只传 role/text），用于「把这条生成日志」这类指代消解
    history: list[dict] = Field(default_factory=list)
    """前端当前态：hazard=隐患识别工作台（仅作为轻微提示，不作为硬分流）"""
    mode: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    project_id: Optional[int] = None
    # 随消息附带的素材（图片识别结果 / 文件解析文本 / 水印），由前端先把图交给视觉模型
    # 再拼成文字；服务端模型本身看不到图片，只能读这段素材。
    material: Optional[str] = None
    # 随消息上传图片的服务端媒体地址（仅用于展示历史回看；模型上下文不收）
    images: list[str] = Field(default_factory=list)
    # 素材里已确定的结构化字段（如照片水印的拍摄时间/地点/天气），建记录时直接并入 args
    prefill: dict = Field(default_factory=dict)
    # 聊天内表单一次提交：字段名 → 值。有它就不再让模型抽参，直接按字段表回填。
    form_values: dict = Field(default_factory=dict)
    # /agent/request-plan 生成并校验过的候选。只作为路由候选，不能代替权限和服务端校验。
    request_plan: dict = Field(default_factory=dict)


class PurposeRecommendRequest(BaseModel):
    message: str = ""
    evidence_summary: str = ""
    attachment_count: int = 0
    provider: Optional[str] = None
    model: Optional[str] = None


class RequestPlanEvidence(BaseModel):
    kind: str = "image"
    observed_date: str = ""
    observed_date_source: str = ""
    project: str = ""
    location: str = ""
    section: str = ""
    activity: str = ""
    observation: str = ""
    note: str = ""
    watermark_fields: dict[str, Any] = Field(default_factory=dict)
    field_sources: dict[str, str] = Field(default_factory=dict)


class RequestPlanRequest(BaseModel):
    message: str = ""
    request_id: Optional[str] = None
    evidence_summary: str = ""
    evidence: list[RequestPlanEvidence] = Field(default_factory=list)
    attachment_count: int = 0
    file_count: int = 0
    history: list[dict] = Field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None


class AgentProgressEventRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=80)
    stage: str = Field(min_length=1, max_length=64)
    status: str = "completed"
    title: str = Field(min_length=1, max_length=180)
    detail: dict[str, Any] = Field(default_factory=dict)
    terminal: bool = False


def _record_form_code(tool: str, args: dict, message: str = "") -> str | None:
    if tool != "create_work_record":
        return None
    record_type = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
    if not record_type:
        return None
    return agent_tools.record_field_spec.form_for_record_type(
        record_type, agent_tools._is_weida(record_type, args, message),
    )


def _generic_field_name(label: str) -> str:
    """没有字段表时，把追问标签落回工具真正读取的参数名。"""
    if "内容" in label or "描述" in label:
        return "work_content" if "记录" in label or "工作" in label else "description"
    if "区域" in label or "部位" in label:
        return "section"
    if "地点" in label or "位置" in label:
        return "location"
    if "表式" in label or "表名" in label:
        return "form_code"
    return label


def _generic_fields(missing: list[str], options: dict[str, list[str]]) -> list[dict]:
    """没有字段表可依时，把仍缺的标签原样变成一组控件。"""
    fields: list[dict] = []
    for label in missing:
        label = str(label)
        opts = [str(x) for x in (options.get(label) or [])]
        fields.append({
            "name": _generic_field_name(label), "label": label,
            "key": _generic_field_name(label), "canonical": _generic_field_name(label),
            "definition_source": "fallback",
            "control": "select" if opts else "text",
            "required": True, "value": "", "options": opts,
            "placeholder": "", "hint": "", "section": "",
        })
    return fields


def _plan_evidence_count(plan: dict) -> int:
    """请求计划里已分配/未分配的图片证据数量。"""
    item_plan = plan.get("item_plan") if isinstance(plan.get("item_plan"), dict) else {}
    count = len(item_plan.get("unassigned_evidence_indexes") or [])
    for item in item_plan.get("items") or []:
        if isinstance(item, dict):
            count += len(item.get("evidence_indexes") or [])
    return count


def _missing_record_type_reply(has_evidence: bool) -> str:
    """记录类型待选时的追问文案。

    只有本轮确实带了素材（图片/文件解析文本、水印预填或计划里的证据）才能
    说“已收到并保留”。否则用户明明没发照片，助手却声称保留，会让人误以为
    上一次的图片还在上下文里。
    """
    if has_evidence:
        return "我已收到并保留这些图片和已有信息。你想整理成巡视、旁站、验收还是见证记录？"
    return "这次我还没收到现场照片。先告诉我整理成巡视、旁站、验收还是见证记录，也可以把照片随下一条消息一起发我。"


def _attachment_block(label: str = "现场照片（可选）", hint: str = "相册或拍摄，自动加水印") -> dict:
    return {"enabled": True, "label": label, "max": 9, "hint": hint, "picked": []}


def _agent_form(
    tool: str, args: dict, result: dict, message: str = "", project_name: str = "",
) -> dict | None:
    """把一次回执变成聊天内可直接填写的表单。

    - 记录类型未定：只给一个类型选择框；
    - 字段未齐：按省统表字段表一次性列出所有缺失栏目（必填在前），照片框内嵌；
    - 记录已建：给一个可跳过的照片框，并附文书/记录的文字链接。
    """
    missing = [str(x) for x in (result.get("need_more") or [])]
    options = result.get("options") or {}
    form_code = _record_form_code(tool, args, message)

    if tool == "create_work_record" and not form_code:
        opts = [str(x) for x in (options.get("记录类型")
                                or agent_tools.record_field_spec.RECORD_TYPE_TO_FORM)]
        return {
            "id": "record_type", "kind": "record_type",
            "schema_version": 1, "field_schema": "record_field_spec.v1", "form_code": "",
            "title": "先选记录类型",
            "hint": "选好类型后按对应表格补齐；如还没发照片，也可随下一条消息一起上传。",
            "fields": [{
                "name": "record_type", "label": "记录类型", "control": "select",
                "key": "record_type", "canonical": "record_type", "definition_source": "agent_action",
                "required": True, "value": "", "options": opts,
                "placeholder": "", "hint": "", "section": "",
            }],
            "attachments": _attachment_block("现场照片", "可多选；选好类型后一起建档"),
            "submit_label": "继续", "skippable": False, "actions": [],
        }

    if tool == "create_work_record" and result.get("ok"):
        record_id = ((result.get("data") or {}).get("record_id")
                     or (result.get("card") or {}).get("id"))
        if not record_id:
            return None
        record_type = agent_tools._norm_record_type(str(args.get("record_type") or "")) or "工作"
        card = result.get("card") or {}
        return {
            "id": f"work_record_photos:{record_id}", "kind": "attachments",
            "schema_version": 1, "field_schema": "record_field_spec.v1", "form_code": form_code or "",
            "record_id": int(record_id),
            "title": f"{record_type}记录已保存 · 补充现场照片",
            "hint": "这一步可以跳过；照片会自动加水印并挂到这条记录。",
            "fields": [],
            "attachments": _attachment_block("添加现场照片", "相册或拍摄，最多 9 张"),
            "target": {
                "date": str(args.get("record_date") or card.get("time") or ""),
                "project": project_name,
                "location": str(args.get("location") or ""),
                "section": str(args.get("section") or ""),
            },
            "submit_label": "上传照片", "skippable": True,
            "actions": [
                {"type": "generate_document", "label": "生成 PDF / Word",
                 "style": "link", "record_id": int(record_id)},
                {"type": "navigate", "label": "查看工作记录",
                 "style": "link", "url": "/pages/work/index"},
            ],
        }

    if tool == "create_work_record" and "最终确认" in missing and form_code:
        fields = [
            agent_tools.field_view(field, agent_tools._field_value(args, field))
            for field in agent_tools.record_field_spec.ask_fields(form_code)
        ]
        record_type = agent_tools._norm_record_type(str(args.get("record_type") or "")) or "工作"
        return {
            "id": f"work_record_confirm:{form_code}",
            "kind": "work_record",
            "schema_version": 1,
            "field_schema": "record_field_spec.v1",
            "form_code": form_code,
            "title": f"{record_type}记录 · 最终核对",
            "hint": "请逐项核对；可直接修改字段。点击确认后，服务端会再次校验合同并创建记录。",
            "fields": fields,
            "filled": [],
            "attachments": _attachment_block(),
            "submit_label": "确认并生成记录",
            "skippable": False,
            "actions": [],
        }

    if result.get("need_more"):
        # 「补齐信息」卡片与服务端一次性返回的缺项对齐（2026-11-04 重构：枚举项已用
        # chips 呈现，表单只作自由文本项的快捷填写，默认收起）。need_more 现在携带本轮
        # 全部缺项，卡片据其过滤后全部展示，不再人为砍成一栏。
        pending_labels = [str(x) for x in (result.get("need_more") or [])]
        fields = agent_tools.missing_field_views(args, form_code) if form_code else []
        if fields and pending_labels:
            fields = [v for v in fields if str(v.get("label") or "") in pending_labels]
        if not fields:
            # 没有字段表/没有命中时退回原来的通用列表，保证能填
            fields = agent_tools.missing_field_views(args, form_code) if form_code else []
            if not fields:
                fields = _generic_fields(missing, options)
        filled = agent_tools.filled_field_views(args, form_code) if form_code else []
        record_type = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
        has_required = any(field.get("required") for field in fields)
        return {
            "id": f"{tool}:{form_code or 'form'}",
            "kind": "work_record" if form_code else "form",
            "schema_version": 1, "field_schema": "record_field_spec.v1", "form_code": form_code or "",
            "title": (f"还差 {len(fields)} 项待补全" if fields
                      else (f"{record_type}记录 · 补齐信息" if record_type else "补齐信息")),
            "hint": "上方可点选的先点；其余直接说或点开填写即可，不方便也能先跳过。" if fields
                    else "都可以留空，直接提交也行。",
            "fields": fields,
            "filled": filled,
            "attachments": _attachment_block(),
            "submit_label": "保存记录" if form_code else "提交",
            "skippable": not has_required,
            "actions": [],
        }
    return None


def _agent_input_components(form: dict | None) -> list[dict[str, Any]]:
    """旧版前端兼容：把表单字段摊平成 input_components。"""
    if not form:
        return []
    return [{
        "field": field.get("name"), "label": field.get("label"),
        "control": field.get("control"), "required": bool(field.get("required")),
        "options": field.get("options") or [], "value": field.get("value") or "",
    } for field in form.get("fields") or []]


class AgentUndoRequest(BaseModel):
    entity: str
    id: int


class ReviewPlanCreate(BaseModel):
    conversation_id: str
    plan: dict[str, Any]
    request_id: Optional[str] = None
    # 随请求上传图片的服务端媒体地址；用于把用户这轮发言（含图）落进展示历史
    images: list[str] = Field(default_factory=list)


class ReviewPlanUpdate(BaseModel):
    revision: int
    plan: dict[str, Any]
    request_id: Optional[str] = None


class ReviewPlanConfirm(BaseModel):
    revision: int
    request_id: Optional[str] = None
    execution_id: Optional[str] = None


async def _validate_review_plan_media(
    db: AsyncSession, user: User, plan: dict[str, Any]
) -> None:
    """确保计划中的证据引用当前用户当前项目已上传的媒体。"""
    media_ids: list[int] = []
    for item in plan.get("evidence", []):
        if not isinstance(item, dict):
            continue
        if item.get("image") and item.get("media_id") is None:
            raise HTTPException(422, "图片资料必须携带服务端 media_id")
        if item.get("media_id") is None:
            continue
        try:
            media_id = int(item["media_id"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "资料 media_id 无效") from exc
        if media_id <= 0:
            raise HTTPException(422, "资料 media_id 无效")
        media_ids.append(media_id)
    if len(set(media_ids)) != len(media_ids):
        raise HTTPException(422, "资料 media_id 重复")
    if not media_ids:
        return
    project_id = getattr(user, "project_id", None)
    if not project_id:
        raise HTTPException(400, "请先选择项目")
    rows = (await db.execute(select(MediaRecord).where(
        MediaRecord.id.in_(media_ids),
        MediaRecord.uploader_id == user.id,
        MediaRecord.project_id == project_id,
        MediaRecord.media_type == MediaType.IMAGE.value,
    ))).scalars().all()
    found = {row.id for row in rows}
    missing = [str(media_id) for media_id in media_ids if media_id not in found]
    if missing:
        raise HTTPException(403, f"资料不存在、不是图片或不属于当前项目：{', '.join(missing)}")


# 🔴 2026-10-16 改版（跑分 75.0% → 97.5%，repeat=3 严格模式）：
# 原 prompt 里「关键参数缺失→先追问不要调工具」与「省略即可（服务端会追问）」
# 两条约束打架，模型把任意字段缺失都当成"关键参数缺失"→ 返回 tool:null 反问用户，
# 表现为「答非所问」。实测 ToolSpec 自带 example「记一条旁站：屋面防水卷材铺贴，
# 9:00 到 12:00，10人」也会被判 null。本版明确：唯一该追问的情况 = 类型不明。
# 回归测试：python tests/agent_eval/runner.py --repeat 3
_AGENT_SYSTEM = """你是「白泽」，广东建筑工程监理智能助手，同时是「监理通」小程序的统一操作入口。

你只提出结构化语义候选，不直接决定执行哪个工具。服务端会根据动作白名单、
记录目录、权限、当前草稿和业务规则决定能否执行。

可用动作目录：
{actions}

记录类型目录：
{record_types}

参数说明（只用于 facts/args 的字段命名）：
{catalog}

只输出一个 JSON 对象，不要输出任何其它文字、不要加代码块标记：
{{"intent": "create_record|archive|analyze_hazard|query|generate_document|navigate|undo|chat|clarify",
 "actions": [{{"id": "动作 id", "confidence": 0.0, "reason": "简短依据"}}],
 "record_type": "巡视|旁站|验收|见证|平行检验|null", "record_type_confidence": 0.0,
 "facts": [{{"field": "参数名", "value": "用户明确提供的值", "source_span": "用户原话片段", "confidence": 0.0}}],
 "args": {{"参数名": "值"}}, "answer": "给用户看的纯文本回答"}}

facts 只记录用户原话明确提供的信息；图片、水印和视觉观察由服务端作为独立证据合并。
args 可以放你已确认的工具参数，但服务端仍会重新抽取、校验和合并。水印/EXIF 中成功识别的
日期、项目、地点、部位和天气优先于用户转述；冲突时用户值仍保留用于提示。图片缺失不能
反向覆盖用户事实。可以返回多个动作；存在多个动作时服务端不会自动执行。

🔴 最高优先级：办事必须输出受控动作，缺字段交给服务端追问
----------------------------------------------------
**唯一需要你直接追问的情况**：用户要新建工作记录，但没说清是 巡视 / 旁站 / 验收 / 见证 / 平行检验 哪一类。
此时仍返回 record_work 动作，record_type 传 null，在 answer 里只问这一个问题；服务端会保留证据。

🔴 但「没说清」只指**用户没口述、素材里也找不到**。如果照片水印 / 公示牌 / 画面文字里已经明确写着类型词
（如「隐蔽工程验收公示牌」「污水管道基础包封验收」「旁站记录」「见证取样」「平行检验记录」），那就**不算没说清**：
直接返回 record_work 并填 record_type（record_type_confidence 按明确程度给 0.7~0.95），在 answer 里说明依据
（「水印写着『隐蔽工程验收公示牌』，按验收整理；要改成别的说一声」）。**绝不允许把你自己已经念出来的类型词
再拿去问用户**——你已经复述了「施工内容：污水管道基础包封验收」，就不能紧接着问「你想整理成哪一类记录」。

**除此之外，任何字段缺失都不要追问、不要退回普通聊天**——返回对应动作，把你已经听到的填进 facts/args，
没听到的一律省略。缺失栏目会由**服务端一次性**以可点选候选（chips）和预填表单呈现，你不需要逐栏代劳。
- 🔴 回答的行文：answer 必须先**自然接住用户这句**——把本句已确认的事实（如「验收结论=合格」）简短转述，再告诉用户「缺的项下面已经给出可选项、点一下就行，或直接说/填」，**不要**在 answer 里手列候选值、不要列填空清单、不要逐栏盘问。例：用户答「合格」→ answer：「收到，验收结论记『合格』。还差验收类型和施工部位，下面点一下验收类型、部位直接说一声就行；不方便也能先跳过。」。
- 🔴 你的 answer 是这条消息的**主角**，服务端兜底文案只在没有可用 answer 时才出现；不要把机械的「还需要补充：XX」塞进 answer。

错误示范（严禁）：用户说「记一条旁站：屋面防水卷材铺贴，9:00 到 12:00，10人」，
你因为「没有旁站结论」而返回 chat 动作去追问结论 —— 这是错的。
正确做法：返回 record_work 动作，args 填 record_type=旁站、station_part=屋面防水卷材铺贴、
station_start_time=9:00、station_end_time=12:00、worker_count=10，结论留给服务端追问。

判断口诀：**能确定是哪一类记录 → 返回 record_work；连类型都定不了 → record_work + 只问类型。**

其他硬约束
----------
- args 只能包含该工具声明的参数；拿不准的参数不要编造，省略即可（服务端会追问缺失项）。
- 🔴 缺失字段由**服务端一次性**以可点选候选（chips）和预填表单呈现，你**不要**在 answer 里逐栏追问、也不要手列候选值。接话时只自然转述已确认事实 + 一句「下面点一下即可」。🔴 带照片水印/素材且已经能判断是哪类记录时，**必须返回 record_work 动作**，不许因为"信息和水印不一致"就退回纯聊天去汇报差异——冲突由服务端用一句提示 + 可点选项承载。验收类型只能从受控枚举里选（隐蔽工程／检验批／进场验收／分项工程验收／分部工程验收／单位工程验收／竣工验收／预验收；「分项分部」为旧值别名），拿不准就说「点下方验收类型」，绝不要自己造类型词（如把「分项分部」说成「分部分部」）。
- section（工程部位/施工区域）只填**用户明确说出来的**具体施工部位（如「3号楼二层东侧临边」）。🔴 上面括号里的字只是示意格式，**不是候选值**：用户没说部位就留空，服务端会追问；严禁按规范/经验列举（例如把「临边洞口防护、高处作业、卸料平台、起重吊装、移动操作平台、模板支撑体系」这类检查项清单当成部位填进来）。用户句中的项目名称（如「巡视XX项目」）也不要填进 section。
- 🔴 用户只回了记录类型（如只答「巡视」「旁站」「验收」）时，**除 record_type 外一个字段都不要填**：不准按经验补部位、工作内容、时间、天气、人数。留空由服务端补齐/追问，编造的字段会直接写进正式文书。
- content（工作内容）只写用户说过的内容；不要把隐患识别结果、检查项清单、规范条目搬进来。
- 记录类栏目口径以**字段表**为准：标「由系统生成=是」的栏（工程项目名称/编号/日期/星期/施工单位/项目章等）由系统带出，**不要追问、也不用传**；标「否」的栏用户没说才要问。表上有、参数清单里没单列的栏目，放进 **extra_fields**（键用服务端给出的字段 key，如 1_13/10_14），值填用户原话。
- 用户补充回答追问时，除了已有参数，也要把该栏目写进 extra_fields 一并传入。
- 用户回复「跳过 / 直接创建 / 不用补」等表示不再补充时，**必须仍返回 record_work**，把历史里已收集到的信息一并填入 facts/args（不要只回文字）。
- 🔴 用户补充回答追问时（如答了类型、补了时间、补了结论），必须把**历史对话里已收集到的全部信息与本次补充合并成完整 args** 一次性传入，然后**立即调用工具**。合并后信息已足够建单，严禁继续追问下一个字段（只传本次提到的字段、或答完又追问，都会导致用户被反复盘问）。
- 你**没有**写库能力，严禁在 answer 里声称「已保存」「已记录」「已生成」「已删除」——落库由服务端执行后回执。
- 用户要换记录类型（如「改成旁站」「这条算验收」）→ 仍返回 record_work，record_type 换成新类型，
  其余已经确认过的部位/时间/内容照抄，不要反问用户重新说一遍。
- 用户说「撤销 / 删掉刚才那条 / 说错了」→ 返回 undo 动作。
- 用户问「今天天气」→ query_weather 动作；问「规范/标准怎么要求」→ query_regulation 动作。
- 🔴 用户只说一个页面名或功能名（如「监理文书」「台账」「查看台账」「待办任务」「巡视记录」）→ 用 navigate 打开该页面，不要追问用户想干什么，也不要当成知识问答。
- 用户说「有没有XX」「查一下XX」「今天记了几条」→ 是**查询**，返回对应 query_* 动作；不是「生成/新建」。
- 输出纯文本，禁止 Markdown 标记（#、*、表格、代码块）。

🔴 图片与素材（最容易编造的地方）
--------------------------------
- 你**看不到图片/照片本身**。只有消息里出现「【图片 N】」素材段（系统已把图交给视觉模型）时，才能引用其中的识别结果。
- 🔴 素材里一旦出现「【水印】」段：**必须先把水印内容逐项复述给用户**（拍摄时间、地点、天气…），再说明哪些信息已作为证据保存。若业务类型仍不明确，可以在复述后只问一个决定性问题，不能把水印识别失败说成用户没有提供信息。
- **严禁凭空说「经图片识别」「看图发现」「照片显示」**，也不要在没有素材时描述现场状况——那是编造，监理会据此签字。
- 素材里没有识别到隐患，就照实说「素材中未提到隐患」，不要补一句「现场安全状况良好」。
- 用户说「识别水印 / 先识别水印 / 看下水印」：
  · 本轮带素材且素材里有【水印】段 → 直接复述水印内容，并说明已自动填入哪些栏；
  · 本轮**带了素材但没读到【水印】** → 说「这张没读到水印，水印文字要清晰、别被裁掉，麻烦重拍一张或从相册选原图」；
  · 本轮**完全没有素材** → 说：「把带水印的照片发给我（点输入框的「+」选相册或拍照），我先读水印里的拍摄时间和地点。你要发的是哪张？」——**不许说「这张没读到水印」**，你根本没看到图；
  · **不要说「我接收不到图片」「无法识别图片」**。

「记录今天工作情况」这类模糊请求
--------------------------------
- 「记录今天工作情况 / 今天干了什么 / 记一下今天」= 要留痕。
- 🔴 **先判文书、再判素材（顺序不能反）**：如果用户这条消息里是一段**完整的监理日报 / 监理日记 / 监理日志叙述正文**（描述当天施工内容与监理工作，且能抽出日期），**无论是否同时附了照片、水印或其它素材**，都按下面「粘贴叙述性监理日报」的规则返回 `generate_document`——照片只作附件保存，**不得因为带了照片就改走 record_work，更不得抽字段让用户逐项确认项目 / 单位 / 人员**。照片与项目资料对不上（水印是别的项目）时，只在回答里提一句「照片水印项目名与当前项目不一致」，不要把它变成待确认项。
- **图片/水印素材不等于巡视，也不等于任何固定表式**。如果用户没有明确说巡视、旁站、验收、见证、平行检验、材料、日志等目标，**且素材里也找不到类型词**，返回 record_work + record_type=null，只问一个决定性问题：「你想把这些内容整理成巡视、旁站、验收、见证、平行检验，还是只整理图片？」
- 用户明确说了记录类型时才填写 record_type 并进入记录候选。用户已经在前文说过日期、地点、时间、工作内容的，必须沿用，不得因图片没有识别到而说成缺失。
- 错误示范（严禁）：直接回一段「今日工作情况结论与建议」，甚至还写「经图片识别」。这是答非所问。
- 用户明确说「生成今天的监理日志 / 月报」时，才返回 generate_document 动作（args.period=day / month）。
- 「安全监理日志 / 安全日志」是**独立表式**，不是普通监理日志：返回 generate_document 时 period=day 且必须同时带 args.form_code="anquan_jianli_rizhi"，否则会被静默生成成普通监理日志。
- 🔴 用户**直接粘贴 / 口述一段叙述性「监理日报 / 监理日记 / 监理日志」文字**（描述当天施工与监理工作，而非点名某张省统表）时，**即使同一条消息还附了照片 / 水印素材也一样**，也返回 generate_document 动作：args 填 period="day"，并从文字里抽出日期写进 args.date（如「10月27日」→ "2026-10-27"）；若是安全监理日志再带 form_code="anquan_jianli_rizhi"。🔴 **严禁在此场景返回 create_provform_doc**——那是新建省统表模板，会被必填项校验卡成「该表还不能生成」；监理日志由日报生成服务聚合 / 起草，你不需要抽任何字段，用户原文会作为起草素材自动带入。

🔴 上述「叙述性文字严禁进 create_provform_doc」的精神适用于**所有省统表**：只要用户发的是一段描述现场 / 工作或记录 / 日志的**叙述性自由文字**、没有明确点名要建哪张省统表表式，就**严禁返回 create_provform_doc**（必填项校验会卡成「该表还不能生成」）。按意图分流：要留痕 → record_work；要出监理文书 → generate_document；只是陈述 / 咨询 → chat。只有用户**明确说出表式名或「建 / 新建 / 生成 XX 表」**（如「建一份监理工程师通知单」「生成旁站记录表」）时才返回 create_provform_doc。

🔴 **例外——续接未完成草稿优先于上面的守门**：如果系统上下文里已给出「未完成草稿 / 仍未收集的字段」（即上一轮你已为某张省统表追问过缺哪几栏），而用户这句正是在补这些栏（描述现场情况、工作内容等），必须**续接上一轮同一个工具**（create_provform_doc）把这段文字填进对应 fields / extra_fields，**不要改投 record_work，也不要重复问已经给过的内容**。守门只拦「凭空新建一张表」，不拦「补全自己刚开的那张表的草稿」。"""


# ---------------------------------------------------------------------------
# 水印/素材预填：把照片水印里客观确定的字段直接写进记录，缺的才问用户
# （2026-10-19 用户反馈：发带水印的照片 + 「记录今天工作」，系统却什么都没填就反问类型）
# ---------------------------------------------------------------------------
# 水印拍摄时间 → 该类型的「开始时间」参数（表字段 arg 名）
_PREFILL_START_ARG = {"巡视": "inspection_start_time", "旁站": "station_start_time"}
_TIME_RE = re.compile(r"(\d{1,2})\s*[:：时]\s*(\d{2})")
_DATE_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")


def _hms(value: Any) -> str:
    """水印拍摄时间文本 → HH:MM；取不到返回空串。"""
    m = _TIME_RE.search(str(value or ""))
    if not m:
        return ""
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _iso_date(value: Any) -> str:
    """水印拍摄时间文本 → YYYY-MM-DD；取不到返回空串。"""
    m = _DATE_RE.search(str(value or ""))
    if not m:
        return ""
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def apply_prefill(
    args: dict,
    prefill: dict,
    record_type: str,
    *,
    protected_keys: set[str] | None = None,
) -> tuple[dict, list[str]]:
    """把水印等客观字段并入建记录参数，返回（新 args，已自动填入的说明）。

    水印/EXIF 对日期、地点、部位、天气等客观栏拥有更高优先级；用户转述
    有冲突时采用照片证据。拍摄时间对“开始时间”只是推断，存在用户明确
    时段时仍保留用户值。用户在表单中主动修改的键通过 protected_keys 保护。
    """
    out = dict(args or {})
    filled: list[str] = []
    protected = protected_keys or set()

    def put(key: str, value: Any, label: str, *, objective: bool = False) -> None:
        if not key:
            return
        text = str(value if value is not None else "").strip()
        if not text or key in protected:
            return
        existing = str(out.get(key) or "").strip()
        if existing:
            if not objective or existing == text:
                return
            out[key] = text
            filled.append(f"{label}（改用照片水印）：{text}")
            return
        out[key] = text
        filled.append(f"{label}：{text}")

    put(
        "record_date",
        prefill.get("record_date") or _iso_date(prefill.get("taken_at")),
        "日期",
        objective=True,
    )
    put("location", prefill.get("location"), "地点", objective=True)

    section = str(prefill.get("section") or "").strip()
    if section:
        if record_type == "旁站":
            put("station_part", section, "关键部位", objective=True)
            if "section" not in protected:
                out["section"] = section
        else:
            put("section", section, "工程部位", objective=True)

    put("weather", prefill.get("weather"), "天气", objective=True)

    hhmm = _hms(prefill.get("taken_at"))
    if hhmm:
        put(
            _PREFILL_START_ARG.get(record_type, ""),
            hhmm,
            "开始时间（水印拍摄时间）",
        )

    return out, filled


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
                "需要留痕时引导用户到「工作记录」页点「+新建」，或直接说「记一条XXX」由系统落库。"
                "你看不到图片本身，只有用户消息里给出图片识别结果时才能引用；"
                "禁止凭空声称「经图片识别」「看图发现」「照片显示」。")
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


async def _generate_request_plan(
    *,
    message: str,
    evidence_summary: str = "",
    evidence: list[dict[str, Any]] | None = None,
    attachment_count: int = 0,
    file_count: int = 0,
    history: list[dict] | None = None,
    provider: str | None = None,
    model: str | None = None,
    system_facts: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    """Ask for semantic candidates, then pass them through the server gate."""
    evidence_items = [item for item in (evidence or []) if isinstance(item, dict)]
    evidence_count = len(evidence_items) + max(0, file_count)
    if not evidence_summary and evidence_items:
        evidence_summary = "\n".join(
            str(item.get("note") or item.get("observation") or "")
            for item in evidence_items
            if str(item.get("note") or item.get("observation") or "").strip()
        )
    prompt = agent_request_plan.build_prompt(
        message, evidence_summary, evidence_items, history,
    )
    try:
        raw, config = await model_gateway.chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "你是监理智能体的语义计划器。只输出候选 JSON；"
                        "不得执行、写入或签发任何业务数据。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            provider=provider,
            model=model,
            response_format={"type": "json_object"},
        )
        candidate = agent_tools._parse_json_object(raw) or {}
        plan = agent_request_plan.normalize_candidate(
            candidate,
            message=message,
            attachment_count=max(0, attachment_count),
            evidence_count=evidence_count,
            evidence=evidence_items,
            system_facts=system_facts,
        )
        plan = agent_request_plan.enrich_plan_facts(
            plan,
            extract_explicit_message_facts(message, message_ref=message),
            evidence=evidence_items,
            system_facts=system_facts,
        )
        return plan, config.model_id
    except (model_gateway.ModelGatewayError, TypeError, ValueError) as exc:
        logger.warning("请求计划器不可用: %s", type(exc).__name__)
        plan = agent_request_plan.degraded_plan(
            attachment_count=max(0, attachment_count),
            evidence_count=evidence_count,
            reason=type(exc).__name__,
        )
        plan = agent_request_plan.enrich_plan_facts(
            plan,
            extract_explicit_message_facts(message, message_ref=message),
            evidence=evidence_items,
            system_facts=system_facts,
        )
        return plan, ""


@router.post("/request-plan")
async def request_plan(
    req: RequestPlanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Build a validated, non-executing plan for text and uploaded evidence."""
    message = (req.message or "").strip()
    evidence = [item.model_dump() for item in req.evidence[:20]]
    attachment_count = max(
        0,
        req.attachment_count or sum(1 for item in evidence if item.get("kind") == "image"),
    )
    request_id = await agent_runtime.start_request(
        request_id=req.request_id,
        user_id=user.id,
        project_id=getattr(user, "project_id", None),
        conversation_id=None,
        endpoint="/api/agent/request-plan",
        stage="request_plan",
        message=message or req.evidence_summary,
        provider=req.provider or "",
        model=req.model or "",
    )
    try:
        project = await db.get(Project, user.project_id) if getattr(user, "project_id", None) else None
        project_facts = project_context(project)
        system_facts = agent_fact_model.build_system_facts(
            project_name=project_facts["project_name"],
            construction_unit=project_facts["construction_unit"],
            supervision_unit=project_facts["supervision_unit"],
            supervision_org=project_facts["supervision_org"],
            inspector=user.real_name or "",
        )
        await agent_runtime.append_progress_event(
            request_id=request_id,
            user_id=int(user.id),
            project_id=getattr(user, "project_id", None),
            endpoint="/api/agent/request-plan",
            stage="request_plan",
            status="running",
            title="正在调用规划模型，根据用户原话和已提取的图片/文件证据生成候选计划",
            detail={
                "image_count": attachment_count,
                "file_count": max(0, req.file_count),
                "evidence_count": len(evidence),
                "model": req.model or "系统选择的模型",
            },
        )
        plan, model_id = await _generate_request_plan(
            message=message,
            evidence_summary=req.evidence_summary,
            evidence=evidence,
            attachment_count=attachment_count,
            file_count=max(0, req.file_count),
            history=req.history,
            provider=req.provider,
            model=req.model,
            system_facts=system_facts,
        )
        plan["model"] = model_id
        plan["request_id"] = request_id
        planned_facts = [
            {"field": item.get("field"), "value": item.get("value"), "source": item.get("source")}
            for item in plan.get("facts", []) if isinstance(item, dict)
        ]
        await agent_runtime.append_progress_event(
            request_id=request_id,
            user_id=int(user.id),
            project_id=getattr(user, "project_id", None),
            endpoint="/api/agent/request-plan",
            stage="request_plan",
            status="completed",
            title=(
                "结构化判断完成："
                + (str(plan.get("record_type") or "记录类型待确认"))
                + ("候选" if plan.get("actions") else "，需确认处理用途")
            ),
            detail={
                "intent": plan.get("intent"),
                "actions": plan.get("actions"),
                "facts": planned_facts[:32],
                "conflicts": plan.get("field_conflicts", [])[:16],
                "clarifications": plan.get("clarifications", [])[:12],
                "degraded": bool(plan.get("degraded")),
                "degraded_reason": (plan.get("trace") or {}).get("degraded_reason"),
                "model": model_id,
            },
        )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user.id,
            endpoint="/api/agent/request-plan",
            stage="request_plan",
            request_plan=plan,
            result={"intent": plan.get("intent"), "actions": plan.get("actions")},
            model=model_id,
        )
        return plan
    except Exception as exc:
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user.id,
            endpoint="/api/agent/request-plan",
            stage="request_plan",
            status="failed",
            error=exc,
        )
        raise


@router.post("/recommend-purpose")
async def recommend_purpose(
    req: PurposeRecommendRequest,
    user: User = Depends(get_current_user),
):
    """Suggest a workflow from user intent and evidence; never creates a record.

    🔴 必须登录：本接口内部会真实调用大模型（付费），历史上漏了鉴权，
    等于把「匿名刷额度」的入口开在公网上。小程序调用方本来就带 Bearer，
    加鉴权不影响正常使用。
    """
    evidence_count = 1 if req.evidence_summary.strip() else 0
    plan, model_id = await _generate_request_plan(
        message=req.message,
        evidence_summary=req.evidence_summary,
        attachment_count=max(0, req.attachment_count or evidence_count),
        evidence_count=evidence_count,
        provider=req.provider,
        model=req.model,
    )
    legacy = agent_request_plan.plan_to_legacy_purpose(plan)
    legacy["model"] = model_id
    if plan.get("degraded"):
        legacy["degraded"] = True
    return legacy


async def _agent_chat_impl(
    req: AgentChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    progress_hook: Any = None,
):
    """统一入口：意图路由 + 执行 + 回执。识别不到工具时退化为纯问答。"""
    async def report_progress(stage: str, status: str, title: str, detail: dict[str, Any] | None = None) -> None:
        if progress_hook is not None:
            await progress_hook(stage, status, title, detail or {})

    "用户的问题 message 如果有包括在req 否则为空 "
    "用户的问题 message 如果有包括在req 否则为空 "
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")

    await report_progress(
        "load_context", "running", "先看看当前项目的资料、刚才的对话，以及还没填完的草稿",
        {"conversation_id": req.conversation_id or "", "message_length": len(message)},
    )
    project_id = getattr(user, "project_id", None)
    project = await db.get(Project, project_id) if project_id else None
    project_facts = project_context(project)
    system_facts = agent_fact_model.build_system_facts(
        project_name=project_facts["project_name"],
        construction_unit=project_facts["construction_unit"],
        supervision_unit=project_facts["supervision_unit"],
        supervision_org=project_facts["supervision_org"],
        inspector=user.real_name or "",
    )
    prefill = req.prefill if isinstance(req.prefill, dict) else {}
    conversation_id = (req.conversation_id or "").strip()[:64] or None
    earlier_messages = await agent_tools.record_conversation_message(
        db, user.id, conversation_id, message, images=req.images
    )

    # Load durable draft context before the routing model runs.  Recent chat
    # history is not a substitute for this state: users often give date,
    # place and activity in separate turns.
    pend_for_context = await agent_tools.get_pending(db, user.id, conversation_id=conversation_id)
    draft_for_context = pend_for_context[1] if pend_for_context else {}
    await report_progress(
        "load_context", "completed", "已了解项目背景和刚才的对话上下文",
        {
            "history_messages": len(earlier_messages or []),
            "has_pending_draft": bool(pend_for_context),
            "pending_tool": pend_for_context[0] if pend_for_context else "",
            "pending_fields": list(draft_for_context.get("missing") or [])[:24],
        },
    )

    system = _AGENT_SYSTEM.format(
        catalog=agent_tools.catalog_for_prompt(),
        actions=json.dumps(agent_request_plan.action_catalog(), ensure_ascii=False),
        record_types=json.dumps(agent_request_plan.RECORD_TYPES, ensure_ascii=False),
    )
    messages: list[dict] = [{"role": "system", "content": system}]
    if pend_for_context:
        messages.append({"role": "system", "content": render_context_summary(
            draft_for_context.get("args"), draft_for_context.get("facts"),
            draft_for_context.get("missing"), draft_for_context.get("messages"))})
    if earlier_messages:
        history_summary = render_conversation_history(earlier_messages)
        if history_summary:
            messages.append({"role": "user", "content": history_summary})
    messages.extend(_history_messages(req.history))
    material = (req.material or "").strip()
    if material:
        # 图/文件已由前端交给视觉模型/文档解析，这里只把文字素材挂到用户消息后面。
        # 不挂的话模型会以为「用户只发了一句话」，既抓不到现场情况，也容易编造。
        messages.append({"role": "user", "content": (
            f"{message}\n\n以下是随消息发送的素材（系统已识别，不是你要识别的图片本体）：\n{material}")})
    else:
        messages.append({"role": "user", "content": message})

    external_plan = agent_request_plan.normalize_candidate(
        req.request_plan,
        message=message,
        attachment_count=1 if material else 0,
        evidence_count=1 if material else 0,
    )
    has_external_plan = bool(external_plan.get("actions"))
    incoming_plan = external_plan

    form_values = req.form_values if isinstance(req.form_values, dict) else {}
    pending_args_for_submit = (pend_for_context[1].get("args") or {}) if pend_for_context else {}
    structured_submit = bool(form_values) and bool(pend_for_context)
    # 表单在字段全部齐全时可能没有新增值；点击“保存并生成记录”仍是一次
    # 明确确认，因此把该无字段提交视为确认动作，而不是重新追问。
    if (
        pend_for_context
        and pending_args_for_submit.get("_awaiting_confirmation")
        and message.strip() in {"按表单补充信息", "确认生成", "确认保存", "保存并生成记录"}
    ):
        structured_submit = True
    model_id = req.model or ""
    parsed: dict | None = None
    raw = ""
    if not structured_submit:
        try:
            await report_progress(
                "intent_model", "running", "正在理解你想做什么，并从你的话里抽取关键字段",
                {"provider": req.provider or "系统路由", "model": req.model or "系统选择", "response_mode": "structured_json"},
            )
            raw, config = await model_gateway.chat_completion(
                messages,
                temperature=0.2,
                provider=req.provider,
                model=req.model,
                response_format={"type": "json_object"},
            )
            model_id = config.model_id
            parsed = agent_tools._parse_json_object(raw)
            await report_progress(
                "intent_model", "completed", "已读懂你的意图，正在核对可用的动作和字段",
                {
                    "model": model_id,
                    "has_json_result": bool(parsed),
                    "proposed_action": str((parsed or {}).get("intent") or ""),
                    "proposed_record_type": str((parsed or {}).get("record_type") or ""),
                    "proposed_fact_fields": list((parsed or {}).get("facts", {}).keys())[:24]
                    if isinstance((parsed or {}).get("facts"), dict)
                    else [str(item.get("field") or item.get("name") or "") for item in ((parsed or {}).get("facts") or [])[:24] if isinstance(item, dict)],
                },
            )
        except model_gateway.ModelGatewayError as exc:
            # 不支持 JSON 模式的端点：退化为纯问答，聊天不中断
            logger.warning("agent JSON 路由失败，退化纯问答: %s", type(exc).__name__)
            try:
                await report_progress(
                    "intent_model", "running", "结构化理解暂时不可用，我改用普通问答来回答",
                    {"reason": type(exc).__name__},
                )
                answer = await _plain_answer(message, req.history, req.provider, req.model, req.mode)
            except model_gateway.ModelGatewayError as exc2:
                raise HTTPException(status_code=503, detail=str(exc2)) from exc2
            await report_progress(
                "intent_model", "completed", "已用普通问答给出回复，本轮没有执行业务写入",
                {"degraded": True},
            )
            return {"kind": "chat", "tool": None, "reply": answer, "card": None,
                    "cards": [], "actions": [], "need_more": [], "undo": None,
                    "form": None, "model": None, "degraded": True,
                    "request_plan": incoming_plan}

    semantic_candidate = dict(parsed or {})
    semantic_args = (
        semantic_candidate.get("args")
        if isinstance(semantic_candidate.get("args"), dict)
        else {}
    )
    # Some models still put the record type only inside args. Preserve that
    # value as a candidate while the action itself remains catalog-controlled.
    if not semantic_candidate.get("record_type") and semantic_args.get("record_type"):
        semantic_candidate["record_type"] = semantic_args.get("record_type")
    # 🔴 素材明示类型：照片水印/公示牌上写着「隐蔽工程验收公示牌」这类字样时，
    # 不再回头问「整理成哪一类记录」。逻辑在 agent_request_plan，跑分器同源调用。
    semantic_candidate = agent_request_plan.apply_material_record_type(
        semantic_candidate, material or "")
    semantic_plan = agent_request_plan.normalize_candidate(
        semantic_candidate,
        message=message,
        attachment_count=1 if material else 0,
        evidence_count=1 if material else 0,
        evidence=[],
        system_facts=system_facts,
    )
    semantic_plan = agent_request_plan.enrich_plan_facts(
        semantic_plan,
        extract_explicit_message_facts(message, message_ref=message),
        evidence=[],
        system_facts=system_facts,
    )
    if has_external_plan:
        # The precomputed attachment plan is authoritative for actions and
        # clarifications; the chat turn contributes the extracted user facts.
        merged_facts = agent_fact_model.merge_fact_candidates(
            external_plan.get("facts"), semantic_plan.get("facts"),
        )
        incoming_plan = dict(external_plan)
        incoming_plan["facts"] = merged_facts
        incoming_plan["system_facts"] = system_facts
        incoming_plan["field_status"] = {
            **agent_fact_model.fact_context_fields(system_facts),
            **agent_fact_model.fact_context_fields(merged_facts),
        }
        prefill_evidence = [{
            "record_date": prefill.get("record_date") or _iso_date(prefill.get("taken_at")),
            "observed_date_source": "watermark",
            "location": prefill.get("location"),
            "section": prefill.get("section"),
            "weather": prefill.get("weather"),
            "field_sources": {
                field: "watermark"
                for field in ("record_date", "location", "section", "weather")
                if str(prefill.get(field) or "").strip()
                or (field == "record_date" and _iso_date(prefill.get("taken_at")))
            },
        }] if prefill else []
        prefill_conflicts = agent_fact_model.detect_source_conflicts(
            merged_facts, prefill_evidence, system_facts,
        )
        existing_conflicts = [
            item for item in (external_plan.get("field_conflicts") or [])
            if isinstance(item, dict)
        ]
        conflict_fields = {str(item.get("field") or "") for item in existing_conflicts}
        incoming_plan["field_conflicts"] = existing_conflicts + [
            item for item in prefill_conflicts
            if str(item.get("field") or "") not in conflict_fields
        ]
        trace = dict(incoming_plan.get("trace") or {})
        trace.update({
            "fact_count": len(merged_facts),
            "conflict_count": len(incoming_plan["field_conflicts"]),
            "semantic_action_ids": [
                str(item.get("id") or "") for item in semantic_plan.get("actions", [])
                if isinstance(item, dict)
            ],
        })
        incoming_plan["trace"] = trace
    else:
        incoming_plan = semantic_plan

    tool = str(incoming_plan.get("selected_tool") or "").strip()
    if tool not in agent_tools.TOOL_BY_NAME:
        tool = ""
    fact_args = agent_fact_model.facts_to_args(incoming_plan.get("facts"))
    args = {**semantic_args, **fact_args}
    if incoming_plan.get("record_type") and not str(args.get("record_type") or "").strip():
        args["record_type"] = incoming_plan["record_type"]
    inferred_type = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
    selected_form = agent_tools.record_field_spec.form_for_record_type(
        inferred_type, agent_tools._is_weida(inferred_type, args, message),
    ) if inferred_type else None
    args = agent_tools.apply_explicit_user_facts(
        args, incoming_plan.get("facts"), selected_form,
    )
    turn_fact_args = dict(args)
    proposed_record_type = agent_tools._norm_record_type(str(args.get("record_type") or ""))
    _thought_record_type = str(incoming_plan.get("record_type") or proposed_record_type or "")
    await report_progress(
        "semantic_validation", "completed",
        ("我判断本轮要处理的是「" + _thought_record_type + "」")
        if _thought_record_type else "已梳理出本轮要执行的动作与字段",
        {
            "intent": incoming_plan.get("intent"),
            "action": incoming_plan.get("selected_tool"),
            "record_type": incoming_plan.get("record_type") or proposed_record_type,
            "model": model_id,
            "facts": [
                {
                    "field": item.get("field"), "value": item.get("value"),
                    "source": item.get("source"), "confidence": item.get("confidence"),
                }
                for item in (incoming_plan.get("facts") or [])
                if isinstance(item, dict)
            ][:32],
            "conflicts": (incoming_plan.get("field_conflicts") or [])[:16],
            "clarifications": (incoming_plan.get("clarifications") or [])[:12],
            "explicit_user_fields": [
                {"field": item.get("field"), "value": item.get("value")}
                for item in (incoming_plan.get("facts") or [])
                if isinstance(item, dict)
                and item.get("source") == "user_message_deterministic"
            ][:24],
        },
    )

    # 上一轮追问留下的草稿（同用户，已落库）：本轮参数合并到草稿上，避免丢字段
    pend = pend_for_context
    pending_tool = pend[0] if pend else ""
    """
    user - message
    assisent
    system
    tools
    有限的目标
    1 2 3 4 5
    1 - 1234
    2 - 3214
    3 - 123123
    4 - 123123
    5 - 123133132

    整理表
    整理成模板内容
    意图 - 对应表
    填入对应表
    确认信息
    登记
    """
    draft = pend[1] if pend else {}
    if structured_submit:
        # 表单一次提交：不再让模型猜字段，直接按字段表写回草稿。
        tool = pending_tool
        draft_args = draft.get("args") or {}
        draft_type = agent_tools._norm_record_type(
            str(draft_args.get("record_type") or form_values.get("record_type") or "")
        ) or ""
        draft_form = agent_tools.record_field_spec.form_for_record_type(
            draft_type, agent_tools._is_weida(draft_type, draft_args, message),
        ) if draft_type else None
        # Keep facts already accumulated in earlier turns; a form submission is
        # an update to the durable draft, not a replacement for it.
        args = agent_tools.apply_form_values(dict(draft_args), draft_form, form_values)
        turn_fact_args = dict(args)
    draft_prefill = (draft.get("args") or {}).get("_prefill")
    if not prefill and isinstance(draft_prefill, dict):
        prefill = draft_prefill
    # 模型没选工具、但草稿未完成：把这句当「补充回答」单独抽一次参数
    # （明显是提问/找页面的话不抽，避免「用户中途问规范」被当成补字段）
    if not tool and pending_tool and not any(h in message for h in _SUPPLEMENT_SKIP_HINTS):
        await report_progress(
            "extract_supplement", "running", "正在把你说的内容，对照草稿里还缺的字段提取出来",
            {"pending_tool": pending_tool, "missing_fields": list(draft.get("missing") or [])[:24]},
        )
        extra = await _extract_supplement(message, pending_tool, draft, req.provider, req.model)
        # 命中「仍缺字段」才当补充回答：标签、extra_fields 槽位、或仍缺字段对应的参数名
        if agent_tools.supplement_hits(extra, draft.get("args") or {}, draft.get("missing")):
            tool = pending_tool
            args = {**args, **extra}
            turn_fact_args.update(extra)
            await report_progress(
                "extract_supplement", "completed", "已把你补充的内容填进草稿对应字段",
                {"fields": list(extra.keys())[:24]},
            )
        else:
            await report_progress(
                "extract_supplement", "completed", "本轮内容没有匹配到当前草稿缺项，原草稿保持不变",
                {"candidate_fields": list(extra.keys())[:24]},
            )
    # A type choice is intentionally kept as a deterministic transition for
    # the evidence-only draft.  The generic supplement extractor cannot infer
    # a form before record_type exists.
    if not tool and pending_tool == "create_work_record":
        candidate = agent_request_plan.canonical_record_type(message)
        if candidate:
            tool = pending_tool
            args = {**args, "record_type": candidate}
            turn_fact_args = dict(args)

    # 素材里已确定的结构化字段（照片水印的拍摄时间/地点/天气）并入建记录参数。
    # 日期、地点、部位和天气以水印/EXIF 客观证据为准；用户冲突描述保留在计划中。
    # 带素材不等于明确了记录类型。素材只提供证据；类型不明时保留澄清，
    # 避免用户上传旁站/验收/材料照片却被静默归成巡视。
    auto_filled: list[str] = []
    needs_record_type = any(
        str(item.get("field") or "") == "record_type"
        for item in incoming_plan.get("clarifications", [])
        if isinstance(item, dict)
    ) and not pending_tool
    if needs_record_type:
        # Never accept a model-inferred form for this ambiguous request.
        tool = ""
        # Preserve facts stated in this turn even when the only missing
        # decision is the target record type.  The next reply ("巡视" etc.)
        # will merge this draft instead of relying on a short chat window.
        draft_args = dict(args)
        draft_args.pop("record_type", None)
        if prefill:
            draft_args["_prefill"] = prefill
        await agent_tools.stash_pending(
            db, user.id, "create_work_record", draft_args, ["记录类型"],
            message=message, fact_args=turn_fact_args, conversation_id=conversation_id,
        )

    # 🔴 服务端接管：模型在「已带素材证据 + 已识别记录类型或存在字段冲突」时仍返回
    # 纯聊天（典型：只汇报「你描述的信息与图片证据不一致…请核对」），整轮会退化成一堵
    # 文字，need_more/options/form 全空，用户既没有可点选项、也无处核对，只能继续打字。
    # 2026-11-05 生产会话实测：kind=chat / need_more=[] / options=NULL。此时不等模型
    # 自觉，直接走建记录流程，由 _create_work_record 一次性给出 chips 与预填表单。
    if (not tool and not needs_record_type
            and (str(material or "").strip() or prefill)
            and (incoming_plan.get("field_conflicts")
                 or agent_tools._norm_record_type(str(args.get("record_type") or "")))):
        tool = "create_work_record"

    if tool == "create_work_record":
        if prefill:
            rtype = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
            if not rtype:
                # Do not apply type-specific prefill when the target form is
                # unknown.  The values stay in req.prefill for the next step.
                rtype = ""
            if not rtype:
                auto_filled = []
            else:
                args, auto_filled = apply_prefill(
                    args, prefill, rtype,
                    protected_keys=set(form_values) if structured_submit else None,
                )

    if tool:
        spec = agent_tools.TOOL_BY_NAME[tool]
        if spec.risk != "read" and not project_id:
            return {"kind": "action", "tool": tool, "reply": "你还没有所属项目，先加入项目后再记录。",
                    "card": None, "cards": [], "actions": [{"type": "navigate", "label": "去加入项目",
                    "url": "/pages/join-project/index"}], "need_more": [], "undo": None,
                    "form": None, "model": model_id, "request_plan": incoming_plan}
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
                if not structured_submit:
                    args = agent_tools.apply_explicit_user_facts(
                        args, draft.get("facts"), _form,
                    )
                    args = agent_tools.apply_explicit_user_facts(
                        args, incoming_plan.get("facts"), _form,
                    )
            args = agent_tools.normalize_extra_fields(args, _form)
        elif pending_tool and spec.risk != "read":
            # 用户换成了另一个写操作 → 旧草稿作废（纯查询/问答不清草稿，草稿有 24 小时 TTL）
            await agent_tools.clear_pending(db, user.id, pending_tool, conversation_id)
        # _prefill is durable context only; it is never a business field.
        args.pop("_prefill", None)
        # 普通聊天创建记录与整理计划统一采用“字段齐全 → 用户确认 → 正式写入”。
        # 首次执行只做合同校验并保存待确认草稿；用户确认后才放行工具写入。
        if tool == "create_work_record":
            awaiting = bool((draft.get("args") or {}).get("_awaiting_confirmation"))
            confirmed = awaiting and (
                structured_submit
                or any(token in message for token in ("确认生成", "确认保存", "保存并生成", "确认建档"))
            )
            args["_require_confirmation"] = not confirmed
            if confirmed:
                args["_execution_confirmed"] = True
        ctx = agent_tools.ToolContext(db=db, user=user, project_id=project_id, project=project, message=message, material=material)
        await report_progress(
            "tool_validation", "running", "确认要执行的动作，并核对字段是否齐全",
            {"tool": tool, "record_type": str(args.get("record_type") or ""), "form": _record_form_code(tool, args, message) or ""},
        )
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
        await report_progress(
            "tool_validation", "completed", "服务端字段合同校验完成",
            {
                "tool": tool,
                "missing_fields": list(result.get("need_more") or [])[:32],
                "provided_fields": [
                    {"field": str(key), "value": value}
                    for key, value in args.items()
                    if not str(key).startswith("_")
                    and key != "extra_fields"
                    and str(value if value is not None else "").strip()
                ][:32],
                "extra_fields": [
                    {"field": str(key), "value": value}
                    for key, value in (args.get("extra_fields") or {}).items()
                    if str(value if value is not None else "").strip()
                ][:32] if isinstance(args.get("extra_fields"), dict) else [],
                "created_ids": [item.get("id") for item in (result.get("cards") or []) if isinstance(item, dict) and item.get("id") is not None][:12],
                "created_id": (result.get("card") or {}).get("id") if isinstance(result.get("card"), dict) else None,
            },
        )
        if result.get("need_more"):
            if tool == "create_work_record" and "最终确认" in [str(x) for x in (result.get("need_more") or [])]:
                args["_awaiting_confirmation"] = True
            # 🔴 追问气泡以「模型自然接话」为主：need_more 时优先用 LLM 的 answer
            # 当主气泡文案（自然接话、不重复标签；bold 规则串只出现在表单里），
            # 模板 reply 只在 answer 缺失/过于机械时才兜底。两者绝不叠加，避免机械感。
            _model_answer = str((parsed or {}).get("answer") or "").strip()
            _tool_reply = str(result.get("reply") or "").strip()
            if result.get("ask_block"):
                # 「一起询问」：全部验收询问项必须完整呈现，不能被模型自然接话覆盖。
                result["reply"] = _tool_reply
            elif _model_answer and not _is_mechanical_reply(_model_answer):
                result["reply"] = _model_answer
            elif _tool_reply:
                result["reply"] = _tool_reply
            await agent_tools.stash_pending(
                db, user.id, tool,
                {**args, "_prefill": prefill}
                if prefill and tool == "create_work_record" else args,
                result.get("need_more"), message=message,
                fact_args=turn_fact_args, conversation_id=conversation_id,
            )
            if result.get("ask_block"):
                # 「一起询问」：验收要点以对话块（ask_block）一次性呈现，用户一并
                # 文字回复即可，**不叠加表单卡片**（2026-11-05 用户真机反馈
                # 「怎么还是填表格」——路由层无条件挂 _agent_form，前端把表格
                # 卡片渲染在询问气泡下方，交互观感又拉回逐栏填写）。
                result["form"] = None
                result["input_components"] = []
            else:
                form = _agent_form(tool, args, result, message, str(getattr(project, "name", "") or ""))
                result["form"] = form
                result["input_components"] = _agent_input_components(form)
            result["input_channels"] = ["text", "voice", "image", "file"]
        elif result.get("ok"):
            await agent_tools.clear_pending(db, user.id, tool, conversation_id)
            result["form"] = _agent_form(tool, args, result, message, str(getattr(project, "name", "") or ""))
        if auto_filled:
            # 让用户一眼看到「水印确实读到了、也确实填进表单了」，而不是被空问一句「要记什么」
            head = "已从照片水印自动填入：" + "、".join(auto_filled)
            if result.get("need_more"):
                rtype = agent_tools._norm_record_type(str(args.get("record_type") or "")) or "巡视"
                head = f"已按「{rtype}」开始记录，{head}。还差的栏目已列在下面，填完一次提交就行："
            result["reply"] = head + "\n" + str(result.get("reply") or "").strip()
        return {"kind": "action", "tool": tool, "model": model_id,
                "request_plan": incoming_plan, **result}

    # Keep evidence context while asking the one missing business decision.
    # This is deliberately a draft, not a record: no database side effect is
    # performed until the user chooses a target type and completes the form.
    if needs_record_type:
        options = {"记录类型": list(agent_tools.record_field_spec.RECORD_TYPE_TO_FORM)}
        has_evidence = bool(
            material or prefill
            or (incoming_plan.get("evidence") if isinstance(incoming_plan.get("evidence"), list) else None)
            or _plan_evidence_count(incoming_plan)
        )
        form = _agent_form(
            "create_work_record", args,
            {"need_more": ["记录类型"], "options": options}, message,
            str(getattr(project, "name", "") or ""),
        )
        return {
            "kind": "action", "tool": "create_work_record",
            "reply": _missing_record_type_reply(has_evidence),
            "card": None, "cards": [], "actions": [],
            "need_more": ["记录类型"],
            "options": options,
            "form": form,
            "input_components": _agent_input_components(form),
            "recommendations": ([{
                "field": "record_type", "value": proposed_record_type,
                "reason": "结合你这次的描述给出的类型建议；需要你点选确认。",
            }] if proposed_record_type in agent_tools.record_field_spec.RECORD_TYPE_TO_FORM else []),
            "undo": None, "model": model_id, "request_plan": incoming_plan,
        }

    # 无工具 → 纯问答（把模型已生成的 answer 直接用；空则再调一次）
    answer = str((parsed or {}).get("answer") or "").strip()
    if not answer:
        try:
            answer = await _plain_answer(message, req.history, req.provider, req.model, req.mode)
        except model_gateway.ModelGatewayError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"kind": "chat", "tool": None, "reply": strip_markdown(answer), "card": None,
            "cards": [], "actions": [], "need_more": [], "undo": None,
            "form": None, "model": model_id, "request_plan": incoming_plan}


@router.post("/chat")
async def agent_chat(
    req: AgentChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run one agent turn, then persist the exact assistant result for restore."""
    user_id = int(user.id)
    project_id = getattr(user, "project_id", None)
    conversation_id = (req.conversation_id or "").strip()[:64] or None
    request_id = await agent_runtime.start_request(
        request_id=req.request_id,
        user_id=user_id,
        project_id=project_id,
        conversation_id=conversation_id,
        endpoint="/api/agent/chat",
        stage="agent_chat",
        message=req.message,
        provider=req.provider or "",
        model=req.model or "",
    )

    async def progress_hook(stage: str, status: str, title: str, detail: dict[str, Any]) -> None:
        await agent_runtime.append_progress_event(
            request_id=request_id,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            endpoint="/api/agent/chat",
            stage=stage,
            status=status,
            title=title,
            detail=detail,
        )

    try:
        result = await _agent_chat_impl(req, user=user, db=db, progress_hook=progress_hook)
    except Exception as exc:
        await agent_runtime.append_progress_event(
            request_id=request_id,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            endpoint="/api/agent/chat",
            stage="agent_chat",
            status="failed",
            title="Agent 本轮处理失败，未返回成功结果",
            detail={"error": str(exc)[:400]},
            terminal=True,
        )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/chat",
            stage="agent_chat",
            status="failed",
            error=exc,
        )
        raise
    plan = result.get("request_plan") if isinstance(result, dict) else None
    conflicts = plan.get("field_conflicts") if isinstance(plan, dict) else None
    if isinstance(conflicts, list) and conflicts and isinstance(result, dict):
        labels = {
            "record_date": "日期", "project": "项目",
            "location": "地点", "section": "工程部位", "weather": "天气",
            "owner_unit": "建设单位", "construction_unit": "施工单位",
            "supervision_unit": "监理单位", "inspector": "检查人员",
        }
        # 冲突详情不再逐条铺成 5 行文字（2026-11-05 真机反馈「怎么还是这样子」：
        # 整屏都是只能读、不能点的说明）。压成一句 + 字段名，具体候选值由下方
        # chips / 表单承载，用户点一下即可改口径。
        conflict_labels = [
            labels.get(str(item.get("field") or ""), str(item.get("field") or "字段"))
            for item in conflicts[:4] if isinstance(item, dict)
        ]
        brief = (f"有 {len(conflicts)} 项与照片水印不一致（{'、'.join(conflict_labels)}），"
                 "已先按水印/EXIF 填入；要改直接说一声，或点下方选项。")
        result["field_conflicts"] = conflicts
        result["reply"] = brief + "\n" + str(result.get("reply") or "").strip()
    if isinstance(result, dict):
        result["request_id"] = request_id
    if conversation_id and isinstance(result, dict):
        result["conversation_id"] = conversation_id
        try:
            await agent_tools.append_conversation_response(
                db, user.id, conversation_id, result,
            )
        except Exception:  # noqa: BLE001
            # The business action may already be committed. Never turn a
            # history-write failure into a misleading failed chat response.
            logger.exception("保存智能体会话回复失败，conversation_id=%s", conversation_id)
            await db.rollback()
            result["history_saved"] = False
    if isinstance(result, dict):
        cards = result.get("cards") if isinstance(result.get("cards"), list) else []
        if not cards and isinstance(result.get("card"), dict):
            cards = [result["card"]]
        needs_user = bool(result.get("need_more")) or bool(result.get("form"))
        await agent_runtime.append_progress_event(
            request_id=request_id,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            endpoint="/api/agent/chat",
            stage="agent_result",
            status="waiting" if needs_user else "completed",
            title=(
                "当前草稿已整理，等待你补充或确认"
                if needs_user else "本轮思考与处理已完成，结果已返回工作台"
            ),
            detail={
                "kind": result.get("kind"),
                "tool": result.get("tool"),
                "missing_fields": list(result.get("need_more") or [])[:32],
                "card_ids": [item.get("id") for item in cards if isinstance(item, dict) and item.get("id") is not None][:12],
                "model": result.get("model") or req.model or "",
            },
            terminal=True,
        )
        plan_result = result.get("request_plan") if isinstance(result.get("request_plan"), dict) else None
        record_refs = [
            {"kind": item.get("kind"), "id": item.get("id")}
            for item in cards
            if isinstance(item, dict) and item.get("id") is not None
        ]
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/chat",
            stage="agent_chat",
            request_plan=plan_result,
            result=result,
            created_record_refs=record_refs,
            model=str(result.get("model") or req.model or ""),
        )
    return result


@router.get("/conversations")
async def agent_conversations(
    limit: int = 30,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List conversations owned by the current account only."""
    conversations = await agent_tools.list_conversation_summaries(db, user.id, limit)
    return {"conversations": conversations}


@router.get("/conversations/{conversation_id}")
async def agent_conversation_detail(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    detail = await agent_tools.get_conversation_detail(
        db, user.id, conversation_id.strip()[:64],
    )
    if not detail:
        raise HTTPException(status_code=404, detail="会话不存在")
    return detail


@router.delete("/conversations/{conversation_id}")
async def agent_conversation_delete(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    deleted = await agent_tools.delete_conversation(
        db, user.id, conversation_id.strip()[:64],
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


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


@router.get("/requests")
async def list_agent_requests(
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List request-level traces owned by the current account and project."""
    rows = await agent_runtime.list_request_traces(
        db, user.id, getattr(user, "project_id", None), limit,
    )
    return {"requests": [agent_runtime.trace_payload(row) for row in rows]}


@router.get("/requests/{request_id}")
async def get_agent_request(
    request_id: str,
    after_sequence: int = 0,
    progress_only: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return one trace by id; ids are scoped to the authenticated account."""
    normalized = agent_runtime.normalize_request_id(request_id)
    row = await agent_runtime.get_request_trace(db, normalized, user.id)
    if not row:
        raise HTTPException(404, "请求追踪不存在")
    if row.project_id is not None and row.project_id != getattr(user, "project_id", None):
        raise HTTPException(403, "请求追踪不属于当前项目")
    payload = agent_runtime.trace_payload(row, after_sequence=max(0, after_sequence))
    if progress_only:
        return {
            "request_id": payload["request_id"],
            "status": payload["status"],
            "current_stage": payload["current_stage"],
            "progress_events": payload["progress_events"],
            "latest_progress_sequence": payload["latest_progress_sequence"],
        }
    return payload


@router.post("/requests/{request_id}/progress")
async def append_agent_progress(
    request_id: str,
    req: AgentProgressEventRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Append an authenticated, bounded progress event for live workbench display."""
    if req.status not in {"running", "completed", "failed", "waiting", "skipped"}:
        raise HTTPException(422, "进度状态无效")
    try:
        detail_size = len(json.dumps(req.detail, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        raise HTTPException(422, "进度详情格式无效")
    if detail_size > 16_000:
        raise HTTPException(413, "进度详情过长")
    project_id = getattr(user, "project_id", None)
    existing = await agent_runtime.get_request_trace(
        db, agent_runtime.normalize_request_id(request_id), int(user.id),
    )
    if existing and existing.project_id is not None and existing.project_id != project_id:
        raise HTTPException(403, "请求追踪不属于当前项目")
    event = await agent_runtime.append_progress_event(
        request_id=request_id,
        user_id=int(user.id),
        project_id=project_id,
        endpoint="/api/agent/progress",
        event_id=req.event_id,
        stage=req.stage,
        status=req.status,
        title=req.title,
        detail=req.detail,
        terminal=req.terminal,
    )
    return {"event": event}


@router.get("/executions/{execution_id}")
async def get_agent_execution(
    execution_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read a durable execution result after timeout, disconnect or restart."""
    normalized = agent_runtime.normalize_execution_id(execution_id)
    row = (await db.execute(select(AgentFlowInstance).where(
        AgentFlowInstance.execution_id == normalized,
        AgentFlowInstance.user_id == user.id,
        AgentFlowInstance.project_id == getattr(user, "project_id", None),
    ))).scalars().first()
    if not row:
        raise HTTPException(404, "执行实例不存在")
    return agent_runtime.flow_payload(row)


class AgentClearDraftsRequest(BaseModel):
    """放弃草稿：指定 tool 只清该工具草稿；省略则清空当前用户全部待补全草稿。"""
    tool: Optional[str] = None
    conversation_id: Optional[str] = None


@router.get("/drafts")
async def agent_drafts(
    conversation_id: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """当前用户所有未过期草稿（聊天即操作的半成品，落库持久化、跨会话可见）。"""
    drafts = await agent_tools.list_pending(
        db, user.id, (conversation_id or "").strip()[:64] or None
    )
    return {"count": len(drafts), "drafts": drafts}


@router.post("/drafts/clear")
async def agent_clear_drafts(
    req: AgentClearDraftsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """放弃草稿：清掉当前用户指定工具或全部待补全草稿。"""
    await agent_tools.clear_pending(
        db, user.id, req.tool, (req.conversation_id or "").strip()[:64] or None
    )
    return {"ok": True}


def _review_plan_payload(row: AgentReviewPlan) -> dict[str, Any]:
    try:
        plan = json.loads(row.plan_json or "{}")
    except (TypeError, ValueError):
        plan = {}
    try:
        execution_result = json.loads(row.execution_result_json or "null")
    except (TypeError, ValueError):
        execution_result = None
    request_plan = plan.get("request_plan") if isinstance(plan.get("request_plan"), dict) else {}
    request_id = agent_runtime.normalize_request_id(
        plan.get("request_id") or request_plan.get("request_id")
        or f"legacy-review-plan-{row.id}",
    )
    from app.services.agent_form_requirements import enrich_plan

    view_plan = enrich_plan(plan)
    return {
        "id": row.id, "conversation_id": row.conversation_id,
        "request_id": request_id,
        "execution_id": agent_runtime.review_execution_id(
            row.id, row.confirmed_revision or row.revision,
        ),
        "revision": row.revision, "status": row.status,
        "confirmed_revision": row.confirmed_revision,
        "executed_revision": row.executed_revision,
        "execution_result": execution_result,
        "plan": view_plan, "issues": agent_review_plan.review_issues(plan),
        "updated_at": row.updated_at.isoformat(),
    }


@router.post("/review-plans")
async def create_review_plan(
    req: ReviewPlanCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a persistent, unconfirmed multi-event review plan."""
    user_id = int(user.id)
    project_id = getattr(user, "project_id", None)
    conversation_id = req.conversation_id.strip()[:64]
    if not conversation_id:
        raise HTTPException(400, "conversation_id 不能为空")
    request_plan = req.plan.get("request_plan") if isinstance(req.plan.get("request_plan"), dict) else {}
    request_id = agent_runtime.normalize_request_id(
        req.request_id or req.plan.get("request_id") or request_plan.get("request_id"),
    )
    stored_plan = agent_review_plan.pin_form_contracts({**req.plan, "request_id": request_id})
    project = await db.get(Project, project_id) if project_id else None
    if project:
        stored_plan["system_project"] = project_context(project)
    await agent_runtime.start_request(
        request_id=request_id,
        user_id=user_id,
        project_id=project_id,
        conversation_id=conversation_id,
        endpoint="/api/agent/review-plans",
        stage="review_plan_save",
        message=stored_plan.get("request_text") or "",
    )
    # 带 attachments 建记录的这轮不走 /agent/chat，用户消息（含图）必须在这里落进
    # 展示历史，否则「查看历史记录」看不到这条消息和照片。落库失败不阻断建计划。
    if str(stored_plan.get("request_text") or "").strip() or req.images:
        try:
            await agent_tools.record_conversation_message(
                db, user_id, conversation_id,
                str(stored_plan.get("request_text") or ""),
                images=req.images,
            )
        except Exception:  # noqa: BLE001 - 历史落库失败不能挡记录整理
            logger.warning("persist review-plan user turn failed", exc_info=True)
    try:
        agent_review_plan.validate_plan(stored_plan)
        await _validate_review_plan_media(db, user, stored_plan)
        existing = (await db.execute(select(AgentReviewPlan).where(
            AgentReviewPlan.user_id == user_id,
            AgentReviewPlan.project_id == project_id,
            AgentReviewPlan.conversation_id == conversation_id,
        ).with_for_update())).scalars().first()
        if existing:
            if existing.status != "executed":
                raise HTTPException(409, detail={
                    "code": "ACTIVE_PLAN_EXISTS",
                    "message": "该会话已有整理计划，请读取并修订现有计划",
                    "plan_id": existing.id,
                    "revision": existing.revision,
                })
            existing.revision += 1
            existing.status = "draft"
            existing.plan_json = json.dumps(stored_plan, ensure_ascii=False)
            existing.confirmed_revision = None
            existing.executed_revision = None
            existing.execution_result_json = None
            existing.updated_at = datetime.now()
            row = existing
        else:
            row = AgentReviewPlan(
                user_id=user_id, conversation_id=conversation_id,
                project_id=project_id,
                revision=1, status="draft", plan_json=json.dumps(stored_plan, ensure_ascii=False),
            )
            db.add(row)
        await db.commit()
        await db.refresh(row)
        payload = _review_plan_payload(row)
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans",
            stage="review_plan_save",
            request_plan=request_plan,
            result={
                "plan_id": row.id,
                "revision": row.revision,
                "status": row.status,
                "event_count": len(stored_plan.get("events") or []),
                "evidence_count": len(stored_plan.get("evidence") or []),
            },
            review_plan_id=row.id,
        )
        return payload
    except IntegrityError as exc:
        await db.rollback()
        # The row lock cannot lock a row that does not exist. The unique
        # session index is the final arbiter when two first saves race.
        raced = (await db.execute(select(AgentReviewPlan).where(
            AgentReviewPlan.user_id == user_id,
            AgentReviewPlan.project_id == project_id,
            AgentReviewPlan.conversation_id == conversation_id,
        ))).scalars().first()
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans",
            stage="review_plan_save",
            status="failed",
            error=exc,
        )
        if raced and raced.status != "executed":
            raise HTTPException(409, detail={
                "code": "ACTIVE_PLAN_EXISTS",
                "message": "该会话已有整理计划，请读取并修订现有计划",
                "plan_id": raced.id,
                "revision": raced.revision,
            }) from exc
        raise
    except Exception as exc:
        await db.rollback()
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans",
            stage="review_plan_save",
            status="failed",
            error=exc,
        )
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc)) from exc
        raise


@router.get("/review-plans")
async def list_review_plans(
    conversation_id: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AgentReviewPlan).where(
        AgentReviewPlan.user_id == user.id,
        AgentReviewPlan.project_id == getattr(user, "project_id", None),
    )
    if conversation_id and conversation_id.strip():
        stmt = stmt.where(AgentReviewPlan.conversation_id == conversation_id.strip()[:64])
    rows = (await db.execute(stmt.order_by(AgentReviewPlan.updated_at.desc()))).scalars().all()
    return {"plans": [_review_plan_payload(row) for row in rows]}


@router.get("/review-plans/{plan_id}")
async def get_review_plan(
    plan_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(select(AgentReviewPlan).where(
        AgentReviewPlan.id == plan_id, AgentReviewPlan.user_id == user.id,
    ))).scalars().first()
    if not row:
        raise HTTPException(404, "整理计划不存在")
    if row.project_id != getattr(user, "project_id", None):
        raise HTTPException(403, "整理计划不属于当前项目")
    return _review_plan_payload(row)


@router.put("/review-plans/{plan_id}")
async def update_review_plan(
    plan_id: int,
    req: ReviewPlanUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = int(user.id)
    project_id = getattr(user, "project_id", None)
    row = (await db.execute(select(AgentReviewPlan).where(
        AgentReviewPlan.id == plan_id, AgentReviewPlan.user_id == user_id,
    ).with_for_update())).scalars().first()
    if not row:
        raise HTTPException(404, "整理计划不存在")
    if row.project_id != project_id:
        raise HTTPException(403, "整理计划不属于当前项目")
    if row.status != "draft":
        raise HTTPException(409, "已确认的整理计划不能修改")
    if row.revision != req.revision:
        raise HTTPException(409, "整理计划已在其他请求中更新，请重新加载")
    try:
        current_plan = json.loads(row.plan_json or "{}")
        request_id = agent_runtime.normalize_request_id(
            req.request_id or req.plan.get("request_id") or current_plan.get("request_id"),
        )
        stored_plan = agent_review_plan.pin_form_contracts(
            {**req.plan, "request_id": request_id}, preserve_existing=True,
        )
        project = await db.get(Project, row.project_id) if row.project_id else None
        if project:
            stored_plan["system_project"] = project_context(project)
        agent_review_plan.validate_plan(stored_plan)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await agent_runtime.start_request(
        request_id=request_id,
        user_id=user_id,
        project_id=row.project_id,
        conversation_id=row.conversation_id,
        endpoint="/api/agent/review-plans/{plan_id}",
        stage="review_plan_save",
        message=stored_plan.get("request_text") or "",
    )
    try:
        await _validate_review_plan_media(db, user, stored_plan)
        row.revision += 1
        row.plan_json = json.dumps(stored_plan, ensure_ascii=False)
        row.confirmed_revision = None
        row.updated_at = datetime.now()
        await db.commit()
        await db.refresh(row)
        payload = _review_plan_payload(row)
        stored_request_plan = (
            stored_plan.get("request_plan")
            if isinstance(stored_plan.get("request_plan"), dict) else {}
        )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}",
            stage="review_plan_save",
            request_plan=stored_request_plan,
            result={
                "plan_id": row.id,
                "revision": row.revision,
                "status": row.status,
                "event_count": len(stored_plan.get("events") or []),
                "evidence_count": len(stored_plan.get("evidence") or []),
            },
            review_plan_id=row.id,
        )
        return payload
    except Exception as exc:
        await db.rollback()
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}",
            stage="review_plan_save",
            status="failed",
            error=exc,
        )
        raise


@router.post("/review-plans/{plan_id}/confirm")
async def confirm_review_plan(
    plan_id: int,
    req: ReviewPlanConfirm,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm only the exact reviewed revision; formal writes are a later command."""
    user_id = int(user.id)
    project_id = getattr(user, "project_id", None)
    row = (await db.execute(select(AgentReviewPlan).where(
        AgentReviewPlan.id == plan_id, AgentReviewPlan.user_id == user_id,
    ).with_for_update())).scalars().first()
    if not row:
        raise HTTPException(404, "整理计划不存在")
    if row.project_id != project_id:
        raise HTTPException(403, "整理计划不属于当前项目")
    if row.status == "confirmed" and row.confirmed_revision == req.revision:
        return _review_plan_payload(row)
    if row.status != "draft" or row.revision != req.revision:
        raise HTTPException(409, "整理计划版本已变化，请重新核对")
    plan = json.loads(row.plan_json or "{}")
    try:
        agent_review_plan.validate_plan(plan)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _validate_review_plan_media(db, user, plan)
    issues = agent_review_plan.review_issues(plan)
    if issues:
        raise HTTPException(422, {"message": "整理计划仍有待确认项", "issues": issues})
    stored_request_plan = plan.get("request_plan") if isinstance(plan.get("request_plan"), dict) else {}
    request_id = agent_runtime.normalize_request_id(
        req.request_id or plan.get("request_id") or stored_request_plan.get("request_id")
        or f"legacy-review-plan-{plan_id}",
    )
    await agent_runtime.start_request(
        request_id=request_id,
        user_id=user_id,
        project_id=row.project_id,
        conversation_id=row.conversation_id,
        endpoint="/api/agent/review-plans/{plan_id}/confirm",
        stage="review_confirm",
        message=plan.get("request_text") or "",
    )
    try:
        row.status = "confirmed"
        row.confirmed_revision = row.revision
        row.updated_at = datetime.now()
        await db.commit()
        await db.refresh(row)
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}/confirm",
            stage="review_confirm",
            result={"plan_id": plan_id, "revision": row.confirmed_revision, "status": row.status},
            review_plan_id=plan_id,
        )
    except Exception as exc:
        await db.rollback()
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}/confirm",
            stage="review_confirm",
            status="failed",
            error=exc,
            review_plan_id=plan_id,
        )
        raise
    return _review_plan_payload(row)


@router.post("/review-plans/{plan_id}/execute")
async def execute_review_plan(
    plan_id: int,
    req: ReviewPlanConfirm,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute one confirmed revision with durable, idempotent recovery."""
    user_id = int(user.id)
    preview = (await db.execute(select(AgentReviewPlan).where(
        AgentReviewPlan.id == plan_id, AgentReviewPlan.user_id == user_id,
    ))).scalars().first()
    if not preview:
        raise HTTPException(404, "整理计划不存在")
    project_id = getattr(user, "project_id", None)
    if preview.project_id != project_id:
        raise HTTPException(403, "整理计划不属于当前项目")
    preview_plan = json.loads(preview.plan_json or "{}")
    stored_request_plan = (
        preview_plan.get("request_plan")
        if isinstance(preview_plan.get("request_plan"), dict) else {}
    )
    request_id = agent_runtime.normalize_request_id(
        req.request_id or preview_plan.get("request_id") or stored_request_plan.get("request_id")
        or f"legacy-review-plan-{plan_id}",
    )
    execution_id = agent_runtime.review_execution_id(plan_id, req.revision)
    await agent_runtime.start_request(
        request_id=request_id,
        user_id=user_id,
        project_id=project_id,
        conversation_id=preview.conversation_id,
        endpoint="/api/agent/review-plans/{plan_id}/execute",
        stage="review_execute",
        message=preview_plan.get("request_text") or "",
    )
    execution_prepared = False
    try:
        flow = await agent_runtime.prepare_execution(
            db,
            execution_id=execution_id,
            request_id=request_id,
            flow_type="review_plan_execute",
            scope_id=f"review-plan:{plan_id}",
            user_id=user_id,
            project_id=project_id,
            conversation_id=preview.conversation_id,
            expected_revision=req.revision,
        )
        execution_prepared = True
        locked_flow = await agent_runtime.lock_execution(db, execution_id)
        if locked_flow is None:
            raise HTTPException(409, "执行实例已丢失，请重新发起")
        if locked_flow.status == "COMMITTED":
            result = agent_runtime.execution_result(locked_flow)
            await db.commit()
            await db.refresh(preview)
            await agent_runtime.finish_request(
                request_id=request_id,
                user_id=user_id,
                endpoint="/api/agent/review-plans/{plan_id}/execute",
                stage="review_execute",
                result=result,
                review_plan_id=plan_id,
                created_record_refs=result.get("records") or [],
            )
            return _review_plan_payload(preview)

        agent_runtime.mark_execution_running(locked_flow)
        row = (await db.execute(select(AgentReviewPlan).where(
            AgentReviewPlan.id == plan_id,
            AgentReviewPlan.user_id == user_id,
        ).with_for_update())).scalars().first()
        if not row:
            raise HTTPException(404, "整理计划不存在")
        if row.project_id != project_id:
            raise HTTPException(403, "整理计划不属于当前项目")
        from app.services.agent_review_execute import execute_confirmed_plan

        result = await execute_confirmed_plan(db, row, user, req.revision)
        agent_runtime.mark_execution_committed(locked_flow, result)
        # Formal records, review-plan terminal state and COMMITTED are atomic.
        await db.commit()
        await db.refresh(row)
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}/execute",
            stage="review_execute",
            result=result,
            review_plan_id=plan_id,
            created_record_refs=result.get("records") or [],
        )
        return _review_plan_payload(row)
    except Exception as exc:
        await db.rollback()
        if execution_prepared:
            await agent_runtime.mark_execution_failed(
                execution_id=execution_id, error=exc,
                user_id=user_id, project_id=project_id,
            )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}/execute",
            stage="review_execute",
            status="failed",
            error=exc,
            review_plan_id=plan_id,
        )
        raise
