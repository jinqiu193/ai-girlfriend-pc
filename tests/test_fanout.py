"""Tests for fanout.gather_many and fanout.schedule_single.

Both functions embody the failure-isolation contract documented in
``companion-extraction.md`` (§7): background work never crashes the
caller, failures are logged with identifying context, parallel tasks
don't serialise behind a slow one.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from fanout import gather_many, schedule_single


# ---------- gather_many ----------


async def test_gather_many_all_succeed():
    async def add(a, b):
        return a + b

    out = await gather_many([add(1, 2), add(3, 4), add(5, 6)])
    assert out == [3, 7, 11]


async def test_gather_many_partial_failure_returns_exception_in_slot():
    async def ok():
        return "ok"

    async def boom():
        raise RuntimeError("nope")

    out = await gather_many([ok(), boom(), ok()])
    assert out[0] == "ok"
    assert isinstance(out[1], RuntimeError)
    assert out[2] == "ok"


async def test_gather_many_all_failure():
    async def boom():
        raise ValueError("a")

    async def bang():
        raise KeyError("b")

    out = await gather_many([boom(), bang()])
    assert isinstance(out[0], ValueError)
    assert isinstance(out[1], KeyError)


async def test_gather_many_empty_input():
    assert await gather_many([]) == []


async def test_gather_many_logs_failure_with_name(caplog: pytest.LogCaptureFixture):
    async def boom():
        raise RuntimeError("explode")

    with caplog.at_level(logging.WARNING, logger="ai-girlfriend.fanout"):
        out = await gather_many([boom()], names=["my-task"])
    assert isinstance(out[0], RuntimeError)
    assert any("my-task" in rec.message for rec in caplog.records)
    assert any("explode" in rec.message for rec in caplog.records)


async def test_gather_many_logs_failure_with_index_when_no_name(
    caplog: pytest.LogCaptureFixture,
):
    async def boom():
        raise RuntimeError("x")

    with caplog.at_level(logging.WARNING, logger="ai-girlfriend.fanout"):
        await gather_many([boom()])
    assert any("task[0]" in rec.message for rec in caplog.records)


async def test_gather_many_parallel_speedup():
    """Two tasks that each sleep 100ms must finish in ~100ms total,
    not ~200ms — proves they're actually running in parallel rather
    than serially."""
    async def slow():
        await asyncio.sleep(0.1)
        return 1

    async def also_slow():
        await asyncio.sleep(0.1)
        return 2

    start = asyncio.get_event_loop().time()
    out = await gather_many([slow(), also_slow()])
    elapsed = asyncio.get_event_loop().time() - start
    assert out == [1, 2]
    assert elapsed < 0.18, f"gather_many did not parallelise (elapsed={elapsed:.3f}s)"


# ---------- schedule_single ----------


async def test_schedule_single_runs_and_completes():
    async def work():
        return 42

    task = schedule_single(work(), name="t1")
    result = await task
    # task's outer coroutine returns None (it swallows the inner result)
    assert result is None


async def test_schedule_single_swallows_exception(caplog: pytest.LogCaptureFixture):
    async def boom():
        raise RuntimeError("kaboom")

    with caplog.at_level(logging.WARNING, logger="ai-girlfriend.fanout"):
        task = schedule_single(boom(), name="t1", context={"pool_id": "abc"})
    # Awaiting must not raise; the exception is captured inside _wrapped.
    await task
    assert any("t1" in rec.message for rec in caplog.records)
    assert any("kaboom" in rec.message for rec in caplog.records)
    assert any("abc" in rec.message for rec in caplog.records)


async def test_schedule_single_logs_timeout(caplog: pytest.LogCaptureFixture):
    async def hangs():
        await asyncio.sleep(10)

    with caplog.at_level(logging.WARNING, logger="ai-girlfriend.fanout"):
        task = schedule_single(hangs(), name="slow", timeout=0.05)
        await task
    assert any("timed out" in rec.message for rec in caplog.records)
    assert any("slow" in rec.message for rec in caplog.records)


async def test_schedule_single_does_not_block_caller():
    """The caller must not have to await schedule_single to release
    the request that scheduled it. We verify this by scheduling a
    task that would block the loop, then running an unrelated
    coroutine immediately afterwards."""
    sentinel: list[str] = []

    async def slow():
        await asyncio.sleep(0.05)
        sentinel.append("slow-done")

    async def fast():
        sentinel.append("fast-done")

    schedule_single(slow(), name="bg")
    await fast()
    # fast ran and finished before slow's sleep elapsed
    assert sentinel == ["fast-done"]
    # give the scheduled task time to finish before pytest tears down
    await asyncio.sleep(0.1)
    assert sentinel == ["fast-done", "slow-done"]