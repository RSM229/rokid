"""验证：建记录意图确定性兜底（user 34 现场消息 127-134 复刻）。

正例：口述命中建记录句式 + 模型判 chat（文档带偏）→ 注入 record_work、
      intent 归位 create_record。
负例：叙述性长日报（generate_document 场景）、模型已选写动作（不抢）、
      普通问答（不误注入）。
"""
import sys

sys.path.insert(0, ".")

from app.services import agent_request_plan as arp


def plan_for(message, model_actions, model_intent, attachment_count=1):
    """模拟语义计划器输出：模型被文档带偏判 chat、没选写动作。"""
    candidate = {
        "intent": model_intent,
        "actions": [{"id": a, "confidence": 0.9, "reason": "模型输出"} for a in model_actions],
        "record_type": None,
        "record_type_confidence": 0.0,
        "facts": [],
    }
    return arp.normalize_candidate(
        candidate,
        message=message,
        attachment_count=attachment_count,
        evidence_count=attachment_count,
    )


def action_ids(plan):
    return [a["id"] for a in plan.get("actions") or []]


def main():
    # ---- 1. user 34 现场复刻：「记录今天的工作」+ 文档附件 + 模型判 chat ----
    p1 = plan_for("记录今天的工作", ["chat"], "chat")
    ids1 = action_ids(p1)
    print("1. chat+文档+建记录口述:", ids1, "| intent:", p1.get("intent"))
    assert "record_work" in ids1, "应注入 record_work"
    assert p1.get("intent") == "create_record", "intent 应归位 create_record"
    # 类型不明 → clarification 一次问一栏（不是模型自由追问）
    clar = [c.get("field") for c in (p1.get("clarifications") or [])]
    assert "record_type" in clar, "应触发 record_type 澄清: " + str(clar)

    # ---- 2. 第二轮原话重复（130 轮现场）同样被救回 ----
    p2 = plan_for("记录今天的工作", ["chat"], "chat")
    assert "record_work" in action_ids(p2)

    # ---- 3. 不误伤：模型已选 generate_document（叙述性日报场景）不抢 ----
    p3 = plan_for(
        "帮我记录11月2日监理日报：w27沉井设备调试安装5人；k20至360路段水稳层弯沉试验",
        ["generate_document"], "generate_document")
    ids3 = action_ids(p3)
    print("3. 日报场景不抢:", ids3)
    assert "record_work" not in ids3, "generate_document 在场时不能注入"

    # ---- 4. 不误伤：模型已选 record_work 时不重复注入 ----
    p4 = plan_for("记一条巡视", ["record_work"], "create_record")
    ids4 = action_ids(p4)
    assert ids4.count("record_work") == 1, "不能重复注入"

    # ---- 5. 不误伤：普通问答（无建记录口述）不注入 ----
    p5 = plan_for("什么是旁站监理", ["chat"], "chat")
    ids5 = action_ids(p5)
    print("5. 普通问答:", ids5)
    assert "record_work" not in ids5

    # ---- 6. 不误伤：长句叙述（>60 字，走 generate_document 的叙述体） ----
    long_msg = ("今天上午对3号楼二层钢筋绑扎进行了巡视检查，钢筋间距符合设计要求，"
                "保护层垫块布置均匀，发现局部绑扎不牢固已要求整改，下午继续跟踪复查，"
                "同时旁站了混凝土浇筑全过程，坍落度实测值合格，振捣密实无漏振")
    p6 = plan_for(long_msg, ["chat"], "chat")
    ids6 = action_ids(p6)
    print("6. 长叙述不硬拦:", ids6)
    # 长句不该由关键词兜底硬注入（语义模型该判 generate_document/record_work）
    assert "record_work" not in ids6

    # ---- 7. 派单 + 建记录复合句：dispatch 兜底与本兜底可共存 ----
    p7 = plan_for("记录一下今天的工作，另外让张三去检查临边防护", ["chat"], "chat")
    ids7 = action_ids(p7)
    print("7. 复合句:", ids7)
    assert "dispatch_task" in ids7 and "record_work" in ids7

    # ---- 8. 变体口述 ----
    for msg in ("记一条", "记一下今天的工序", "登记今天的工作", "今天的工作"):
        p = plan_for(msg, ["chat"], "chat")
        ids = action_ids(p)
        print(f"8. 变体「{msg}」:", ids)
        assert "record_work" in ids, f"变体「{msg}」应命中"

    # ---- 9. 隐患描述不误注（「记录」不在句式里） ----
    p9 = plan_for("楼面积水还有碎石，有滑倒风险", ["chat"], "chat")
    assert "record_work" not in action_ids(p9), "隐患描述不该注入 record_work"

    print("\nALL PASS")


if __name__ == "__main__":
    main()
