"""국정감사형 — AI에게 무엇을 주고 무엇을 안 주는지 본다.

검사기로 막는 것과 **애초에 줄 기회를 안 주는 것**은 다르다. 뒤가 낫다.

못 채우기로 정한 칸은 AI에게 **아예 알려 주지 않는다.** 알려 주면 AI는
"이 칸은 자료가 없어 못 씁니다"라고 쓰는 대신 그럴듯하게 채운다. 자리를
안 주면 채울 자리 자체가 없다.

이것은 4일차에서 배운 것과 같다 — *효력을 말하는 자리를 AI에게서 거둬
Harness가 만든다.* 여기서는 한 걸음 더 간다. **자료가 없으면 자리도 없다.**
"""

from __future__ import annotations

import json

from app.audit.contracts import (
    AuditFact,
    AuditFactKind,
    AuditLedger,
    Evidence,
    SlotKind,
)
from app.audit.drafting import build_drafting_request, parse_draft
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


def _ledger() -> AuditLedger:
    """gold 재료. 네 칸이 차고 세 칸이 빈다."""
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


def _request():
    ledger = _ledger()
    return build_drafting_request(
        ledger=ledger,
        plans=plan_slots(ledger),
        headline="태양광 3년간 상암축구장\n6천 개 규모 산림 사라져",
        subheads=["3년간 베어진 나무만 233만 그루"],
    )


# ---------------------------------------------------------------------------
# AI에게 주는 것 / 안 주는 것
# ---------------------------------------------------------------------------


def test_못_채우는_칸은_AI에게_알려_주지_않는다() -> None:
    """**막는 것보다 자리를 안 주는 것이 낫다.**

    `(중요한 사실)` 칸을 알려 주면 AI는 연도별 수치를 지어내서라도 채운다.
    자리를 안 주면 채울 자리가 없다.
    """
    payload = json.dumps(_request().payload, ensure_ascii=False)

    assert "KEY_FACT" not in payload
    assert "EXTRA" not in payload
    assert "AGENCY_VIEW" not in payload


def test_채우는_칸만_알려_준다() -> None:
    payload = json.dumps(_request().payload, ensure_ascii=False)

    for slot in ("LEAD_1", "LEAD_2", "DETAIL", "COMMENT"):
        assert slot in payload, slot


def test_칸마다_쓸_수_있는_사실만_준다() -> None:
    """`(멘트)` 칸에 지역별 수치를 주면 AI가 거기에 숫자를 넣는다."""
    slots = {s["slot"]: s for s in _request().payload["slots"]}

    comment_facts = {f["fact_id"] for f in slots["COMMENT"]["facts"]}
    assert "AF-09" in comment_facts
    assert "AF-05" not in comment_facts, "멘트 칸에 지역별 수치를 줬습니다."


def test_같은_시점_같은_지역_사실을_묶어서_준다() -> None:
    """실제로 여기서 글이 망가졌다.

    연도별 사실이 `529ha(2016년)` `314,528그루(2016년)` `1,435ha(2017년)`…
    처럼 **납작하게** 오면, AI가 그것을 연도별로 짝지어야 한다. 그 부담 때문에
    숫자를 통째로 빼고 `연도별 현황이 집계됐음`이라고만 쓴 적이 있다.

    부탁으로 풀 일이 아니다. **묶어서 주면 짝지을 일이 없다.**
    """
    ledger = _ledger()
    ledger.facts.extend(
        [
            _fact("AF-10", AuditFactKind.TIME_SERIES, "529ha", "2016년"),
            _fact("AF-11", AuditFactKind.TIME_SERIES, "314,528그루", "2016년"),
            _fact("AF-12", AuditFactKind.TIME_SERIES, "1,435ha", "2017년"),
        ]
    )
    request = build_drafting_request(
        ledger=ledger, plans=plan_slots(ledger), headline="제목", subheads=[]
    )
    slots = {s["slot"]: s for s in request.payload["slots"]}

    scopes = [g["scope"] for g in slots["KEY_FACT"]["groups"]]
    groups = {g["scope"]: g for g in slots["KEY_FACT"]["groups"]}

    assert "2016년" in groups, scopes
    assert [v["value"] for v in groups["2016년"]["values"]] == ["529ha", "314,528그루"]
    # 시점 순서는 **자료에 나온 차례**를 지킨다. 글자순으로 정렬하지 않는다.
    assert scopes.index("2016년") < scopes.index("2017년"), scopes


def test_추이_칸에는_총량을_주지_않는다() -> None:
    """`(중요한 사실)`은 **얼마나 늘었나**를 쓰는 자리다.

    여기에 총량(`4,407ha`)을 곁들여 주면 AI가 그것을 써서 리드2와 똑같은
    문장이 나온다. 실제로 그랬다.
    """
    ledger = _ledger()
    ledger.facts.extend(
        [
            _fact("AF-10", AuditFactKind.TIME_SERIES, "529ha", "2016년"),
            _fact("AF-11", AuditFactKind.TIME_SERIES, "1,435ha", "2017년"),
        ]
    )
    request = build_drafting_request(
        ledger=ledger, plans=plan_slots(ledger), headline="제목", subheads=[]
    )
    slots = {s["slot"]: s for s in request.payload["slots"]}

    given = {f["value"] for f in slots["KEY_FACT"]["facts"]}
    assert "4,407ha" not in given, given
    assert "529ha" in given


def test_나열하는_칸은_목록을_Harness가_만든다() -> None:
    """**지시문으로 세 번 고쳤는데 세 번 다 다른 데가 망가졌다.**

    연도를 넣으라 했더니 숫자를 뺐고, 숫자를 넣으라 했더니 연도를 뺐다.
    부탁으로 풀 문제가 아니다.

    목록은 Harness가 만들어 주고, AI는 `{목록}` 자리를 둔 이음말만 쓴다.
    4일차에서 효력을 말하는 자리를 AI에게서 거둔 것과 같다.
    """
    ledger = _ledger()
    ledger.facts.extend(
        [
            _fact("AF-10", AuditFactKind.TIME_SERIES, "529ha", "2016년"),
            _fact("AF-11", AuditFactKind.TIME_SERIES, "314,528그루", "2016년"),
            _fact("AF-12", AuditFactKind.TIME_SERIES, "1,435ha", "2017년"),
        ]
    )
    request = build_drafting_request(
        ledger=ledger, plans=plan_slots(ledger), headline="제목", subheads=[]
    )
    slots = {s["slot"]: s for s in request.payload["slots"]}

    assert slots["KEY_FACT"]["list_text"] == (
        "2016년 529ha(314,528그루), 2017년 1,435ha"
    ), slots["KEY_FACT"]["list_text"]
    # 리드는 나열하는 자리가 아니라 목록을 주지 않는다.
    assert "list_text" not in slots["LEAD_1"]


def test_AI가_둔_목록_자리를_Harness가_채운다() -> None:
    ledger = _ledger()
    ledger.facts.extend(
        [
            _fact("AF-10", AuditFactKind.TIME_SERIES, "529ha", "2016년"),
            _fact("AF-11", AuditFactKind.TIME_SERIES, "1,435ha", "2017년"),
        ]
    )
    plans = plan_slots(ledger)
    draft = parse_draft(
        {
            "schema_version": "1.0.0",
            "slots": [
                {
                    "slot": "KEY_FACT",
                    "text": "연도별 현황을 살펴보면 {목록}으로 늘었음.",
                    "fact_ids": ["AF-10", "AF-11"],
                }
            ],
        },
        plans=plans,
        headline="제목",
        subheads=[],
        ledger=ledger,
    )

    key = next(s for s in draft.slots if s.slot is SlotKind.KEY_FACT)
    assert "{목록}" not in key.text
    assert "2016년 529ha" in key.text
    assert "2017년 1,435ha" in key.text
    assert key.text.startswith("연도별 현황을 살펴보면")


def test_사실에_근거_문구를_함께_준다() -> None:
    """AI가 값을 그대로 쓰게 하려면 원문 문구를 보여 줘야 한다."""
    slots = {s["slot"]: s for s in _request().payload["slots"]}
    first = slots["LEAD_1"]["facts"][0]

    assert "value" in first and "quote" in first


def test_쓸_수_있는_수를_따로_알려_준다() -> None:
    """지시문에 "이 수만 쓸 수 있다"를 분명히 넣는다."""
    payload = json.dumps(_request().payload, ensure_ascii=False)
    assert "233만 그루" in payload
    assert "4,407ha" in payload


# ---------------------------------------------------------------------------
# AI가 준 답을 받는 방식
# ---------------------------------------------------------------------------


def _parse(ai_slots: list[dict]):
    ledger = _ledger()
    return parse_draft(
        {"schema_version": "1.0.0", "slots": ai_slots},
        plans=plan_slots(ledger),
        headline="태양광 3년간 상암축구장",
        subheads=["3년간 베어진 나무만 233만 그루"],
    )


def test_못_채우는_칸은_이유와_함께_초안에_들어간다() -> None:
    """AI가 아무 말 안 해도 Harness가 빈 칸을 만들어 넣는다.

    빈 칸을 빼 버리면 결과물만 보는 사람은 빠진 것을 모른다.
    """
    draft = _parse([{"slot": "LEAD_1", "text": "3년간 훼손됐음.", "fact_ids": ["AF-03"]}])

    key = next(s for s in draft.slots if s.slot is SlotKind.KEY_FACT)
    assert key.filled is False
    assert "시점별" in key.note or "연도별" in key.note, key.note


def test_AI가_못_채우는_칸을_채워_보내도_버린다() -> None:
    """**구조로 막는다.** 검사기에 오기 전에 걷어낸다."""
    draft = _parse(
        [
            {"slot": "LEAD_1", "text": "3년간 훼손됐음.", "fact_ids": ["AF-03"]},
            {
                "slot": "KEY_FACT",
                "text": "2016년 529ha에서 2018년 2,443ha로 늘었음.",
                "fact_ids": [],
            },
        ]
    )

    key = next(s for s in draft.slots if s.slot is SlotKind.KEY_FACT)
    assert key.filled is False
    assert "529ha" not in key.text
    assert "2,443ha" not in key.note


def test_모르는_칸_이름은_버린다() -> None:
    draft = _parse([{"slot": "결론", "text": "심각함.", "fact_ids": []}])
    assert [s.slot for s in draft.slots if s.filled] == []


def test_칸_순서는_역피라미드로_되돌린다() -> None:
    """AI가 순서를 뒤섞어 보내도 계약이 정한 순서로 놓는다."""
    draft = _parse(
        [
            {"slot": "COMMENT", "text": "촉구했음.", "fact_ids": ["AF-09"]},
            {"slot": "LEAD_1", "text": "3년간 훼손됐음.", "fact_ids": ["AF-03"]},
        ]
    )

    order = [s.slot for s in draft.slots]
    assert order.index(SlotKind.LEAD_1) < order.index(SlotKind.COMMENT)


def test_제목과_부제는_재료에_있던_그대로_간다() -> None:
    """AI가 제목을 다시 쓰지 못하게 한다. 재료에 있던 것이 정답이다."""
    draft = _parse([{"slot": "LEAD_1", "text": "3년간 훼손됐음.", "fact_ids": ["AF-03"]}])

    assert draft.headline == "태양광 3년간 상암축구장"
    assert draft.subheads == ["3년간 베어진 나무만 233만 그루"]
