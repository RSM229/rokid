# -*- coding: utf-8 -*-
"""create_provform_doc 表式名解析回归。

跑法：cd cloud-backend && python -m unittest tests.test_provform_resolve -v

🔴 为什么要这个文件
------------------
2026-10-27 发现：原实现「按 PROVFORM_CHOICES 顺序做子串匹配 + break」会把
**长名误配到短名**，而且是**静默建错文书**（不是追问，用户拿到一张错的表）：

    '安全监理日志'                    → gd_b_220_rizhi（监理日志）      ✗ 应为 anquan_jianli_rizhi
    '危险性较大的分部分项工程…记录表'   → gd_b_214_xunshi（巡视记录）     ✗ 应为 gdaq_30306_weida

根因：`gd_b_220_rizhi` 在清单里排在 `anquan_jianli_rizhi` 之前，
而「监理日志」是「安全监理日志」的子串 → 先命中就 break。
同时 registry 官方名/常用简称（监理通知单、巡视记录、旁站记录、
材料/构配件/设备进场台账）**全部未识别**，只会追问。

修复后 `resolve_provform_form_code` 三级匹配且不依赖清单顺序：
  1) 精确等于 code / CHOICES 名 / registry 名 / 表号
  2) 候选名 ⊂ 用户话 → 取最长（「安全监理日志」6字 胜 「监理日志」4字）
  3) 用户话 ⊂ 候选名 → 取最短
"""
from __future__ import annotations

import unittest


class ResolveProvformFormCodeTests(unittest.TestCase):
    # (用户/模型说法, 期望 form_code)
    CASES = [
        # --- 🔴 原实现会静默建错文书的两条（最高优先级）---
        ("安全监理日志", "anquan_jianli_rizhi"),
        ("危险性较大的分部分项工程监理巡视检查记录表", "gdaq_30306_weida"),

        # --- 不能被长名抢走的短名 ---
        ("监理日志", "gd_b_220_rizhi"),
        ("巡视检查记录", "gd_b_214_xunshi"),
        ("危大工程巡视记录", "gdaq_30306_weida"),
        ("旁站监理记录", "gd_b_212_pangzhan"),

        # --- 🔴 原实现完全未识别的 registry 官方名 / 常用简称 ---
        ("巡视记录", "gd_b_214_xunshi"),
        ("旁站记录", "gd_b_212_pangzhan"),
        ("监理通知单", "gd_b_215_tongzhi"),
        ("监理通知单（质量类）", "gd_b_215_tongzhi"),
        ("材料/构配件/设备进场台账", "gd_b_219_cailiao"),

        # --- 本来就该工作的（防回归）---
        ("监理工程师通知单", "gd_b_215_tongzhi"),
        ("材料台账", "gd_b_219_cailiao"),
        ("验收记录", "acceptance_record"),
        ("平行检验记录", "gd_b_213_pingxing"),
        ("监理月报", "gd_b_221_yuebao"),

        # --- 直接给 code / 表号 ---
        ("gd_b_220_rizhi", "gd_b_220_rizhi"),
        ("gd_b_215_tongzhi", "gd_b_215_tongzhi"),
        ("GD-B-215", "gd_b_215_tongzhi"),
    ]

    NEGATIVE = ["乱七八糟不存在的表", "", None, "   ", "GDAQ30399"]

    def test_resolves_every_known_wording(self):
        from app.services.agent_tools import resolve_provform_form_code as R

        bad = []
        for say, want in self.CASES:
            got = R(say)
            if got != want:
                bad.append(f"{say!r} → {got!r}（期望 {want!r}）")
        self.assertEqual(bad, [], "表式名解析不符：\n  " + "\n".join(bad))

    def test_unresolvable_returns_none(self):
        from app.services.agent_tools import resolve_provform_form_code as R

        for say in self.NEGATIVE:
            self.assertIsNone(R(say), f"{say!r} 不该被解析成任何表式")

    def test_resolution_does_not_depend_on_choice_order(self):
        """🔴 关键性质：把清单顺序打乱，解析结果必须不变。

        原实现的 bug 正是「依赖顺序 + break」；这条测试锁死「不依赖顺序」。
        """
        import app.services.agent_tools as m

        original = m.PROVFORM_CHOICES
        try:
            m.PROVFORM_CHOICES = tuple(reversed(original))
            for say, want in self.CASES:
                self.assertEqual(
                    m.resolve_provform_form_code(say), want,
                    f"清单反序后 {say!r} 解析变了（说明仍依赖顺序）",
                )
        finally:
            m.PROVFORM_CHOICES = original

    def test_every_choice_is_reachable_by_its_own_name(self):
        """清单里每个表式，用它自己的名字必须能解析回自己（不漏项）。"""
        import app.services.agent_tools as m

        for code, name in m.PROVFORM_CHOICES:
            self.assertEqual(
                m.resolve_provform_form_code(name), code,
                f"{name!r} 解析不回 {code}",
            )
            self.assertEqual(m.resolve_provform_form_code(code), code)

    # 不填字段表单、改由 generate_supervision_log 按日聚合个人记录生成的表式。
    # form_registry 里 source="aggregate"。jianli_riji 2026-11-19 补齐 schema
    # （复用 GD-B-220 栏目）后移出豁免。
    AGGREGATE_WITHOUT_SCHEMA = set()

    def test_all_choices_have_schema(self):
        """desc 承诺可聊天创建的表式，必须真有 schema，否则用户会撞「暂不支持」。"""
        import app.services.agent_tools as m
        from app.services import provform_schema as ps
        from app.services.form_registry import FORM_BY_CODE

        # 豁免必须名副其实：确实是 aggregate 类，且确实还没有 schema。
        # 防止有人拿这条豁免当万能挡箭牌，或表式改成走表单后仍被跳过。
        for code in self.AGGREGATE_WITHOUT_SCHEMA:
            spec = FORM_BY_CODE.get(code)
            self.assertIsNotNone(spec, f"{code} 不在注册表里，豁免理由不成立")
            self.assertEqual(spec.source, "aggregate", f"{code} 已不是聚合生成")
        self.assertEqual(
            [c for c in self.AGGREGATE_WITHOUT_SCHEMA if ps.get_schema(c)], [],
            "这些表式已有 schema，不该继续豁免",
        )

        missing = [
            c for c, _ in m.PROVFORM_CHOICES
            if c not in self.AGGREGATE_WITHOUT_SCHEMA and not ps.get_schema(c)
        ]
        self.assertEqual(missing, [], f"这些表式无 schema，聊天创建会失败：{missing}")

    def test_user_visible_count_matches_choices(self):
        """🔴 用户可见提示里的类数必须动态取自清单（原硬编码「11 类」实为 10 类）。

        只看**可执行代码**：注释里解释历史成因时提到「等 11 类」是必要文档，
        不该被这条守门误伤。清单现已扩到 11 项，所以「等 11 类」本身没错，
        错的是任何写死的数字。
        """
        import app.services.agent_tools as m
        from pathlib import Path

        src = (Path(m.__file__)).read_text(encoding="utf-8")
        code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
        hardcoded = [ln.strip() for ln in code_lines if "等 11 类" in ln or "等 10 类" in ln]
        self.assertEqual(
            hardcoded, [],
            f"可执行代码里仍有硬编码类数（清单增删后会与事实脱钩）：{hardcoded}",
        )
        self.assertIn("len(PROVFORM_CHOICES)", src, "提示语应动态取 len(PROVFORM_CHOICES)")

    def test_need_more_prompt_shows_real_count(self):
        """🔴 端到端：不传 form_code 时，追问文案里的类数必须等于清单真实长度。

        旧断言额外 assertNotIn("等 11 类") 是当年「文案写死 11、清单只有 10」
        的临时守门；清单扩到 11 项后文案正确了，反而把这条测试自己弄红。
        现在改成提取文案里出现的每一个类数，逐一与清单比对。
        """
        import asyncio
        import re

        import app.services.agent_tools as m

        res = asyncio.run(m._create_provform_doc(_NullCtx(), {"form_code": ""}))
        # _need_more 的文案在 reply 字段
        text = str(res.get("reply") or "")
        self.assertTrue(text, f"追问文案为空：{res}")
        self.assertIn("表式", str(res.get("need_more")), "该追问「表式」这一项")
        expect = str(len(m.PROVFORM_CHOICES))
        counts = re.findall(r"等\s*(\d+)\s*类", text)
        self.assertTrue(counts, f"追问文案没给出类数：{text[:160]}")
        self.assertEqual(
            set(counts), {expect},
            f"文案类数与清单真实长度 {expect} 不一致：{text[:160]}",
        )


class _NullCtx:
    """空 ctx：本用例只走「没给 form_code → 追问」这条早退路径，用不到 db/user。"""

    project_id = 0
    user = None
    project = None
    db = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
