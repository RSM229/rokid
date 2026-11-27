"""读取循环：agent 自己决定看图/读资料，结果回灌（2026-11-27）。"""
import asyncio
import json

from app.services import agent_read_tools, agent_research, model_gateway


def _scripted(monkeypatch, decisions):
    calls = {"n": 0}

    async def fake_chat(messages, **kwargs):
        index = min(calls["n"], len(decisions) - 1)
        calls["n"] += 1
        return json.dumps(decisions[index], ensure_ascii=False), None

    monkeypatch.setattr(model_gateway, "chat_completion", fake_chat)
    return calls


def test_agent_views_image_and_gets_result(monkeypatch):
    _scripted(monkeypatch, [
        {"reads": [{"tool": "view_image", "args": {"index": 1, "question": "部位在哪"}}], "reason": "确认部位"},
        {"done": True, "reason": "够了"},
    ])

    async def fake_view(image, question):
        return {"ok": True, "observation": f"三层梁板，{question}：梁底模板已支好"}

    monkeypatch.setattr(agent_read_tools, "view_image", fake_view)

    async def scenario():
        block = await agent_research.research(
            None, type("U", (), {"id": 1})(), 1, "这张照片里是哪个部位？",
            images=["/uploads/media/a.jpg"], material="",
        )
        assert "图片1" in block
        assert "三层梁板" in block
        assert "梁底模板已支好" in block

    asyncio.run(scenario())


def test_no_research_when_nothing_to_look_at(monkeypatch):
    calls = _scripted(monkeypatch, [{"done": True}])

    async def scenario():
        block = await agent_research.research(
            None, type("U", (), {"id": 1})(), 1, "记一条巡视：三层梁板",
            images=[], material="",
        )
        assert block == ""
        assert calls["n"] == 0  # 没有图片也不是查资料，不该多打模型

    asyncio.run(scenario())


def test_document_excerpt_picks_relevant_paragraphs():
    text = "第一条 总则。\n模板支撑立杆间距不得超过1.5米。\n第三条 验收要求。\n混凝土浇筑温度不宜高于35度。"
    excerpt = agent_read_tools._excerpt(text, "模板支撑立杆间距")
    assert "1.5米" in excerpt
