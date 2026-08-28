"""국정감사·자료분석형 보도자료의 데이터 계약.

`references/보도자료예시/01_역피라미드_태양광_산림훼손.txt`(gold)를 기준으로 만들었다.
그 문서는 본문에 칸 이름을 그대로 적어 두었다 —
`(리드1) (리드2) (중요한 사실) (세부사실) (추가사실) (멘트)`.
**양식이 이미 정해져 있다는 뜻**이라 그대로 옮긴다.

기존 본회의 통과형(`app/gates`, `app/harness`)과 **따로 간다.** 그쪽은 의안번호·
조문·처리결과에 깊이 매여 있어 여기서 재사용하면 둘 다 망가진다. 대신 원문 보존
(`source_normalizer`)과 AI 연결(`model_gateway`)은 그대로 쓴다.

핵심 원칙은 그대로다 — **자료에 없는 값은 못 쓴다.** 다른 점은 "무엇이 자료인가"
뿐이다. 법률안 대신 기관이 제출한 수치가 자료다.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


#: 이 형식의 판. 바뀌면 올린다.
AUDIT_SCHEMA_VERSION = "1.0.0"

#: 화면과 파일에서 절대 사라지면 안 되는 표시. 통과형과 같은 값을 쓴다.
DRAFT_LABEL = "DRAFT / 내부 검토용"


class AuditFactKind(StrEnum):
    """재료에서 뽑은 사실의 종류.

    **칸을 채울 수 있는지 판정하는 데 쓴다.** 종류가 곧 자격이다. 예를 들어
    `(중요한 사실)` 칸은 시점이 다른 수치가 둘 이상 있어야 "늘었다/줄었다"를
    말할 수 있다. 하나뿐이면 추이를 말할 수 없고, 말하면 지어낸 것이다.
    """

    #: 총량. `233만 그루`, `4,407ha`
    TOTAL = "TOTAL"
    #: 기간. `3년간`, `최근 3년`
    PERIOD = "PERIOD"
    #: 시점별 수치. `2016년 529ha`. **둘 이상 있어야 추이를 말할 수 있다.**
    TIME_SERIES = "TIME_SERIES"
    #: 항목·지역별 수치. `전남 1,025ha`
    BREAKDOWN = "BREAKDOWN"
    #: 개별 사례. `경북 봉화군 ㈜창미에너지발전소 13ha`
    CASE = "CASE"
    #: 비유·환산. `상암 월드컵경기장 6,040개`, `여의도 면적의 15배`
    COMPARISON = "COMPARISON"
    #: 조사·발표 주체. `윤상직 의원`, `산림청 제출 자료`
    SUBJECT = "SUBJECT"
    #: 기관의 입장·반론. `산림청은 …라는 입장임`
    AGENCY_POSITION = "AGENCY_POSITION"
    #: 사람의 발언. 의원 멘트.
    STATEMENT = "STATEMENT"


#: 사람이 읽을 종류 이름. 못 채운 칸을 설명할 때 쓴다.
FACT_KIND_LABELS: dict[AuditFactKind, str] = {
    AuditFactKind.TOTAL: "총량 수치",
    AuditFactKind.PERIOD: "기간",
    AuditFactKind.TIME_SERIES: "시점별(연도별) 수치",
    AuditFactKind.BREAKDOWN: "항목·지역별 수치",
    AuditFactKind.CASE: "개별 사례",
    AuditFactKind.COMPARISON: "비유·환산 값",
    AuditFactKind.SUBJECT: "조사·발표 주체",
    AuditFactKind.AGENCY_POSITION: "기관의 입장·반론",
    AuditFactKind.STATEMENT: "발언",
}


class SlotKind(StrEnum):
    """보도자료 본문의 칸. **gold가 본문에 적어 둔 이름 그대로다.**

    순서가 곧 역피라미드다. 아래로 갈수록 덜 중요하고, 뒤에서 잘라도 말이 된다.
    """

    LEAD_1 = "LEAD_1"
    LEAD_2 = "LEAD_2"
    KEY_FACT = "KEY_FACT"
    DETAIL = "DETAIL"
    EXTRA = "EXTRA"
    AGENCY_VIEW = "AGENCY_VIEW"
    COMMENT = "COMMENT"


#: 화면과 결과물에 쓰는 칸 이름. gold의 표기를 따른다.
SLOT_LABELS: dict[SlotKind, str] = {
    SlotKind.LEAD_1: "리드1",
    SlotKind.LEAD_2: "리드2",
    SlotKind.KEY_FACT: "중요한 사실",
    SlotKind.DETAIL: "세부사실",
    SlotKind.EXTRA: "추가사실",
    SlotKind.AGENCY_VIEW: "기관 입장",
    SlotKind.COMMENT: "멘트",
}

#: 각 칸이 하는 일. 사람에게 왜 못 채웠는지 설명할 때 함께 보여 준다.
SLOT_PURPOSE: dict[SlotKind, str] = {
    SlotKind.LEAD_1: "상황과 결론을 한 문단으로",
    SlotKind.LEAD_2: "누가 · 어떤 자료로 · 무엇이 확인됐나",
    SlotKind.KEY_FACT: "무엇이 얼마나 늘었나 (추이)",
    SlotKind.DETAIL: "항목·지역별로 나눠 보기",
    SlotKind.EXTRA: "가장 두드러진 개별 사례",
    SlotKind.AGENCY_VIEW: "해당 기관의 입장과 그에 대한 평가",
    SlotKind.COMMENT: "의원 발언",
}


class Evidence(BaseModel):
    """이 사실이 어느 자료 몇 번째 줄에서 왔는지.

    **모든 사실에 반드시 붙는다.** 근거를 못 붙이면 사실로 받지 않는다.
    보도자료는 틀리면 기사가 되어 나가므로, 되짚을 수 없는 값은 쓰지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_name: str = ""
    #: 원문에 **글자 그대로** 나오는 문구. 비슷한 문장은 근거가 아니다.
    quote: str
    #: 원문 기준 줄 번호. 1부터.
    line: int = 0


class AuditFact(BaseModel):
    """재료에서 뽑아 Harness가 근거까지 확인한 사실 하나."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    kind: AuditFactKind
    #: 무엇에 대한 값인가. `베어진 나무`, `훼손 산지면적`
    subject: str
    #: 값 그대로. `233만 그루`, `4,407ha`
    value: str
    #: 값이 적용되는 범위. `3년간`, `전남`, `2016년`. 없으면 빈 문자열.
    scope: str = ""
    evidence: Evidence
    #: 사람이 원문과 맞대어 확인했는가. 확인해야 보호된다.
    confirmed: bool = False


class AuditLedger(BaseModel):
    """재료에서 확인된 사실 전부. 초안은 여기 있는 값만 쓸 수 있다."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = AUDIT_SCHEMA_VERSION
    facts: list[AuditFact] = Field(default_factory=list)
    #: 근거를 못 붙여 버린 후보. **버린 것도 사람에게 보여 준다.**
    #: 조용히 버리면 사람은 자료를 더 넣어야 하는지 알 수 없다.
    rejected: list[str] = Field(default_factory=list)

    def of_kind(self, kind: AuditFactKind) -> list[AuditFact]:
        return [f for f in self.facts if f.kind is kind]


class SlotPlan(BaseModel):
    """칸 하나를 채울 수 있는지에 대한 **코드의 판정**.

    AI가 정하지 않는다. 자료에 어떤 종류의 사실이 몇 건 있는지만 보고 센다.
    AI에게 "쓸 수 있겠니"라고 물으면 언제나 "네"라고 답한다 — 그래서 안 묻는다.
    """

    model_config = ConfigDict(extra="forbid")

    slot: SlotKind
    fillable: bool
    #: 이 칸에 쓸 수 있는 사실 ID. 채울 수 없으면 빈 목록.
    usable_fact_ids: list[str] = Field(default_factory=list)
    #: 못 채운 이유를 사람 말로. 채울 수 있으면 빈 문자열.
    reason: str = ""
    #: 무엇을 더 넣으면 채워지는지. 채울 수 있으면 빈 문자열.
    needed: str = ""


class SlotText(BaseModel):
    """칸 하나에 실제로 들어간 글.

    **못 채운 칸도 지우지 않고 남긴다.** 칸을 빼 버리면 결과물만 보는 사람은
    무엇이 빠졌는지 모른다. 빈 칸과 그 이유가 함께 보여야 보좌관이 무엇을 더
    구해야 하는지 안다.
    """

    model_config = ConfigDict(extra="forbid")

    slot: SlotKind
    filled: bool
    #: 채웠을 때의 본문. 못 채웠으면 빈 문자열.
    text: str = ""
    #: 이 글이 쓴 사실. 되짚을 때 쓴다.
    fact_ids: list[str] = Field(default_factory=list)
    #: 못 채웠을 때 사람에게 하는 말. 무엇을 더 넣으면 되는지.
    note: str = ""


class AuditDraft(BaseModel):
    """국정감사형 보도자료 초안 하나.

    제목과 부제는 **재료에 있던 것을 그대로 쓴다.** 이번 프로토타입에서
    사람이 준 재료가 제목·부제이고, AI가 만드는 것은 본문이다.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = AUDIT_SCHEMA_VERSION
    #: 화면과 파일에서 지울 수 없는 표시.
    draft_label: str = DRAFT_LABEL
    headline: str
    subheads: list[str] = Field(default_factory=list)
    slots: list[SlotText] = Field(default_factory=list)


class Severity(StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


class Finding(BaseModel):
    """검사가 찾은 것 하나."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: Severity = Severity.BLOCKING
    #: 어느 칸에서 났는가.
    where: str = ""
    #: 사람이 읽을 말. 영어 코드만 보이면 무엇을 고쳐야 할지 알 수 없다.
    message: str
