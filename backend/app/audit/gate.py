"""국정감사형 초안 검사 — 자료에 없는 값이 본문에 들어가는 것을 막는다.

이 종류에서 가장 위험한 것은 **숫자**다. 보도자료는 숫자로 말한다.
`2016년 529ha가 2018년 2,443ha로 늘었음` 같은 문장은 자료가 없어도 AI가
얼마든지 만들고, 그럴듯하다. 받은 기자는 구분하지 못한다.

그래서 본문에 나온 **모든 수를 원장과 맞대어** 본다. 원장에 없는 수가 하나라도
있으면 초안을 내주지 않는다.

기존 본회의형 `draft_gate.py`와 방향은 같고 재료가 다르다. 그쪽은 법률 낱말과
조문을 보고, 여기는 통계 수치를 본다.
"""

from __future__ import annotations

import re

from app.audit.contracts import (
    DRAFT_LABEL,
    SLOT_LABELS,
    AuditDraft,
    AuditFactKind,
    AuditLedger,
    Finding,
    Severity,
    SlotKind,
    SlotPlan,
)
from app.audit.slots import SLOT_EXTRAS, SLOT_NEEDS

#: 글에서 수를 찾는 자.
#:
#: **소수점과 자릿점을 한 값에 넣는다.** `4.6배`를 `4`와 `6`으로 쪼개 읽으면
#: 원장에 `4,407`과 `6천 개`가 있다는 이유로 통과해 버린다 (README 이관 목록 7번).
#: 뒤에 붙은 마침표는 문장 끝일 수 있으므로 떼고 본다.
_NUMBER = re.compile(r"\d[\d.,]*")

#: 개조식 어미. gold는 `~했음`·`~임`으로 끝난다.
#: **허용 목록이다.** 막을 어미를 세는 방식은 한국어에서 진다.
_OUTLINE_ENDINGS = ("음.", "음", "임.", "임", "함.", "함")

#: 값을 **나열하는** 칸의 사실 종류. 이 칸들은 글에 값이 실제로 들어 있어야 한다.
#: 리드·기관 입장·멘트는 나열하는 자리가 아니라 여기 없다.
_ENUMERATED_KINDS = frozenset(
    {AuditFactKind.TIME_SERIES, AuditFactKind.BREAKDOWN, AuditFactKind.CASE}
)

_WHITESPACE = re.compile(r"\s+")


def _packed(text: str) -> str:
    return _WHITESPACE.sub("", text)


def _numbers_in(text: str) -> list[str]:
    """글에 나온 수 전부. 뒤에 붙은 마침표·쉼표는 뗀다."""
    found = []
    for raw in _NUMBER.findall(text):
        cleaned = raw.rstrip(".,")
        if cleaned:
            found.append(cleaned)
    return found


def _allowed_numbers(ledger: AuditLedger) -> set[str]:
    """원장이 허락하는 수.

    세 군데에서 뽑는다.

    - **값** — `4,407ha`
    - **범위** — `2016년`처럼 범위에만 수가 있는 경우
    - **근거 문구** — `천천2호 태양광발전소`처럼 **이름에 든 숫자**

    근거 문구를 넣는 이유가 중요하다. 실제로 `천천2호`의 `2`가 막혔다. 값이
    아니라 시설 이름인데 지어낸 값으로 본 것이다. 근거 문구는 Harness가
    **원문에 글자 그대로 있는지 확인한 것**이므로, 그 안의 숫자는 자료에 있는
    숫자다. 자료 밖으로 나가지 않는다는 성질은 그대로 지킨다.
    """
    allowed: set[str] = set()
    for fact in ledger.facts:
        allowed.update(_numbers_in(fact.value))
        allowed.update(_numbers_in(fact.scope))
        allowed.update(_numbers_in(fact.evidence.quote))
    return allowed


def check_draft(
    draft: AuditDraft, ledger: AuditLedger, plans: list[SlotPlan]
) -> list[Finding]:
    """초안 하나를 원장·칸 계획과 맞대어 검사한다.

    `plans`는 코드가 미리 정한 "어느 칸을 채울 수 있는가"다. AI가 그 판정을
    뒤집지 못하게 여기서 다시 확인한다.
    """
    findings: list[Finding] = []

    def add(rule_id: str, where: str, message: str, severity=Severity.BLOCKING) -> None:
        findings.append(
            Finding(rule_id=rule_id, severity=severity, where=where, message=message)
        )

    # DRAFT 표시는 어떤 경우에도 지울 수 없다. 내려받은 파일은 밖으로 나간다.
    if draft.draft_label != DRAFT_LABEL:
        add(
            "REQUIRED_MARK_MISSING",
            "초안",
            f"‘{DRAFT_LABEL}’ 표시가 바뀌었거나 사라졌습니다. 이 표시는 지울 수 없습니다.",
        )

    allowed = _allowed_numbers(ledger)
    known_facts = {f.fact_id for f in ledger.facts}
    fillable = {p.slot: p for p in plans}

    for entry in draft.slots:
        where = f"‘{SLOT_LABELS[entry.slot]}’"
        plan = fillable.get(entry.slot)

        if not entry.filled:
            # 못 채운 칸은 **왜 못 채웠는지** 반드시 남긴다. 빈 칸만 있으면
            # 사람은 무엇을 더 구해야 하는지 알 수 없다.
            if not entry.note.strip():
                add(
                    "MISSING_NOTE",
                    where,
                    f"{where} 칸을 비웠는데 이유를 적지 않았습니다. "
                    "무엇을 더 넣으면 채워지는지 알려 주어야 합니다.",
                )
            # **자료가 없어 못 쓰는 것과 AI가 안 쓴 것은 다르다.**
            #
            # `effort=max`로 돌렸더니 AI가 출력 토큰을 추론에 다 쓰고 본문을
            # 한 칸도 안 냈다. 그런데 검사는 `막힌 것 0건`을 냈다 — 안 쓴 칸에
            # 이유가 붙어 있었기 때문이다. 빈 결과물이 통과해 버렸다.
            #
            # 자료가 모자란 칸은 그대로 통과시킨다. 그건 실패가 아니라
            # 이 프로그램이 하려는 일 자체다.
            if plan is not None and plan.fillable:
                add(
                    "FILLABLE_SLOT_NOT_WRITTEN",
                    where,
                    f"{where}은(는) 자료가 있어 채울 수 있는 자리인데 AI가 "
                    "쓰지 않았습니다. 다시 시도해야 합니다.",
                )
            continue

        # 코드가 "못 채운다"고 정한 칸을 AI가 몰래 채우는 길을 막는다.
        # **여기서 멈추지 않는다.** 무엇이 잘못됐는지 한 번에 다 보여 준다.
        # 하나만 알려 주면 사람은 고치고 다시 돌리고를 반복하게 된다.
        if plan is not None and not plan.fillable:
            add(
                "UNFILLABLE_SLOT_FILLED",
                where,
                f"{where} 칸은 자료가 모자라 채울 수 없다고 판정된 자리인데 "
                "글이 들어 있습니다. 자료에 없는 내용을 쓴 것입니다.",
            )

        if not entry.text.strip():
            add(
                "SLOT_TEXT_EMPTY",
                where,
                f"{where} 칸을 채웠다고 했는데 글이 비어 있습니다.",
            )
            continue

        # --- 숫자. 이 검사기의 존재 이유다 -----------------------------------
        for number in _numbers_in(entry.text):
            if number not in allowed:
                add(
                    "NUMBER_NOT_IN_LEDGER",
                    where,
                    f"{where}에 쓴 수 `{number}`이(가) 자료에 없습니다. "
                    "자료에서 확인된 수만 쓸 수 있습니다.",
                )

        # --- 나열하는 칸은 값을 실제로 담아야 한다 ----------------------------
        #
        # 같은 자료로 돌려도 결과가 흔들려서 넣었다. AI가 `연도별 현황이
        # 집계됐음`처럼 **아무 수도 없는 문장**을 쓸 때가 있다. 형식은 멀쩡하고
        # 다른 검사도 다 통과하지만 아무것도 말하지 않는다.
        #
        # 리드는 여기서 뺀다. 규모를 한눈에 보이게 하는 자리라 비유만으로도
        # 말이 된다. 나열 검사를 거기까지 걸면 멀쩡한 문장이 막힌다.
        # 그리고 **준 값을 하나도 빠뜨리면 안 된다.** AI가 자꾸 절반만 쓴다.
        # 시설 5곳의 `13ha(131,426m²)`를 주면 `13ha`만 쓰고 `m²`를 통째로
        # 뺐다. 골라 쓰면 초안이 자료보다 빈약해진다. gold는 다 쓴다.
        packed_text = _packed(entry.text)
        for kind in SLOT_NEEDS[entry.slot]:
            if kind not in _ENUMERATED_KINDS:
                continue
            given = [f for f in ledger.facts if f.kind is kind]
            missed = [f for f in given if _packed(f.value) not in packed_text]
            if missed:
                add(
                    "SLOT_MISSING_REQUIRED_VALUES",
                    where,
                    f"{where}은(는) 값을 나열하는 자리인데 준 값 {len(given)}개 중 "
                    f"{len(missed)}개를 빠뜨렸습니다: "
                    f"{', '.join(f.value for f in missed[:5])}"
                    f"{' 등' if len(missed) > 5 else ''}. 준 값은 다 써야 합니다.",
                )

        # --- 근거 -----------------------------------------------------------
        usable_kinds = (*SLOT_NEEDS[entry.slot].keys(), *SLOT_EXTRAS[entry.slot])
        for fact_id in entry.fact_ids:
            if fact_id not in known_facts:
                add(
                    "FACT_NOT_IN_LEDGER",
                    where,
                    f"{where}에 자료에 없는 사실 `{fact_id}`을(를) 근거로 달았습니다.",
                )
                continue
            fact = next(f for f in ledger.facts if f.fact_id == fact_id)
            if fact.kind not in usable_kinds:
                add(
                    "FACT_NOT_USABLE_IN_SLOT",
                    where,
                    f"{where}에는 쓸 수 없는 종류의 사실 `{fact_id}`을(를) "
                    "근거로 달았습니다. 칸마다 쓸 수 있는 사실이 정해져 있습니다.",
                )

        # --- 문체. 경고만 낸다 ------------------------------------------------
        #
        # 서술체 어미를 목록으로 잡으려 했더니 `순이었다`를 놓쳤다. 한국어
        # 어미는 끝이 없어서 **막을 것을 세는 방식은 진다.** 반대로 본다 —
        # 개조식으로 **안 끝났으면** 경고한다. 허용 목록이 이긴다
        # (4일차에서 낱말 검사를 금지 목록에서 허용 목록으로 바꾼 것과 같다).
        packed = _packed(entry.text)
        if not any(packed.endswith(end) for end in _OUTLINE_ENDINGS):
            add(
                "STYLE_NOT_OUTLINE_FORM",
                where,
                f"{where}이(가) 개조식(`~했음`·`~임`)으로 끝나지 않습니다. "
                "이 양식은 개조식입니다.",
                Severity.WARNING,
            )

    return findings


def blocking(findings: list[Finding]) -> list[Finding]:
    """막아야 하는 것만. 경고는 보여 주되 막지 않는다."""
    return [f for f in findings if f.severity is Severity.BLOCKING]


__all__ = ["check_draft", "blocking", "SlotKind"]
