"""A simple repeating async timer (used e.g. as a strategy watchdog)."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class AsyncTimer:
    def __init__(self, interval: float, callback: Callable[[], Awaitable], loop=None):
        self.interval = interval
        self.callback = callback
        self.loop = loop
        self._task: asyncio.Task | None = None
        self._stopped = False

    async def _run(self):
        while not self._stopped:
            await asyncio.sleep(self.interval)
            if self._stopped:
                break
            try:
                await self.callback()
            except Exception as exc:
                logger.error("AsyncTimer callback error: %s", exc)

    async def start(self):
        if self._task is None or self._task.done():
            self._stopped = False
            self._task = asyncio.create_task(self._run())
        return self._task

    async def stop(self):
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None