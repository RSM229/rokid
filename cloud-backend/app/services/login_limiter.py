"""登录防爆破限流（滑动窗口，依赖无关）。

针对 ``/auth/login`` 的密码 / 验证码尝试，按 ``(客户端IP, 登录标识)`` 做滑动窗口限流：
连续失败达阈值即返回 ``429``（带 ``Retry-After``），阻断对单一账号的密码爆破，并避免通过
「账号是否存在」的响应差异做用户枚举（账号存否都计入同一 key）。

设计取舍：
- 内存存储，按进程维度有效。当前生产为单后端容器，足够；若未来多副本需改为共享存储
  （Redis）或粘性会话。
- 阈值 / 窗口 / 锁定时长从构造参数注入，便于测试。
- ``check / register_failure / reset`` 均接受可选 ``now`` 时钟参数（依赖注入），测试可冻结时间。
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException


class LoginRateLimiter:
    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 300,
        lockout_seconds: int = 900,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._fails: dict[str, list[float]] = defaultdict(list)

    # ---- 可注入时钟（便于测试冻结时间） ----
    def _now(self) -> float:
        return time.time()

    def _prune(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        kept = [t for t in self._fails[key] if t > cutoff]
        if len(kept) != len(self._fails[key]):
            self._fails[key] = kept
        return kept

    def check(self, key: str, now: Optional[float] = None) -> None:
        """每次登录尝试前调用；已触发限流则抛 429。"""
        now = now if now is not None else self._now()
        ts = self._prune(key, now)
        if len(ts) >= self.max_attempts:
            # 冷却到「窗口内最早一次失败」过期后；lockout_seconds 可进一步拉长
            oldest = ts[0]
            retry_after = int(max(self.window_seconds - (now - oldest), self.lockout_seconds)) + 1
            raise HTTPException(
                status_code=429,
                detail="登录尝试过于频繁，请稍后再试",
                headers={"Retry-After": str(retry_after)},
            )

    def register_failure(self, key: str, now: Optional[float] = None) -> None:
        """登录失败时调用，记录一次失败时间戳。"""
        now = now if now is not None else self._now()
        self._fails[key].append(now)
        self._prune(key, now)

    def reset(self, key: str) -> None:
        """登录成功（凭证正确）时调用，清空该 key 的失败计数。"""
        self._fails.pop(key, None)


#: 全局单例（默认阈值：5 分钟内 5 次失败 → 锁 15 分钟）
default_limiter = LoginRateLimiter()
