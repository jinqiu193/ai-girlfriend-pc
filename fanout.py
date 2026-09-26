"""Background-task helpers: parallel fan-out + fire-and-forget scheduling.

Centralises the failure-isolation contract documented in
``companion-extraction.md`` (§7) so every caller — caption backfill,
future profile/emotion/promise extractors, memU commit tasks — gets
the same guarantees:

- background work never propagates exceptions to the user reply;
- failures are visible in logs (WARNING with a stable name + context);
- parallel work doesn't block the request that scheduled it;
- a slow / hung task can't deadlock the rest.

The verbosity of the warning logs is deliberate: when something fails
in production, the operator needs the pool_id / context that
identifies *which* task went wrong, not just "an extractor raised".
This matches the verbose-logging-and-swallow style already used in
``routes/character_routes.py``'s ``_backfill`` and ``_run``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Sequence, TypeVar

T = TypeVar("T")

log = logging.getLogger("ai-girlfriend.fanout")


async def gather_many(
    coros: Sequence[Awaitable[T]],
    *,
    names: Sequence[str] | None = None,
    logger: logging.Logger | None = None,
) -> list[T | BaseException]:
    """Run ``coros`` concurrently with verbose failure logging.

    Behaves like ``asyncio.gather(*coros, return_exceptions=True)`` but
    additionally logs each failure at WARNING with the task's
    ``name`` (or its index if no name was supplied). Returns the
    result list in input order — successful entries are the task's
    return value, failed entries are the ``BaseException`` that was
    raised.

    No exception ever escapes this function; callers can iterate the
    returned list and ``isinstance(slot, BaseException)`` to find
    failures without try/except plumbing.
    """
    log_obj = logger or log
    if not coros:
        return []
    results = await asyncio.gather(*coros, return_exceptions=True)
    for i, res in enumerate(results):
        if isinstance(res, BaseException):
            label = names[i] if names and i < len(names) else f"task[{i}]"
            log_obj.warning("fanout task '%s' failed: %s", label, res)
    return results


def schedule_single(
    coro: Awaitable[T],
    *,
    name: str,
    context: dict | None = None,
    timeout: float | None = None,
    logger: logging.Logger | None = None,
) -> asyncio.Task:
    """Fire-and-forget wrapper around ``asyncio.create_task``.

    Runs ``coro`` on the event loop, swallows exceptions, and logs at
    WARNING with ``name`` + ``context`` on failure so the caller can
    tell which background task went wrong. Returns the scheduled
    ``asyncio.Task`` so callers can ``await`` it in unit tests or
    attach further `` done callbacks.

    :param ``timeout``: When set, the coroutine is wrapped in
        ``asyncio.wait_for`` so a hung LLM call can't tie up the
        background task indefinitely.
    """
    log_obj = logger or log

    async def _wrapped() -> None:
        try:
            if timeout is not None:
                await asyncio.wait_for(coro, timeout=timeout)
            else:
                await coro
        except asyncio.TimeoutError:
            log_obj.warning(
                "background task '%s' timed out after %.1fs (context=%s)",
                name, timeout, context,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — intentional swallow
            log_obj.warning(
                "background task '%s' failed: %s (context=%s)",
                name, exc, context,
            )

    return asyncio.create_task(_wrapped())