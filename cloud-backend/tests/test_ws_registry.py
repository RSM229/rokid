"""WebSocket 连接池注册/注销的回归测试。

覆盖两个原实现的问题：
1. 用户断开后仍保留在 `_project_users` → 下线用户继续收到项目推送（跨项目泄露），
   且该字典只增不减。
2. 用户切换项目后，旧项目的映射残留 → 仍会收到旧项目推送。
"""

import unittest

from app.routers import ws


class WsRegistryTests(unittest.TestCase):
    def setUp(self):
        ws._user_connections.clear()
        ws._project_users.clear()

    def tearDown(self):
        ws._user_connections.clear()
        ws._project_users.clear()

    def test_last_connection_removes_user_from_project_mapping(self):
        conn = object()
        ws._register_connection(7, 3, conn)
        self.assertEqual(ws._project_users, {3: {7}})

        ws._unregister_connection(7, 3, conn)
        self.assertNotIn(7, ws._user_connections)
        self.assertEqual(ws._project_users, {}, "下线用户仍留在项目映射中")

    def test_remaining_connection_keeps_project_mapping(self):
        a, b = object(), object()
        ws._register_connection(7, 3, a)
        ws._register_connection(7, 3, b)

        ws._unregister_connection(7, 3, a)
        self.assertEqual(ws._project_users, {3: {7}}, "还有在线连接却被摘除")

        ws._unregister_connection(7, 3, b)
        self.assertEqual(ws._project_users, {})

    def test_switching_project_drops_old_mapping(self):
        conn = object()
        ws._register_connection(7, 3, conn)
        # 同一用户重新连接（已切到项目 5）
        conn2 = object()
        ws._register_connection(7, 5, conn2)
        self.assertNotIn(3, ws._project_users, "切换项目后旧项目映射仍残留")
        self.assertEqual(ws._project_users, {5: {7}})

    def test_other_users_unaffected(self):
        a, b = object(), object()
        ws._register_connection(7, 3, a)
        ws._register_connection(8, 3, b)

        ws._unregister_connection(7, 3, a)
        self.assertEqual(ws._project_users, {3: {8}})


if __name__ == "__main__":
    unittest.main()
