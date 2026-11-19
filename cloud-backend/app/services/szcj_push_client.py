"""穗建通数据采集推送客户端（SUP-001 起步）。

职责边界（与 mapper 分工）：
- ``szcj_sup001_mapper``：纯函数，记录 dict → 请求体；不碰 DB、不发 HTTP。
- 本模块：HTTP 发送 + 重试 + 幂等判定 + 配置闸门。不发猜的请求——
  凭证（base_url/appCode/token）任一缺失直接 ``PushNotConfiguredError``。

对接契约（21 类接口通用）：
    POST {base}/tenantgz/e-api/ods/push/{标识}
    Headers: Authorization: Bearer {token} / appCode / X-Data-Source: jlt
    Body: {"data": {...}}
    code==0 即成功；data.status 为 INSERT/UPDATE/DUPLICATE_INSERT/DUPLICATE_UPDATE，
    DUPLICATE_* 是幂等重推（不算失败）。

重试口径：网络错误/5xx/429 退避重试（0.5s/1s/2s）；4xx 业务拒绝不重试
（重试也不会好，且可能重复写入）。幂等键 = 监理通记录 id，穗建通按 srcId 去重。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from app.config import settings
from app.services.szcj_sup001_mapper import MissingRequiredError, assert_duplicate_ok

logger = logging.getLogger(__name__)

# X-Data-Source 是文档里唯一确认的固定头（jlt = 监理通）
_DATA_SOURCE = "jlt"
_PUSH_PREFIX = "/tenantgz/e-api/ods/push"

_RETRYABLE_STATUS = {429, 502, 503, 504}
_RETRY_DELAYS = (0.5, 1.0, 2.0)


class PushNotConfiguredError(RuntimeError):
    """穗建通凭证未配置（base_url / appCode / bearer token 任一缺失）。"""


class PushRejectedError(RuntimeError):
    """穗建通明确拒绝（4xx / code!=0 且非幂等重推）。重试无意义。"""


@dataclass
class PushResult:
    ok: bool
    status: str = ""            # INSERT / UPDATE / DUPLICATE_* / ""（未发送）
    http_status: int = 0
    response: dict = field(default_factory=dict)
    attempts: int = 0
    error: str = ""


def _require_config() -> tuple[str, str, str]:
    base = (settings.szcj_push_base_url or "").strip().rstrip("/")
    app_code = (settings.szcj_app_code or "").strip()
    token = (settings.szcj_bearer_token or "").strip()
    if not base or not app_code or not token:
        missing = [
            name for name, v in (
                ("SZCJ_PUSH_BASE_URL", base), ("SZCJ_APP_CODE", app_code),
                ("SZCJ_BEARER_TOKEN", token),
            ) if not v
        ]
        raise PushNotConfiguredError(f"穗建通凭证未配置：{', '.join(missing)}（联系穗建通下发后填 env）")
    return base, app_code, token


def project_code_for(project_id: int | None) -> str:
    """本地项目 id → 穗建通项目编码（PRJ2026…）。未映射返回空串，调用方决定拒收。"""
    if project_id is None:
        return ""
    return str(settings.szcj_project_codes.get(str(project_id), "") or "").strip()


async def push_payload(
    endpoint: str,
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> PushResult:
    """推送一个已映射好的请求体到指定采集接口（如 ``patrol_record``）。

    凭证缺失 → PushNotConfiguredError（调用方据 needs_more 类似口径提示运营配置）；
    网络错误/5xx/429 → 退避重试 3 次；4xx/code!=0 → PushRejectedError 包进 result。
    """
    base, app_code, token = _require_config()
    url = f"{base}{_PUSH_PREFIX}/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "appCode": app_code,
        "X-Data-Source": _DATA_SOURCE,
        "Content-Type": "application/json",
    }
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    attempts = 0
    last_error = ""
    try:
        for delay in (None, *_RETRY_DELAYS):
            if delay is not None:
                await asyncio.sleep(delay)
            attempts += 1
            try:
                resp = await http_client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:  # 网络层：连不上/超时/断流
                last_error = f"网络错误：{exc}"
                logger.warning("穗建通推送网络错误（第 %s 次）%s: %s", attempts, endpoint, exc)
                continue
            body: dict = {}
            try:
                body = resp.json() if resp.content else {}
            except ValueError:
                last_error = f"响应非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}"
                if resp.status_code in _RETRYABLE_STATUS:
                    continue
                return PushResult(ok=False, status="", http_status=resp.status_code,
                                  attempts=attempts, error=last_error)
            if resp.status_code in _RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}：{body}"
                logger.warning("穗建通推送可重试状态（第 %s 次）%s: %s", attempts, endpoint, last_error)
                continue
            if resp.status_code >= 400:
                return PushResult(ok=False, status="", http_status=resp.status_code,
                                  response=body, attempts=attempts,
                                  error=f"HTTP {resp.status_code}：{body}")
            code = body.get("code")
            status = str((body.get("data") or {}).get("status") or "")
            if code == 0:
                return PushResult(ok=True, status=status or "OK",
                                  http_status=resp.status_code, response=body, attempts=attempts)
            # code != 0：业务拒绝。DUPLICATE_* 已被 code==0 分支吃掉，这里只剩真拒绝。
            return PushResult(ok=False, status=status, http_status=resp.status_code,
                              response=body, attempts=attempts,
                              error=f"穗建通业务拒绝（code={code}）：{body.get('msg') or body}")
        return PushResult(ok=False, status="", attempts=attempts,
                          error=f"重试 {attempts} 次后仍失败：{last_error}")
    finally:
        if owns_client:
            await http_client.aclose()


async def push_sup001(
    record: dict[str, Any],
    *,
    project_code: str,
    user_name: str,
    photo_urls: list[str],
    created_time: Any,
    updated_time: Any,
    content_type: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> PushResult:
    """巡视记录一键推送（映射 + 发送）。

    MissingRequiredError 会原样抛出（数据不齐是调用方的责任，不算网络失败）；
    其余失败都收敛进 PushResult，调用方按 ok 字段处置。
    """
    from app.services.szcj_sup001_mapper import map_sup001

    payload = map_sup001(
        record,
        project_code=project_code,
        user_name=user_name,
        photo_urls=photo_urls,
        created_time=created_time,
        updated_time=updated_time,
        content_type=content_type,
    )
    return await push_payload("patrol_record", payload, client=client)
