"""个性化（选择埋点 + browse + 加分）的单元测试。

三个东西必须稳住，否则「智能感」会变成「越用越窄」或「空面板」：

1. ``_personal_boost`` 有上限，永远压不过文本相关性（精确 100 分）
2. ``browse`` 在新用户（无埋点）时仍有兜底内容，不能返回空
3. ``record_mentions`` 幂等累加，且拒绝越权 kb_id
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.services import kb_search


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _QueuedSession:
    def __init__(self, *results):
        self._queue = list(results)
        self.commits = 0

    async def execute(self, _query):
        if not self._queue:
            raise AssertionError("execute 调用次数超出预期")
        return _Rows(self._queue.pop(0))

    async def commit(self):
        self.commits += 1

    def add(self, _obj):
        pass


class BoostTests(unittest.TestCase):
    def test_no_history_means_no_boost(self) -> None:
        self.assertEqual(kb_search._personal_boost({}, "folder", 11), 0)

    def test_hits_scale_up_to_cap(self) -> None:
        stats = {("folder", 11): {"hits": 3, "last": datetime.now() - timedelta(days=30)}}
        # 3 次 × 2 = 6，且已超出 7 天窗口，没有近期加分
        self.assertEqual(kb_search._personal_boost(stats, "folder", 11), 6)

    def test_recent_use_adds_extra_but_still_capped(self) -> None:
        stats = {("folder", 11): {"hits": 20, "last": datetime.now() - timedelta(days=2)}}
        boost = kb_search._personal_boost(stats, "folder", 11)
        # 20 次本应 40，封顶 15；再加近期 8
        self.assertEqual(boost, kb_search.BOOST_HITS_CAP + kb_search.BOOST_RECENT)

    def test_boost_never_overrides_exact_match(self) -> None:
        # 最极端的个性化加分 + 最低档文本分（子序列 55）仍应低于精确命中 100
        self.assertLess(
            kb_search.S_SUBSEQ + kb_search.BOOST_HITS_CAP + kb_search.BOOST_RECENT,
            kb_search.S_EXACT,
        )

    def test_old_use_gets_no_recency_bonus(self) -> None:
        stats = {("doc", 88): {"hits": 1, "last": datetime.now() - timedelta(days=30)}}
        self.assertEqual(kb_search._personal_boost(stats, "doc", 88), 2)


class ProjectBoostTests(unittest.TestCase):
    def test_same_project_kb_gets_boost(self) -> None:
        project_map = {3: 7, 4: None}
        self.assertEqual(kb_search._project_boost(project_map, 3, 7), kb_search.BOOST_PROJECT)

    def test_other_project_and_personal_kb_get_nothing(self) -> None:
        project_map = {3: 7, 4: None, 5: 8}
        self.assertEqual(kb_search._project_boost(project_map, 5, 7), 0)   # 别的项目
        self.assertEqual(kb_search._project_boost(project_map, 4, 7), 0)   # 个人库

    def test_no_current_project_means_no_boost(self) -> None:
        self.assertEqual(kb_search._project_boost({3: 7}, 3, None), 0)

    def test_project_boost_alone_cannot_overtake_next_tier(self) -> None:
        # 路径命中 70 +5 仍低于包含 90；子序列 55 +5 仍低于 65
        self.assertLess(kb_search.S_PATH_CONTAINS + kb_search.BOOST_PROJECT, kb_search.S_CONTAINS)
        self.assertLess(kb_search.S_SUBSEQ + kb_search.BOOST_PROJECT, kb_search.S_PATH_TOKENS)

class BrowseTests(unittest.IsolatedAsyncioTestCase):
    def _nodes(self):
        return {
            10: {"id": 10, "kb_id": 3, "parent_id": None, "name": "项目文件夹", "path": "项目文件夹"},
            11: {"id": 11, "kb_id": 3, "parent_id": 10, "name": "验收记录", "path": "项目文件夹/验收记录"},
            12: {"id": 12, "kb_id": 3, "parent_id": 10, "name": "监理日志", "path": "项目文件夹/监理日志"},
        }

    async def test_new_user_gets_folders_even_without_history(self) -> None:
        # 无埋点：doc 统计空 → folder 统计空 → 只剩 folders 兜底
        session = _QueuedSession([], [])
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]), \
             patch.object(kb_search, "_folder_path_map", return_value=self._nodes()), \
             patch.object(kb_search, "_folder_doc_counts", return_value={11: 2, 12: 5}), \
             patch.object(kb_search, "_kb_name_map", return_value={3: "我的库"}):
            result = await kb_search.browse(session, 1)
        self.assertEqual(result["mode"], "browse")
        self.assertEqual(result["recent"], [])
        # 「最常用」一段已移除：数据量小时它与 recent 重叠，会导致同一项出现两次
        self.assertNotIn("popular", result)
        # 有内容的文件夹先出，且按子树文档数倒序（监理日志 5 > 验收记录 2）
        self.assertEqual([f["name"] for f in result["folders"]][:2], ["监理日志", "验收记录"])
        # 根目录仍会补上，保证面板不空
        self.assertIn("项目文件夹", [f["name"] for f in result["folders"]])

    async def test_recent_prefers_latest_use_and_mixes_folder_with_doc(self) -> None:
        now = datetime.now()
        doc_stats = [(88, 3, 4, now - timedelta(hours=1))]          # 文档，1 小时前
        folder_stats = [(11, 3, 9, now - timedelta(days=3))]        # 文件夹，3 天前
        doc_rows = [(88, 3, "钢筋绑扎验收记录.docx", 11)]
        session = _QueuedSession(doc_stats, doc_rows, folder_stats)
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]), \
             patch.object(kb_search, "_folder_path_map", return_value=self._nodes()), \
             patch.object(kb_search, "_folder_doc_counts", return_value={11: 2, 12: 5}), \
             patch.object(kb_search, "_kb_name_map", return_value={3: "我的库"}):
            result = await kb_search.browse(session, 1)
        self.assertEqual([x["kind"] for x in result["recent"]], ["doc", "folder"])
        self.assertEqual(result["recent"][0]["path"], "项目文件夹/验收记录")
        # 已出现在 recent 的文件夹必须从候选里剔除，否则同一项会出现两次
        recent_keys = {(x["kind"], x["id"]) for x in result["recent"]}
        folder_keys = {(x["kind"], x["id"]) for x in result["folders"]}
        self.assertFalse(recent_keys & folder_keys, "recent 与 folders 不能重复")
        self.assertNotIn(11, [f["id"] for f in result["folders"]])


class RecordMentionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_row_when_absent(self) -> None:
        session = _QueuedSession([], [])   # 两次 select → 都没有
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]):
            written = await kb_search.record_mentions(
                session, 1,
                [
                    {"kb_id": 3, "target_type": "folder", "target_id": 11},
                    {"kb_id": 3, "target_type": "doc", "target_id": 88},
                ],
            )
        self.assertEqual(written, 2)
        self.assertEqual(session.commits, 1)

    async def test_increments_existing_hits(self) -> None:
        existing = type("Row", (), {"hits": 4, "last_used_at": None})()
        session = _QueuedSession([existing])
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]):
            written = await kb_search.record_mentions(
                session, 1, [{"kb_id": 3, "target_type": "folder", "target_id": 11}],
            )
        self.assertEqual(written, 1)
        self.assertEqual(existing.hits, 5)

    async def test_accepts_pydantic_objects_not_only_dicts(self) -> None:
        # 路由层传进来的是模型对象；只认 dict 会把所有上报静默丢掉（线上踩过）
        from app.routers.knowledge_base import MentionStatItem

        session = _QueuedSession([])
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]):
            written = await kb_search.record_mentions(
                session, 1,
                [MentionStatItem(kb_id=3, target_type="folder", target_id=11)],
            )
        self.assertEqual(written, 1)
    async def test_rejects_foreign_kb_and_bad_type(self) -> None:
        session = _QueuedSession()
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]):
            written = await kb_search.record_mentions(
                session, 1,
                [
                    {"kb_id": 999, "target_type": "folder", "target_id": 11},   # 越权
                    {"kb_id": 3, "target_type": "query", "target_id": 1},        # 类型不支持
                    {"kb_id": "x", "target_type": "doc", "target_id": 2},        # 非法
                ],
            )
        self.assertEqual(written, 0)
        self.assertEqual(session.commits, 0)


if __name__ == "__main__":
    unittest.main()
