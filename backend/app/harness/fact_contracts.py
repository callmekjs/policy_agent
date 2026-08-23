"""사실 추출 관련 데이터 계약 (README §2.10, §2.14).

두 층으로 나눈다.

- **raw**: `FactExtractionAgent`가 돌려준 아직 믿지 않는 결과. 형식만 맞춘다.
  Agent는 위치·해시·provenance·보호 여부를 정하지 않는다.
- **검증됨**: Harness가 근거 완전 일치·참조·역할·충돌 Gate를 통과시킨 뒤
  원문 위치와 출처를 붙여 만든 Fact 원장.

raw 형식은 `test_sets/fact_extraction_result.schema.json`과 같은 모양이며,
그 파일이 기계용 정본이다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.harness.contracts import (
    Disposition,
    EffectStatus,
    FactProvenance,
    ProcedureStage,
    SourceRole,
)

#: raw 결과의 형식 버전. test_sets catalog v1.2.1과 맞춘다.
FACT_RESULT_SCHEMA_VERSION = "1.2.1"

#: 화면에 보이는 쉬운 역할 이름 -> 내부 enum. Agent는 쉬운 이름으로 답한다.
ROLE_LABEL_TO_ENUM: dict[str, SourceRole] = {
    "의안정보": SourceRole.BILL_INFORMATION,
    "현행 조문": SourceRole.CURRENT_PROVISION,
    "발의안": SourceRole.INTRODUCED_TEXT,
    "본회의 상정안": SourceRole.PLENARY_AGENDA_TEXT,
    "위원회 최종문": SourceRole.COMMITTEE_FINAL_TEXT,
    "본회의 표결 결과": SourceRole.PLENARY_VOTE_RESULT,
    "본회의 최종문": SourceRole.PLENARY_FINAL_TEXT,
    "대안 관계": SourceRole.BILL_RELATION,
    "공포 개정문": SourceRole.PROMULGATED_TEXT,
    "부칙": SourceRole.SUPPLEMENTARY_PROVISION,
    "공식 제안·개정이유": SourceRole.OFFICIAL_REASON,
    "공식 발언문": SourceRole.OFFICIAL_STATEMENT,
}


class SupplementaryKind(StrEnum):
    """부칙 규칙의 종류 (§2.16.4)."""

    EFFECTIVE_DATE = "EFFECTIVE_DATE"
    PROVISION_EFFECTIVE_DATE = "PROVISION_EFFECTIVE_DATE"
    APPLICATION = "APPLICATION"
    TRANSITION = "TRANSITION"
    SPECIAL_CASE = "SPECIAL_CASE"


#: 이 종류의 사실은 반드시 공식 자료 근거가 하나로 특정돼야 한다 (validation.yaml).
#: 근거가 여러 곳에 반복되어 어디를 가리키는지 알 수 없으면 차단한다.
HIGH_RISK_FACT_KINDS: frozenset[str] = frozenset(
    {
        "BILL_IDENTITY",
        "BILL_TITLE",
        "BILL_RELATION",
        "PLENARY_RESULT",
        "ORIGIN_BILL_DISPOSITION",
        "PROVISION_CHANGE",
        "LAW_NUMBER",
        "PROMULGATION_DATE",
        "EFFECTIVE_DATE",
        "SUPPLEMENTARY_EFFECTIVE_DATES",
        "SUPPLEMENTARY_APPLICATION",
        "SUPPLEMENTARY_TRANSITION",
        "QUOTE",
    }
)

#: 같은 종류의 사실은 같은 항목으로 비교한다. 비교 항목은 Harness가 정하며
#: Agent는 `kind`와 `value`만 돌려준다(고정 형식이 그 두 개만 허용한다).
#: 종류가 다르면 서로 비교하지 않는다. 예를 들어 위원회 표결 수와 본회의
#: 표결 수는 종류가 달라 충돌로 보지 않는다.
COMMITTEE_KIND_PREFIX = "COMMITTEE_"


def subject_of(kind: str) -> str:
    """이 종류의 사실을 무엇과 비교할지."""
    return kind.lower()


Ident = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=400)]


# ---------------------------------------------------------------------------
# raw — Agent가 돌려주는 아직 믿지 않는 결과
# ---------------------------------------------------------------------------


class EvidenceCandidate(BaseModel):
    """Agent가 제시한 원문 근거 문구 하나.

    위치는 담지 않는다. Harness가 정규화문에서 직접 찾아 계산한다.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: Ident
    source_id: Ident
    quote: Annotated[str, StringConstraints(min_length=1, max_length=600)]


class SourceRoleCandidate(BaseModel):
    """자료 역할이 `잘 모르겠음`일 때 Agent가 제안하는 후보 하나."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: Ident
    source_id: Ident
    role: str = Field(description="쉬운 역할 이름")
    label: ShortText = Field(description="사용자에게 보여줄 한 줄 설명")
    evidence_ids: list[Ident] = Field(min_length=1, max_length=5)


class RawFact(BaseModel):
    """Agent가 뽑은 사실 후보 하나."""

    model_config = ConfigDict(extra="forbid")

    fact_id: Ident
    kind: Ident
    value: ShortText | Annotated[list[ShortText], Field(min_length=1)] = Field(
        description="값 하나 또는 값 목록. 목록은 부칙 ID 묶음처럼 여러 항목을 가리킬 때"
    )
    source_id: Ident
    evidence_id: Ident
    valid_source_role_candidate_ids: list[Ident] = Field(default_factory=list)


class RawLegislativeEvent(BaseModel):
    """제공 자료에서 확인된 입법 사건 하나."""

    model_config = ConfigDict(extra="forbid")

    event_id: Ident
    bill_id: Ident
    procedure_stage: ProcedureStage
    disposition: Disposition
    occurred_on: Annotated[str, StringConstraints(max_length=10)]
    source_id: Ident
    evidence_id: Ident
    valid_source_role_candidate_ids: list[Ident] = Field(default_factory=list)


class RawSupplementaryRule(BaseModel):
    """부칙 규칙 하나."""

    model_config = ConfigDict(extra="forbid")

    rule_id: Ident
    kind: SupplementaryKind
    applies_to: ShortText
    source_id: Ident
    evidence_id: Ident
    valid_source_role_candidate_ids: list[Ident] = Field(default_factory=list)


class RawBillIdentity(BaseModel):
    """의안 하나의 신원."""

    model_config = ConfigDict(extra="forbid")

    bill_id: Ident
    bill_number: Ident
    is_draft_subject: bool = False
    source_id: Ident
    evidence_ids: list[Ident] = Field(default_factory=list)


class RawBillRelation(BaseModel):
    """원안과 대안의 공식 관계."""

    model_config = ConfigDict(extra="forbid")

    origin_bill_id: Ident
    alternative_bill_id: Ident
    relation_type: Ident
    source_id: Ident
    evidence_ids: list[Ident] = Field(default_factory=list)


class RawProvisionComparison(BaseModel):
    """조문 하나의 전후 비교."""

    model_config = ConfigDict(extra="forbid")

    comparison_id: Ident
    provision_id: Ident
    current_source_id: Ident
    current_evidence_id: Ident
    final_source_id: Ident
    final_evidence_id: Ident


class ScopeError(BaseModel):
    """한 번에 담지 못할 만큼 자료가 큰 경우."""

    model_config = ConfigDict(extra="forbid")

    subject: ShortText
    reason: ShortText


class FactExtractionResult(BaseModel):
    """`FactExtractionAgent`의 raw 결과.

    `result_status=OK`가 아니면 배열은 전부 비어 있어야 한다. 부분 결과를
    저장하거나 보여주지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    result_status: Literal["OK", "FACT_SCOPE_TOO_LARGE"]
    scope_error: ScopeError | None
    source_role_candidates: list[SourceRoleCandidate] = Field(max_length=18)
    evidence: list[EvidenceCandidate] = Field(max_length=40)
    facts: list[RawFact] = Field(max_length=30)
    bill_identities: list[RawBillIdentity] = Field(max_length=2)
    bill_relations: list[RawBillRelation] = Field(max_length=1)
    legislative_events: list[RawLegislativeEvent] = Field(max_length=6)
    provision_comparisons: list[RawProvisionComparison] = Field(max_length=3)
    supplementary_rules: list[RawSupplementaryRule] = Field(max_length=8)


class FactExtractionEnvelope(BaseModel):
    """Agent 응답의 바깥 껍데기."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.2.1"] = FACT_RESULT_SCHEMA_VERSION
    result: FactExtractionResult


# ---------------------------------------------------------------------------
# 검증됨 — Harness가 원문 위치와 출처를 붙인 결과
# ---------------------------------------------------------------------------


class EvidenceLocation(BaseModel):
    """근거 문구가 실제로 있는 자리. Harness만 만든다."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_id: str
    source_name: str
    quote: str
    normalized_start: int
    normalized_end: int
    raw_start_line: int
    raw_start_column: int
    raw_end_line: int
    raw_end_column: int
    raw_excerpt: str
    occurrence_count: int = Field(description="정규화문에서 몇 군데 나왔는지")


class VerifiedFact(BaseModel):
    """근거가 실제 원문에 있는 것까지 확인한 사실 하나 (§2.10 Fact)."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    kind: str
    subject: str = ""
    #: 사람이 읽고 코드가 비교하는 값. **언제나 문자열이다.**
    #: raw 결과는 목록도 허용하지만, 그 합집합은 원장 경계에서 닫는다.
    #: 합집합이 밖으로 새면 값을 읽는 코드가 생길 때마다 새 고장점이 된다.
    value: str
    #: 원래가 목록이었으면 항목을 그대로 남긴다. 비교·표시에는 쓰지 않는다.
    value_items: list[str] = Field(default_factory=list)
    normalized_value: str = ""
    unit: str = ""
    provenance: FactProvenance = FactProvenance.OFFICIAL_SOURCE
    source_id: str
    confirmed_role: SourceRole | None = None
    valid_source_role_candidate_ids: list[str] = Field(default_factory=list)
    evidence: EvidenceLocation
    procedure_stage: ProcedureStage | None = None
    effect_status: EffectStatus | None = None
    basis_date: str = ""
    protected: bool = False


class FactLedger(BaseModel):
    """Harness가 검증·보강한 Fact 원장 (§2.10 FactLedger).

    raw Agent 응답과 분리해 저장한다. provenance·위치·보호 여부는 Harness만
    붙이며 Agent는 손댈 수 없다.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    schema_version: str = FACT_RESULT_SCHEMA_VERSION
    facts: list[VerifiedFact] = Field(default_factory=list)
    legislative_events: list[RawLegislativeEvent] = Field(default_factory=list)
    supplementary_rules: list[RawSupplementaryRule] = Field(default_factory=list)
    bill_identities: list[RawBillIdentity] = Field(default_factory=list)
    bill_relations: list[RawBillRelation] = Field(default_factory=list)
    provision_comparisons: list[RawProvisionComparison] = Field(default_factory=list)
    rejected_fact_ids: list[str] = Field(
        default_factory=list, description="근거를 찾지 못해 버린 사실"
    )

    @property
    def ready(self) -> bool:
        return bool(self.facts)


# `Run.fact_ledger`가 이 파일의 FactLedger를 가리키도록 마지막에 연결한다.
# contracts가 fact_contracts를 먼저 부르면 순환 참조가 되므로 방향을 한쪽으로 둔다.
from app.harness import contracts as _contracts  # noqa: E402

_contracts.FactLedger = FactLedger
_contracts.Run.model_rebuild()
