"""Disconnected collection requests release their watcher, never a running reader."""

from __future__ import annotations

import asyncio
import threading

import pytest
from starlette.requests import Request
from starlette.types import Message

from mechanical_librarian.interfaces.web.app import _browse_response


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_browse_transport_awaits_worker_and_closes_disconnect_watcher(
    outcome: str,
) -> None:
    async def exercise() -> None:
        watching = asyncio.Event()
        stopped = asyncio.Event()
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        event_loop_thread = threading.get_ident()

        async def receive() -> Message:
            watching.set()
            try:
                await asyncio.Event().wait()
                raise AssertionError("receive must be cancelled")
            finally:
                stopped.set()

        def read(check_active):
            assert threading.get_ident() != event_loop_thread
            check_active()
            entered.set()
            try:
                assert release.wait(5)
                if outcome == "error":
                    raise ValueError("read failed")
                return "validated response"
            finally:
                finished.set()

        request = Request({"type": "http"}, receive)
        task = asyncio.create_task(_browse_response(request, read))
        await asyncio.wait_for(watching.wait(), 2)
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            if outcome == "cancel":
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done() and not finished.is_set()
            release.set()
            if outcome == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == "error":
                with pytest.raises(ValueError, match="read failed"):
                    await task
            else:
                assert await task == "validated response"
            assert finished.is_set() and stopped.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
