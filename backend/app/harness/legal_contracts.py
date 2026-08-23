"""법률 단계 전용 계약 (README §2.16).

`ResolvedFinalText`와 `ChangedArticleSet`은 **Harness만** 만든다. Agent는 이
값을 만들지도 덮어쓰지도 못한다. 초안에 쓰는 "무엇이 최종 내용인가"와 "어느
조문이 바뀌었는가"를 AI 판단에 맡기면, 자료에 없는 내용이 초안에 들어가는
길이 열리기 때문이다.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

#: 최종 의결문을 고른 방법. 어느 규칙으로 골랐는지 항상 기록한다.
RULE_EXPLICIT_FINAL_TEXT = "EXPLICIT_FINAL_TEXT"
RULE_AGENDA_WITH_ORIGINAL_PASSED = "PLENARY_AGENDA_WITH_ORIGINAL_PASSED"
RULE_ORIGINAL_UNCHANGED_CHAIN = "ORIGINAL_UNCHANGED_CHAIN_V2"

FINAL_TEXT_RULE_VERSION = "final_text_v1"
ARTICLE_PARSER_VERSION = "article_target_parser_v1"

#: 개정문 본칙의 시작·끝 표지 (§2.16.3).
BODY_START_MARKER = "다음과 같이 개정한다."
BODY_END_MARKER = "부칙"


class FinalTextConfirmation(BaseModel):
    """발의안을 최종 의결 내용으로 대신 쓸 때 사용자가 한 확인 (§2.16.2 조건 5).

    이 확인은 **사실의 진실을 승인하는 절차가 아니다.** 자료에 개정문과 부칙이
    처음부터 끝까지 들어 있는지를 사람이 원문을 보고 답한 기록일 뿐이다.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    scope: str = "COMPLETE_AMENDMENT_BODY_AND_SUPPLEMENT"
    confirmed: bool = False
    confirmation_mode: str = "USER_ANSWER"
    confirmed_at: str = ""


class ResolvedFinalText(BaseModel):
    """이번 보도자료가 설명하는 **최종 의결 내용**의 확정 결과 (§2.16.2)."""

    model_config = ConfigDict(extra="forbid")

    derivation_id: str
    rule: str
    rule_version: str = FINAL_TEXT_RULE_VERSION
    #: 실제 개정문을 담은 자료. 표결·심사이력 문장은 여기에 합치지 않는다.
    source_id: str
    source_name: str = ""
    #: 판단에 쓴 모든 자료. 근거를 되짚을 때 쓴다.
    input_source_ids: list[str] = Field(default_factory=list)
    normalized_sha256: str = ""
    bill_number: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    #: 개정문 본칙의 정규화 원문 안 위치.
    body_start: int = 0
    body_end: int = 0
    text: str = ""

    @property
    def body_text(self) -> str:
        return self.text[self.body_start : self.body_end]


class ProvisionDirective(BaseModel):
    """개정 지시문 하나와 그 뒤에 딸린 새 본문의 원문 위치."""

    model_config = ConfigDict(extra="forbid")

    article_id: str
    start: int
    end: int
    text: str = ""


class UnparsedSpan(BaseModel):
    """해석하지 못한 구간. 하나라도 있으면 진행하지 않는다."""

    model_config = ConfigDict(extra="forbid")

    start: int
    end: int
    text: str = ""


class ChangedArticleSet(BaseModel):
    """코드가 직접 센 변경 최상위 조문 집합 (§2.16.3).

    `unparsed_spans`가 비어 있고 본칙 비공백 문자를 100% 소비했을 때만 쓴다.
    일부만 센 결과를 성공으로 처리하지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    parser_version: str = ARTICLE_PARSER_VERSION
    final_text_derivation_id: str = ""
    normalized_sha256: str = ""
    body_start: int = 0
    body_end: int = 0
    article_ids: list[str] = Field(default_factory=list)
    directives: list[ProvisionDirective] = Field(default_factory=list)
    unparsed_spans: list[UnparsedSpan] = Field(default_factory=list)
    consumed_non_space: int = 0
    total_non_space: int = 0

    @property
    def fully_consumed(self) -> bool:
        return (
            not self.unparsed_spans
            and self.total_non_space > 0
            and self.consumed_non_space == self.total_non_space
        )


def derivation_id(rule: str, parts: list[str]) -> str:
    """같은 입력이면 항상 같은 ID가 나오도록 만든다."""
    joined = "|".join([rule, FINAL_TEXT_RULE_VERSION, *parts])
    return "FT-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16].upper()
