"""어느 칸을 채울 수 있는지 **코드가** 판정한다.

이 파일이 이 종류의 심장이다.

보통 AI에게 "이 자료로 연도별 추이를 써 줘"라고 하면 **언제나 써 준다.**
자료에 2016·2017·2018년 수치가 없어도 `2016년 500ha, 2017년 1,400ha…`처럼
그럴듯한 숫자를 만든다. 읽는 사람은 진짜와 구분하지 못하고, 그 보도자료는
기사가 되어 나간다.

그래서 **AI에게 묻지 않는다.** 자료에 어떤 종류의 사실이 몇 건 있는지만
코드가 세어 정한다. 부족하면 그 칸은 비우고 **무엇을 더 넣어야 하는지**
사람에게 말한다.

이것은 13차 검토가 남긴 결론과 같은 이야기다 — *"값을 믿는 대신 자리를 믿는다."*
여기서는 한 걸음 더 간다. **값이 없으면 자리도 만들지 않는다.**
"""

from __future__ import annotations

from app.audit.contracts import (
    FACT_KIND_LABELS,
    SLOT_LABELS,
    SLOT_PURPOSE,
    AuditFactKind,
    AuditLedger,
    SlotKind,
    SlotPlan,
)

#: 칸의 순서. **이 순서가 곧 역피라미드다.**
#: 기자는 긴 보도자료를 뒤에서부터 자른다. 중요한 것이 앞에 있어야 잘린 뒤에도
#: 말이 된다 (`보도자료작성요령교육.pdf` 6쪽).
SLOT_ORDER: tuple[SlotKind, ...] = (
    SlotKind.LEAD_1,
    SlotKind.LEAD_2,
    SlotKind.KEY_FACT,
    SlotKind.DETAIL,
    SlotKind.EXTRA,
    SlotKind.AGENCY_VIEW,
    SlotKind.COMMENT,
)

#: 칸을 채우려면 **반드시** 있어야 하는 사실의 종류와 최소 건수.
#:
#: 건수가 중요한 자리가 둘 있다.
#:
#: - `KEY_FACT`는 시점이 다른 수치가 **둘 이상**이어야 한다. 하나로는
#:   "늘었다/줄었다"를 말할 수 없다. 하나만 놓고 추이를 말하면 지어낸 것이다.
#: - `DETAIL`도 **둘 이상**이어야 한다. 하나로는 "가장 심한 곳"이라는 순위를
#:   말할 수 없다.
SLOT_NEEDS: dict[SlotKind, dict[AuditFactKind, int]] = {
    SlotKind.LEAD_1: {AuditFactKind.TOTAL: 1},
    SlotKind.LEAD_2: {AuditFactKind.TOTAL: 1, AuditFactKind.SUBJECT: 1},
    SlotKind.KEY_FACT: {AuditFactKind.TIME_SERIES: 2},
    SlotKind.DETAIL: {AuditFactKind.BREAKDOWN: 2},
    SlotKind.EXTRA: {AuditFactKind.CASE: 1},
    SlotKind.AGENCY_VIEW: {AuditFactKind.AGENCY_POSITION: 1},
    SlotKind.COMMENT: {AuditFactKind.STATEMENT: 1},
}

#: 필수는 아니지만 그 칸에서 **함께 쓸 수 있는** 종류.
#: 기간(`3년간`)이나 비유(`상암축구장 6천 개`)는 그것만으로 칸을 열지는 못하지만,
#: 칸이 열리면 문장을 살리는 재료가 된다.
SLOT_EXTRAS: dict[SlotKind, tuple[AuditFactKind, ...]] = {
    SlotKind.LEAD_1: (AuditFactKind.PERIOD, AuditFactKind.COMPARISON),
    SlotKind.LEAD_2: (AuditFactKind.PERIOD, AuditFactKind.COMPARISON),
    # 총량은 주지 않는다. 추이를 쓰는 자리에 총량을 주면 AI가 그것을 써서
    # 리드2와 똑같은 문장이 나온다. 실제로 그랬다.
    SlotKind.KEY_FACT: (AuditFactKind.PERIOD,),
    SlotKind.DETAIL: (AuditFactKind.PERIOD,),
    SlotKind.EXTRA: (),
    SlotKind.AGENCY_VIEW: (AuditFactKind.SUBJECT,),
    SlotKind.COMMENT: (AuditFactKind.SUBJECT,),
}


def _shortfall(ledger: AuditLedger, kind: AuditFactKind, need: int) -> tuple[int, int]:
    """이 종류가 몇 건 있고 몇 건 필요한가."""
    return len(ledger.of_kind(kind)), need


def plan_slots(ledger: AuditLedger) -> list[SlotPlan]:
    """원장을 보고 칸마다 채울 수 있는지 정한다.

    **AI를 부르지 않는다.** 세기만 한다. 그래서 같은 자료면 언제나 같은 판정이
    나오고, 왜 그렇게 판정했는지 사람에게 그대로 설명할 수 있다.
    """
    plans: list[SlotPlan] = []

    for slot in SLOT_ORDER:
        needs = SLOT_NEEDS[slot]
        missing: list[str] = []
        for kind, need in needs.items():
            have, want = _shortfall(ledger, kind, need)
            if have < want:
                label = FACT_KIND_LABELS[kind]
                missing.append(f"{label} {want}건 이상 (지금 {have}건)")

        if missing:
            # **못 채운 칸은 사실을 하나도 달지 않는다.** 재료를 달아 두면
            # 다음 단계에서 "있는 걸로 대충 써 보자"가 된다.
            plans.append(
                SlotPlan(
                    slot=slot,
                    fillable=False,
                    usable_fact_ids=[],
                    reason=(
                        f"‘{SLOT_LABELS[slot]}’은(는) {SLOT_PURPOSE[slot]}을(를) "
                        "쓰는 자리인데, 자료에 그 값이 없습니다."
                    ),
                    needed=" · ".join(missing),
                )
            )
            continue

        usable_kinds = (*needs.keys(), *SLOT_EXTRAS[slot])
        usable = [f.fact_id for f in ledger.facts if f.kind in usable_kinds]
        plans.append(SlotPlan(slot=slot, fillable=True, usable_fact_ids=usable))

    return plans
