"""국정감사형 — 내려받는 결과물이 정직한지 본다.

내보내기는 화면보다 위험하다. 화면 글은 이 프로그램 안에 머물지만, 내려받은
파일은 메일로 가고 다른 사람 손에 들어간다. 받은 사람은 **그 파일만 보고**
판단한다.

그래서 두 가지를 본다.

1. **글을 새로 만들지 않는가** — 초안에 없는 말이 파일에서 생기면 안 된다
2. **못 채운 칸이 파일에도 남는가** — 빈 칸을 조용히 빼면, 받은 사람은
   빠진 것이 있다는 사실 자체를 모른다
"""

from __future__ import annotations

from app.audit.contracts import (
    DRAFT_LABEL,
    AuditDraft,
    AuditFact,
    AuditFactKind,
    AuditLedger,
    Evidence,
    SlotKind,
    SlotText,
)
from app.audit.export import to_markdown


def _ledger() -> AuditLedger:
    return AuditLedger(
        facts=[
            AuditFact(
                fact_id="AF-01",
                kind=AuditFactKind.TOTAL,
                subject="베어진 나무",
                value="233만 그루",
                scope="3년간",
                evidence=Evidence(
                    source_id="SRC-01",
                    source_name="국정감사 재료",
                    quote="3년간 베어진 나무만 233만 그루로",
                    line=4,
                ),
            )
        ]
    )


def _draft() -> AuditDraft:
    return AuditDraft(
        headline="태양광 3년간 상암축구장\n6천 개 규모 산림 사라져",
        subheads=[
            "3년간 베어진 나무만 233만 그루로 4,407ha의 산림 훼손",
            "지역별로 전남(1,025ha), 경북(790ha), 전북(684ha)순으로 많이 훼손",
        ],
        slots=[
            SlotText(
                slot=SlotKind.LEAD_1,
                filled=True,
                text="최근 3년간 233만 그루가 베어진 것으로 나타났음.",
                fact_ids=["AF-01"],
            ),
            SlotText(
                slot=SlotKind.KEY_FACT,
                filled=False,
                note="시점별(연도별) 수치 2건 이상 (지금 0건)",
            ),
        ],
    )


def test_DRAFT_표시가_위아래_둘_다_남는다() -> None:
    """5일차 검토가 지적한 자리다.

    표시가 위·아래 둘인데 시험이 하나만 보면, 위쪽을 잘라 내고 돌려도
    통과한다. **개수를 센다.**
    """
    text = to_markdown(_draft(), _ledger())
    assert text.count(DRAFT_LABEL) >= 2, text.count(DRAFT_LABEL)


def test_표시를_지운_초안이라도_파일에는_표시가_남는다() -> None:
    """표시를 초안이 아니라 **상수**에서 가져오므로 지울 길이 없다."""
    draft = _draft()
    draft.draft_label = "최종본"
    text = to_markdown(draft, _ledger())
    assert DRAFT_LABEL in text
    assert "최종본" not in text


def test_못_채운_칸이_이유와_함께_파일에_남는다() -> None:
    """**이 프로토타입이 보여 주려는 것.**

    보통 AI는 여기에 그럴듯한 연도별 수치를 지어 넣는다. 이 프로그램은
    비워 두고 무엇이 더 필요한지 적는다. 그 사실이 파일에 남아야 한다.
    """
    text = to_markdown(_draft(), _ledger())
    assert "중요한 사실" in text
    assert "시점별(연도별) 수치 2건 이상" in text


#: 내보내기가 **덧붙여도 되는 말 전부.** 여기 없는 말이 파일에 나오면 실패다.
#:
#: 허용 목록으로 적는다. "이건 괜찮겠지" 하고 하나씩 늘리면 아무것도 못 지키는
#: 시험이 된다(13차 검토가 지적한 실패 모양). 내보내기에 문장을 새로 넣으면
#: 이 시험이 먼저 빨간불을 내야 한다.
_FIXED_BOILERPLATE = """
> **DRAFT / 내부 검토용**
#
-
○
⚠ 못 채움 — 필요한 자료:
〈끝〉
## 근거
| 사실 | 값 | 출처 | 줄 | 원문 |
|---|---|---|---|---|
---
**DRAFT / 내부 검토용** · 사람이 확인해야 하는 초안입니다.
"""


def test_글을_새로_만들지_않는다() -> None:
    """파일에 새로 생기는 말은 **미리 정한 고정 머리글뿐**이어야 한다.

    내보내기가 문장을 만들면 그 자리가 새 고장점이 된다. 검사를 통과한 초안의
    글자만 옮겨야 한다.
    """
    from app.audit.contracts import SLOT_LABELS

    draft = _draft()
    ledger = _ledger()
    text = to_markdown(draft, ledger)

    # 1) 초안이 들고 있는 글 전부. **못 채운 칸의 이유(note)도 초안의 글이다.**
    allowed: set[str] = set()
    allowed.update(draft.headline.split())
    for sub in draft.subheads:
        allowed.update(sub.split())
    for entry in draft.slots:
        allowed.update(entry.text.split())
        allowed.update(entry.note.split())

    # 2) 원장이 들고 있는 글 전부.
    for fact in ledger.facts:
        allowed.update(
            f"{fact.fact_id} {fact.subject} {fact.value} {fact.scope} "
            f"{fact.evidence.quote} {fact.evidence.source_name} "
            f"{fact.evidence.line}".split()
        )

    # 3) 칸 이름. 계약이 정한 고정 낱말이다.
    for label in SLOT_LABELS.values():
        allowed.update(f"({label})".split())

    # 4) 고정 머리글.
    allowed.update(_FIXED_BOILERPLATE.split())

    made_up = {w for w in text.split() if w not in allowed}
    assert made_up == set(), made_up


def test_내보내기에_문장을_더하면_이_시험이_잡는다() -> None:
    """**대조군.** 위 시험이 정말 지키는지 확인한다.

    허용 목록에 없는 말이 파일에 들어가면 반드시 걸려야 한다. 안 걸리면
    위 시험은 아무것도 안 지키는 것이다.
    """
    from app.audit.contracts import SLOT_LABELS

    draft = _draft()
    ledger = _ledger()
    text = to_markdown(draft, ledger) + "\n전문가들은 심각하다고 평가했다."

    allowed: set[str] = set(_FIXED_BOILERPLATE.split())
    allowed.update(draft.headline.split())
    for sub in draft.subheads:
        allowed.update(sub.split())
    for entry in draft.slots:
        allowed.update(entry.text.split())
        allowed.update(entry.note.split())
    for fact in ledger.facts:
        allowed.update(
            f"{fact.fact_id} {fact.subject} {fact.value} {fact.scope} "
            f"{fact.evidence.quote} {fact.evidence.source_name} "
            f"{fact.evidence.line}".split()
        )
    for label in SLOT_LABELS.values():
        allowed.update(f"({label})".split())

    made_up = {w for w in text.split() if w not in allowed}
    assert "전문가들은" in made_up, made_up


def test_근거표에_줄_번호가_붙는다() -> None:
    """보좌관이 원문에서 직접 확인할 수 있어야 한다."""
    text = to_markdown(_draft(), _ledger())
    assert "국정감사 재료" in text
    assert "4" in text  # 줄 번호
    assert "3년간 베어진 나무만 233만 그루로" in text


def test_끝_표시가_붙는다() -> None:
    """실제 보도자료는 `〈끝〉`으로 닫는다 (gold와 같음)."""
    assert "〈끝〉" in to_markdown(_draft(), _ledger())


def test_제목_두_줄이_그대로_나온다() -> None:
    text = to_markdown(_draft(), _ledger())
    assert "태양광 3년간 상암축구장" in text
    assert "6천 개 규모 산림 사라져" in text
