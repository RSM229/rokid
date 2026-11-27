"""回答前的读取循环：agent 自己决定要不要看图、读哪份资料（2026-11-27）。

与 `agent_materials`（服务端按规则收集材料）不同，这里是**模型主动要**：
每一轮它输出要调用的读工具，服务端取文件/鉴权/截断，把结果回给它，直到 done。

工具只有读能力，不写库：
    view_image(index, question)      看第 N 张图的像素
    search_kb(query)                 找资料（返回 id/名称/文件夹）
    read_document(doc_id, question)  读正文（按问题挑段落，最多 6000 字）
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import agent_read_tools, model_gateway
from app.services.agent_materials import _looks_like_lookup

MAX_ROUNDS = 2
MAX_READS_PER_ROUND = 3
MAX_OBSERVATION_CHARS = 4000

_SYSTEM = (
    "你是监理智能体的资料查阅阶段。回答用户前，你可以先查看手头的图片和资料，"
    "确认关键事实后再让主流程回答。你只能读，不能写。\n"
    "每一步只输出一个 JSON："
    '{"reads":[{"tool":"工具名","args":{...}}],"reason":"为什么看这些"}\n'
    "看完就输出 {\"done\":true,\"reason\":\"材料够了\"}。\n"
    "工具：\n"
    "- view_image(args:{index:整数, question:字符串})：看第 index 张图片的像素，question 写你真正想确认的点\n"
    "- search_kb(args:{query:字符串})：在资料库里找文件，返回 id 和名称\n"
    "- read_document(args:{doc_id:整数, question:字符串})：读一份资料正文，按问题挑段落\n"
    "规则：一次最多 3 个调用；只查与用户问题相关的内容；没必要时直接 done。"
)


def _should_research(message: str, images: Sequence[str], mentions: Sequence[dict]) -> bool:
    if images:
        return True
    if mentions:
        return True
    return _looks_like_lookup(message)


def _inventory(images: Sequence[agent_read_tools.EvidenceImage], material: str) -> str:
    lines = []
    if images:
        lines.append("本轮图片：" + "、".join(f"{item.index}" for item in images))
    if (material or "").strip():
        lines.append(f"前端已给的素材摘要：{material.strip()[:1200]}")
    return "\n".join(lines) or "（没有随消息附带的图片）"


async def _execute(
    read: dict[str, Any],
    *,
    db: AsyncSession,
    user,
    project_id: int | None,
    images: Sequence[agent_read_tools.EvidenceImage],
) -> str:
    tool = str(read.get("tool") or "").strip()
    args = read.get("args") if isinstance(read.get("args"), dict) else {}
    if tool == "view_image":
        try:
            index = int(args.get("index") or 0)
        except (TypeError, ValueError):
            return "图片序号不合法"
        image = next((item for item in images if item.index == index), None)
        if image is None:
            return f"没有第 {index} 张图片"
        result = await agent_read_tools.view_image(image, str(args.get("question") or ""))
        if not result.get("ok"):
            return f"看图失败：{result.get('error')}"
        return f"图片{index}：{result.get('observation')}"
    if tool == "search_kb":
        query = str(args.get("query") or "").strip()
        if not query:
            return "检索词为空"
        result = await agent_read_tools.search_kb(db, user, project_id, query)
        docs = result.get("documents") or []
        folders = result.get("folders") or []
        parts = [f"文件夹：{item['path']}（{item['count']} 份）" for item in folders]
        parts += [f"文件 [{item['id']}]《{item['name']}》· {item['kb']} / {item['folder']}" for item in docs]
        return "\n".join(parts) or "资料库里没有匹配的文件"
    if tool == "read_document":
        try:
            doc_id = int(args.get("doc_id") or 0)
        except (TypeError, ValueError):
            return "资料编号不合法"
        result = await agent_read_tools.read_document(db, user, doc_id, str(args.get("question") or ""))
        if not result.get("ok"):
            return f"读资料失败：{result.get('error')}"
        return f"《{result.get('name')}》正文摘录：\n{result.get('text')}"
    return f"未知工具 {tool!r}"


async def research(
    db: AsyncSession,
    user,
    project_id: int | None,
    message: str,
    *,
    images: Sequence[str] = (),
    material: str = "",
    mentions: Sequence[dict] | None = None,
    provider: str | None = None,
    model: str | None = None,
    report=None,
) -> str:
    """跑读取循环，返回可注入 system 的观察结果；不需要时返回空串。"""
    if not _should_research(message, images, mentions or []):
        return ""
    evidence = agent_read_tools.image_evidence(images)
    conversation = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"用户问题：{message}\n\n{_inventory(evidence, material)}\n"
                "需要看什么？不需要就直接 done。"
            ),
        },
    ]
    observations: list[str] = []
    for round_index in range(MAX_ROUNDS):
        try:
            raw, _cfg = await model_gateway.chat_completion(
                conversation,
                temperature=0.1,
                provider=provider,
                model=model,
                response_format={"type": "json_object"},
            )
        except model_gateway.ModelGatewayError:
            break
        try:
            decision = json.loads(raw)
        except (TypeError, ValueError):
            break
        if not isinstance(decision, dict):
            break
        reads = decision.get("reads")
        if not isinstance(reads, list) or not reads:
            break
        for read in reads[:MAX_READS_PER_ROUND]:
            if not isinstance(read, dict):
                continue
            tool = str(read.get("tool") or "").strip()
            if report is not None:
                await report(
                    "research", "completed",
                    f"查看材料：{tool}",
                    {
                        "chain_kind": "tool",
                        "chain_step": f"{round_index + 1}.1",
                        "chain_label": f"工具调用 {round_index + 1}.1",
                        "tool": tool,
                        "args": read.get("args") if isinstance(read.get("args"), dict) else {},
                    },
                )
            observation = await _execute(
                read, db=db, user=user, project_id=project_id, images=evidence,
            )
            observation = observation[:MAX_OBSERVATION_CHARS]
            observations.append(f"- {tool}：{observation}")
            conversation.append({"role": "assistant", "content": json.dumps(read, ensure_ascii=False)})
            conversation.append({"role": "user", "content": f"工具结果：{observation}"})
        if decision.get("done"):
            break
    if not observations:
        return ""
    return (
        "【回答前查看的材料（agent 主动读取）】\n"
        + "\n".join(observations)
        + "\n【使用要求】依据这些内容作答；没有看到的不要编造。"
    )
