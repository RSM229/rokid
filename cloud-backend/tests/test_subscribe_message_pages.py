"""微信订阅消息跳转页面回归测试。

背景：订阅消息 page 指向 app.json 未注册的页面时，微信 subscribeMessage.send
会整条失败（用户连通知都收不到）。这里校验：
  1. 白名单里的页面确实都在 app.json 注册过；
  2. routers 下所有 ``page=`` 字面量都落在白名单内；
  3. 发送前校验函数会丢弃非法 page、保留合法 page 与 query。
"""
import ast
import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import wechat_notice
from app.services.subscribe_pages import SUBSCRIBE_MESSAGE_PAGES, normalize_subscribe_page

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_JSON = REPO_ROOT / "wechat-miniprogram" / "miniprogram" / "app.json"
ROUTERS_DIR = REPO_ROOT / "cloud-backend" / "app" / "routers"

# 历史上写错、导致整条通知发不出去的三个路径
LEGACY_BAD_PAGES = {
    "pages/task/detail",
    "pages/hazard/detail",
    "pages/document/detail",
}


def _static_page_value(node: ast.AST) -> str | None:
    """提取 page= 的字符串常量或 f-string 的静态部分。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return None


class SubscribePageWhitelistTests(unittest.TestCase):
    def test_whitelist_pages_are_registered_in_app_json(self) -> None:
        pages = set(json.loads(APP_JSON.read_text(encoding="utf-8"))["pages"])
        unregistered = SUBSCRIBE_MESSAGE_PAGES - pages
        self.assertEqual(set(), unregistered, f"白名单含未注册页面: {sorted(unregistered)}")

    def test_normalize_keeps_valid_page_and_query(self) -> None:
        self.assertEqual("pages/todo/index?id=7", normalize_subscribe_page("pages/todo/index?id=7"))
        self.assertEqual("pages/todo/index?id=7", normalize_subscribe_page("/pages/todo/index?id=7"))
        self.assertEqual("pages/documents/detail", normalize_subscribe_page("pages/documents/detail"))

    def test_normalize_drops_unknown_or_empty_page(self) -> None:
        for bad in (*LEGACY_BAD_PAGES, "", None, "pages/not-exist/index"):
            self.assertIsNone(normalize_subscribe_page(bad), f"应丢弃: {bad!r}")

    def test_router_subscribe_pages_are_all_whitelisted(self) -> None:
        """扫描 routers 下所有 page= 关键字参数，杜绝再次写错页面路径。"""
        found: list[tuple[str, int, str]] = []
        for source in sorted(ROUTERS_DIR.glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg != "page":
                        continue
                    raw = _static_page_value(kw.value)
                    if not raw:
                        continue
                    found.append((source.name, node.lineno, raw.split("?", 1)[0].lstrip("/")))
        self.assertGreater(len(found), 0, "没扫到任何订阅消息 page，检查扫描逻辑是否失效")
        unknown = [item for item in found if item[2] not in SUBSCRIBE_MESSAGE_PAGES]
        self.assertEqual([], unknown, f"订阅消息 page 指向白名单外页面: {unknown}")
        legacy = [item for item in found if item[2] in LEGACY_BAD_PAGES]
        self.assertEqual([], legacy, f"仍在使用历史上错误的页面路径: {legacy}")


class SubscribeMessageSendTests(unittest.TestCase):
    """校验防御确实接在发送链路上：非法 page 不进 payload，通知仍能发出。"""

    def _send(self, page: str) -> dict:
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"errcode": 0, "errmsg": "ok"}
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=None)
        with patch.object(wechat_notice, "get_access_token", AsyncMock(return_value="token")), \
             patch.object(wechat_notice.httpx, "AsyncClient", MagicMock(return_value=context)):
            result = asyncio.run(wechat_notice.send_subscribe_message(
                openid="openid", template_id="tpl", data={"thing1": {"value": "x"}}, page=page,
            ))
        return {"result": result, "payload": client.post.call_args.kwargs["json"]}

    def test_invalid_page_is_dropped_but_message_still_sends(self) -> None:
        out = self._send("pages/task/detail?id=1")
        self.assertNotIn("page", out["payload"])
        self.assertEqual(0, out["result"]["errcode"])

    def test_valid_page_is_forwarded_with_query(self) -> None:
        out = self._send("pages/todo/index?id=7")
        self.assertEqual("pages/todo/index?id=7", out["payload"]["page"])
        self.assertEqual(0, out["result"]["errcode"])


if __name__ == "__main__":
    unittest.main()
