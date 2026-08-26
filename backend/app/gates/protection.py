"""보호 후보를 정하는 결정형 규칙 (README §4.3, §2.10).

README가 못 박은 것이 하나 있다.

> **Agent는 보호 여부를 정하지 않는다.** Harness가 활성 Contract의 결정형
> 규칙으로 보호 후보를 지정하고, **사용자가 원문과 대조해 확인한 뒤**
> `protected=true`가 된다.

그래서 이 파일에는 AI가 끼어들 자리가 없다. 사실의 **종류**만 보고 정한다.
종류는 코드가 정한 목록이라 지어낼 수 없다.

그리고 보호는 **자동으로 붙지 않는다.** 사람이 체크리스트에서 "맞다"를
눌러야 붙는다. 사람이 안 본 값을 보호한다고 말하면, 틀린 값을 보호하게 된다.
"""

from __future__ import annotations

from app.harness.fact_contracts import FactLedger, VerifiedFact
from app.harness.review_contracts import FactReview, FactVerdict

#: 보호 후보가 되는 사실 종류.
#:
#: 고르는 기준은 하나다 — **틀렸을 때 읽는 사람이 가장 크게 오해하는 값**인가.
#:
#: - `PLENARY_RESULT` 처리 결과. `원안가결`이 `부결`로 바뀌면 기사 전체가 거짓이다.
#: - `PLENARY_DECIDED_ON` 의결일. 언제 일어난 일인지가 바뀐다.
#: - `BILL_IDENTITY` 의안번호. 다른 법안 이야기가 된다.
#: - `PROVISION_CHANGE` 바뀐 조문. 무엇이 바뀌는지가 바뀐다.
#:
#: `BILL_TITLE`은 넣지 않는다. 가짜 추출기가 인용된 다른 법률 이름까지 이
#: 종류로 뽑는 것이 이미 `남은 일`로 적혀 있다(4일차 검토). 확실하지 않은 것을
#: 보호하면 사람이 "맞다"를 누르기 어려워진다.
PROTECTED_CANDIDATE_KINDS = frozenset(
    {
        "PLENARY_RESULT",
        "PLENARY_DECIDED_ON",
        "BILL_IDENTITY",
        "PROVISION_CHANGE",
    }
)


def is_protected_candidate(fact: VerifiedFact) -> bool:
    """이 사실이 사람 확인을 거치면 보호가 되는가."""
    return fact.kind in PROTECTED_CANDIDATE_KINDS


def apply_reviews(ledger: FactLedger, reviews: list[FactReview]) -> FactLedger:
    """사람이 확인한 결과를 원장에 반영한다.

    "맞다"를 누른 **보호 후보**만 `protected=true`가 된다. "틀렸다"를 누른
    것은 보호하지 않는다 — 틀린 값을 지킬 이유가 없다. 확인하지 않은 것도
    보호하지 않는다.
    """
    verdicts = {r.fact_id: r.verdict for r in reviews}
    facts = [
        fact.model_copy(
            update={
                "protected": (
                    is_protected_candidate(fact)
                    and verdicts.get(fact.fact_id) is FactVerdict.OK
                )
            }
        )
        for fact in ledger.facts
    ]
    return ledger.model_copy(update={"facts": facts})


def unreviewed_fact_ids(ledger: FactLedger, reviews: list[FactReview]) -> list[str]:
    """아직 사람이 안 본 사실.

    하나라도 남아 있으면 초안을 내려받을 수 없다(5일차 합격선 `M1`).
    """
    seen = {r.fact_id for r in reviews}
    return [f.fact_id for f in ledger.facts if f.fact_id not in seen]
