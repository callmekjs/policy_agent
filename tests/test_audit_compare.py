"""사람이 쓴 것과 나란히 놓는 비교표.

이 표가 **결과물의 절반**이다. 초안만 보면 잘 썼는지 판단할 기준이 없다.

특히 두 가지를 정확히 말해야 한다.

1. **gold에 있는 칸을 우리가 못 썼다면** 그 사실이 표에 보여야 한다
2. **썼는데 숫자가 없으면** 그것도 보여야 한다 — 글이 짧은 것과 내용이 없는
   것은 다르다
"""

from __future__ import annotations

from app.audit.compare import build_comparison, split_gold_body
from app.audit.contracts import (
    AuditDraft,
    AuditFact,
    AuditFactKind,
    AuditLedger,
    Evidence,
    SlotKind,
    SlotText,
)
from app.audit.slots import plan_slots

#: 실제 gold와 **같은 구조**로 적는다. 칸을 줄여 놓고 시험하면 자리로 가르는
#: 규칙을 잘못 재게 된다. `○ 이에 산림청은…`처럼 **이름이 없는 문단**이 있는
#: 것이 이 문서의 특징이고, 그것이 이 시험이 겨누는 자리다.
GOLD_BODY = """○ (리드1) 상암축구장 6천 개 규모 산림이 훼손됐음.

○ (리드2) 최근 3년간 2,327,495그루가 베어져 4,407ha가 훼손됐음.

○ (중요한 사실) 2016년 529ha, 2017년 1,435ha, 2018년 2,443ha로 늘었음.

○ (세부사실) 전남 1,025ha, 경북 790ha 순임.

○ (추가사실) 경북 봉화군 13ha 등임.

○ 이에 산림청은 산지관리법 시행령 개정 이후(2018.12.4.) 신청 건수가 감소했다는 입장임.

○ (멘트) 윤 의원은 “즉각 복원하라”고 촉구했음."""


def _ledger() -> AuditLedger:
    return AuditLedger(
        facts=[
            AuditFact(
                fact_id="AF-01",
                kind=AuditFactKind.TOTAL,
                subject="훼손 면적",
                value="4,407ha",
                scope="3년간",
                evidence=Evidence(source_id="SRC-01", quote="4,407ha", line=1),
            )
        ]
    )


def _draft() -> AuditDraft:
    return AuditDraft(
        headline="제목",
        slots=[
            SlotText(
                slot=SlotKind.LEAD_1,
                filled=True,
                text="3년간 4,407ha가 훼손됐음.",
                fact_ids=["AF-01"],
            ),
            SlotText(
                slot=SlotKind.KEY_FACT,
                filled=False,
                note="시점별(연도별) 수치 2건 이상 (지금 0건)",
            ),
            SlotText(
                slot=SlotKind.AGENCY_VIEW,
                filled=True,
                text="산림청은 감소했다는 입장임.",
                fact_ids=[],
            ),
        ],
    )


def _report() -> str:
    ledger = _ledger()
    return build_comparison(
        draft=_draft(),
        ledger=ledger,
        plans=plan_slots(ledger),
        findings=[],
        material="재료",
        answer=GOLD_BODY,
        attempts=1,
    )


# ---------------------------------------------------------------------------
# gold 가르기
# ---------------------------------------------------------------------------


def test_이름_없는_문단을_다음_칸으로_보낸다() -> None:
    """gold의 `○ 이에 산림청은…`에는 칸 이름이 없다.

    앞 칸에 붙이면 `(기관 입장)`이 "gold에 없다"고 잘못 나온다. 실제로 그랬다.
    """
    order = ["리드1", "리드2", "중요한 사실", "세부사실", "추가사실", "기관 입장", "멘트"]
    by_slot = split_gold_body(GOLD_BODY, order)

    assert "산림청" in by_slot["기관 입장"], by_slot.get("기관 입장")
    assert "산림청" not in by_slot["중요한 사실"]


def test_이름_있는_문단은_그대로_간다() -> None:
    order = ["리드1", "리드2", "중요한 사실", "세부사실", "추가사실", "기관 입장", "멘트"]
    by_slot = split_gold_body(GOLD_BODY, order)
    assert by_slot["리드1"].startswith("상암축구장")
    assert "2016년" in by_slot["중요한 사실"]


# ---------------------------------------------------------------------------
# 표가 사실대로 말하는가
# ---------------------------------------------------------------------------


def test_문장을_한_줄에_나란히_놓는다() -> None:
    """**이 문서의 본체.** 사람이 쓴 문장과 AI가 쓴 문장이 한 줄에 있어야
    눈으로 맞대어 볼 수 있다."""
    report = _report()
    assert "| 칸 | 사람이 쓴 것 (gold) | 내 AI가 쓴 것 |" in report
    # 한 줄에 둘 다 있어야 한다.
    row = next(l for l in report.splitlines() if l.startswith("| **리드1**"))
    assert "상암축구장 6천 개 규모 산림이 훼손됐음." in row
    assert "3년간 4,407ha가 훼손됐음." in row


def test_표_칸에_줄바꿈이_들어가지_않는다() -> None:
    """줄바꿈이 한 칸에 들어가면 표가 통째로 깨진다."""
    report = _report()
    body = report[report.index("| 칸 | 사람이") : report.index("## 한눈에 보기")]
    for line in body.splitlines():
        if line.startswith("|"):
            assert line.count("|") >= 4, line


def test_못_쓴_칸을_표에_적는다() -> None:
    """`(중요한 사실)`은 gold에 있고 우리는 못 썼다. 그 사실이 보여야 한다."""
    report = _report()
    assert "⚠ 자료 없어 못 씀" in report


def test_썼는데_숫자가_없으면_구분해_적는다() -> None:
    """`(기관 입장)`은 썼지만 수가 없다.

    글이 짧은 것과 **내용이 없는 것**은 다르다. 뭉뚱그리면 안 된다.
    """
    assert "△ 썼지만 수가 없음" in _report()


def test_수를_다_담은_칸은_통과로_적는다() -> None:
    """대조군. 다 문제라고 하면 이 표는 아무것도 말하지 않는다."""
    assert "✅ 수를 다 담음" in _report()


def test_숫자_기준_채움_비율을_낸다() -> None:
    """분량이 아니라 **숫자 개수**로 잰다. 짧아도 수가 다 있으면 내용은 있다."""
    assert "숫자 기준" in _report()


def test_다시_쓴_횟수를_숨기지_않는다() -> None:
    """AI가 흔들린다는 사실도 결과의 일부다."""
    assert "AI가 다시 쓴 횟수" in _report()


def test_아직_못_하는_것을_적는다() -> None:
    """이름을 검사하지 않는다는 한계를 결과물에 남긴다.

    채용 심사자가 물으면 정확히 답할 수 있어야 한다.
    """
    report = _report()
    assert "이름·기관명은 검사하지 않는다" in report
