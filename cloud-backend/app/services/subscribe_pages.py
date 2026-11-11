"""微信订阅消息跳转页面白名单（唯一真源）。

订阅消息 payload 里的 ``page`` 若指向小程序未注册页面，微信
``subscribeMessage.send`` 会整条返回错误——用户连通知都收不到，而不只是跳转不了。
因此这里集中维护允许跳转的页面，并在真正发送前做校验。

新增/改动跳转页面时须同步两处：
  1. ``wechat-miniprogram/miniprogram/app.json`` 的 ``pages``
  2. 本文件的 ``SUBSCRIBE_MESSAGE_PAGES``

回归测试 ``tests/test_subscribe_message_pages.py`` 会校验白名单与 app.json 一致，
并扫描 routers 下所有 ``page=`` 字面量，杜绝再次出现未注册页面。
"""
import logging

logger = logging.getLogger(__name__)

# 与 app.json pages 保持同步；只列订阅消息会用到的页面，不是全量页面清单。
SUBSCRIBE_MESSAGE_PAGES = frozenset({
    "pages/todo/index",
    "pages/hazards/index",
    "pages/documents/detail",
    "pages/agent/index",
    "pages/work/index",
    "pages/kb/index",
})


def normalize_subscribe_page(page: str | None) -> str | None:
    """校验并规范化订阅消息 ``page``。

    合法：返回不带前导斜杠、保留 query 的路径（如 ``pages/todo/index?id=7``）。
    非法：返回 ``None`` —— 发送时不带 ``page``，让通知仍能送达。
    宁可少一次跳转，也不能让整条服务通知发送失败。
    """
    if not page:
        return None
    raw = page.strip()
    path, sep, query = raw.partition("?")
    path = path.lstrip("/")
    if path not in SUBSCRIBE_MESSAGE_PAGES:
        logger.warning(
            "[wechat_notice] 订阅消息 page 不在白名单，已丢弃跳转以避免整条通知发送失败: %s",
            raw,
        )
        return None
    return path + (sep + query if sep else "")
