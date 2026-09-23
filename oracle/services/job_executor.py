"""Where a job's work runs. The GUI server runs it on a worker thread, so a job that holds
its thread for a long stretch (an analysis pass) never stalls the pages."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from oracle.services.context import AppContext, worker_context

__all__ = ["Executor", "InlineExecutor", "ThreadExecutor", "Work"]

Work = Callable[[AppContext], Awaitable[Any]]


class Executor(Protocol):
    async def run(self, work: Work) -> Any:
        """The work's result. Cancelling the caller cancels the work, and returns only
        once the work has finished cleaning up."""


class InlineExecutor:
    """On the caller's loop, with its context."""

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx

    async def run(self, work: Work) -> Any:
        return await work(self._ctx)


class ThreadExecutor:
    """Each run on a thread of its own, with its own loop and connection."""

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx

    async def run(self, work: Work) -> Any:
        async def in_worker() -> Any:
            with worker_context(self._ctx) as ctx:
                return await work(ctx)

        loop = asyncio.new_event_loop()
        task = loop.create_task(in_worker())

        def drive() -> Any:
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(task)
            finally:
                _close(loop)

        done = asyncio.ensure_future(asyncio.to_thread(drive))
        try:
            return await asyncio.shield(done)
        except asyncio.CancelledError:
            with suppress(RuntimeError):  # the loop closed: the work had just finished
                loop.call_soon_threadsafe(task.cancel)
            # The job's own cleanup (a lock release, an interrupted record) finishes first,
            # however many more cancels arrive meanwhile.
            while not done.done():
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(done)
            raise


def _close(loop: asyncio.AbstractEventLoop) -> None:
    """What `asyncio.run` does on the way out: tasks the work left behind (a client's
    internals) are cancelled and awaited rather than destroyed while pending."""
    try:
        left = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in left:
            t.cancel()
        if left:
            loop.run_until_complete(asyncio.gather(*left, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
    finally:
        asyncio.set_event_loop(None)
        loop.close()
