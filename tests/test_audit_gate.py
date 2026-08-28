"""국정감사형 — AI가 쓴 본문에 **자료에 없는 값**이 들어가는지 본다.

이 종류에서 가장 위험한 것은 숫자다. 보도자료는 숫자로 말한다. `2016년 529ha가
2018년 2,443ha로 늘었다` 같은 문장은 자료가 없어도 AI가 얼마든지 만든다. 그리고
그럴듯하다.

그래서 본문에 나온 **모든 수**를 원장과 맞대어 본다. 원장에 없는 수가 하나라도
있으면 초안을 내주지 않는다.
"""

from __future__ import annotations

from app.audit.contracts import (
    AuditDraft,
    AuditFact,
    AuditFactKind,
    AuditLedger,
    Evidence,
    SlotKind,
    SlotText,
)
from app.audit.gate import blocking, check_draft
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


def _draft(slots: list[SlotText]) -> AuditDraft:
    return AuditDraft(headline="태양광 3년간 상암축구장 6천 개 규모 산림 사라져", slots=slots)


def _empty_notes() -> list[SlotText]:
    """채울 수 없는 세 칸. 정상 초안에도 이 모양으로 들어간다."""
    return [
        SlotText(slot=s, filled=False, note="자료가 더 필요합니다.")
        for s in (SlotKind.KEY_FACT, SlotKind.EXTRA, SlotKind.AGENCY_VIEW)
    ]


def _rules(slots: list[SlotText]) -> set[str]:
    ledger = _ledger()
    findings = check_draft(_draft(slots), ledger, plan_slots(ledger))
    return {f.rule_id for f in blocking(findings)}


# ---------------------------------------------------------------------------
# 막아야 하는 것
# ---------------------------------------------------------------------------


def test_원장에_없는_수를_쓰면_막는다() -> None:
    """**이 검사기의 존재 이유.**

    자료에는 3년간 총량만 있다. 연도별 수치는 없다. 그런데 AI는 이런 문장을
    아주 잘 만든다.
    """
    filled = [
        SlotText(
            slot=SlotKind.KEY_FACT,
            filled=True,
            text="2016년 529ha였던 것이 2018년 2,443ha로 늘었음.",
            fact_ids=["AF-01"],
        )
    ]
    assert "NUMBER_NOT_IN_LEDGER" in _rules(filled)


def test_배수를_지어내면_막는다() -> None:
    """`4.6배` 같은 계산값도 자료에 없으면 못 쓴다."""
    filled = [
        SlotText(
            slot=SlotKind.LEAD_1,
            filled=True,
            text="3년간 4,407ha가 훼손돼 4.6배 늘었음.",
            fact_ids=["AF-02"],
        ),
        *_empty_notes(),
    ]
    assert "NUMBER_NOT_IN_LEDGER" in _rules(filled)


def test_못_채우기로_한_칸에_글이_있으면_막는다() -> None:
    """코드가 "못 채운다"고 정한 칸을 AI가 몰래 채우는 길을 막는다."""
    filled = [
        SlotText(
            slot=SlotKind.KEY_FACT,
            filled=True,
            text="산림 훼손이 해마다 늘고 있음.",
            fact_ids=[],
        )
    ]
    assert "UNFILLABLE_SLOT_FILLED" in _rules(filled)


def test_원장에_없는_사실을_근거로_달면_막는다() -> None:
    filled = [
        SlotText(
            slot=SlotKind.LEAD_1,
            filled=True,
            text="3년간 4,407ha가 훼손됐음.",
            fact_ids=["AF-99"],
        ),
        *_empty_notes(),
    ]
    assert "FACT_NOT_IN_LEDGER" in _rules(filled)


def test_그_칸에_못_쓰는_사실을_달면_막는다() -> None:
    """`(멘트)` 칸에 지역별 수치를 근거로 다는 것 같은 경우."""
    filled = [
        SlotText(
            slot=SlotKind.COMMENT,
            filled=True,
            text="윤 의원은 즉각 복원하라고 밝혔음.",
            fact_ids=["AF-05"],
        ),
        *_empty_notes(),
    ]
    assert "FACT_NOT_USABLE_IN_SLOT" in _rules(filled)


def test_채웠다면서_글이_비면_막는다() -> None:
    filled = [
        SlotText(slot=SlotKind.LEAD_1, filled=True, text="   ", fact_ids=["AF-01"]),
        *_empty_notes(),
    ]
    assert "SLOT_TEXT_EMPTY" in _rules(filled)


def test_채울_수_있는_칸을_AI가_안_쓰면_막는다() -> None:
    """**실제로 통과해 버린 구멍이다.**

    `effort=max`로 돌렸더니 AI가 출력 토큰을 추론에 다 쓰고 본문을 한 칸도
    안 냈다. 그런데 검사는 `막힌 것 0건`을 냈다 — 안 쓴 칸마다 이유가 붙어
    있었기 때문이다.

    자료가 없어 못 쓰는 것과 **AI가 안 쓴 것**은 다르다. 뒤는 실패다.
    """
    ledger = _ledger()
    slots = [
        SlotText(slot=slot, filled=False, note="AI가 이 칸을 쓰지 않았습니다.")
        for slot in SlotKind
    ]
    findings = check_draft(_draft(slots), ledger, plan_slots(ledger))
    assert "FILLABLE_SLOT_NOT_WRITTEN" in {f.rule_id for f in blocking(findings)}


def test_자료가_없어_못_쓰는_칸은_막지_않는다() -> None:
    """대조군. 이것까지 막으면 이 프로그램의 핵심이 죽는다.

    `(중요한 사실)`은 자료가 없어서 못 쓰는 자리다. 그건 실패가 아니라
    **정직한 결과**다.
    """
    slots = [
        SlotText(
            slot=SlotKind.LEAD_1,
            filled=True,
            text="3년간 4,407ha가 훼손됐음.",
            fact_ids=["AF-02"],
        ),
        *_empty_notes(),
    ]
    assert "FILLABLE_SLOT_NOT_WRITTEN" not in _rules(slots)


def test_못_채운_칸에_이유가_없으면_막는다() -> None:
    """빈 칸만 남기고 이유를 안 적으면 사람은 무엇을 해야 할지 모른다."""
    filled = [
        SlotText(slot=SlotKind.KEY_FACT, filled=False, note=""),
    ]
    assert "MISSING_NOTE" in _rules(filled)


def test_DRAFT_표시를_지우면_막는다() -> None:
    ledger = _ledger()
    draft = _draft([])
    draft.draft_label = "최종본"
    rules = {f.rule_id for f in blocking(check_draft(draft, ledger, plan_slots(ledger)))}
    assert "REQUIRED_MARK_MISSING" in rules


# ---------------------------------------------------------------------------
# 대조군 — 정상 초안은 통과해야 한다
# ---------------------------------------------------------------------------


def test_자료_안에서_쓴_초안은_통과한다() -> None:
    """**이게 없으면 위 시험들은 검사기가 아니라 벽을 재고 있는 것이다.**"""
    slots = [
        SlotText(
            slot=SlotKind.LEAD_1,
            filled=True,
            text=(
                "최근 3년간 태양광 사업 추진으로 상암축구장 6천 개 규모의 "
                "산림이 훼손된 것으로 나타났음."
            ),
            fact_ids=["AF-03", "AF-04"],
        ),
        SlotText(
            slot=SlotKind.LEAD_2,
            filled=True,
            text="윤 의원이 확인한 자료에 따르면 233만 그루가 베어져 4,407ha가 훼손됐음.",
            fact_ids=["AF-01", "AF-02", "AF-08"],
        ),
        SlotText(
            slot=SlotKind.DETAIL,
            filled=True,
            text="지역별로는 전남 1,025ha, 경북 790ha, 전북 684ha 순이었음.",
            fact_ids=["AF-05", "AF-06", "AF-07"],
        ),
        SlotText(
            slot=SlotKind.COMMENT,
            filled=True,
            text="윤 의원은 즉각 복원하라고 촉구했음.",
            fact_ids=["AF-08", "AF-09"],
        ),
        *_empty_notes(),
    ]
    findings = check_draft(_draft(slots), _ledger(), plan_slots(_ledger()))
    assert blocking(findings) == [], [f.rule_id for f in blocking(findings)]


def test_이름에_든_숫자는_자료에_있으면_쓸_수_있다() -> None:
    """`천천2호 태양광발전소`의 `2`.

    실제로 막혔다. 값이 아니라 **시설 이름**에 든 숫자인데 검사기가 지어낸
    값으로 봤다. 근거 문구는 원문을 그대로 옮긴 것이므로, 그 안의 숫자는
    자료에 있는 숫자다.
    """
    ledger = _ledger()
    ledger.facts.append(
        AuditFact(
            fact_id="AF-10",
            kind=AuditFactKind.CASE,
            subject="시설별 훼손",
            value="3.2ha",
            scope="전북 장수군 천천면",
            evidence=Evidence(
                source_id="SRC-01",
                quote="전북 장수군 천천면 천천2호 태양광발전소 3.2ha",
                line=1,
            ),
        )
    )
    slots = [
        SlotText(
            slot=SlotKind.EXTRA,
            filled=True,
            text="전북 장수군 천천면 천천2호 태양광발전소가 3.2ha임.",
            fact_ids=["AF-10"],
        ),
        SlotText(slot=SlotKind.KEY_FACT, filled=False, note="자료 필요"),
        SlotText(slot=SlotKind.AGENCY_VIEW, filled=False, note="자료 필요"),
    ]
    findings = check_draft(_draft(slots), ledger, plan_slots(ledger))
    assert "NUMBER_NOT_IN_LEDGER" not in {f.rule_id for f in blocking(findings)}


def test_근거_문구에도_없는_수는_여전히_막는다() -> None:
    """**대조군.** 근거 문구까지 허용했다고 아무 수나 통과하면 안 된다."""
    ledger = _ledger()
    slots = [
        SlotText(
            slot=SlotKind.LEAD_1,
            filled=True,
            text="2016년에 4,407ha가 훼손됐음.",
            fact_ids=["AF-02"],
        ),
        *_empty_notes(),
    ]
    findings = check_draft(_draft(slots), ledger, plan_slots(ledger))
    assert "NUMBER_NOT_IN_LEDGER" in {f.rule_id for f in blocking(findings)}


def test_나열하는_칸에_값이_없으면_막는다() -> None:
    """**같은 자료로 돌려도 결과가 흔들려서 넣은 검사다.**

    `(중요한 사실)`은 연도별 수치를 나열하는 자리인데, AI가 `연도별 현황이
    집계됐음`처럼 **아무 수도 없는 문장**을 쓸 때가 있다. 형식은 멀쩡하고
    검사도 통과하지만 아무것도 말하지 않는다.

    나열하는 칸(추이·항목별·사례)은 **요구한 개수만큼 값이 글에 있어야** 한다.
    """
    ledger = _ledger()
    ledger.facts.extend(
        [
            _fact("AF-10", AuditFactKind.TIME_SERIES, "529ha", "2016년"),
            _fact("AF-11", AuditFactKind.TIME_SERIES, "1,435ha", "2017년"),
        ]
    )
    slots = [
        SlotText(
            slot=SlotKind.KEY_FACT,
            filled=True,
            text="연도별 산림훼손 현황이 집계됐음.",
            fact_ids=["AF-10", "AF-11"],
        ),
        SlotText(slot=SlotKind.EXTRA, filled=False, note="자료 필요"),
        SlotText(slot=SlotKind.AGENCY_VIEW, filled=False, note="자료 필요"),
    ]
    findings = check_draft(_draft(slots), ledger, plan_slots(ledger))
    assert "SLOT_MISSING_REQUIRED_VALUES" in {f.rule_id for f in blocking(findings)}


def test_나열하는_칸은_준_값을_하나도_빠뜨리면_안_된다() -> None:
    """**AI가 자꾸 절반만 쓴다.**

    시설 5곳의 `13ha(131,426m²)`를 주면 `13ha`만 쓰고 `m²`를 통째로 뺐다.
    gold는 둘 다 쓴다. 준 값을 골라 쓰면 초안이 자료보다 빈약해진다.
    """
    ledger = _ledger()
    ledger.facts.extend(
        [
            _fact("AF-10", AuditFactKind.CASE, "13ha", "경북 봉화군"),
            _fact("AF-11", AuditFactKind.CASE, "131,426m²", "경북 봉화군"),
        ]
    )
    slots = [
        SlotText(
            slot=SlotKind.EXTRA,
            filled=True,
            text="경북 봉화군 13ha임.",  # m² 를 빠뜨렸다
            fact_ids=["AF-10", "AF-11"],
        ),
        SlotText(slot=SlotKind.KEY_FACT, filled=False, note="자료 필요"),
        SlotText(slot=SlotKind.AGENCY_VIEW, filled=False, note="자료 필요"),
    ]
    findings = check_draft(_draft(slots), ledger, plan_slots(ledger))
    assert "SLOT_MISSING_REQUIRED_VALUES" in {f.rule_id for f in blocking(findings)}


def test_준_값을_다_쓰면_통과한다() -> None:
    """대조군. 다 쓴 글까지 막으면 아무것도 못 쓴다."""
    ledger = _ledger()
    ledger.facts.extend(
        [
            _fact("AF-10", AuditFactKind.CASE, "13ha", "경북 봉화군"),
            _fact("AF-11", AuditFactKind.CASE, "131,426m²", "경북 봉화군"),
        ]
    )
    slots = [
        SlotText(
            slot=SlotKind.EXTRA,
            filled=True,
            text="경북 봉화군 13ha(131,426m²)임.",
            fact_ids=["AF-10", "AF-11"],
        ),
        SlotText(slot=SlotKind.KEY_FACT, filled=False, note="자료 필요"),
        SlotText(slot=SlotKind.AGENCY_VIEW, filled=False, note="자료 필요"),
    ]
    findings = check_draft(_draft(slots), ledger, plan_slots(ledger))
    assert "SLOT_MISSING_REQUIRED_VALUES" not in {f.rule_id for f in blocking(findings)}


def test_값을_요구한_만큼_담으면_통과한다() -> None:
    """대조군. 값을 제대로 쓴 문장까지 막으면 글을 못 쓴다."""
    ledger = _ledger()
    ledger.facts.extend(
        [
            _fact("AF-10", AuditFactKind.TIME_SERIES, "529ha", "2016년"),
            _fact("AF-11", AuditFactKind.TIME_SERIES, "1,435ha", "2017년"),
        ]
    )
    slots = [
        SlotText(
            slot=SlotKind.KEY_FACT,
            filled=True,
            text="연도별로는 2016년 529ha, 2017년 1,435ha임.",
            fact_ids=["AF-10", "AF-11"],
        ),
        SlotText(slot=SlotKind.EXTRA, filled=False, note="자료 필요"),
        SlotText(slot=SlotKind.AGENCY_VIEW, filled=False, note="자료 필요"),
    ]
    findings = check_draft(_draft(slots), ledger, plan_slots(ledger))
    assert "SLOT_MISSING_REQUIRED_VALUES" not in {f.rule_id for f in blocking(findings)}


def test_리드는_값_개수를_강제하지_않는다() -> None:
    """리드는 나열하는 자리가 아니다.

    `(리드1)`은 규모를 한눈에 보이게 하는 자리라 비유만으로도 말이 된다.
    나열 검사를 여기까지 걸면 멀쩡한 문장이 막힌다.
    """
    slots = [
        SlotText(
            slot=SlotKind.LEAD_1,
            filled=True,
            text="3년간 상암축구장 6천 개 규모의 산림이 사라졌음.",
            fact_ids=["AF-03", "AF-04"],
        ),
        *_empty_notes(),
    ]
    assert "SLOT_MISSING_REQUIRED_VALUES" not in _rules(slots)


def test_소수를_통째로_읽는다() -> None:
    """README 이관 목록 7번.

    `4.6배`를 `4`와 `6`으로 쪼개 읽으면, 원장에 `4,407`과 `6천 개`가 있다는
    이유로 통과해 버린다. 소수는 **한 값으로** 봐야 막힌다.
    """
    slots = [
        SlotText(
            slot=SlotKind.LEAD_1,
            filled=True,
            text="3년간 4.6배 늘었음.",
            fact_ids=["AF-03"],
        ),
        *_empty_notes(),
    ]
    assert "NUMBER_NOT_IN_LEDGER" in _rules(slots)


def test_서술체로_쓰면_경고한다() -> None:
    """README 이관 목록 11번. gold는 `~했음` 개조식이다.

    문체는 안전 문제가 아니라 **경고**다 (README §2 — 문체 문제는 일반 경고).
    막아 버리면 글을 못 쓴다.
    """
    slots = [
        SlotText(
            slot=SlotKind.DETAIL,
            filled=True,
            text="지역별로는 전남 1,025ha, 경북 790ha, 전북 684ha 순이었다.",
            fact_ids=["AF-05", "AF-06", "AF-07"],
        ),
        *_empty_notes(),
    ]
    ledger = _ledger()
    findings = check_draft(_draft(slots), ledger, plan_slots(ledger))

    # 경고는 뜨지만 막지는 않는다.
    assert "STYLE_NOT_OUTLINE_FORM" in {f.rule_id for f in findings}
    assert "STYLE_NOT_OUTLINE_FORM" not in {f.rule_id for f in blocking(findings)}


def test_숫자가_없는_문장은_숫자_검사에_안_걸린다() -> None:
    """숫자를 아예 안 쓰는 문장까지 막으면 글을 쓸 수 없다."""
    slots = [
        SlotText(
            slot=SlotKind.COMMENT,
            filled=True,
            text="윤 의원은 산림 훼손을 중단하라고 촉구했음.",
            fact_ids=["AF-09"],
        ),
        *_empty_notes(),
    ]
    assert "NUMBER_NOT_IN_LEDGER" not in _rules(slots)
