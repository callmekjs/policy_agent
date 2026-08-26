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

from app.agents.draft_writing import DraftResultError
from app.agents.draft_writing import build_request as build_draft_request
from app.agents.draft_writing import parse_result as parse_draft_result
from app.agents.fact_extraction import AgentResultError, build_request, parse_result
from app.gates.conflict_gate import check_conflicts
from pydantic import ValidationError

from app.gates.draft_gate import blocking, check_draft
from app.harness.draft_contracts import (
    DraftCandidate,
    ValidationFinding,
    ValidationSeverity,
)
from app.gates.protection import apply_reviews, unreviewed_fact_ids
from app.gates.revision_gate import check_revision
from app.gates.draft_sections import build_fixed_sections
from app.gates.draft_template import (
    HARNESS_ID_PREFIX,
    HARNESS_OWNED,
    load_template,
)
from app.gates.evidence_gate import build_fact_ledger
from app.gates.final_text_gate import resolve_final_text
from app.gates.input_gate import check_input
from app.gates.source_role_gate import candidate_choices, check_source_roles
from app.harness.article_parser import (
    UNSUPPORTED_COUNT,
    ArticleParseError,
    parse_changed_articles,
    top_level_article,
)
from app.harness.contract_loader import load_writing_contract
from app.harness.review_contracts import (
    FactReview,
    RevisionAttempt,
    RevisionOutcome,
)
from app.harness.contracts import (
    ACTIVE_CONTRACT_ID,
    SUPPORTED_PROCEDURE_STAGE,
    SUPPORTED_PROCEDURE_STAGE_LABEL,
    CreateRunRequest,
    EffectStatus,
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
    ModelCallRequest,
    ModelGateway,
)
from app.infrastructure.run_store import RunStore

#: 아직 구현하지 않은 단계에 도달했을 때의 코드. 성공한 것처럼 보이지 않는다.
DAY1_SCOPE_LIMIT = "DAY1_SCOPE_LIMIT"

#: 고치기 Agent. 이름·프롬프트 판·출력 상한은 실행 중 바꾸지 않는다 (§7.2).
REVISION_AGENT_NAME = "RevisionAgent"
REVISION_PROMPT_VERSION = "revision_v1"
#: README §7.2가 정한 수정 출력 상한.
REVISION_MAX_OUTPUT_TOKENS = 4500

#: 화면과 초안에 같은 말로 나가는 효력 상태 이름.
EFFECT_STATUS_LABEL = "아직 법률 아님"

#: 문의처는 사람이 확인해야 채워진다. 만들어 내지 않는다.
CONTACT_PLACEHOLDER = "[문의처 확인 필요]"
RELEASE_DATE_PLACEHOLDER = "[보도일 확인 필요]"

#: 계약이 요구하는 안내 문구 (§2.16.2).
INTERNET_NOTICE = "※ 시스템이 인터넷에서 최신 상태를 별도로 확인한 것은 아닙니다."


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


#: 근거를 확인하지 못한 이유별 안내. 이유가 다르면 처방도 달라야 한다.
#: 원문에 아예 없는데 "한 번만 나오는 자료를 넣으라"고 하면 통하지 않는다.
REJECTION_GUIDE: dict[str, tuple[str, str]] = {
    "NOT_FOUND": (
        "AI가 제시한 근거 문구를 자료 원문에서 찾지 못했습니다.",
        "그 내용이 실제로 적힌 공식 자료를 넣어 주세요.",
    ),
    "UNKNOWN_EVIDENCE": (
        "AI가 제시한 근거 문구를 자료 원문에서 찾지 못했습니다.",
        "그 내용이 실제로 적힌 공식 자료를 넣어 주세요.",
    ),
    "AMBIGUOUS": (
        "근거 문구가 자료에서 여러 곳에 나와 어디를 가리키는지 알 수 없습니다.",
        "그 부분이 한 번만 나오는 자료를 넣거나, 해당 대목만 잘라 넣어 주세요.",
    ),
    "UNKNOWN_SOURCE": (
        "사실과 근거가 서로 다른 자료를 가리킵니다.",
        "그 사실이 적힌 자료를 함께 넣어 주세요.",
    ),
}


def _rejected_fact_issues(evidence, next_index: int = 1) -> list[Issue]:
    """근거를 확인하지 못해 버린 사실을 사람에게 보여 준다.

    버린 값이 어떤 값이었는지, 어느 자료 몇 행이었는지 함께 적는다. 내부 ID만
    남기면 화면에서 알아볼 수 없고, 그 값에 걸려 있던 충돌이 사라진 것도
    눈치챌 수 없다.

    이유가 다르면 Issue를 나눈다. 한 화면에서 두 문단이 서로 다른 진단을
    말하면 사용자가 무엇을 해야 할지 알 수 없다.
    """
    grouped: dict[str, list] = {}
    for problem in evidence.problems:
        if problem.kind in REJECTION_GUIDE and problem.value:
            grouped.setdefault(problem.kind, []).append(problem)

    issues: list[Issue] = []
    index = next_index
    for kind, problems in grouped.items():
        message, question = REJECTION_GUIDE[kind]
        lines = [f"· {p.describe()}" for p in problems]
        issues.append(
            Issue(
                issue_id=f"ISS-{index:03d}",
                code=IssueCode.REQUIRED_SOURCE_MISSING,
                subject=f"EVIDENCE_{kind}",
                message=(
                    f"다음 값은 쓰지 않았습니다. {message}\n"
                    + "\n".join(lines)
                ),
                question=question,
                source_ids=sorted({p.source_name for p in problems if p.source_name}),
                resolution_kind=ResolutionKind.NEW_RUN_WITH_SOURCES,
                requires_new_run=True,
            )
        )
        index += 1
    return issues


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
            final_text_confirmations=list(request.final_text_completeness_confirmations),
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
                if exc.code == "FACT_SCOPE_TOO_LARGE":
                    # 이것은 프로그램 고장이 아니라 **지원 범위를 넘은 자료**다.
                    # 기술 오류로 끝내면 사용자가 무엇을 해야 할지 알 수 없다.
                    # 쉬운 설명과 함께 사람에게 물어보는 자리로 보낸다.
                    run.issues = [
                        Issue(
                            issue_id="ISS-001",
                            code=IssueCode.REQUIRED_SOURCE_MISSING,
                            subject="UNSUPPORTED_SCOPE",
                            message=(
                                "이 자료는 지금 버전이 한 번에 처리할 수 있는 범위를 "
                                f"넘습니다. {exc.detail}"
                            ),
                            question=(
                                "이번 보도자료에 꼭 필요한 공식 자료만 남겨 새 작업으로 "
                                "다시 넣어 주세요."
                            ),
                            resolution_kind=ResolutionKind.NEW_RUN_WITH_SOURCES,
                            requires_new_run=True,
                        )
                    ]
                    self._transition(run, RunState.NEEDS_INPUT)
                    return

                self._fail(
                    run,
                    FailureKind.TECHNICAL,
                    exc.code,
                    f"AI 결과를 쓸 수 없습니다. {exc.detail}",
                    "잠시 뒤 새 작업으로 다시 시도해 주세요.",
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
            # 버린 사실과 충돌을 **함께** 보여 준다. 버린 사실 하나가 있다고
            # 진짜 충돌 질문을 가리면, 두 값이 어긋난다는 사실이 기록되지 않는다.
            rejected = _rejected_fact_issues(evidence)
            conflicts = check_conflicts(ledger, next_index=len(rejected) + 1)
            issues = rejected + conflicts

        async with self.store.lock:
            run = self.store.get(run_id)
            if run is None:
                return

            run.fact_ledger = ledger
            run.role_choices = candidate_choices(
                raw.source_role_candidates, evidence.locations
            )
            run.rejected_evidence = [p.describe() for p in evidence.problems]

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

            confirmations = list(run.final_text_confirmations)
            subject_input = run.announcement_subject_input or ""

        # 5) 최종 의결 내용을 코드가 확정한다. 못 하면 초안 없이 멈춘다.
        await self._write_draft(
            run_id,
            sources=sources,
            normalized=normalized,
            ledger=ledger,
            confirmations=confirmations,
            purpose=purpose,
            basis_date=basis_date,
            announcement_subject=subject_input,
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

    # -- 초안 작성 ---------------------------------------------------------
    async def _write_draft(
        self,
        run_id: str,
        *,
        sources: list,
        normalized: dict,
        ledger,
        confirmations: list,
        purpose: str,
        basis_date: str,
        announcement_subject: str,
    ) -> None:
        """최종 의결문 확정 -> 조문 계산 -> 초안 -> 초안 검사 (§2.11 5~6단계).

        어느 단계에서 멈추든 **초안은 만들어지지 않는다.** 사람에게는 왜 멈췄는지
        쉬운 말로 알린다.
        """
        # 5) 최종 의결 내용을 코드가 확정한다.
        draft_bill_number = next(
            (b.bill_number for b in ledger.bill_identities if b.is_draft_subject), ""
        )
        final_text, issues = resolve_final_text(
            sources, normalized, confirmations, draft_bill_number=draft_bill_number
        )
        if final_text is None:
            await self._needs_input(run_id, issues)
            return

        # 6) 변경 조문을 코드가 직접 센다. AI 값을 믿지 않는다.
        try:
            article_set = parse_changed_articles(final_text)
        except ArticleParseError as exc:
            await self._needs_input(run_id, [_article_issue(exc, final_text)])
            return

        # 7) AI가 만든 조문 비교와 코드 집합이 정확히 같은가 (§2.16.3).
        ai_articles = {
            top_level_article(c.provision_id) for c in ledger.provision_comparisons
        }
        counted = set(article_set.article_ids)
        if ai_articles != counted:
            missing = sorted(counted - ai_articles)
            extra = sorted(ai_articles - counted)
            detail = []
            if missing:
                detail.append(f"AI가 빠뜨린 조문: {', '.join(missing)}")
            if extra:
                detail.append(f"AI가 더 넣은 조문: {', '.join(extra)}")
            async with self.store.lock:
                run = self.store.get(run_id)
                if run is None:
                    return
                run.resolved_final_text = final_text
                run.changed_article_set = article_set
                self._fail(
                    run,
                    FailureKind.TECHNICAL,
                    "PROVISION_SET_MISMATCH",
                    "바뀐 조문을 코드와 AI가 다르게 셌습니다. " + ". ".join(detail) + ".",
                    "초안을 만들지 않았습니다. 새 작업으로 다시 시도해 주세요.",
                )
            return

        async with self.store.lock:
            run = self.store.get(run_id)
            if run is None:
                return
            run.resolved_final_text = final_text
            run.changed_article_set = article_set
            self._transition(run, RunState.DRAFTING)

        # 8) 초안 AI. 원문 전체가 아니라 확인된 재료만 준다.
        request = build_draft_request(
            purpose=purpose,
            basis_date=basis_date,
            procedure_stage=SUPPORTED_PROCEDURE_STAGE,
            effect_status=EffectStatus.NOT_A_LAW,
            procedure_stage_label=SUPPORTED_PROCEDURE_STAGE_LABEL,
            effect_status_label=EFFECT_STATUS_LABEL,
            ledger=ledger,
            final_text=final_text,
            article_set=article_set,
            announcement_subject=announcement_subject,
            contact_text=CONTACT_PLACEHOLDER,
        )
        call = await self.gateway.call(request)

        async with self.store.lock:
            run = self.store.get(run_id)
            if run is None:
                return
            if not call.is_fake:
                run.actual_model_calls += 1
                run.estimated_cost_usd += call.estimated_cost_usd

        template = load_template(load_writing_contract().template)

        try:
            candidate = parse_draft_result(call)
            # 값이 정해진 자리는 Harness가 직접 채운다. AI가 쓴 것은 버린다.
            candidate = candidate.model_copy(
                update={
                    "paragraphs": [
                        *build_fixed_sections(
                            template,
                            basis_date=basis_date,
                            procedure_stage_label=SUPPORTED_PROCEDURE_STAGE_LABEL,
                            effect_status_label=EFFECT_STATUS_LABEL,
                            announcement_subject=announcement_subject,
                            contact_text=template.placeholders.get(
                                "contact", CONTACT_PLACEHOLDER
                            ),
                            release_date_text=template.placeholders.get(
                                "release_date", RELEASE_DATE_PLACEHOLDER
                            ),
                            internet_notice=INTERNET_NOTICE,
                            # 부칙은 자료에 적힌 그대로다. AI에게 맡기지 않는다.
                            supplementary_rules=ledger.supplementary_rules,
                        ),
                        *[
                            p
                            for p in candidate.paragraphs
                            # 값이 정해진 자리는 버린다. Harness 이름표(`HS-`)를
                            # 흉내 낸 것도 버린다. 그러지 않으면 AI가 그 이름을
                            # 달고 검사를 건너뛸 수 있다.
                            if p.section_kind not in HARNESS_OWNED
                            and not p.paragraph_id.startswith(HARNESS_ID_PREFIX)
                        ],
                    ]
                }
            )
        except DraftResultError as exc:
            async with self.store.lock:
                run = self.store.get(run_id)
                if run is None:
                    return
                self._fail(
                    run,
                    FailureKind.TECHNICAL,
                    exc.code,
                    f"AI 초안을 쓸 수 없습니다. {exc.detail}",
                    "초안을 만들지 않았습니다. 새 작업으로 다시 시도해 주세요.",
                )
            return

        async with self.store.lock:
            run = self.store.get(run_id)
            if run is None:
                return
            self._transition(run, RunState.CHECKING_DRAFT)

        # 9) 초안 검사. 차단이 하나라도 있으면 초안을 내주지 않는다.
        findings = check_draft(
            candidate,
            ledger,
            final_text,
            article_set,
            normalized,
            announcement_subject=announcement_subject,
            # 공식 발언문 자료가 있을 때만 남의 말을 옮길 수 있다 (§2.16.2).
            template=template,
            has_statement_source=any(
                s.use_scope is SourceUseScope.ATTRIBUTED_STATEMENT_ONLY for s in sources
            ),
            # Harness가 스스로 넣는 정형 표시. AI가 지어낸 말이 아니다.
            fixed_labels=(
                SUPPORTED_PROCEDURE_STAGE_LABEL,
                EFFECT_STATUS_LABEL,
                CONTACT_PLACEHOLDER,
                RELEASE_DATE_PLACEHOLDER,
                INTERNET_NOTICE,
                basis_date,
                # Harness가 스스로 만든 문단. 지어낸 값이 아니다.
                *[
                    p.text
                    for p in candidate.paragraphs
                    if p.paragraph_id.startswith("HS-")
                ],
            ),
        )
        blocked = blocking(findings)

        async with self.store.lock:
            run = self.store.get(run_id)
            if run is None:
                return
            run.validation_findings = findings
            if blocked:
                self._fail(
                    run,
                    FailureKind.QUALITY_GATE,
                    "DRAFT_BLOCKED",
                    f"안전 검사에서 막힌 항목이 {len(blocked)}건 있어 초안을 "
                    "내주지 않았습니다.",
                    "막힌 이유를 확인하고 공식 자료를 보완해 새 작업으로 "
                    "다시 시도해 주세요.",
                )
                return

            run.draft = candidate
            run.draft_version = candidate.version
            self._transition(run, RunState.REVIEW_READY)

    # -- 5일차: 사람이 확인하고 고친다 -------------------------------------

    def _check_candidate(self, run: Run, candidate: DraftCandidate) -> list:
        """초안 하나에 **4일차 검사를 그대로** 건다 (5일차 합격선 `L1`).

        수정본이라고 검사를 덜 받으면 안 된다. 처음 초안이 통과한 검사를
        수정본도 통과해야 한다. 검사 재료는 Run에 남아 있는 것으로 다시 만든다.
        """
        normalized = {
            source.source_id: normalize_source(source.raw_text)
            for source in run.sources
        }
        template = load_template(load_writing_contract().template)
        return check_draft(
            candidate,
            run.fact_ledger,
            run.resolved_final_text,
            run.changed_article_set,
            normalized,
            announcement_subject=run.announcement_subject_input or "",
            template=template,
            has_statement_source=any(
                s.use_scope is SourceUseScope.ATTRIBUTED_STATEMENT_ONLY
                for s in run.sources
            ),
            fixed_labels=(
                SUPPORTED_PROCEDURE_STAGE_LABEL,
                EFFECT_STATUS_LABEL,
                CONTACT_PLACEHOLDER,
                RELEASE_DATE_PLACEHOLDER,
                INTERNET_NOTICE,
                run.basis_date.isoformat(),
                *[
                    p.text
                    for p in candidate.paragraphs
                    if p.paragraph_id.startswith("HS-")
                ],
            ),
        )

    def review_facts(self, run_id: str, reviews: list[FactReview]) -> Run:
        """사람이 사실을 확인한 결과를 받는다 (`M1`·`K1`).

        확인이 곧 보호다. "맞다"를 누른 보호 후보만 `protected=true`가 된다.
        AI는 이 판정에 끼어들지 못한다 (README §4.3).
        """
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.fact_ledger is None:
            raise ValueError("아직 사실이 정리되지 않았습니다.")
        merged = {r.fact_id: r for r in run.fact_reviews}
        known = {f.fact_id for f in run.fact_ledger.facts}
        for review in reviews:
            if review.fact_id not in known:
                raise ValueError(f"자료에 없는 사실입니다: {review.fact_id}")
            merged[review.fact_id] = review
        run.fact_reviews = list(merged.values())
        run.fact_ledger = apply_reviews(run.fact_ledger, run.fact_reviews)
        run.last_user_action_at = _now()
        run.updated_at = run.last_user_action_at
        return run

    def finalize(self, run_id: str) -> Run:
        """확인을 마친 초안을 완료로 옮긴다 (`M1`).

        **하나라도 안 본 사실이 있으면 거부한다.** 확인 절차가 있는데 건너뛸
        수 있으면 없는 것과 같다.
        """
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.draft is None:
            raise ValueError("아직 초안이 없습니다.")
        left = unreviewed_fact_ids(run.fact_ledger, run.fact_reviews)
        if left:
            raise ValueError(
                f"아직 확인하지 않은 사실이 {len(left)}건 있습니다. "
                "모두 확인해야 내려받을 수 있습니다."
            )
        self._transition(run, RunState.DRAFT_READY)
        run.finished_at = run.updated_at
        run.last_user_action_at = run.updated_at
        return run

    async def revise(
        self, run_id: str, *, client_request_id: str, instruction: str
    ) -> Run:
        """사람이 부탁한 대로 초안을 고친다.

        **고치는 데 실패해도 이전 초안을 그대로 둔다** (`K4`). 사람은 고쳐
        달라고 했을 뿐인데 있던 것까지 없어지면 프로그램을 믿을 수 없다.
        """
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.draft is None:
            raise ValueError("아직 초안이 없습니다.")

        # 같은 키로 두 번 오면 한 번만 처리한다 (`N2`).
        # 사용자가 버튼을 두 번 누르는 일은 늘 일어난다.
        for attempt in run.revision_attempts:
            if attempt.client_request_id == client_request_id:
                return run

        previous = run.draft
        self._transition(run, RunState.REVISING)
        call = await self.gateway.call(
            ModelCallRequest(
                agent_name=REVISION_AGENT_NAME,
                prompt_version=REVISION_PROMPT_VERSION,
                payload={
                    "draft": json.loads(previous.model_dump_json()),
                    "instruction": instruction,
                },
                max_output_tokens=REVISION_MAX_OUTPUT_TOKENS,
            )
        )
        if not call.is_fake:
            run.actual_model_calls += 1
            run.estimated_cost_usd += call.estimated_cost_usd
        self._transition(run, RunState.CHECKING_REVISION)

        blocked: list = []
        try:
            revised = DraftCandidate.model_validate(
                (call.result or {}).get("result") or {}
            )
        except ValidationError:
            revised = None
            blocked = [
                ValidationFinding(
                    finding_id="RV-000",
                    rule_id="REVISION_SCHEMA_INVALID",
                    rule_document="README §2.10",
                    affected_part="수정본",
                    severity=ValidationSeverity.BLOCKING,
                    message="고친 결과가 정해진 양식과 맞지 않습니다.",
                )
            ]

        if revised is not None:
            findings = [
                *check_revision(
                    previous=previous,
                    revised=revised,
                    ledger=run.fact_ledger,
                    fact_reviews=run.fact_reviews,
                ),
                *self._check_candidate(run, revised),
            ]
            blocked = blocking(findings)

        now = _now()
        if blocked:
            # **이전 초안을 건드리지 않는다.** 무엇이 막았는지만 남긴다.
            run.revision_attempts.append(
                RevisionAttempt(
                    attempt_id=f"RA-{len(run.revision_attempts) + 1:03d}",
                    client_request_id=client_request_id,
                    instruction=instruction,
                    outcome=RevisionOutcome.REJECTED,
                    blocking_rule_ids=sorted({f.rule_id for f in blocked}),
                    attempted_at=now,
                )
            )
        else:
            run.draft_history.append(previous)
            run.draft = revised
            run.draft_version = previous.version + 1
            run.revision_attempts.append(
                RevisionAttempt(
                    attempt_id=f"RA-{len(run.revision_attempts) + 1:03d}",
                    client_request_id=client_request_id,
                    instruction=instruction,
                    outcome=RevisionOutcome.APPLIED,
                    resulting_version=run.draft_version,
                    attempted_at=now,
                )
            )
        self._transition(run, RunState.REVIEW_READY)
        run.last_user_action_at = now
        return run

    async def _needs_input(self, run_id: str, issues: list[Issue]) -> None:
        """초안 없이 사람에게 묻고 멈춘다."""
        async with self.store.lock:
            run = self.store.get(run_id)
            if run is None:
                return
            run.issues = issues
            self._transition(run, RunState.NEEDS_INPUT)


def _article_issue(exc: ArticleParseError, final_text) -> Issue:
    """조문을 셀 수 없을 때의 Issue. 왜 못 셌는지에 따라 푸는 방법이 다르다."""
    if exc.subject.startswith("UNSUPPORTED_SYNTAX"):
        resolution = ResolutionKind.UNSUPPORTED_IN_V1
        question = "1차에서 지원하는 형태의 공식 자료로 다시 시도해 주세요."
    elif exc.code == UNSUPPORTED_COUNT:
        resolution = ResolutionKind.UNSUPPORTED_IN_V1
        question = "조문을 나누어 보도자료를 따로 만들어 주세요."
    else:
        resolution = ResolutionKind.NEW_RUN_WITH_SOURCES
        question = "개정문이 처음부터 끝까지 담긴 공식 자료를 새 작업으로 넣어 주세요."
    return Issue(
        issue_id="ISS-001",
        code=IssueCode(exc.code),
        subject=exc.subject,
        message=(
            f"‘{final_text.source_name}’에서 바뀐 조문을 셀 수 없습니다. {exc.detail}"
        ),
        question=question,
        source_ids=[final_text.source_id],
        resolution_kind=resolution,
        requires_new_run=resolution is ResolutionKind.NEW_RUN_WITH_SOURCES,
    )
