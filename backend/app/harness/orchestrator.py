"""고정 순서와 상태 전환 (README §2.13).

Harness는 글을 쓰는 AI가 아니라 코드 기반 진행 관리자다. 순서·상태·Gate·한도를
소유하고, Agent는 읽기 전용 입력으로 후보 결과만 만든다.

1일차 범위는 `CREATED → VALIDATING_INPUT → (NEEDS_INPUT | EXTRACTING_FACTS)`까지다.
사실 정리 Gate는 2일차, 초안은 3일차에 이어서 붙인다. 아직 만들지 않은 단계를
성공한 것처럼 보여주지 않고 `FAILED/TECHNICAL`과 `DAY1_SCOPE_LIMIT`로 멈춘다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime

from app.agents.fact_extraction import AgentResultError, build_request, parse_result
from app.gates.conflict_gate import check_conflicts
from app.gates.evidence_gate import build_fact_ledger
from app.gates.input_gate import check_input
from app.gates.source_role_gate import candidate_choices, check_source_roles
from app.harness.contracts import (
    ACTIVE_CONTRACT_ID,
    SUPPORTED_PROCEDURE_STAGE_LABEL,
    CreateRunRequest,
    ExternalAiConfirmation,
    Issue,
    IssueCode,
    ResolutionKind,
    Run,
    SourceRole,
    SourceUseScope,
    StoredSource,
)
from app.harness.source_normalizer import SourceNormalizationError, normalize_source
from app.harness.states import FailureKind, RunState, assert_transition
from app.infrastructure.model_gateway import (
    CONFIGURED_MODEL,
    CONFIGURED_PROVIDER,
    ModelGateway,
)
from app.infrastructure.run_store import RunStore

#: 아직 구현하지 않은 단계에 도달했을 때의 코드. 성공한 것처럼 보이지 않는다.
DAY1_SCOPE_LIMIT = "DAY1_SCOPE_LIMIT"
DAY3_SCOPE_LIMIT = "DAY3_SCOPE_LIMIT"


def _now() -> datetime:
    return datetime.now(UTC)


def new_run_id() -> str:
    """사람이 화면에서 읽고 말할 수 있는 실행 ID."""
    return f"RUN-{uuid.uuid4().hex[:12].upper()}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload_hash(request: CreateRunRequest) -> str:
    """멱등 키 비교에 쓸 요청 내용 해시 (README §2.13).

    멱등 키 자체는 빼고 나머지 입력만 정규화해서 센다. 같은 키인데 내용이
    달라지면 이 값이 달라지므로 거부할 수 있다.
    """
    payload = request.model_dump(mode="json", exclude={"client_request_id"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


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
            request_payload_sha256=payload_hash(request),
            purpose=request.purpose.strip(),
            disclosure=request.disclosure,
            basis_date=request.basis_date,
            announcement_subject_input=(request.announcement_subject or "").strip() or None,
            sources=sources,
            external_ai=confirmation,
        )
        self.store.put(run)
        return run

    # -- 사실 정리 ---------------------------------------------------------
    async def _extract_facts(self, run_id: str) -> None:
        """자료를 정규화하고 사실 후보를 뽑아 Gate를 통과시킨다 (§2.11 순서 2~4).

        3일차 범위는 여기까지다. 통과하면 초안 작성 자리에서 정직하게 멈춘다.
        """
        async with self.store.lock:
            run = self.store.get(run_id)
            if run is None:
                return
            sources = list(run.sources)
            purpose = run.purpose
            disclosure = run.disclosure
            basis_date = run.basis_date.isoformat()

        # 2) 자료 정규화. 실패하면 AI를 부르지 않는다.
        normalized = {
            source.source_id: normalize_source(source.raw_text) for source in sources
        }

        async with self.store.lock:
            run = self.store.get(run_id)
            if run is None:
                return
            for source in run.sources:
                shape = normalized[source.source_id]
                source.text_version = shape.version
                source.normalized_sha256 = shape.normalized_sha256
                source.normalized_char_count = len(shape.normalized_text)

        # 3) 사실·역할 후보 추출. 가짜 게이트웨이면 외부 호출 0회다.
        request = build_request(
            purpose=purpose,
            disclosure=disclosure,
            basis_date=basis_date,
            procedure_stage_label=SUPPORTED_PROCEDURE_STAGE_LABEL,
            effect_status_label="아직 법률 아님",
            sources=sources,
            normalized=normalized,
        )
        call = await self.gateway.call(request)

        async with self.store.lock:
            run = self.store.get(run_id)
            if run is None:
                return
            if not call.is_fake:
                run.actual_model_calls += 1
                run.estimated_cost_usd += call.estimated_cost_usd

        try:
            raw = parse_result(call)
        except AgentResultError as exc:
            async with self.store.lock:
                run = self.store.get(run_id)
                if run is None:
                    return
                self._fail(
                    run,
                    FailureKind.TECHNICAL,
                    exc.code,
                    f"AI 결과를 쓸 수 없습니다. {exc.detail}",
                    "잠시 뒤 새 작업으로 다시 시도해 주세요. 자료가 많으면 줄여 주세요.",
                )
            return

        # 4) 근거·역할·충돌 Gate. 순서는 §2.11의 고정 우선순위를 따른다.
        names = {s.source_id: s.display_name for s in sources}
        ledger, evidence = build_fact_ledger(raw, normalized, names)

        role_issues = check_source_roles(
            sources, raw.source_role_candidates, evidence.locations
        )
        issues = list(role_issues)
        if not issues:
            issues = check_conflicts(ledger)

        async with self.store.lock:
            run = self.store.get(run_id)
            if run is None:
                return

            run.fact_ledger = ledger
            run.role_choices = candidate_choices(
                raw.source_role_candidates, evidence.locations
            )
            run.rejected_evidence = [
                f"{p.fact_id}: {p.detail}" for p in evidence.problems
            ]

            if issues:
                run.issues = issues
                self._transition(run, RunState.NEEDS_INPUT)
                return

            if not ledger.ready:
                run.issues = [
                    Issue(
                        issue_id="ISS-001",
                        code=IssueCode.REQUIRED_SOURCE_MISSING,
                        subject="VERIFIED_FACT",
                        message=(
                            "자료에서 원문 근거가 확인된 사실을 하나도 찾지 못했습니다."
                        ),
                        question="의안번호·표결 결과가 담긴 공식 자료를 넣어 주세요.",
                        resolution_kind=ResolutionKind.NEW_RUN_WITH_SOURCES,
                        requires_new_run=True,
                    )
                ]
                self._transition(run, RunState.NEEDS_INPUT)
                return

            # 여기까지가 3일차 범위다. 초안 작성은 4일차에 붙인다.
            self._fail(
                run,
                FailureKind.TECHNICAL,
                DAY3_SCOPE_LIMIT,
                f"자료에서 사실 {len(ledger.facts)}건을 정리했습니다. "
                "초안을 쓰는 단계는 아직 만들지 않았습니다.",
                "사실과 원문 근거가 연결되는지 확인하는 단계입니다. "
                "초안 작성은 4일차에 이어서 만듭니다.",
            )

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

            await self._extract_facts(run_id)
        except SourceNormalizationError as exc:
            async with self.store.lock:
                run = self.store.get(run_id)
                if run is None:
                    return
                self._fail(
                    run,
                    FailureKind.TECHNICAL,
                    SourceNormalizationError.code,
                    f"자료 원문을 그대로 보존하지 못했습니다. {exc}",
                    "자료를 다시 붙여 넣고 새 작업으로 시도해 주세요.",
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
