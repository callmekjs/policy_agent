"""수정본 안전 검사 (README §2.10, §2.16.4, §4.2 · 누적 5일차 합격선 K·M4).

4일차 초안 검사(`draft_gate`)와 **방향이 반대다.**

- `draft_gate`는 **새로 들어온 것**을 센다. 원장에 없는 값이 초안에 나타났는가.
- 이 파일은 **없어진 것**을 센다. 있던 값이 고치는 과정에서 사라졌는가.

둘 다 필요하다. 사용자가 "이 문장 좀 다듬어 줘"라고 했을 때 AI가 시행일이나
표결 수를 슬쩍 빼먹으면, 새로 들어온 거짓말이 없으므로 `draft_gate`는 통과시킨다.
읽는 사람은 무엇이 빠졌는지 알 수 없다.

수정본은 **이 검사와 4일차 검사를 둘 다** 받는다. 하나라도 걸리면 수정본을
버리고 **이전 초안을 그대로 둔다.** 고치는 데 실패했다고 멀쩡하던 초안까지
잃으면 안 된다(K4).
"""

from __future__ import annotations

from app.gates.draft_gate import _letters, _squeeze
from app.gates.protection import used_fact_ids
from app.harness.draft_contracts import (
    DraftCandidate,
    ValidationFinding,
    ValidationSeverity,
)
from app.harness.fact_contracts import FactLedger
from app.harness.review_contracts import FactReview, FactVerdict

RULE_DOC = "README §"


def _finding(
    index: int, rule_id: str, doc: str, part: str, message: str
) -> ValidationFinding:
    return ValidationFinding(
        finding_id=f"RV-{index:03d}",
        rule_id=rule_id,
        rule_document=f"{RULE_DOC}{doc}",
        affected_part=part,
        severity=ValidationSeverity.BLOCKING,
        message=message,
    )


def _all_text(candidate: DraftCandidate) -> str:
    """초안이 사람에게 보여 주는 글 전부."""
    parts = [
        candidate.title.text,
        candidate.lead.text,
        *(p.text for p in candidate.key_points),
        *(p.text for p in candidate.paragraphs),
    ]
    return _letters(_squeeze(" ".join(parts)))


def _all_fact_ids(candidate: DraftCandidate) -> set[str]:
    """`protection`과 **같은 정의**를 쓴다.

    "초안이 어느 사실을 쓰고 있는가"를 두 벌로 적으면 한쪽만 고쳐지고 어긋난다.
    `L1`이 정확히 그렇게 났다.
    """
    return used_fact_ids(candidate)


def _all_rule_ids(candidate: DraftCandidate) -> set[str]:
    return {r for p in candidate.paragraphs for r in p.supplementary_rule_ids}


def check_revision(
    *,
    previous: DraftCandidate,
    revised: DraftCandidate,
    ledger: FactLedger,
    fact_reviews: list[FactReview],
) -> list[ValidationFinding]:
    """고친 결과에서 **없어진 것**을 센다.

    통과선은 `verification/day5-pass-bar.md`의 K1·K2·K3·K5와 M4다.
    K4(실패한 수정이 이전 초안을 덮지 않는다)는 검사가 아니라 **부르는 쪽의
    책임**이라 여기서 재지 않는다. 이 함수가 무엇이든 돌려주면 부르는 쪽은
    수정본을 버려야 한다.
    """
    findings: list[ValidationFinding] = []

    def add(rule_id: str, doc: str, part: str, message: str) -> None:
        findings.append(_finding(len(findings) + 1, rule_id, doc, part, message))

    before_text = _all_text(previous)
    after_text = _all_text(revised)
    before_facts = _all_fact_ids(previous)
    after_facts = _all_fact_ids(revised)

    # --- K1. 보호 사실은 고칠 수 없다 ---------------------------------------
    #
    # 보호 사실은 자료가 말한 값 중 **틀리면 가장 위험한 것**이다. 표결 수,
    # 의결일, 의안번호가 그렇다. 빠지는 것과 바뀌는 것을 따로 센다. 바뀌는
    # 쪽이 더 위험하다 — 읽는 사람이 틀린 값을 사실로 믿는다.
    # **전과 후를 견준다.** "이 사실이 쓰였는가"가 아니라 "**있던 것이
    # 없어졌는가**"를 본다. 처음부터 안 쓰인 사실은 이 검사의 관심이 아니다.
    # 그 판정은 4일차 검사가 이미 한다.
    for fact in ledger.facts:
        if not fact.protected:
            continue
        # 근거가 떨어져 나갔다.
        if fact.fact_id in before_facts and fact.fact_id not in after_facts:
            add(
                "PROTECTED_FACT_DROPPED",
                "2.10",
                f"사실 {fact.fact_id}",
                f"고치는 과정에서 보호 사실 `{fact.value}`이(가) 빠졌습니다. "
                "이 값은 고칠 수 없습니다.",
            )
            continue
        # 근거는 붙어 있는데 글에서 값이 사라졌다. 빠지는 것보다 위험하다 —
        # 근거를 달고 다른 값을 말하면 읽는 사람이 틀린 값을 사실로 믿는다.
        value = _letters(_squeeze(fact.value))
        if value and value in before_text and value not in after_text:
            add(
                "PROTECTED_FACT_CHANGED",
                "2.10",
                f"사실 {fact.fact_id}",
                f"고치는 과정에서 보호 사실의 값 `{fact.value}`이(가) "
                "글에서 사라졌습니다. 이 값은 고칠 수 없습니다.",
            )

    # --- K2. 부칙은 사라질 수 없다 ------------------------------------------
    #
    # 부칙은 시행일·적용례·경과조치·특례다. 빠지면 읽는 사람이 **언제부터
    # 적용되는지** 모른다. §2.16.4가 중대한 실패로 정한 자리다.
    # **번호가 아니라 글을 견준다.** 읽는 사람은 `SR-01`이라는 번호를 보지
    # 않는다. 시행일이 적힌 **문장**을 읽는다. 번호만 견주면 문장을 통째로
    # 다른 말로 바꿔도 검사가 아무것도 못 본다 (5일차 검토 `K2`).
    after_rules = _all_rule_ids(revised)
    for rule_id in sorted(_all_rule_ids(previous)):
        if rule_id not in after_rules:
            add(
                "REVISION_DROPPED_RULE",
                "2.16.4",
                "부칙",
                f"고치는 과정에서 부칙 `{rule_id}`이(가) 빠졌습니다. "
                "부칙은 고칠 수 없습니다.",
            )
            continue
        # 번호는 붙어 있는데 글이 사라졌다. 빠지는 것보다 위험하다 — 되짚어
        # 보면 "부칙이 있다"고 나오는데 정작 언제부터 적용되는지는 알 수 없다.
        for paragraph in previous.paragraphs:
            if rule_id not in paragraph.supplementary_rule_ids:
                continue
            kept = _letters(_squeeze(paragraph.text))
            if kept and kept not in after_text:
                add(
                    "REVISION_DROPPED_RULE",
                    "2.16.4",
                    "부칙",
                    f"고치는 과정에서 부칙 `{rule_id}`의 글이 사라졌습니다. "
                    "부칙은 자료에 적힌 그대로여야 합니다.",
                )
                break

    # --- K3. 발표 주체는 고칠 수 없다 ---------------------------------------
    #
    # 누가 발표하는지는 **사용자가 확인한 값**이다. AI가 고치면서 바꿀 수 없다.
    if previous.announcement_subject_fact_id != revised.announcement_subject_fact_id:
        add(
            "ANNOUNCER_CHANGED",
            "2.10",
            "발표 주체",
            "고치는 과정에서 발표 주체가 바뀌었습니다. 발표 주체는 사용자가 "
            "확인한 값이라 고칠 수 없습니다.",
        )

    # --- K5. 요청만으로 새 사실을 넣을 수 없다 ------------------------------
    #
    # 사용자가 "재석 250인이라고 써 줘"라고 해도 원장에 없으면 못 쓴다.
    # **사람의 요청은 자료가 아니다.**
    known = {f.fact_id for f in ledger.facts}
    for fact_id in sorted(after_facts - known):
        add(
            "REVISION_FACT_NOT_IN_LEDGER",
            "4.2",
            "수정본",
            f"자료에 없는 사실 `{fact_id}`을(를) 근거로 달았습니다. "
            "사람이 요청한 내용은 자료가 아닙니다.",
        )

    # --- M4. 사람이 틀렸다고 한 사실은 남을 수 없다 -------------------------
    #
    # 체크리스트에서 "틀렸다"를 눌렀는데 그 사실을 쓴 문장이 그대로 남아 있으면
    # 확인 절차가 아무 뜻이 없다.
    wrong = {r.fact_id for r in fact_reviews if r.verdict is FactVerdict.WRONG}
    for fact_id in sorted(wrong & after_facts):
        add(
            "WRONG_FACT_STILL_USED",
            "3.7",
            "수정본",
            f"사람이 틀렸다고 표시한 사실 `{fact_id}`을(를) 아직 쓰고 있습니다.",
        )

    return findings
