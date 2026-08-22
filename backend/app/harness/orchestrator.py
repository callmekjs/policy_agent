"""고정 순서와 상태 전환 (README §2.13).

Harness는 글을 쓰는 AI가 아니라 코드 기반 진행 관리자다. 순서·상태·Gate·한도를
소유하고, Agent는 읽기 전용 입력으로 후보 결과만 만든다.

1일차 범위는 `CREATED → VALIDATING_INPUT → (NEEDS_INPUT | EXTRACTING_FACTS)`까지다.
사실 정리 Gate는 2일차, 초안은 3일차에 이어서 붙인다. 아직 만들지 않은 단계를
성공한 것처럼 보여주지 않고 `FAILED/TECHNICAL`과 `DAY1_SCOPE_LIMIT`로 멈춘다.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime

from app.gates.input_gate import check_input
from app.harness.contracts import (
    ACTIVE_CONTRACT_ID,
    CreateRunRequest,
    ExternalAiConfirmation,
    Run,
    SourceRole,
    SourceUseScope,
    StoredSource,
)
from app.harness.states import FailureKind, RunState, assert_transition
from app.infrastructure.model_gateway import (
    CONFIGURED_MODEL,
    CONFIGURED_PROVIDER,
    ModelCallRequest,
    ModelGateway,
)
from app.infrastructure.run_store import RunStore

#: 1일차에서 아직 구현하지 않은 단계에 도달했을 때의 코드.
DAY1_SCOPE_LIMIT = "DAY1_SCOPE_LIMIT"


def _now() -> datetime:
    return datetime.now(UTC)


def new_run_id() -> str:
    """사람이 화면에서 읽고 말할 수 있는 실행 ID."""
    return f"RUN-{uuid.uuid4().hex[:12].upper()}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Orchestrator:
    """Run 하나의 진행을 책임진다."""

    def __init__(self, store: RunStore, gateway: ModelGateway) -> None:
        self.store = store
        self.gateway = gateway

    # -- 상태 전이 --------------------------------------------------------
    def _transition(self, run: Run, target: RunState) -> None:
        """허용 표에 있는 전이만 적용한다."""
        assert_transition(RunState(run.state), target)
        run.state = target.value
        run.updated_at = _now()

    def _fail(
        self,
        run: Run,
        kind: FailureKind,
        code: str,
        message: str,
        next_action: str,
    ) -> None:
        self._transition(run, RunState.FAILED)
        run.failure_kind = kind.value
        run.failure_code = code
        run.failure_message = message
        run.next_action = next_action
        run.finished_at = run.updated_at

    # -- Run 생성 ---------------------------------------------------------
    def create_run(self, request: CreateRunRequest) -> Run:
        """입력을 Run으로 만든다. 아직 검사하지 않은 CREATED 상태다."""
        now = _now()
        sources = [
            StoredSource(
                source_id=f"SRC-{i:02d}",
                display_name=(s.display_name.strip() or f"붙여넣기 자료 {i}"),
                role=s.role,
                use_scope=(
                    SourceUseScope.ATTRIBUTED_STATEMENT_ONLY
                    if s.role is SourceRole.OFFICIAL_STATEMENT
                    else SourceUseScope.FULL_FACT
                ),
                input_method=s.input_method,
                original_filename=s.original_filename,
                original_page=s.original_page,
                original_document_id=s.original_document_id,
                supplied_as_official=s.supplied_as_official,
                char_count=len(s.text),
                raw_text=s.text,
                raw_sha256=sha256_text(s.text),
            )
            for i, s in enumerate(request.sources, start=1)
        ]

        confirmation: ExternalAiConfirmation | None = None
        if request.external_ai_transfer_confirmed:
            confirmation = ExternalAiConfirmation(
                policy_version=request.external_ai_policy_version,
                confirmed_at=now,
                provider=CONFIGURED_PROVIDER,
                model=CONFIGURED_MODEL,
            )

        run = Run(
            run_id=new_run_id(),
            state=RunState.CREATED.value,
            contract_id=ACTIVE_CONTRACT_ID,
            created_at=now,
            updated_at=now,
            last_user_action_at=now,
            client_request_id=request.client_request_id,
            purpose=request.purpose.strip(),
            disclosure=request.disclosure,
            basis_date=request.basis_date,
            announcement_subject_input=(request.announcement_subject or "").strip() or None,
            sources=sources,
            external_ai=confirmation,
        )
        self.store.put(run)
        return run

    # -- 처리 -------------------------------------------------------------
    async def process(self, run_id: str, request: CreateRunRequest, today: date | None = None) -> None:
        """백그라운드에서 Run을 진행한다. 모든 예외를 기술 실패로 기록한다."""
        try:
            async with self.store.lock:
                run = self.store.get(run_id)
                if run is None:  # 처리 시작 전에 삭제됐거나 만료됨
                    return
                self._transition(run, RunState.VALIDATING_INPUT)

            issues = check_input(request, today or datetime.now(UTC).date())

            async with self.store.lock:
                run = self.store.get(run_id)
                if run is None:
                    return
                run.issues = issues

                if issues:
                    self._transition(run, RunState.NEEDS_INPUT)
                    return

                self._transition(run, RunState.EXTRACTING_FACTS)

            # 가짜 ModelGateway로 사실 추출 자리를 한 번 지난다.
            # 실제 외부 호출은 0회이고 비용은 0달러다.
            result = await self.gateway.call(
                ModelCallRequest(
                    agent_name="FactExtractionAgent",
                    prompt_version="day1-placeholder",
                    payload={"run_id": run_id},
                    max_output_tokens=12_000,
                )
            )

            async with self.store.lock:
                run = self.store.get(run_id)
                if run is None:
                    return
                if not result.is_fake:  # 1일차에는 진짜 호출을 허용하지 않는다
                    run.actual_model_calls += 1
                    run.estimated_cost_usd += result.estimated_cost_usd

                self._fail(
                    run,
                    FailureKind.TECHNICAL,
                    DAY1_SCOPE_LIMIT,
                    "여기까지가 개발 1일차 범위입니다. 자료에서 사실을 정리하는 단계는 "
                    "아직 만들지 않았습니다.",
                    "입력·상태·규칙 파일이 제대로 도는지 확인하는 단계입니다. "
                    "사실 정리와 초안 작성은 2~3일차에 이어서 만듭니다.",
                )
        except Exception as exc:  # noqa: BLE001 - 모든 예외를 기술 실패로 남긴다
            async with self.store.lock:
                run = self.store.get(run_id)
                if run is None:
                    return
                if RunState(run.state) is not RunState.FAILED:
                    run.state = RunState.FAILED.value
                    run.updated_at = _now()
                    run.finished_at = run.updated_at
                run.failure_kind = FailureKind.TECHNICAL.value
                run.failure_code = type(exc).__name__
                run.failure_message = "처리 중 문제가 생겼습니다."
                run.next_action = "잠시 뒤 새 작업으로 다시 시도해 주세요."
