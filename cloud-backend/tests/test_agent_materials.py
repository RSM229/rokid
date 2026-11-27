"""材料收集链路的纯逻辑测试（2026-11-27）。

不依赖数据库：只验证「按消息挑探针」「什么时候补第二轮」「材料块怎么分类渲染」
以及「思维链 → 工具调用 → 调用结果」事件是否按顺序发出。
"""
import asyncio
from types import SimpleNamespace

from app.services.agent_materials import (
    Materials,
    collect,
    needs_second_round,
    plan_probes,
)


def test_plan_probes_picks_sources_by_intent():
    lookup = plan_probes("这个验收规范怎么要求的？", has_attachments=True, has_pending=False)
    keys = [probe.key for probe in lookup]
    assert keys[:2] == ["conversation", "attachments"]
    assert "kb" in keys

    quick = plan_probes("记一条巡视：3号楼钢筋绑扎", has_attachments=False, has_pending=False)
    quick_keys = [probe.key for probe in quick]
    assert "kb" not in quick_keys, "纯记录动作不该去搜资料库"
    assert "records" not in quick_keys


def test_second_round_only_when_lookup_and_hits_are_thin():
    thin = Materials(buckets=[{"key": "kb", "count": 1}])
    enough = Materials(buckets=[{"key": "kb", "count": 3}])
    assert needs_second_round("上次那份验收规范怎么写的", thin) is True
    assert needs_second_round("上次那份验收规范怎么写的", enough) is False
    assert needs_second_round("记一条巡视", thin) is False


def test_collect_emits_chain_and_renders_buckets():
    async def scenario():
        events: list[dict] = []

        async def report(stage, status, title, detail):
            events.append(detail)

        materials = await collect(
            None,  # 这条消息只走「最近对话 + 记忆」，不查库
            SimpleNamespace(id=1, real_name="张三"),
            None,
            None,
            "记一条巡视：3号楼二层钢筋绑扎",
            earlier_messages=[
                {"role": "user", "content": "昨天验收了 3 号楼"},
                {"role": "assistant", "content": "好的，已记下"},
            ],
            project_memories=[SimpleNamespace(content="整改回复必须附前后对比照片")],
            personal_memories=[SimpleNamespace(rule="文书落款一律用全名")],
            report=report,
        )
        kinds = [event.get("chain_kind") for event in events]
        labels = [event.get("chain_label") for event in events]
        assert kinds[:2] == ["think", "tool"]
        assert kinds[-2:] == ["result", "think"]
        assert labels[0] == "思维链 1.1"
        assert labels[1] == "工具调用 1.1"
        assert "调用结果 1.1" in labels
        assert "最近对话" in materials.block
        assert "项目：整改回复必须附前后对比照片" in materials.block
        assert "个人：文书落款一律用全名" in materials.block
        assert materials.rounds == 1

    asyncio.run(scenario())
