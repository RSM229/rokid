"""短信验证码发送 —— Provider 抽象 + 腾讯云适配器 + mock 兜底。

设计：
- ``SMSSender`` 只有 ``send(phone, code)`` 一个方法，新增服务商只需加一个类。
- ``get_sms_sender()`` 按 ``settings.sms_provider`` 选择实现：
  - ``mock``    ：验证码打印到日志，不发真实短信（开发/测试自测用）；
  - ``tencent`` ：腾讯云短信（TC3-HMAC-SHA256 直连，不依赖 SDK，适配无外网容器）。
- 阿里云后续可补 ``AliyunSMSSender``，不影响其它代码。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class SMSSender:
    """短信发送器协议（结构化子类约定）。"""

    async def send(self, phone: str, code: str) -> None:
        raise NotImplementedError


class MockSMSSender(SMSSender):
    """本地/测试用：验证码只打到日志，不真正下发。

    开发自测时，注册/登录流程完整可跑通，验证码去服务端日志取即可。
    """

    async def send(self, phone: str, code: str) -> None:
        logger.info("[MOCK SMS] phone=%s code=%s（mock 模式，未真实下发）", phone, code)


class TencentCloudSMSSender(SMSSender):
    """腾讯云短信 SendSms（TC3-HMAC-SHA256 签名，httpx 直连，无 SDK 依赖）。"""

    ENDPOINT = "sms.tencentcloudapi.com"
    SERVICE = "sms"
    VERSION = "2019-07-11"
    ACTION = "SendSms"
    REGION = "ap-guangzhou"

    def __init__(self) -> None:
        self.secret_id = settings.sms_tencent_secret_id
        self.secret_key = settings.sms_tencent_secret_key
        self.sdk_app_id = settings.sms_tencent_sdk_app_id
        self.sign = settings.sms_tencent_sign
        self.template_id = settings.sms_tencent_template_id

    def _normalize_phone(self, phone: str) -> str:
        phone = phone.strip()
        if phone.startswith("+"):
            return phone
        if phone.startswith("1") and len(phone) == 11:
            return f"+86{phone}"
        return phone

    def _sign(self, payload: str, timestamp: int) -> str:
        # 用时区感知的 UTC（datetime.utcfromtimestamp 自 3.12 起弃用）
        date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
        hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        canonical = (
            "POST\n/\n\n"
            f"content-type:application/json; charset=utf-8\nhost:{self.ENDPOINT}\n\n"
            "content-type;host\n" + hashed_payload
        )
        string_to_sign = (
            "TC3-HMAC-SHA256\n"
            f"{timestamp}\n"
            f"{date}/{self.SERVICE}/tc3_request\n"
            + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        )
        secret_date = hmac.new(
            ("TC3" + self.secret_key).encode("utf-8"), date.encode("utf-8"), hashlib.sha256
        ).digest()
        secret_service = hmac.new(secret_date, self.SERVICE.encode("utf-8"), hashlib.sha256).digest()
        secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
        signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        return signature

    async def send(self, phone: str, code: str) -> None:
        if not (self.secret_id and self.secret_key and self.sdk_app_id and self.sign and self.template_id):
            raise RuntimeError(
                "腾讯云短信未完整配置（缺少 SecretId/SecretKey/SdkAppId/签名/模板ID）"
            )
        payload = json.dumps(
            {
                "PhoneNumberSet": [self._normalize_phone(phone)],
                "SmsSdkAppId": self.sdk_app_id,
                "SignName": self.sign,
                "TemplateId": self.template_id,
                "TemplateParamSet": [code],
            },
            ensure_ascii=False,
        )
        timestamp = int(time.time())
        signature = self._sign(payload, timestamp)
        # 用时区感知的 UTC（datetime.utcfromtimestamp 自 3.12 起弃用）
        date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
        authorization = (
            "TC3-HMAC-SHA256 Credential="
            + self.secret_id
            + "/"
            + date
            + "/"
            + self.SERVICE
            + "/tc3_request, SignedHeaders=content-type;host, Signature="
            + signature
        )
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": self.ENDPOINT,
            "X-TC-Action": self.ACTION,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": self.VERSION,
            "X-TC-Region": self.REGION,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://" + self.ENDPOINT, content=payload.encode("utf-8"), headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
        if data.get("Response", {}).get("Error"):
            err = data["Response"]["Error"]
            raise RuntimeError(f"腾讯云短信发送失败: {err.get('Code')} {err.get('Message')}")
        logger.info("[SMS] 已向 %s 下发验证码（腾讯云）", phone)


def get_sms_sender() -> SMSSender:
    """按配置选择短信发送器。默认 mock（不阻断开发/测试）。"""
    provider = (settings.sms_provider or "mock").lower()
    if provider == "tencent":
        return TencentCloudSMSSender()
    # 未配置真实服务商时回落 mock，保证注册链路本地可自测。
    return MockSMSSender()
