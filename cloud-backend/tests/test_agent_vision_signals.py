"""agent_vision_signals：从聊天素材重建逐图结构化信号。

重点回归「否定句式不应误置标志」——这是早期版本把『未见举牌 / 无封样 /
未见施工』误判成「做了」的根因。
"""
import unittest

from app.services.agent_vision_signals import parse_material_signals
from app.services import multi_image_split as ms


def _one_block(n, wm_raw, obs):
    return (
        f"【图片 {n}】\n"
        f"【水印】拍摄时间：2026-10-19；地点：广州市黄埔区；项目：沙盛四路；"
        f"部位：w{n}；天气：晴；人员：甲；【水印识别原文】{wm_raw}\n"
        f"【图片内容观察】{obs}\n\n"
    )


class TestParseMaterialSignals(unittest.TestCase):

    def test_negation_not_mis_set(self):
        """否定句式不应置位对应标志。"""
        material = (
            _one_block(1, "隐蔽工程验收公示牌 箱涵钢筋绑扎",
                       "监理与施工共同举牌验收，背景为钢筋绑扎作业面")
            + _one_block(2, "水泥搅拌桩施工", "正在进行水泥搅拌桩成桩作业，未见举牌，无封样")
            + _one_block(3, "进场钢筋原材 待检", "钢筋原材进场堆放，未见施工，未见举牌")
            + _one_block(4, "深基坑开挖", "深基坑开挖作业，监理现场巡视，未见举牌")
        )
        sigs = parse_material_signals(material, 4)
        # 图1：举牌验收 + 施工中
        self.assertTrue(sigs[0].get("has_board"))
        self.assertTrue(sigs[0].get("in_progress"))
        # 图2：施工中但未见举牌、无封样 → 二者均不应置位
        self.assertFalse(sigs[1].get("has_board"))
        self.assertFalse(sigs[1].get("has_seal"))
        self.assertTrue(sigs[1].get("in_progress"))
        # 图3：材料进场、未见施工 → 不应 in_progress
        self.assertTrue(sigs[2].get("material_incoming"))
        self.assertFalse(sigs[2].get("in_progress"))
        self.assertFalse(sigs[2].get("has_board"))

    def test_empty_material_returns_empty(self):
        self.assertEqual(parse_material_signals("", 3), [])
        self.assertEqual(parse_material_signals("纯文字无标记", 2), [])

    def test_material_drives_multi_group(self):
        """material 重建信号后，group_images 应分出多组（不再是单条）。"""
        material = (
            _one_block(1, "隐蔽工程验收公示牌 箱涵钢筋绑扎", "共同举牌验收")
            + _one_block(2, "水泥搅拌桩施工", "成桩作业，未见举牌")
            + _one_block(3, "进场钢筋原材 待检", "原材进场堆放，未见施工，未见举牌")
        )
        sigs = parse_material_signals(material, 3)
        imgs = [f"https://cdn/x{i}.jpg" for i in range(1, 4)]
        groups = ms.group_images(imgs, sigs)
        # 至少分出游 2 组（不应全部归待定或单组）
        self.assertGreater(len(groups), 1)
        types = {g.record_type for g in groups}
        self.assertIn("验收", types)
        self.assertIn("见证", types)


if __name__ == "__main__":
    unittest.main()
