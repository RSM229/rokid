"""agent_vision_signals：从聊天素材重建逐图结构化信号。

重点回归「否定句式不应误置标志」——这是早期版本把『未见举牌 / 无封样 /
未见施工』误判成「做了」的根因。
"""
import unittest

from app.services.agent_vision_signals import parse_material_signals, watermark_lines
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


# ---------------------------------------------------------------- 真机批次回归
# 2026-11-12 12:38 生产那批 8 张照片，水印文字取自同批图 10:49 的完整逐张复述。
# 只有第 6 张原文含「牌面」，其余验收图没有举牌关键词 —— 这正是当时被分成
# 「巡视2 / 旁站5 / 验收1」的原因。
REAL_BATCH = [
    ("2026-10-27 16:07", "建材纵一路", "K1+315-K1+390段", "水泥搅拌桩施工", "多云30℃"),
    ("2026-10-27 15:59", "大兴路", "dxw9-10段污水管道", "钢板桩支护安装", "多云30℃"),
    ("2026-10-27 16:47", "沙盛四路", "ssy37-38雨水管道", "基础混凝土浇筑", "多云30℃"),
    ("2026-10-27 15:37", "沙盛四路", "K0+500-K0+550段", "给水管道安装", "多云30℃"),
    ("2026-10-27 09:55", "大兴路", "dxy5-6段雨水箱涵", "底板钢筋模板验收", "晴27℃"),
    ("2026-10-27 09:30", "大兴路", "dxw7-9段污水管道", "9m钢板桩收方，牌面显示隐蔽工程验收", "晴26℃"),
    ("2026-10-27 09:26", "大兴路", "dxw7-9段污水管道", "9m钢板桩收方", "晴26℃"),
    ("2026-10-27 15:18", "大兴路", "dxy5-6段雨水箱涵", "顶板钢筋模板验收", "多云31℃"),
]


def _real_material() -> str:
    blocks = []
    for i, (t, loc, sec, content, weather) in enumerate(REAL_BATCH, 1):
        wm = f"【水印】拍摄时间：{t}；地点：{loc}；部位：{sec}；天气：{weather}"
        raw = (f"【水印识别原文】工程名称：沙盛四路等四条道路工程 施工单位：中铁十二局 "
               f"施工内容：{content} 拍摄时间：{t}")
        blocks.append(f"【图片 {i}】\n{wm}；{raw}\n【图片内容观察】现场作业留影")
    return "\n\n".join(blocks)


class TestRealBatchClassification(unittest.TestCase):

    def test_eight_photos_split_correctly(self):
        """校准后应为 巡视1 / 旁站4 / 验收3，而不是 巡视2 / 旁站5 / 验收1。"""
        sigs = parse_material_signals(_real_material(), 8)
        imgs = [f"https://cdn/p{i}.jpg" for i in range(1, 9)]
        groups = ms.group_images(imgs, sigs)
        self.assertEqual([(g.record_type, len(g.images)) for g in groups],
                         [("巡视", 1), ("旁站", 4), ("验收", 3)])


class TestWatermarkLines(unittest.TestCase):

    def test_one_line_per_image(self):
        lines = watermark_lines(_real_material(), 8)
        self.assertEqual(len(lines), 8)
        for i, line in enumerate(lines, 1):
            self.assertTrue(line.startswith(f"图片{i}："))
            # 新格式：每张图多行，字段「名：值」各占一行
            self.assertIn("：", line)
        # 逐行带字段名：图片1 至少含 拍摄时间/施工内容/天气 三类标签
        self.assertIn("拍摄时间：", lines[0])
        self.assertIn("施工内容：", lines[0])
        self.assertIn("天气：", lines[0])
        self.assertIn("2026-10-27 16:07", lines[0])
        self.assertIn("水泥搅拌桩施工", lines[0])
        self.assertIn("多云30℃", lines[0])
        self.assertIn("9m钢板桩收方", lines[5])

    def test_degrades_gracefully(self):
        self.assertEqual(watermark_lines("", 3), [])
        self.assertEqual(watermark_lines("纯文字无标记", 3), [])
        self.assertEqual(watermark_lines(_one_block(1, "施工内容：开挖", "观察"), 2)[1],
                         "图片2：未读到水印。")


if __name__ == "__main__":
    unittest.main()
