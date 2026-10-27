# -*- coding: utf-8 -*-
"""省统表聊天建表的「系统字段预填」回归。

跑法：cd cloud-backend && python -m unittest tests.test_provform_prefill -v

🔴 背景（2026-10-27，方案 A）
--------------------------
安全监理日志 anquan_jianli_rizhi 把「日期/星期/天气」列为必填，用户即使说全了
施工内容/安全检查/处理措施/隐患整改/危大状况 5 个安全栏目，仍会被 validate_values
打回要求补这 3 项 —— 而它们系统本可推导。方案 A：聊天建表时自动预填，把用户口述
字段从 8 个降到 5 个（纯安全栏目）。

三条底线（天气尤其要守）：
  1) 只在项目配了经纬度时才尝试联网取天气；
  2) 取不到真实天气就**留空**，绝不塞预报值/默认值冒充实况（法定文书，错填有合规风险）；
  3) 天气联网失败/超时**不阻断建表**（退回不预填天气）。
另：用户已口述的日期/天气必须被尊重，不被系统值覆盖。
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import app.models  # noqa: E402

for _mod in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module("app.models." + _mod.name)

from app.services import agent_tools as m  # noqa: E402
from app.services.provform_schema import validate_values  # noqa: E402

ANQUAN = "anquan_jianli_rizhi"
# 安全监理日志的 5 个安全栏目（canonical: construction_progress/supervision_section/
# handling/problems_section/hazard_status → schema key 11_05..11_09）
SAFETY_5 = {
    "11_05": "主体三层施工", "11_06": "临边防护到位",
    "11_07": "无", "11_08": "隐患已闭环", "11_09": "危大工程受控",
}


class PrefillDateWeekdayTests(unittest.TestCase):
    def test_prefills_date_and_weekday_when_user_omits_them(self):
        out = m.prefill_provform_date_weekday(ANQUAN, dict(SAFETY_5))
        self.assertTrue(out.get("11_02"), "日期未预填")
        self.assertTrue(out.get("11_03"), "星期未预填")
        # 星期与日期自洽
        wd = m._PROVFORM_WEEKDAY_CN[date.fromisoformat(out["11_02"]).weekday()]
        self.assertEqual(out["11_03"], wd)
        # 用户原字段不被破坏
        for k, v in SAFETY_5.items():
            self.assertEqual(out[k], v)

    def test_respects_user_given_date_and_derives_weekday_from_it(self):
        """用户口述了日期 → 用用户的日期，星期按它算（2026-10-25 是周日）。"""
        out = m.prefill_provform_date_weekday(ANQUAN, {"11_02": "2026-10-25", **SAFETY_5})
        self.assertEqual(out["11_02"], "2026-10-25")
        self.assertEqual(out["11_03"], "日")

    def test_respects_user_given_weekday(self):
        out = m.prefill_provform_date_weekday(ANQUAN, {"11_03": "六", **SAFETY_5})
        self.assertEqual(out["11_03"], "六", "用户给的星期被覆盖了")

    def test_review_date_not_touched(self):
        """审阅日期 11_11 canonical=review_date，不是 date，绝不该被预填成今天。"""
        out = m.prefill_provform_date_weekday(ANQUAN, dict(SAFETY_5))
        self.assertNotIn("11_11", out)

    def test_inspection_record_not_prefilled(self):
        """巡视记录日期字段是 start_time/end_time（含时分），不在预填范围。"""
        out = m.prefill_provform_date_weekday("gd_b_214_xunshi", {})
        from app.services.provform_schema import canonical_key
        for k in out:
            self.assertNotIn(
                canonical_key("gd_b_214_xunshi", k), ("start_time", "end_time"),
                f"巡视记录的 {k}(start/end_time) 不该被预填",
            )

    def test_after_prefill_only_weather_remains_required(self):
        """日期星期预填后，validate 应只剩「天气」一项缺失（天气走异步单独预填）。"""
        out = m.prefill_provform_date_weekday(ANQUAN, dict(SAFETY_5))
        chk = validate_values(ANQUAN, out)
        missing = [e["label"] for e in chk.get("errors", [])]
        self.assertEqual(missing, ["天气（上午/下午/夜间）"], f"预填后仍缺：{missing}")


class PrefillWeatherTests(unittest.IsolatedAsyncioTestCase):
    def _ctx(self, lat=None, lon=None):
        proj = SimpleNamespace(name="回归项目", latitude=lat, longitude=lon)
        return SimpleNamespace(project=proj, project_id=1, user=None, db=None)

    async def test_fills_weather_when_project_has_coords(self):
        """项目有经纬度 + 拿到真实天气 → 预填。"""
        ctx = self._ctx(lat="23.1", lon="113.2")
        fake = {"weather": "晴", "temp_high": "30", "temp_low": "22",
                "weather_source": "open-meteo", "weather_missing": []}
        with patch("app.services.weather.daily_weather", new=AsyncMock(return_value=fake)):
            out = await m.prefill_provform_weather(ctx, ANQUAN, dict(SAFETY_5))
        self.assertEqual(out.get("11_04"), "晴")

    async def test_leaves_blank_when_weather_unavailable(self):
        """底线2：取不到真实天气（降级/待补填）→ 留空，绝不塞默认值。"""
        ctx = self._ctx(lat="23.1", lon="113.2")
        fake = {"weather": "", "temp_high": "", "temp_low": "",
                "weather_source": "待补填", "weather_missing": ["weather"]}
        with patch("app.services.weather.daily_weather", new=AsyncMock(return_value=fake)):
            out = await m.prefill_provform_weather(ctx, ANQUAN, dict(SAFETY_5))
        self.assertFalse(str(out.get("11_04") or "").strip(), "取不到天气时应留空，不塞假值")

    async def test_blank_weather_source_weibuti_not_filled(self):
        """底线2 强化：即便 weather 有值但 source=待补填（占位），也不当实况填。"""
        ctx = self._ctx(lat="23.1", lon="113.2")
        fake = {"weather": "多云", "weather_source": "待补填", "weather_missing": []}
        with patch("app.services.weather.daily_weather", new=AsyncMock(return_value=fake)):
            out = await m.prefill_provform_weather(ctx, ANQUAN, dict(SAFETY_5))
        self.assertFalse(str(out.get("11_04") or "").strip(), "source=待补填 时不该填天气")

    async def test_network_failure_does_not_raise(self):
        """底线3：daily_weather 抛异常 → 不冒泡，退回不预填天气。"""
        ctx = self._ctx(lat="23.1", lon="113.2")
        with patch("app.services.weather.daily_weather",
                   new=AsyncMock(side_effect=RuntimeError("网络超时"))):
            out = await m.prefill_provform_weather(ctx, ANQUAN, dict(SAFETY_5))
        self.assertFalse(str(out.get("11_04") or "").strip())
        for k, v in SAFETY_5.items():
            self.assertEqual(out[k], v, "天气失败不该动其它字段")

    async def test_respects_user_given_weather(self):
        """用户口述了天气 → 尊重，不联网覆盖。"""
        ctx = self._ctx(lat="23.1", lon="113.2")
        spy = AsyncMock(return_value={"weather": "晴", "weather_source": "x"})
        with patch("app.services.weather.daily_weather", new=spy):
            out = await m.prefill_provform_weather(ctx, ANQUAN, {"11_04": "暴雨", **SAFETY_5})
        self.assertEqual(out.get("11_04"), "暴雨")
        spy.assert_not_awaited()   # 用户已给天气就不该发起联网

    async def test_no_coords_still_safe(self):
        """底线1：项目没经纬度 → daily_weather 内部返回空，天气留空，不报错。"""
        ctx = self._ctx(lat=None, lon=None)
        fake = {"weather": "", "weather_source": "待补填", "weather_missing": ["weather"]}
        with patch("app.services.weather.daily_weather", new=AsyncMock(return_value=fake)):
            out = await m.prefill_provform_weather(ctx, ANQUAN, dict(SAFETY_5))
        self.assertFalse(str(out.get("11_04") or "").strip())

    async def test_form_without_weather_field_untouched(self):
        """没有天气字段的表（如材料台账）→ 原样返回，不报错。"""
        ctx = self._ctx(lat="23.1", lon="113.2")
        out = await m.prefill_provform_weather(ctx, "gd_b_219_cailiao", {"x": "y"})
        self.assertEqual(out, {"x": "y"})


class _FakeRow:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeDb:
    """最小异步 db 替身：够走完 _create_provform_doc 的 add/flush/commit/refresh。"""
    def __init__(self):
        self.added = []
        self._seq = 0

    def add(self, obj):
        self._seq += 1
        if getattr(obj, "id", None) is None:
            obj.id = self._seq
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    async def get(self, model, pk):
        return next((o for o in self.added if isinstance(o, model) and o.id == pk), None)


class CreateProvformEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """端到端：验证「预填增强」真的让用户少口述字段就能建成文书。"""

    async def test_safety_log_builds_with_only_business_fields(self):
        """🔴 方案 A 的验收标准：用户只说 5 个安全栏目（+天气），
        日期/星期由系统预填 → 文书成功创建，content 里日期星期天气齐全。
        """
        from app.models.document import Document

        db = _FakeDb()
        ctx = SimpleNamespace(
            db=db, user=SimpleNamespace(id=7, username="u7"),
            project_id=7, project=SimpleNamespace(name="回归项目", latitude=None, longitude=None),
            message="",
        )
        # 项目无经纬度 → 天气预填留空；故用户仍需口述天气（这是诚实的边界）
        user_args = {
            "form_code": "安全监理日志",   # 用中文表名，验证 resolve + 预填全链路
            "fields": {**SAFETY_5, "11_04": "晴"},
        }
        with patch.object(m, "_audit", new=AsyncMock(return_value=None)):
            res = await m._create_provform_doc(ctx, user_args)

        self.assertTrue(res.get("ok"), f"建表失败：{res.get('reply')}")
        doc = next(o for o in db.added if isinstance(o, Document))
        self.assertEqual(doc.form_code, ANQUAN)
        # 系统预填的日期/星期必须落到 content
        self.assertTrue(doc.content.get("date"), "日期未预填进 content")
        self.assertTrue(doc.content.get("weekday"), "星期未预填进 content")
        self.assertEqual(doc.content.get("weather"), "晴")
        self.assertEqual(doc.content.get("construction_progress"), SAFETY_5["11_05"])
        self.assertEqual(doc.content.get("project_name"), "回归项目")

    async def test_without_prefill_it_would_have_been_rejected(self):
        """反向对照：同样只给 5 安全栏目、不给日期星期，
        若绕过预填直接 validate，会被打回（证明预填确实解决了问题）。
        """
        chk = validate_values(ANQUAN, dict(SAFETY_5))
        self.assertFalse(chk["ok"], "不给日期/星期/天气时本就该校验失败")
        missing = {e["label"] for e in chk.get("errors", [])}
        self.assertIn("日期", missing)
        self.assertIn("星期", missing)

    async def test_weather_prefilled_end_to_end_when_coords_present(self):
        """项目有经纬度 + 天气服务返回值 → 用户连天气都不用说，全预填成功建表。"""
        from app.models.document import Document

        db = _FakeDb()
        ctx = SimpleNamespace(
            db=db, user=SimpleNamespace(id=7, username="u7"),
            project_id=7, project=SimpleNamespace(name="回归项目", latitude="23.1", longitude="113.2"),
            message="",
        )
        fake_wx = {"weather": "多云", "temp_high": "30", "temp_low": "22",
                   "weather_source": "open-meteo", "weather_missing": []}
        with patch.object(m, "_audit", new=AsyncMock(return_value=None)), \
             patch("app.services.weather.daily_weather", new=AsyncMock(return_value=fake_wx)):
            res = await m._create_provform_doc(ctx, {"form_code": "anquan_jianli_rizhi",
                                                     "fields": dict(SAFETY_5)})
        self.assertTrue(res.get("ok"), f"建表失败：{res.get('reply')}")
        doc = next(o for o in db.added if isinstance(o, Document))
        self.assertEqual(doc.content.get("weather"), "多云", "天气未端到端预填")
        self.assertTrue(doc.content.get("date") and doc.content.get("weekday"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
