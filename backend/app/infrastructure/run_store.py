"""프로세스 메모리의 임시 실행 저장소 (README §2.1, §2.12).

원문·초안·수정 요청은 여기에만 둔다. 데이터베이스와 파일 저장은 쓰지 않으므로
서버가 꺼지면 전부 사라진다. 마지막 사용자 동작 뒤 2시간이 지나면 자동 삭제한다.
상태 조회(polling)는 사용자 동작으로 세지 않는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from app.harness.contracts import RUN_TTL_SECONDS, Run


def _now() -> datetime:
    return datetime.now(UTC)


class RunStore:
    """한 번에 작업 1건만 두는 메모리 저장소.

    1차 프로토타입은 단일 사용자·단일 worker이므로 Run은 최대 1개다.
    """

    def __init__(
        self,
        ttl_seconds: int = RUN_TTL_SECONDS,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._runs: dict[str, Run] = {}
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        """상태 변경을 직렬화하는 잠금."""
        return self._lock

    def _is_expired(self, run: Run) -> bool:
        age = (self._clock() - run.last_user_action_at).total_seconds()
        return age >= self._ttl_seconds

    def get(self, run_id: str) -> Run | None:
        """만료되지 않은 Run만 돌려준다. 만료됐으면 지우고 None."""
        run = self._runs.get(run_id)
        if run is None:
            return None
        if self._is_expired(run):
            del self._runs[run_id]
            return None
        return run

    def find_by_client_request_id(self, client_request_id: str) -> Run | None:
        """같은 멱등 키의 기존 Run을 찾는다. 중복 클릭·중복 과금을 막는다."""
        for run in list(self._runs.values()):
            if self._is_expired(run):
                del self._runs[run.run_id]
                continue
            if run.client_request_id == client_request_id:
                return run
        return None

    def active_runs(self) -> list[Run]:
        """만료되지 않은 Run 전체."""
        self.sweep_expired()
        return list(self._runs.values())

    def put(self, run: Run) -> None:
        self._runs[run.run_id] = run

    def delete(self, run_id: str) -> bool:
        return self._runs.pop(run_id, None) is not None

    def clear(self) -> None:
        """서버 종료 시 메모리를 비운다."""
        self._runs.clear()

    def touch(self, run: Run) -> None:
        """실제 사용자 동작이 있었을 때만 TTL을 늘린다."""
        run.last_user_action_at = self._clock()
        run.updated_at = run.last_user_action_at

    def sweep_expired(self) -> list[str]:
        """만료된 Run을 지우고 지운 ID 목록을 돌려준다."""
        removed: list[str] = []
        for run_id, run in list(self._runs.items()):
            if self._is_expired(run):
                del self._runs[run_id]
                removed.append(run_id)
        return removed
