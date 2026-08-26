"""사람이 사실을 확인하고 초안을 고치는 계약 (README §2.10, §3.7 누적 5일차).

5일차부터 **사람이 초안을 고칠 수 있다.** 4일차까지는 초안이 한 번 만들어지면
그대로였다. 이제 위험이 하나 늘었다 — **고치는 과정에서 중요한 값이 조용히
사라지는 것**이다.

그래서 이 파일의 계약은 두 가지를 붙들고 있다.

1. **사람이 무엇을 확인했는가** — 확인하지 않은 초안은 내려받을 수 없다.
   사람이 "틀렸다"고 한 사실은 초안에 남아 있으면 안 된다.
2. **고친 뒤에도 남아 있어야 할 것** — 보호 사실, 부칙, 발표 주체.
   하나라도 바뀌면 고친 결과를 **버리고 이전 초안을 그대로 둔다.**

고치는 데 실패했다고 멀쩡하던 초안까지 잃으면 안 된다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Ident = Annotated[str, Field(min_length=1, max_length=100)]


class FactVerdict(StrEnum):
    """사람이 사실 하나에 내린 판정."""

    #: 자료와 맞다.
    OK = "OK"
    #: 자료와 다르다. 이 사실을 쓴 문장은 초안에 남을 수 없다.
    WRONG = "WRONG"


class FactReview(BaseModel):
    """사람이 사실 하나를 확인한 기록 (§3.7 누적 5일차 `사실 확인 체크리스트`)."""

    model_config = ConfigDict(extra="forbid")

    fact_id: Ident
    verdict: FactVerdict
    #: 사람이 남긴 메모. 초안에는 절대 들어가지 않는다.
    note: Annotated[str, Field(max_length=500)] = ""
    reviewed_at: datetime


class RevisionRequest(BaseModel):
    """사람이 초안을 고쳐 달라고 한 요청 (§2.10 RevisionResult)."""

    model_config = ConfigDict(extra="forbid")

    #: 같은 키로 두 번 오면 한 번만 처리한다 (§2.13).
    client_request_id: Ident
    #: 사람이 쓴 요청. **이 글 자체는 초안에 들어가지 않는다.**
    #: 사용자가 여기에 사실을 적어도 원장에 없으면 쓸 수 없다.
    instruction: Annotated[str, Field(min_length=1, max_length=2000)]


class RevisionOutcome(StrEnum):
    """고친 결과."""

    #: 검사를 통과해 새 버전이 되었다.
    APPLIED = "APPLIED"
    #: 검사에 걸려 버렸다. 이전 초안은 그대로다.
    REJECTED = "REJECTED"


class RevisionAttempt(BaseModel):
    """고치기 한 번의 기록.

    실패한 시도도 남긴다. 무엇을 요청했고 왜 막혔는지 되짚을 수 없으면
    사람이 같은 요청을 반복하게 된다.
    """

    model_config = ConfigDict(extra="forbid")

    attempt_id: Ident
    client_request_id: Ident
    instruction: str
    outcome: RevisionOutcome
    #: 막혔으면 그 이유. 통과했으면 빈 목록이다.
    blocking_rule_ids: list[str] = Field(default_factory=list)
    #: 통과했을 때 새로 생긴 판 번호.
    resulting_version: int = 0
    attempted_at: datetime
