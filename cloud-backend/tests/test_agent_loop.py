"""草稿 agent 循环：模型选工具、服务端只校验（2026-11-27）。"""
import asyncio
import json

from app.services import agent_loop, model_gateway


def _scripted(monkeypatch, decisions):
    calls = {"n": 0}

    async def fake_chat(messages, **kwargs):
        index = min(calls["n"], len(decisions) - 1)
        calls["n"] += 1
        return json.dumps(decisions[index], ensure_ascii=False), None

    monkeypatch.setattr(model_gateway, "chat_completion", fake_chat)
    return calls


def test_agent_decides_type_fields_and_question(monkeypatch):
    calls = _scripted(monkeypatch, [
        {"thought": "用户说了巡视", "tool": "set_record_type", "args": {"record_type": "巡视"}},
        {"thought": "部位和时间都明确", "tool": "fill_fields",
         "args": {"fields": {"section": "内围排水沟", "start_time": "2026/11/27 16:41"}}},
        {"thought": "只差问题描述", "tool": "ask_user", "args": {"question": "发现问题及处理情况是什么？"}},
    ])

    async def scenario():
        turn = await agent_loop.run_draft_agent(
            None,
            message="2026/11/27 16:41 日常巡视 内围排水沟",
            draft={"args": {"record_type": "待定"}, "missing": ["记录类型"]},
        )
        assert turn.record_type == "巡视"
        assert turn.fields["section"] == "内围排水沟"
        assert turn.fields["inspection_start_time"] == "16:41"
        assert turn.question.startswith("发现问题")
        assert [step["tool"] for step in turn.steps] == ["set_record_type", "fill_fields", "ask_user"]
        assert calls["n"] == 3

    asyncio.run(scenario())


def test_label_keys_and_chinese_time_are_accepted(monkeypatch):
    """模型常用中文标签而不是内部参数名，服务端按字段表解析，不要求它背名字。"""
    _scripted(monkeypatch, [
        {"thought": "用户说了巡视", "tool": "set_record_type", "args": {"record_type": "巡视"}},
        {"thought": "按标签填", "tool": "fill_fields", "args": {"fields": {
            "巡视的工程部位": "内围排水沟",
            "巡视开始时间（年 月 日 时）": "2026年11月27日16时41分",
        }}},
        {"thought": "结束", "tool": "done", "args": {}},
    ])

    async def scenario():
        turn = await agent_loop.run_draft_agent(
            None,
            message="2026/11/27 16:41 日常巡视 内围排水沟",
            draft={"args": {"record_type": "待定"}, "missing": ["记录类型"]},
        )
        assert turn.fields["section"] == "内围排水沟"
        assert turn.fields["inspection_start_time"] == "16:41"

    asyncio.run(scenario())


def test_server_rejects_unknown_type_and_field(monkeypatch):
    _scripted(monkeypatch, [
        {"thought": "猜一个不存在的类型", "tool": "set_record_type", "args": {"record_type": "随便"}},
        {"thought": "编一个字段", "tool": "fill_fields", "args": {"fields": {"不存在的字段": "x"}}},
        {"thought": "结束", "tool": "done", "args": {}},
    ])

    async def scenario():
        turn = await agent_loop.run_draft_agent(
            None,
            message="随便说点什么",
            draft={"args": {"record_type": "巡视"}, "missing": ["巡视的工程部位"]},
        )
        assert turn.record_type == "巡视"  # 非法类型没有覆盖原值
        assert turn.fields == {}           # 编造的字段没有被接收

    asyncio.run(scenario())
