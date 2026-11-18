"""端到端验证：验收「一起询问」防重问修复（_acceptance_asked 跨轮标记）。

模拟用户多轮对话，用同一个 draft dict 充当草稿跨轮持久化载体，断言：
  轮1 带出询问块 → 写入 _acceptance_ask_state + _acceptance_asked
  轮2 「跳过，直接创建」→ 消费 ask_state（清空），保留 _acceptance_asked
  轮3 「全都符合」→ 不再重挂 _acceptance_ask_state（修复点）

对照：抹掉 _acceptance_asked 并屏蔽守卫（模拟旧逻辑）→ 轮3 会重挂（证明修复必要）。

运行：cd cloud-backend && .venv/Scripts/python.exe tmp/verify_acceptance_no_repeat.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock, patch

# 让 strong_match 稳定命中同一 (分部,工序)，entry 带结构化询问式要点
STRONG = {"div": "房建分部", "item": "钢筋绑扎", "points": "x"}
ENTRY = {
    "div": "房建分部",
    "item": "钢筋绑扎",
    "分部工程": "房建分部",
    "关键工序_关键部位": "钢筋绑扎",
    "验收要点_询问式": ["要点1", "要点2", "要点3", "要点4"],
}


class FakeCtx:
    def __init__(self, message):
        self.message = message
        self.material = None
        self.project = None
        self.project_id = 1
        self.db = AsyncMock()
        self.user = MagicMock()
        self.user.has_permission = MagicMock(return_value=True)
        self.user.id = 1


async def one_round(message, draft, extra_patches=None):
    from app.services import agent_tools

    ctx = FakeCtx(message)
    patches = [
        patch.object(agent_tools.acceptance_checklists, "strong_match", return_value=STRONG),
        patch.object(agent_tools, "_aat_find_entry", return_value=ENTRY),
        patch.object(agent_tools, "_infer_acceptance_type", return_value=""),
        patch.object(agent_tools, "_acceptance_type_from_entry", return_value=""),
    ]
    for p in patches:
        p.start()
    try:
        if extra_patches:
            for p in extra_patches:
                p.start()
        return await agent_tools._create_work_record(ctx, draft)
    finally:
        for p in (extra_patches or []):
            p.stop()
        for p in patches:
            p.stop()


def main():
    async def run():
        # ---- 修复后行为 ----
        draft = {"record_type": "验收"}
        await one_round("我要做钢筋绑扎验收", draft)
        assert draft.get("_acceptance_ask_state") == {"div": "房建分部", "item": "钢筋绑扎"}, draft
        assert draft.get("_acceptance_asked") == {"div": "房建分部", "item": "钢筋绑扎"}, draft
        print("轮1  带出询问块 + 写入 _acceptance_asked                    ✓")

        await one_round("跳过，直接创建", draft)
        assert "_acceptance_ask_state" not in draft, draft
        assert draft.get("_acceptance_asked") == {"div": "房建分部", "item": "钢筋绑扎"}, draft
        print("轮2  跳过消费 _acceptance_ask_state，_acceptance_asked 保留  ✓")

        await one_round("全都符合", draft)
        assert "_acceptance_ask_state" not in draft, "修复失效：轮3 又重挂了询问块！draft=%s" % draft
        print("轮3  「全都符合」不再重挂询问块（修复点）                  ✓")

        # ---- 对照：旧逻辑（无 _acceptance_asked 守卫）会重问 ----
        draft2 = {"record_type": "验收"}
        await one_round("我要做钢筋绑扎验收", draft2)
        draft2.pop("_acceptance_asked", None)  # 旧代码不写 asked
        await one_round("跳过，直接创建", draft2)
        # 屏蔽守卫，模拟旧分支（命中即重挂）
        guard_off = patch.object(agent_tools_guard(), "_aat_already_asked", return_value=False)
        await one_round("全都符合", draft2, extra_patches=[guard_off])
        assert draft2.get("_acceptance_ask_state") is not None, "对照应重问但未重问"
        print("对照  旧逻辑下轮3 重挂了询问块（证明修复必要）              ✓")

    asyncio.run(run())
    print("\nALL PASS ✅ 验收防重问修复验证通过")


def agent_tools_guard():
    from app.services import agent_tools
    return agent_tools


if __name__ == "__main__":
    main()
