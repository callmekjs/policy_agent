"""국정감사형 — AI가 준 사실을 Harness가 **원문과 맞대어** 거르는지 본다.

AI는 형식을 완벽히 지키면서 값을 지어낼 수 있다. 진짜 문장을 근거로 달고 그
안에 없는 숫자를 붙이는 것이 가장 위험하다. 근거가 있으니 사람이 믿는다.

그래서 Harness는 두 가지를 따로 본다.

1. **근거 문구가 원문에 글자 그대로 있는가**
2. **값이 그 근거 문구 안에 있는가**

둘 중 하나라도 아니면 버린다. 그리고 **버린 것을 사람에게 보여 준다** —
조용히 버리면 사람은 자료를 더 넣어야 하는지 알 수 없다.
"""

from __future__ import annotations

from app.audit.contracts import AuditFactKind
from app.audit.extraction import verify_facts
from app.harness.source_normalizer import normalize_source

#: gold의 제목 + 부제 3줄. 이 프로토타입의 재료다.
#: **제목이 두 줄로 갈려 있다** — `상암축구장`과 `6천 개` 사이에 줄바꿈이 있다.
MATERIAL = """태양광 3년간 상암축구장
6천 개 규모 산림 사라져

-3년간 베어진 나무만 233만 그루로 4,407ha의 산림 훼손

-지역별로 전남(1,025ha), 경북(790ha),
전북(684ha)순으로 많이 훼손

-윤 의원, “매해 산림훼손 기하급수적으로 증가 중, 미세먼지 필터인 산림 훼손 중단하고 즉각 복원하라”
"""


def _verify(raw_facts: list[dict]):
    return verify_facts(
        {"schema_version": "1.0.0", "facts": raw_facts},
        normalized=normalize_source(MATERIAL),
        source_id="SRC-01",
        source_name="국정감사 재료",
    )


def _raw(kind: str, value: str, quote: str, subject: str = "산지훼손") -> dict:
    return {"kind": kind, "subject": subject, "value": value, "quote": quote, "scope": ""}


# ---------------------------------------------------------------------------
# 막아야 하는 것
# ---------------------------------------------------------------------------


def test_원문에_없는_근거는_버린다() -> None:
    """AI가 근거 문구 자체를 지어낸 경우."""
    ledger = _verify(
        [_raw("TOTAL", "529ha", "2016년에 529ha였던 것이 2018년에는 2,443ha로 늘었다")]
    )

    assert ledger.facts == []
    assert ledger.rejected, "버린 것을 남기지 않았습니다."


def test_근거는_진짜인데_값이_그_안에_없으면_버린다() -> None:
    """**가장 위험한 경우.** 진짜 문장을 달고 그 안에 없는 숫자를 붙인다.

    근거가 붙어 있으니 사람은 확인했다고 여긴다. 여기서 못 막으면 근거를
    다는 일 자체가 뜻을 잃는다.
    """
    ledger = _verify(
        [_raw("TOTAL", "500만 그루", "3년간 베어진 나무만 233만 그루로")]
    )

    assert ledger.facts == [], [f.value for f in ledger.facts]
    assert any("500만 그루" in r for r in ledger.rejected), ledger.rejected


def test_근거가_비면_버린다() -> None:
    ledger = _verify([_raw("TOTAL", "233만 그루", "")])
    assert ledger.facts == []


def test_모르는_종류는_버린다() -> None:
    """AI가 목록에 없는 종류를 지어내면 받지 않는다."""
    ledger = _verify(
        [_raw("추측", "233만 그루", "3년간 베어진 나무만 233만 그루로")]
    )
    assert ledger.facts == []


# ---------------------------------------------------------------------------
# 받아야 하는 것 — 대조군. 다 버리면 검사기가 아니라 벽이다
# ---------------------------------------------------------------------------


def test_원문에_있는_사실은_받고_줄_번호를_붙인다() -> None:
    ledger = _verify(
        [_raw("TOTAL", "233만 그루", "3년간 베어진 나무만 233만 그루로")]
    )

    assert len(ledger.facts) == 1
    fact = ledger.facts[0]
    assert fact.value == "233만 그루"
    assert fact.kind is AuditFactKind.TOTAL
    # 되짚을 수 있어야 한다. 몇 번째 줄인지 없으면 사람이 원문에서 못 찾는다.
    assert fact.evidence.line == 4, fact.evidence.line
    assert fact.evidence.source_name == "국정감사 재료"
    # 확인은 아직 안 했다. **사람이 봐야 보호된다.**
    assert fact.confirmed is False


def test_줄바꿈이_끼어도_근거를_찾는다() -> None:
    """제목이 두 줄로 갈려 있다.

    `상암축구장`과 `6천 개` 사이에 줄바꿈이 있어서, 줄바꿈을 그대로 두고
    찾으면 진짜 사실을 못 찾는다. 사람 눈에는 한 문장이다.
    """
    ledger = _verify(
        [_raw("COMPARISON", "상암축구장 6천 개", "상암축구장 6천 개 규모 산림 사라져")]
    )

    assert len(ledger.facts) == 1, ledger.rejected
    assert ledger.facts[0].value == "상암축구장 6천 개"


def test_지역별_수치_셋을_다_받는다() -> None:
    """이 셋이 있어야 `(세부사실)` 칸이 열린다."""
    ledger = _verify(
        [
            _raw("BREAKDOWN", "1,025ha", "지역별로 전남(1,025ha)"),
            _raw("BREAKDOWN", "790ha", "경북(790ha)"),
            _raw("BREAKDOWN", "684ha", "전북(684ha)순으로 많이 훼손"),
        ]
    )

    assert len(ledger.facts) == 3, ledger.rejected
    assert {f.fact_id for f in ledger.facts} == {"AF-01", "AF-02", "AF-03"}


def test_버린_것과_받은_것이_섞여도_각각_처리한다() -> None:
    """하나가 나쁘다고 나머지를 버리면 안 된다."""
    ledger = _verify(
        [
            _raw("TOTAL", "233만 그루", "3년간 베어진 나무만 233만 그루로"),
            _raw("TIME_SERIES", "529ha", "2016년에 529ha였던 것이"),
            _raw("BREAKDOWN", "1,025ha", "지역별로 전남(1,025ha)"),
        ]
    )

    assert [f.value for f in ledger.facts] == ["233만 그루", "1,025ha"]
    assert len(ledger.rejected) == 1
