"""초안 후보 계약 (`test_sets/draft_candidate.schema.json` v1.1.0).

그 schema 파일이 기계가 읽는 원본이다. 이 파일은 같은 모양을 Python에서
쓰기 위한 거울이며, 필드를 늘리거나 줄이면 안 된다. `extra="forbid"`는
schema의 `additionalProperties: false`와 짝이다.

여기서 눈여겨볼 것은 `ClaimText`다. 제목·핵심 요약·리드는 **반드시 하나 이상의
`fact_ids`를 달아야 한다.** 어느 사실에서 나온 문장인지 되짚을 수 없는 글은
초안에 넣지 않는다는 규칙이 계약 자체에 박혀 있다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

DRAFT_SCHEMA_VERSION = "1.1.0"

#: 화면과 파일에서 절대 사라지면 안 되는 표시 (§4.2).
DRAFT_LABEL = "DRAFT / 내부 검토용"

#: 상태 칸에 쓸 수 있는 코드. 여기 없는 값은 막는다.
#: 이 칸들은 사람이 읽는 글이 아니라 정해진 코드다. 자유로운 글을 허용하면
#: 화면에 그대로 나가는 자리에 지어낸 사실을 담을 수 있다.
STATUS_CODES = frozenset({"OK", "NEEDS_CONFIRMATION", "MISSING", "NOT_APPLICABLE"})

#: 육하원칙 칸의 열쇠말.
SIX_W_KEYS = frozenset({"who", "what", "when", "where", "why", "how"})

#: 핵심 요약 개수. schema의 minItems·maxItems와 같은 값이다.
MIN_KEY_POINTS = 2
MAX_KEY_POINTS = 3

Ident = Annotated[str, Field(min_length=1, max_length=100)]


class ClaimText(BaseModel):
    """근거를 달고 다니는 문장 하나."""

    model_config = ConfigDict(extra="forbid")

    text: str
    claim_ids: Annotated[list[Ident], Field(min_length=1)]
    fact_ids: Annotated[list[Ident], Field(min_length=1)]


class DraftParagraph(BaseModel):
    """본문 문단 하나."""

    model_config = ConfigDict(extra="forbid")

    paragraph_id: Ident
    section_kind: str
    priority_rank: Annotated[int, Field(ge=1)]
    text: str
    claim_ids: list[Ident] = Field(default_factory=list)
    fact_ids: list[Ident] = Field(default_factory=list)
    supplementary_rule_ids: list[Ident] = Field(default_factory=list)


class DraftClaim(BaseModel):
    """초안이 주장하는 내용 하나와 그 근거 사실."""

    model_config = ConfigDict(extra="forbid")

    claim_id: Ident
    text: str
    fact_ids: list[Ident] = Field(default_factory=list)


class DraftCandidate(BaseModel):
    """AI가 만든 초안 후보 하나. Harness가 검사하기 전까지는 믿지 않는다."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1.0"] = DRAFT_SCHEMA_VERSION
    candidate_id: Ident
    version: Annotated[int, Field(ge=1)]
    procedure_stage: Literal["PLENARY_DECIDED"]
    effect_status: Literal["NOT_A_LAW"]
    basis_date: str
    announcement_subject_fact_id: str
    announcement_subject_provenance: Literal[
        "USER_CONFIRMED", "OFFICIAL_SOURCE", "APPROVED_PROFILE"
    ]
    release_date_status: str
    release_date_fact_id: str
    draft_label: str
    title: ClaimText
    key_points: Annotated[
        list[ClaimText], Field(min_length=MIN_KEY_POINTS, max_length=MAX_KEY_POINTS)
    ]
    lead: ClaimText
    paragraphs: Annotated[list[DraftParagraph], Field(min_length=1)]
    contact_status: str
    contact_text: str
    #: 공식 자료에 인용문이 있을 때만 채운다. 없으면 `None`이다.
    quote: dict | None = None
    attachments: list[dict] = Field(default_factory=list)
    six_w_status: dict = Field(default_factory=dict)
    claims: list[DraftClaim] = Field(default_factory=list)
    validation_finding_ids: list[str] = Field(default_factory=list)
    placeholders: list[str] = Field(default_factory=list)
    generated_at: str = ""
    next_procedure_fact_ids: list[str] = Field(default_factory=list)


class DraftEnvelope(BaseModel):
    """초안 Agent 응답 봉투."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1.0"] = DRAFT_SCHEMA_VERSION
    result: DraftCandidate


class ValidationSeverity(StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


class ValidationFinding(BaseModel):
    """초안 검사 결과 하나 (README §2.10, §4.2).

    `rule_id`·기준 문서 위치·영향받은 초안 부분이 **셋 다** 있어야 한다. 하나라도
    비면 왜 막혔는지 되짚을 수 없고, 그 자체가 §4.2의 중대한 실패다.
    """

    model_config = ConfigDict(extra="forbid")

    finding_id: Ident
    rule_id: Annotated[str, Field(min_length=1)]
    rule_document: Annotated[str, Field(min_length=1)]
    affected_part: Annotated[str, Field(min_length=1)]
    severity: ValidationSeverity = ValidationSeverity.BLOCKING
    message: str
    excerpt: str = ""

    def describe(self) -> str:
        head = f"{self.affected_part}: {self.message}"
        return f"{head} · {self.rule_id} ({self.rule_document})"
