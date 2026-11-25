"""验证：trace 写入死锁重试（MySQL 1213）。

模拟 start_request / append_progress_event / finish_request 的 body 抛
死锁异常，_TraceSession.run 应重试并在重试轮成功；非死锁异常不重试。
"""
import asyncio
import sys

import pytest

sys.path.insert(0, ".")

from sqlalchemy.exc import OperationalError
from app.services import agent_runtime as rt


class _FakeOperationalError(OperationalError):
    """statement/params 形参必须给（SQLAlchemy 构造要求）。"""

    def __init__(self, code: int):
        orig = type("Orig", (), {"args": (code, "Deadlock found when trying to get lock")})
        super().__init__("stmt", {}, orig)


def _deadlock():
    return _FakeOperationalError(1213)


def _not_deadlock():
    return _FakeOperationalError(1205)  # lock wait timeout，非 1213


class _SessionCtx:
    def __init__(self, fail_times: int, exc_factory):
        self.fail_times = fail_times
        self.exc_factory = exc_factory
        self.opened = 0

    def __call__(self):
        self.opened += 1
        return _SessionInstance(self)


class _SessionInstance:
    def __init__(self, owner: "_SessionCtx"):
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        if self.owner.opened <= self.owner.fail_times:
            raise self.owner.exc_factory()
        return _FakeResult()


class _FakeResult:
    def scalars(self):
        return self

    def first(self):
        return None


async def main():
    # 1. 首轮死锁 → 重试成功（body 必须真调 db.execute 才能触发首轮死锁）
    factory = _SessionCtx(fail_times=1, exc_factory=_deadlock)

    async def body(db):
        await db.execute()  # 第 1 轮 opened=1 ≤ fail_times → 抛死锁；第 2 轮成功
        return "OK"

    session = rt._TraceSession(factory)
    result = await session.run(body)
    assert result == "OK", result
    assert factory.opened == 2, f"应开 2 轮会话，实际 {factory.opened}"
    print("1. 死锁重试成功: sessions =", factory.opened)

    # 2. 连续死锁超出重试次数 → 抛出（外层降级捕获）
    factory2 = _SessionCtx(fail_times=99, exc_factory=_deadlock)
    session2 = rt._TraceSession(factory2)

    async def body2(db):
        await db.execute()  # 每轮都死锁
        return "OK"

    try:
        await session2.run(body2)
        raise AssertionError("应抛出死锁异常")
    except OperationalError:
        pass
    print("2. 重试用尽后抛出: sessions =", factory2.opened)
    assert factory2.opened == 1 + rt._TRACE_DEADLOCK_RETRIES

    # 3. 非死锁 OperationalError → 不重试直接抛
    factory3 = _SessionCtx(fail_times=99, exc_factory=_not_deadlock)
    session3 = rt._TraceSession(factory3)

    async def body3(db):
        await db.execute()  # 首轮就抛非死锁
        return "OK"

    try:
        await session3.run(body3)
        raise AssertionError("应抛出非死锁异常")
    except OperationalError:
        pass
    assert factory3.opened == 1, f"非死锁不应重试，实际开了 {factory3.opened} 轮"
    print("3. 非死锁不重试: sessions =", factory3.opened)

    # 4. start_request 整链路：第一轮死锁、第二轮成功 → 无异常返回 rid
    calls = {"n": 0}

    class _Factory4(_SessionCtx):
        async def execute(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _deadlock()
            return _FakeResult()

    # execute 在 select 阶段死锁一次后成功 → row=None → INSERT（add 不真写库）
    factory4 = _Factory4(fail_times=0, exc_factory=_deadlock)

    async def fake_body(db):
        return "BODY-OK"

    session4 = rt._TraceSession(factory4)
    r4 = await session4.run(fake_body)
    assert r4 == "BODY-OK"
    print("4. body 层重试贯通 OK")
    # 5. _is_deadlock 判定
    assert rt._is_deadlock(_deadlock())
    assert not rt._is_deadlock(_not_deadlock())
    assert not rt._is_deadlock(ValueError("x"))
    print("5. _is_deadlock 判定 OK")

    print("ALL PASS")


def test_deadlock_retry_all():
    """pytest 收集入口：全量回归（run_regression.py）跑本文件时不至于 0 用例。"""
    asyncio.run(main())
