# -*- coding: utf-8 -*-
"""公开端点白名单回归（不连库、不调外部服务）。

跑法：``cd cloud-backend && python -m unittest tests.test_public_endpoints``

为什么要锁这条：
    免登录端点就是公网攻击面。2026-10-29 审计发现
    ``POST /api/agent/recommend-purpose`` 漏了 ``get_current_user``——
    该接口内部真实调用**付费大模型**，等于把「匿名刷额度」开在公网上。
    这类漏挂在代码里看不出来（少一行 Depends 而已），只能靠清单比对兜住。

判据：OpenAPI 里 ``security`` 为空的 operation 必须**恰好**等于下面的白名单，
    或落在 MANUAL_AUTH_ENDPOINTS（自定义 Depends 手动鉴权，OpenAPI 不认）。
    多一个 = 新写了免登录接口没经评审；少一个 = 白名单过期，顺手删掉。

新增免登录接口时必须显式加进 ``PUBLIC_ENDPOINTS`` 并说明理由，
不要为了让测试变绿去删断言。
"""
from __future__ import annotations

import unittest

from app.main import app

# 键 = "METHOD /path"，值 = 为什么它可以免登录。
PUBLIC_ENDPOINTS: dict[str, str] = {
    "GET /health": "存活探针，部署脚本与外部监控直接打，不能要 token",
    "POST /api/auth/login": "登录本身",
    "POST /api/auth/register": "注册本身",
    "GET /api/auth/roles": "注册页选角色，此时还没有账号",
    "GET /api/auth/invite-codes/validate": "注册前校验邀请码是否可用",
    "POST /api/auth/send-sms-code": "注册/改密前的验证码下发，此时无账号",
    "POST /api/auth/forgot/questions": "忘记密码：取密保问题（未登录态）",
    "POST /api/auth/forgot/verify": "忘记密码：校验密保答案换重置凭证（未登录态）",
    "POST /api/auth/forgot/reset": "忘记密码：凭重置凭证设新密码（未登录态）",
    "POST /api/auth/glasses/scan": "眼镜端用一次性 ticket 换 token，ticket 由已登录端签发",
    # 扫码登录（5ecbb8c）：Web 未登录态发起/轮询，与登录接口同级；
    # create 只产一次性 ticket+secret，status 凭 secret 才换 token
    "POST /api/auth/scan-login/create": "扫码登录：Web 未登录时生成一次性二维码 ticket",
    "GET /api/auth/scan-login/status": "扫码登录：Web 凭 ticket+secret 轮询并换发 token",
    "POST /api/miniprogram/auth/login": "小程序登录",
    "POST /api/miniprogram/auth/register": "小程序注册",
    "POST /api/miniprogram/auth/wechat-register": "小程序微信注册",
    "POST /api/miniprogram/auth/bind": "小程序绑定手机号，本阶段尚未拿到正式 token",
    "POST /api/omni/tts": "占位接口，当前直接返回 501，无副作用",
}

# 这些端点在 OpenAPI 里 ``security`` 为空，但**不是**匿名接口：
# app/routers/files.py 用自定义 Depends(_user_from_file_request) 手动校验 token
# （同时接受 ?access_token=，供小程序 <image> 标签携带凭证）。FastAPI 只会把
# SecurityScheme 写进 security 字段，自定义依赖不会被识别，故清单比对需显式豁免。
# 🔴 豁免不等于放行：下面 test_manual_auth_endpoints_reject_anonymous 会真实
# 打一次 401；谁把 Depends 摘了，那条测试立刻变红。
MANUAL_AUTH_ENDPOINTS: dict[str, str] = {
    "GET /uploads/{rest}": "受控上传文件：_user_from_file_request 校验 token",
    "GET /exports/{rest}": "受控导出文件：_user_from_file_request 校验 token",
}


def _collect_public() -> dict[str, list[str]]:
    """返回 {公开 operation: [摘要]}。"""
    spec = app.openapi()
    found: dict[str, list[str]] = {}
    for path, ops in spec.get("paths", {}).items():
        for method, op in ops.items():
            if op.get("security"):
                continue
            found[f"{method.upper()} {path}"] = list(op.get("tags") or [])
    return found


class PublicEndpointWhitelistTests(unittest.TestCase):
    def test_no_unexpected_public_endpoint(self) -> None:
        found = _collect_public()
        unexpected = sorted(
            set(found) - set(PUBLIC_ENDPOINTS) - set(MANUAL_AUTH_ENDPOINTS)
        )
        self.assertEqual(
            unexpected,
            [],
            "以下接口无需登录即可访问，且不在白名单/手动鉴权例外内。"
            "若确实应该公开，请加进 PUBLIC_ENDPOINTS 并写明理由；"
            "若走自定义 Depends 鉴权，请加进 MANUAL_AUTH_ENDPOINTS 并补 401 验证；"
            "否则补上 Depends(get_current_user)：\n  " + "\n  ".join(unexpected),
        )

    def test_whitelist_has_no_stale_entry(self) -> None:
        """白名单里的路径必须仍然存在且仍然公开，避免改名后静默失效。"""
        found = _collect_public()
        stale = sorted(set(PUBLIC_ENDPOINTS) - set(found))
        self.assertEqual(
            stale,
            [],
            "白名单里这些条目已经不存在或已改为需要登录，请删除：\n  " + "\n  ".join(stale),
        )

    def test_manual_auth_whitelist_has_no_stale_entry(self) -> None:
        """手动鉴权例外同样要防过期，否则改名后会留一个永久豁免后门。"""
        found = _collect_public()
        stale = sorted(set(MANUAL_AUTH_ENDPOINTS) - set(found))
        self.assertEqual(
            stale,
            [],
            "这些手动鉴权端点已不存在或已改用 SecurityScheme，请同步例外表：\n  "
            + "\n  ".join(stale),
        )

    def test_manual_auth_endpoints_reject_anonymous(self) -> None:
        """豁免不是放行：无 token 访问受控文件必须 401。

        _user_from_file_request 在拿不到凭证时立即抛 401，早于任何 DB 查询，
        所以这里用 TestClient 实打实请求也不会连库，可以安全当上线门禁。
        """
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # starlette TestClient 的 httpx 兼容警告
            from fastapi.testclient import TestClient

            client = TestClient(app)
            for key in MANUAL_AUTH_ENDPOINTS:
                path = key.split(" ", 1)[1].replace("{rest}", "__probe_not_exist__.bin")
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(
                        response.status_code,
                        401,
                        f"{path} 无 token 未返回 401，受控文件服务可能失去鉴权",
                    )

    def test_paid_llm_endpoints_require_auth(self) -> None:
        """防回归：直接烧钱的接口必须登录。

        ``/api/agent/*`` 与 ``/api/omni/*`` 内部都可能触发大模型调用，
        除明确占位的 501 之外一律不允许匿名。
        """
        found = _collect_public()
        paid_prefixes = ("/api/agent/", "/api/ai/", "/api/omni/", "/api/local/")
        offenders = [
            key
            for key in found
            if any(key.split(" ", 1)[1].startswith(p) for p in paid_prefixes)
            and key != "POST /api/omni/tts"
        ]
        self.assertEqual(
            offenders,
            [],
            "以下接口会调用付费模型却不需要登录：\n  " + "\n  ".join(sorted(offenders)),
        )


if __name__ == "__main__":
    unittest.main()
