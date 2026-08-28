"""국정감사형 — 어느 칸을 채울 수 있는지 **코드가** 판정하는지 본다.

이것이 이 종류의 핵심이다. 보통 AI는 자료에 없는 연도별 수치도 그럴듯하게
지어낸다. `2016년 500ha, 2017년 1,400ha…` 읽는 사람은 구분하지 못한다.

그래서 **AI에게 물어보지 않는다.** 자료에 어떤 종류의 사실이 몇 건 있는지만
세어 코드가 정한다. 부족하면 그 칸은 비우고 무엇이 더 필요한지 말한다.

기준 자료는 `references/보도자료예시/01_역피라미드_태양광_산림훼손.txt`의
제목 + 부제 3줄이다. 그 재료로는 4칸이 차고 3칸이 빈다.
"""

from __future__ import annotations

from app.audit.contracts import (
    AuditFact,
    AuditFactKind,
    AuditLedger,
    Evidence,
    SlotKind,
)
from app.audit.slots import plan_slots


def _fact(fact_id: str, kind: AuditFactKind, value: str, scope: str = "") -> AuditFact:
    return AuditFact(
        fact_id=fact_id,
        kind=kind,
        subject="산지훼손",
        value=value,
        scope=scope,
        evidence=Evidence(source_id="SRC-01", quote=value, line=1),
    )


def _gold_material() -> AuditLedger:
    """gold의 제목 + 부제 3줄에서 뽑히는 사실.

    **이 목록이 이 프로토타입의 출발점이다.** 여기에 연도별 수치도, 개별
    발전소 사례도, 산림청 입장도 없다. 없는 것이 이 시험의 요지다.
    """
    return AuditLedger(
        facts=[
            _fact("AF-01", AuditFactKind.TOTAL, "233만 그루", "3년간"),
            _fact("AF-02", AuditFactKind.TOTAL, "4,407ha", "3년간"),
            _fact("AF-03", AuditFactKind.PERIOD, "3년간"),
            _fact("AF-04", AuditFactKind.COMPARISON, "상암축구장 6천 개"),
            _fact("AF-05", AuditFactKind.BREAKDOWN, "1,025ha", "전남"),
            _fact("AF-06", AuditFactKind.BREAKDOWN, "790ha", "경북"),
            _fact("AF-07", AuditFactKind.BREAKDOWN, "684ha", "전북"),
            _fact("AF-08", AuditFactKind.SUBJECT, "윤 의원"),
            _fact("AF-09", AuditFactKind.STATEMENT, "즉각 복원하라"),
        ]
    )


def _by_slot(plans):
    return {p.slot: p for p in plans}


# ---------------------------------------------------------------------------
# 채울 수 없는 칸 — 이 프로토타입이 보여 주려는 것
# ---------------------------------------------------------------------------


def test_연도별_수치가_없으면_중요한_사실을_못_채운다() -> None:
    """`(중요한 사실)`은 "무엇이 얼마나 늘었나"를 쓰는 칸이다.

    추이를 말하려면 **시점이 다른 수치가 둘 이상** 있어야 한다. gold 재료에는
    `3년간` 총량만 있고 연도별 수치가 없다. 그런데도 쓰면 지어낸 것이다.
    """
    plan = _by_slot(plan_slots(_gold_material()))[SlotKind.KEY_FACT]

    assert plan.fillable is False
    assert plan.usable_fact_ids == []
    # 사람이 읽고 **무엇을 더 넣어야 하는지** 알 수 있어야 한다.
    assert "시점별" in plan.needed or "연도별" in plan.needed, plan.needed


def test_개별_사례가_없으면_추가사실을_못_채운다() -> None:
    """`(추가사실)`은 가장 두드러진 개별 사례를 쓰는 칸이다."""
    plan = _by_slot(plan_slots(_gold_material()))[SlotKind.EXTRA]

    assert plan.fillable is False
    assert "개별 사례" in plan.needed, plan.needed


def test_기관_입장이_없으면_그_칸을_못_채운다() -> None:
    """`(기관 입장)`은 지적받은 기관의 반론을 싣는 칸이다.

    한쪽 말만 실으면 보도자료가 아니라 주장문이 된다.
    """
    plan = _by_slot(plan_slots(_gold_material()))[SlotKind.AGENCY_VIEW]

    assert plan.fillable is False
    assert "입장" in plan.needed, plan.needed


# ---------------------------------------------------------------------------
# 채울 수 있는 칸 — 대조군. 다 못 채운다고 하면 검사기가 아니라 벽이다
# ---------------------------------------------------------------------------


def test_총량이_있으면_리드1을_채운다() -> None:
    plan = _by_slot(plan_slots(_gold_material()))[SlotKind.LEAD_1]

    assert plan.fillable is True
    assert "AF-01" in plan.usable_fact_ids


def test_지역별_수치가_둘_이상이면_세부사실을_채운다() -> None:
    """전남·경북·전북 셋이 있으니 순위를 말할 수 있다."""
    plan = _by_slot(plan_slots(_gold_material()))[SlotKind.DETAIL]

    assert plan.fillable is True
    assert {"AF-05", "AF-06", "AF-07"} <= set(plan.usable_fact_ids)


def test_발언이_있으면_멘트를_채운다() -> None:
    plan = _by_slot(plan_slots(_gold_material()))[SlotKind.COMMENT]

    assert plan.fillable is True
    assert "AF-09" in plan.usable_fact_ids


def test_gold_재료로는_네_칸이_차고_세_칸이_빈다() -> None:
    """전체 그림. 이 숫자가 결과물에 그대로 나간다."""
    plans = plan_slots(_gold_material())

    filled = {p.slot for p in plans if p.fillable}
    empty = {p.slot for p in plans if not p.fillable}

    assert filled == {
        SlotKind.LEAD_1,
        SlotKind.LEAD_2,
        SlotKind.DETAIL,
        SlotKind.COMMENT,
    }, sorted(s.value for s in filled)
    assert empty == {
        SlotKind.KEY_FACT,
        SlotKind.EXTRA,
        SlotKind.AGENCY_VIEW,
    }, sorted(s.value for s in empty)


def test_자료가_넉넉하면_일곱_칸이_다_찬다() -> None:
    """**대조군.** 무엇을 넣어도 못 채운다고 하면 이 판정은 아무 일도 안 한다.

    gold 본문에 실제로 있던 사실들을 마저 넣으면 일곱 칸이 다 차야 한다.
    """
    ledger = _gold_material()
    ledger.facts.extend(
        [
            _fact("AF-10", AuditFactKind.TIME_SERIES, "529ha", "2016년"),
            _fact("AF-11", AuditFactKind.TIME_SERIES, "1,435ha", "2017년"),
            _fact("AF-12", AuditFactKind.TIME_SERIES, "2,443ha", "2018년"),
            _fact("AF-13", AuditFactKind.CASE, "13ha", "경북 봉화군 봉성면"),
            _fact("AF-14", AuditFactKind.AGENCY_POSITION, "신청 건수가 감소했다는 입장"),
        ]
    )

    plans = plan_slots(ledger)
    empty = [p.slot.value for p in plans if not p.fillable]
    assert empty == [], empty


def test_재료가_비면_한_칸도_못_채운다() -> None:
    """반대쪽 대조군. 빈 원장으로 초안이 나오면 안 된다."""
    plans = plan_slots(AuditLedger())
    assert [p.slot for p in plans if p.fillable] == []


def test_칸_순서는_역피라미드다() -> None:
    """중요한 것이 앞에. **뒤에서 잘라도 말이 되어야** 한다.

    기자는 긴 보도자료를 뒤에서부터 자른다. 순서가 흐트러지면 잘린 뒤에
    말이 안 된다.
    """
    plans = plan_slots(_gold_material())
    assert [p.slot for p in plans] == [
        SlotKind.LEAD_1,
        SlotKind.LEAD_2,
        SlotKind.KEY_FACT,
        SlotKind.DETAIL,
        SlotKind.EXTRA,
        SlotKind.AGENCY_VIEW,
        SlotKind.COMMENT,
    ]
