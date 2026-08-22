"""Run 상태와 허용된 전이 (README §2.13).

Harness만 상태를 바꾼다. Agent는 이 모듈을 통해 상태를 변경할 수 없다.
허용 표에 없는 전이는 InvalidTransition으로 거부한다.
"""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    """내부 상태. 사용자에게는 §2.8의 쉬운 이름만 보여준다."""

    CREATED = "CREATED"
    VALIDATING_INPUT = "VALIDATING_INPUT"
    NEEDS_INPUT = "NEEDS_INPUT"
    EXTRACTING_FACTS = "EXTRACTING_FACTS"
    DRAFTING = "DRAFTING"
    CHECKING_DRAFT = "CHECKING_DRAFT"
    REVIEW_READY = "REVIEW_READY"
    REVISING = "REVISING"
    CHECKING_REVISION = "CHECKING_REVISION"
    DRAFT_READY = "DRAFT_READY"
    FAILED = "FAILED"


class FailureKind(StrEnum):
    """실패 종류. 품질 차단과 기술 오류를 섞지 않는다 (README §2.13)."""

    QUALITY_GATE = "QUALITY_GATE"
    TECHNICAL = "TECHNICAL"


#: 허용된 전이만 담는다. README §2.13의 표를 그대로 옮긴 것이며
#: 여기 없는 조합은 전부 거부된다.
ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.VALIDATING_INPUT}),
    RunState.VALIDATING_INPUT: frozenset(
        {
            RunState.NEEDS_INPUT,
            RunState.EXTRACTING_FACTS,
            RunState.DRAFTING,
            RunState.FAILED,
        }
    ),
    RunState.NEEDS_INPUT: frozenset({RunState.VALIDATING_INPUT}),
    RunState.EXTRACTING_FACTS: frozenset(
        {RunState.NEEDS_INPUT, RunState.DRAFTING, RunState.FAILED}
    ),
    RunState.DRAFTING: frozenset({RunState.CHECKING_DRAFT, RunState.FAILED}),
    RunState.CHECKING_DRAFT: frozenset({RunState.REVIEW_READY, RunState.FAILED}),
    RunState.REVIEW_READY: frozenset({RunState.REVISING, RunState.DRAFT_READY}),
    RunState.REVISING: frozenset({RunState.CHECKING_REVISION, RunState.FAILED}),
    RunState.CHECKING_REVISION: frozenset({RunState.REVIEW_READY}),
    # 종료 상태에서는 어떤 전이도 허용하지 않는다.
    RunState.DRAFT_READY: frozenset(),
    RunState.FAILED: frozenset(),
}

#: 더 이상 움직이지 않는 상태.
TERMINAL_STATES: frozenset[RunState] = frozenset({RunState.DRAFT_READY, RunState.FAILED})

#: 백그라운드 작업이 진행 중인 상태. 이때는 삭제·새 작업을 막는다 (§2.12).
BUSY_STATES: frozenset[RunState] = frozenset(
    {
        RunState.CREATED,
        RunState.VALIDATING_INPUT,
        RunState.EXTRACTING_FACTS,
        RunState.DRAFTING,
        RunState.CHECKING_DRAFT,
        RunState.REVISING,
        RunState.CHECKING_REVISION,
    }
)

#: DELETE를 허용하는 상태 (README §3.10).
DELETABLE_STATES: frozenset[RunState] = frozenset(
    {
        RunState.NEEDS_INPUT,
        RunState.REVIEW_READY,
        RunState.DRAFT_READY,
        RunState.FAILED,
    }
)

#: 내부 상태를 사용자 화면 문구로 바꾼다 (README §2.8).
USER_FACING_STATUS: dict[RunState, str] = {
    RunState.CREATED: "AI 처리 중",
    RunState.VALIDATING_INPUT: "AI 처리 중",
    RunState.NEEDS_INPUT: "입력 보완 필요",
    RunState.EXTRACTING_FACTS: "AI 처리 중",
    RunState.DRAFTING: "AI 처리 중",
    RunState.CHECKING_DRAFT: "AI 처리 중",
    RunState.REVIEW_READY: "사용자 검토",
    RunState.REVISING: "AI 처리 중",
    RunState.CHECKING_REVISION: "AI 처리 중",
    RunState.DRAFT_READY: "초안 준비 완료",
    RunState.FAILED: "처리 실패",
}


class InvalidTransition(RuntimeError):
    """허용 표에 없는 상태 전이를 시도했을 때."""

    def __init__(self, current: RunState, target: RunState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"허용되지 않은 상태 전이입니다: {current} -> {target}")


def can_transition(current: RunState, target: RunState) -> bool:
    """current에서 target으로 갈 수 있는지 확인한다."""
    return target in ALLOWED_TRANSITIONS[current]


def assert_transition(current: RunState, target: RunState) -> None:
    """허용되지 않은 전이면 InvalidTransition을 올린다."""
    if not can_transition(current, target):
        raise InvalidTransition(current, target)


def user_facing_status(state: RunState) -> str:
    """사용자에게 보여줄 쉬운 상태 이름."""
    return USER_FACING_STATUS[state]
