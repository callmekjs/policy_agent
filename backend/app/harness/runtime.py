"""작업 registry·동시 실행 제한·TTL 청소 (README §3.10).

FastAPI lifespan이 이 객체를 소유한다. 한 번에 작업 1건만 돌리기 위해
전역 Semaphore(1)를 쓰고, 실행 중인 asyncio.Task 참조를 붙잡아 둔다.
별도 실행 스레드와 BackgroundTasks는 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from typing import Any

from app.infrastructure.run_store import RunStore

#: TTL 청소 주기(초). README는 60초마다 만료를 확인하도록 정한다.
SWEEP_INTERVAL_SECONDS = 60


class BusyError(RuntimeError):
    """다른 작업이 이미 처리 중일 때."""


class HarnessRuntime:
    """실행 중인 작업과 만료 청소를 관리한다."""

    def __init__(self, store: RunStore) -> None:
        self.store = store
        self._semaphore = asyncio.Semaphore(1)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._sweeper: asyncio.Task[None] | None = None
        self._shutting_down = False

    # -- 종료 상태 --------------------------------------------------------
    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def busy_run_id(self) -> str | None:
        """처리 중인 Run ID. 없으면 None."""
        for run_id, task in self._tasks.items():
            if not task.done():
                return run_id
        return None

    # -- 작업 등록 --------------------------------------------------------
    def spawn(self, run_id: str, coro: Coroutine[Any, Any, None]) -> None:
        """Run 처리를 백그라운드 작업으로 등록한다.

        202 Accepted를 돌려주기 전에 부른다. 이미 처리 중이면 BusyError.
        """
        if self._shutting_down:
            coro.close()
            raise BusyError("서버가 종료 중입니다.")
        if self.busy_run_id() is not None:
            coro.close()
            raise BusyError("다른 작업이 이미 처리 중입니다.")

        task = asyncio.create_task(self._guarded(coro), name=f"run:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(run_id, None))

    async def _guarded(self, coro: Coroutine[Any, Any, None]) -> None:
        """한 번에 하나만 돌도록 감싼다. 예외는 orchestrator가 처리한다."""
        async with self._semaphore:
            await coro

    async def wait_for(self, run_id: str, timeout: float = 30.0) -> None:
        """테스트와 종료 절차에서 특정 작업이 끝나기를 기다린다."""
        task = self._tasks.get(run_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    async def wait_all(self, timeout: float = 30.0) -> None:
        tasks = [t for t in self._tasks.values() if not t.done()]
        if tasks:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait(tasks, timeout=timeout)

    # -- TTL 청소 ---------------------------------------------------------
    def start_sweeper(self, interval: int = SWEEP_INTERVAL_SECONDS) -> None:
        if self._sweeper is None:
            self._sweeper = asyncio.create_task(self._sweep_loop(interval), name="ttl-sweeper")

    async def _sweep_loop(self, interval: int) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                async with self.store.lock:
                    self.store.sweep_expired()
        except asyncio.CancelledError:  # 종료 시 정상 경로
            raise

    async def shutdown(self) -> None:
        """새 요청을 막고, 진행 중인 작업을 정리한 뒤 메모리를 비운다."""
        self._shutting_down = True
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
            self._sweeper = None

        for task in list(self._tasks.values()):
            task.cancel()
        await self.wait_all(timeout=5.0)
        self._tasks.clear()
        self.store.clear()
