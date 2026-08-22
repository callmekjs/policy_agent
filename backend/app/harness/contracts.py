"""내부 데이터 계약 (README §2.10).

사용자는 JSON을 볼 필요가 없지만, 화면·API·Harness·Agent가 같은 의미로
정보를 주고받도록 여기서 모양을 고정한다. 1일차 범위는 Run·Source·Issue와
API 입출력이며, Fact·Draft 계열은 2~5일차에 이 파일에 이어서 추가한다.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# ---------------------------------------------------------------------------
# 고정 enum
# ---------------------------------------------------------------------------


class Disclosure(StrEnum):
    """공개 범위. 내부·엠바고는 1차 프로토타입에서 차단한다 (§2.2)."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    EMBARGO = "EMBARGO"


class SourceRole(StrEnum):
    """자료 역할 (§2.16.2).

    사용자는 기본값 UNKNOWN(`잘 모르겠음`)으로 두고, AI가 제안한 후보 중에서
    고를 수 있다. Harness·API·테스트가 이 enum 하나를 같이 쓴다.
    """

    UNKNOWN = "UNKNOWN"
    BILL_INFORMATION = "BILL_INFORMATION"
    CURRENT_PROVISION = "CURRENT_PROVISION"
    INTRODUCED_TEXT = "INTRODUCED_TEXT"
    PLENARY_AGENDA_TEXT = "PLENARY_AGENDA_TEXT"
    COMMITTEE_FINAL_TEXT = "COMMITTEE_FINAL_TEXT"
    PLENARY_VOTE_RESULT = "PLENARY_VOTE_RESULT"
    PLENARY_FINAL_TEXT = "PLENARY_FINAL_TEXT"
    BILL_RELATION = "BILL_RELATION"
    PROMULGATED_TEXT = "PROMULGATED_TEXT"
    SUPPLEMENTARY_PROVISION = "SUPPLEMENTARY_PROVISION"
    OFFICIAL_REASON = "OFFICIAL_REASON"
    OFFICIAL_STATEMENT = "OFFICIAL_STATEMENT"


#: 화면에 보여줄 쉬운 자료 역할 이름.
SOURCE_ROLE_LABELS: dict[SourceRole, str] = {
    SourceRole.UNKNOWN: "잘 모르겠음",
    SourceRole.BILL_INFORMATION: "의안정보",
    SourceRole.CURRENT_PROVISION: "현행 조문",
    SourceRole.INTRODUCED_TEXT: "발의안",
    SourceRole.PLENARY_AGENDA_TEXT: "본회의 상정안",
    SourceRole.COMMITTEE_FINAL_TEXT: "위원회 최종문",
    SourceRole.PLENARY_VOTE_RESULT: "본회의 표결 결과",
    SourceRole.PLENARY_FINAL_TEXT: "본회의 최종문",
    SourceRole.BILL_RELATION: "대안 관계",
    SourceRole.PROMULGATED_TEXT: "공포 개정문",
    SourceRole.SUPPLEMENTARY_PROVISION: "부칙",
    SourceRole.OFFICIAL_REASON: "공식 제안·개정이유",
    SourceRole.OFFICIAL_STATEMENT: "공식 발언문",
}


class SourceUseScope(StrEnum):
    """자료를 어디까지 쓸 수 있는지 (§2.15.4). FactProvenance와 완전히 분리한다."""

    FULL_FACT = "FULL_FACT"
    ATTRIBUTED_STATEMENT_ONLY = "ATTRIBUTED_STATEMENT_ONLY"
    STYLE_ONLY = "STYLE_ONLY"


class FactProvenance(StrEnum):
    """사실의 허용 출처. 이 세 값만 존재한다 (§2.15.4).

    STYLE_ONLY는 여기 없다. 참고 사례에서는 Fact를 한 건도 만들지 않는다.
    """

    OFFICIAL_SOURCE = "OFFICIAL_SOURCE"
    USER_CONFIRMED = "USER_CONFIRMED"
    APPROVED_PROFILE = "APPROVED_PROFILE"


class ProcedureStage(StrEnum):
    """절차 단계 (§2.16.1)."""

    INTRODUCED = "INTRODUCED"
    COMMITTEE_DECIDED = "COMMITTEE_DECIDED"
    PLENARY_DECIDED = "PLENARY_DECIDED"
    SENT_TO_GOVERNMENT = "SENT_TO_GOVERNMENT"
    PROMULGATED = "PROMULGATED"


class EffectStatus(StrEnum):
    """효력 상태. 절차 단계와 따로 관리한다 (§2.16.1)."""

    NOT_A_LAW = "NOT_A_LAW"
    PROMULGATED_NOT_EFFECTIVE = "PROMULGATED_NOT_EFFECTIVE"
    PARTIALLY_EFFECTIVE = "PARTIALLY_EFFECTIVE"
    AMENDMENT_FULLY_EFFECTIVE = "AMENDMENT_FULLY_EFFECTIVE"
    UNKNOWN = "UNKNOWN"


class Disposition(StrEnum):
    """처리 결과 (§2.16.1)."""

    PENDING = "PENDING"
    ORIGINAL_PASSED = "ORIGINAL_PASSED"
    MODIFIED_PASSED = "MODIFIED_PASSED"
    SUBSTITUTE_ADOPTED = "SUBSTITUTE_ADOPTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    COMMITTEE_DISCARDED = "COMMITTEE_DISCARDED"
    TERM_EXPIRED = "TERM_EXPIRED"
    ALTERNATIVE_REFLECTED_DISCARDED = "ALTERNATIVE_REFLECTED_DISCARDED"
    RECONSIDERATION_REQUESTED = "RECONSIDERATION_REQUESTED"
    RECONSIDERED = "RECONSIDERED"


class IssueCode(StrEnum):
    """사람이 답해야 하는 문제의 고정 코드 (§2.10).

    code와 구체 대상 subject를 한 문자열로 합치지 않는다.
    """

    REQUIRED_INPUT_MISSING = "REQUIRED_INPUT_MISSING"
    REQUIRED_SOURCE_MISSING = "REQUIRED_SOURCE_MISSING"
    FACT_CONFLICT = "FACT_CONFLICT"
    DATE_WEEKDAY_MISMATCH = "DATE_WEEKDAY_MISMATCH"
    SOURCE_ROLE_CONTENT_MISMATCH = "SOURCE_ROLE_CONTENT_MISMATCH"
    PROCEDURE_STAGE_MISMATCH = "PROCEDURE_STAGE_MISMATCH"
    BILL_RELATION_MISSING = "BILL_RELATION_MISSING"
    UNSUPPORTED_ORIGIN_BILL_COUNT = "UNSUPPORTED_ORIGIN_BILL_COUNT"
    UNSUPPORTED_CHANGED_PROVISION_COUNT = "UNSUPPORTED_CHANGED_PROVISION_COUNT"
    CHANGED_PROVISION_COUNT_UNDETERMINABLE = "CHANGED_PROVISION_COUNT_UNDETERMINABLE"
    DISCLOSURE_NOT_ALLOWED = "DISCLOSURE_NOT_ALLOWED"


class ResolutionKind(StrEnum):
    """Issue를 어떻게 풀 수 있는지 (§2.10)."""

    ANSWER_IN_SAME_RUN = "ANSWER_IN_SAME_RUN"
    NEW_RUN_WITH_SOURCES = "NEW_RUN_WITH_SOURCES"
    UNSUPPORTED_IN_V1 = "UNSUPPORTED_IN_V1"


class IssueSeverity(StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


class InputMethod(StrEnum):
    PASTED = "PASTED"
    UPLOADED = "UPLOADED"


# ---------------------------------------------------------------------------
# 1차 고정 한도 (§2.3, §2.12)
# ---------------------------------------------------------------------------

MAX_SOURCES = 6
MAX_TOTAL_CHARS = 30_000
PURPOSE_MIN_CHARS = 10
PURPOSE_MAX_CHARS = 500
RUN_TTL_SECONDS = 2 * 60 * 60

#: 1차 활성 Contract. 클라이언트가 고르거나 바꿀 수 없다 (§3.10).
ACTIVE_CONTRACT_ID = "assembly_member_partial_amendment_plenary_v1@1.0.0"

#: 고정 지원 절차 단계. 화면에 읽기 전용으로 표시한다 (§2.2).
SUPPORTED_PROCEDURE_STAGE = ProcedureStage.PLENARY_DECIDED
SUPPORTED_PROCEDURE_STAGE_LABEL = "본회의 의결 결과"

#: 외부 AI 전송 안내 정책 버전. 화면과 서버가 같은 값을 확인한다 (§3.10).
EXTERNAL_AI_POLICY_VERSION = "external_ai_transfer_notice_v1"


NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


# ---------------------------------------------------------------------------
# API 입력
# ---------------------------------------------------------------------------


class SourceInput(BaseModel):
    """사용자가 제출한 자료 1개 (§2.3).

    한 Source에는 실제 문서 1개와 자료 역할 1개만 둔다.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(default="", max_length=200, description="사용자가 붙인 자료명")
    text: NonEmptyStr = Field(description="붙여 넣었거나 파일에서 읽은 본문")
    role: SourceRole = Field(
        default=SourceRole.UNKNOWN,
        description="사용자가 고른 자료 역할. 기본값은 `잘 모르겠음`",
    )
    input_method: InputMethod = InputMethod.PASTED
    original_filename: str | None = Field(default=None, max_length=260)
    original_page: str | None = Field(default=None, max_length=50)
    original_document_id: str | None = Field(default=None, max_length=200)
    supplied_as_official: bool = Field(
        default=True,
        description="사용자가 공식 자료라고 표시했는지. 독립 인증을 뜻하지 않는다",
    )


class CreateRunRequest(BaseModel):
    """POST /api/runs 본문 (§3.10).

    활성 Contract는 서버가 고정하므로 클라이언트가 보내지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    client_request_id: NonEmptyStr = Field(max_length=100, description="멱등 키")
    purpose: str = Field(description="보도 목적")
    disclosure: Disclosure
    basis_date: date = Field(description="사용자가 확인한 자료 기준일")
    sources: list[SourceInput] = Field(default_factory=list)
    announcement_subject: str | None = Field(
        default=None,
        max_length=200,
        description="선택. 사용자가 직접 확인한 발표 주체",
    )
    external_ai_policy_version: NonEmptyStr = Field(max_length=100)
    external_ai_transfer_confirmed: bool = False


# ---------------------------------------------------------------------------
# 내부 상태
# ---------------------------------------------------------------------------


class Issue(BaseModel):
    """사람이 답해야 하는 입력 문제 1개 (§2.10)."""

    model_config = ConfigDict(extra="forbid")

    issue_id: str
    code: IssueCode
    subject: str = Field(description="code와 합치지 않는 구체 대상")
    severity: IssueSeverity = IssueSeverity.BLOCKING
    message: str = Field(description="쉬운 한국어 설명")
    question: str = Field(default="", description="사용자에게 할 질문")
    source_ids: list[str] = Field(default_factory=list)
    resolution_kind: ResolutionKind = ResolutionKind.ANSWER_IN_SAME_RUN
    requires_new_run: bool = False


class StoredSource(BaseModel):
    """정규화까지 마친 자료 1개. 원문 보존 규칙은 2일차에 이어서 채운다."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    display_name: str
    role: SourceRole
    use_scope: SourceUseScope = SourceUseScope.FULL_FACT
    input_method: InputMethod
    original_filename: str | None = None
    original_page: str | None = None
    original_document_id: str | None = None
    supplied_as_official: bool = True
    char_count: int
    raw_text: str
    raw_sha256: str


class ExternalAiConfirmation(BaseModel):
    """매 Run의 외부 AI 전송 확인 기록 (§2.12)."""

    model_config = ConfigDict(extra="forbid")

    policy_version: str
    confirmed_at: datetime
    provider: str
    model: str


class Run(BaseModel):
    """보도자료 작업 1건 (§2.10).

    Harness만 이 값을 바꾼다. 상태 전이는 states.assert_transition을 거친다.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    state: str = Field(description="RunState 값")
    contract_id: str = ACTIVE_CONTRACT_ID
    procedure_stage: ProcedureStage = SUPPORTED_PROCEDURE_STAGE
    created_at: datetime
    updated_at: datetime
    last_user_action_at: datetime = Field(
        description="TTL 기준. 상태 조회(polling)로는 갱신하지 않는다"
    )
    finished_at: datetime | None = None

    client_request_id: str
    request_payload_sha256: str = Field(
        default="",
        description="정규화한 요청 payload의 해시. 같은 키·다른 내용을 거부하는 데 쓴다",
    )
    purpose: str
    disclosure: Disclosure
    basis_date: date
    announcement_subject_input: str | None = None
    sources: list[StoredSource] = Field(default_factory=list)

    external_ai: ExternalAiConfirmation | None = None
    issues: list[Issue] = Field(default_factory=list)

    draft_version: int = 0
    actual_model_calls: int = Field(default=0, description="SDK가 실제 보낸 요청 수")
    estimated_cost_usd: float = 0.0

    failure_kind: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    next_action: str | None = None


class ApiError(BaseModel):
    """모든 API 오류의 공통 모양 (§3.10)."""

    model_config = ConfigDict(extra="forbid")

    error_code: str
    message: str = Field(description="쉬운 한국어 설명")
    next_action: str = Field(description="사용자가 다시 할 수 있는 행동")
    run_id: str | None = None
