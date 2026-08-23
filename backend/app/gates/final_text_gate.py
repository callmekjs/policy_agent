"""최종 의결문 확정 Gate (README §2.16.2).

이번 보도자료가 설명하는 **최종 의결 내용**이 무엇인지 코드가 정한다. AI에게
묻지 않는다. 여기서 잘못 고르면 "국회를 통과한 내용"이라며 실제로 통과하지
않은 문장이 초안에 들어간다.

고르는 순서는 세 가지뿐이고, 어느 것도 만족하지 못하면 **초안 없이 멈춘다.**
추측해서 고르는 길은 두지 않는다.
"""

from __future__ import annotations

import hashlib
import re

from app.harness.contracts import (
    SOURCE_ROLE_LABELS,
    Disposition,
    Issue,
    IssueCode,
    ResolutionKind,
    SourceRole,
    SourceUseScope,
    StoredSource,
)
from app.harness.legal_contracts import (
    BODY_END_MARKER,
    BODY_START_MARKER,
    RULE_AGENDA_WITH_ORIGINAL_PASSED,
    RULE_EXPLICIT_FINAL_TEXT,
    RULE_ORIGINAL_UNCHANGED_CHAIN,
    FinalTextConfirmation,
    ResolvedFinalText,
    derivation_id,
)
from app.harness.source_normalizer import NormalizedSource

#: 왜 최종 의결문을 확정하지 못했는지 구분하는 대상 이름.
FINAL_TEXT_UNSAFE_SUBJECT = "FINAL_TEXT_DERIVATION_UNSAFE"
FINAL_TEXT_CONFIRMATION_SUBJECT = "FINAL_TEXT_COMPLETENESS_CONFIRMATION_REQUIRED"
BOUNDARY_SUBJECT = "SOURCE_TEXT:BOUNDARY_MISSING_OR_AMBIGUOUS"

#: 처리결과 낱말 -> enum. **긴 낱말을 먼저** 본다.
#: `대안반영폐기`가 `폐기`로 읽히면 전혀 다른 뜻이 된다.
DISPOSITION_WORDS: list[tuple[str, Disposition]] = [
    ("대안반영폐기", Disposition.ALTERNATIVE_REFLECTED_DISCARDED),
    ("임기만료폐기", Disposition.TERM_EXPIRED),
    ("본회의불부의", Disposition.COMMITTEE_DISCARDED),
    ("수정가결", Disposition.MODIFIED_PASSED),
    ("원안가결", Disposition.ORIGINAL_PASSED),
    ("대안반영", Disposition.ALTERNATIVE_REFLECTED_DISCARDED),
    ("재의요구", Disposition.RECONSIDERATION_REQUESTED),
    ("철회", Disposition.WITHDRAWN),
    ("부결", Disposition.REJECTED),
    ("폐기", Disposition.COMMITTEE_DISCARDED),
]

#: 이 예외를 절대 쓰면 안 되는 신호. 하나라도 보이면 멈춘다 (§2.16.2 조건 4).
UNSAFE_CHAIN_DISPOSITIONS = frozenset(
    {
        Disposition.MODIFIED_PASSED,
        Disposition.SUBSTITUTE_ADOPTED,
        Disposition.ALTERNATIVE_REFLECTED_DISCARDED,
    }
)

#: 의안번호를 코드가 직접 읽는다. AI가 준 값을 믿지 않는다 (§2.16.2 조건 2).
BILL_NUMBER_PATTERNS = [
    re.compile(r"의안\s*번호[^0-9]{0,10}(\d{6,8})"),
    re.compile(r"제\s*(\d{6,8})\s*호"),
]

#: 처리결과가 적힌 줄인지 알아보는 말.
RESULT_LINE_WORDS = ("회의결과", "처리결과", "의결결과", "심의결과")

COMMITTEE_LINE = "소관위"
LEGISLATION_COMMITTEE_LINE = "법사위"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_bill_numbers(text: str) -> set[str]:
    """원문에서 의안번호를 읽는다. 정확히 1개일 때만 쓸 수 있다."""
    found: set[str] = set()
    for pattern in BILL_NUMBER_PATTERNS:
        found.update(pattern.findall(text))
    return found


def read_disposition(line: str) -> Disposition | None:
    """한 줄에서 처리결과를 읽는다. 긴 낱말부터 본다."""
    for word, disposition in DISPOSITION_WORDS:
        if word in line:
            return disposition
    return None


def _dispositions_in(text: str) -> list[tuple[str, Disposition]]:
    """원문 전체에서 (줄, 처리결과)를 모은다."""
    out: list[tuple[str, Disposition]] = []
    for line in text.split("\n"):
        disposition = read_disposition(line)
        if disposition is not None:
            out.append((line.strip(), disposition))
    return out


def _issue(
    index: int,
    *,
    subject: str,
    message: str,
    question: str,
    source_ids: list[str],
    code: IssueCode = IssueCode.REQUIRED_SOURCE_MISSING,
    resolution: ResolutionKind = ResolutionKind.NEW_RUN_WITH_SOURCES,
) -> Issue:
    return Issue(
        issue_id=f"ISS-{index:03d}",
        code=code,
        subject=subject,
        message=message,
        question=question,
        source_ids=source_ids,
        resolution_kind=resolution,
        requires_new_run=resolution is ResolutionKind.NEW_RUN_WITH_SOURCES,
    )


def _body_span(text: str) -> tuple[int, int] | None:
    """개정문 본칙 구간을 찾는다 (§2.16.3).

    시작·끝 경계가 없거나 여러 개면 `None`을 돌려 멈추게 한다. 경계를 추측해서
    좁히면 세지 못한 조문이 조용히 빠진다.
    """
    starts = [m.end() for m in re.finditer(re.escape(BODY_START_MARKER), text)]
    if len(starts) != 1:
        return None
    start = starts[0]
    # 독립된 `부칙` 제목만 끝 경계로 본다. 본문 속 `부칙 제2조` 참조는 아니다.
    pattern = re.compile(r"(?m)^[ \t]*#{0,6}[ \t]*" + BODY_END_MARKER + r"[ \t]*$")
    ends = [m.start() for m in pattern.finditer(text) if m.start() > start]
    if len(ends) != 1:
        return None
    return start, ends[0]


def _only(sources: list[StoredSource], role: SourceRole) -> StoredSource | None:
    """그 역할의 자료가 **정확히 1개**일 때만 돌려준다."""
    found = [s for s in sources if s.role is role]
    return found[0] if len(found) == 1 else None


def _single_bill_number(text: str) -> str:
    found = read_bill_numbers(text)
    return next(iter(found)) if len(found) == 1 else ""


def _plenary_disposition(vote_text: str) -> Disposition | None:
    """표결 자료에서 본회의 처리 결과를 읽는다."""
    for line, disposition in _dispositions_in(vote_text):
        if any(word in line for word in RESULT_LINE_WORDS):
            return disposition
    found = _dispositions_in(vote_text)
    return found[0][1] if len(found) == 1 else None


def _stage_disposition(info_text: str, keyword: str) -> Disposition | None:
    """전체 심사이력에서 한 단계의 처리결과를 읽는다."""
    for line in info_text.split("\n"):
        if keyword in line:
            return read_disposition(line)
    return None


def resolve_final_text(
    sources: list[StoredSource],
    normalized: dict[str, NormalizedSource],
    confirmations: list[FinalTextConfirmation],
    *,
    draft_bill_number: str = "",
    next_index: int = 1,
) -> tuple[ResolvedFinalText | None, list[Issue]]:
    """최종 의결문을 확정하거나, 못 하면 이유를 담은 Issue를 돌려준다."""
    usable = [s for s in sources if s.use_scope is not SourceUseScope.STYLE_ONLY]
    names = {s.source_id: s.display_name for s in sources}

    # 1) 명시적 최종 의결문이 있으면 그것을 우선 쓴다.
    explicit = [
        s
        for s in usable
        if s.role in (SourceRole.PLENARY_FINAL_TEXT, SourceRole.COMMITTEE_FINAL_TEXT)
    ]
    if len(explicit) == 1:
        return _from_single_source(
            explicit[0], normalized, names, RULE_EXPLICIT_FINAL_TEXT, next_index
        )
    if len(explicit) > 1:
        return None, [
            _issue(
                next_index,
                subject=FINAL_TEXT_UNSAFE_SUBJECT,
                message=(
                    f"최종 의결문으로 표시된 자료가 {len(explicit)}개입니다. "
                    "어느 것이 최종인지 코드가 임의로 정하지 않습니다."
                ),
                question=(
                    "이번 보도자료가 설명할 최종 의결문 자료 하나만 남겨 "
                    "새 작업으로 넣어 주세요."
                ),
                source_ids=[s.source_id for s in explicit],
            )
        ]

    # 2) 완전한 본회의 상정안 + 공식 원안가결이면 상정안을 쓴다.
    agenda = _only(usable, SourceRole.PLENARY_AGENDA_TEXT)
    vote = _only(usable, SourceRole.PLENARY_VOTE_RESULT)
    if agenda is not None and vote is not None:
        plenary = _plenary_disposition(normalized[vote.source_id].normalized_text)
        if plenary is Disposition.ORIGINAL_PASSED:
            return _from_single_source(
                agenda,
                normalized,
                names,
                RULE_AGENDA_WITH_ORIGINAL_PASSED,
                next_index,
                extra_sources=[vote.source_id],
            )

    # 3) 좁은 예외: 발의안을 최종 의결 내용으로 대신 쓴다 (§2.16.2).
    return _original_unchanged_chain(
        usable, normalized, names, confirmations, draft_bill_number, next_index
    )


def _from_single_source(
    source: StoredSource,
    normalized: dict[str, NormalizedSource],
    names: dict[str, str],
    rule: str,
    next_index: int,
    *,
    extra_sources: list[str] | None = None,
) -> tuple[ResolvedFinalText | None, list[Issue]]:
    text = normalized[source.source_id].normalized_text
    span = _body_span(text)
    if span is None:
        return None, [
            _issue(
                next_index,
                subject=BOUNDARY_SUBJECT,
                code=IssueCode.CHANGED_PROVISION_COUNT_UNDETERMINABLE,
                message=(
                    f"‘{names.get(source.source_id, source.source_id)}’에서 개정문이 "
                    "어디서 시작해 어디서 끝나는지 확인하지 못했습니다."
                ),
                question=(
                    "‘… 일부를 다음과 같이 개정한다.’부터 부칙까지 빠짐없이 담긴 "
                    "공식 자료를 새 작업으로 넣어 주세요."
                ),
                source_ids=[source.source_id],
            )
        ]
    start, end = span
    return (
        ResolvedFinalText(
            derivation_id=derivation_id(rule, [source.source_id, _sha256(text)]),
            rule=rule,
            source_id=source.source_id,
            source_name=names.get(source.source_id, source.source_id),
            input_source_ids=[source.source_id, *(extra_sources or [])],
            normalized_sha256=_sha256(text),
            bill_number=_single_bill_number(text),
            body_start=start,
            body_end=end,
            text=text,
        ),
        [],
    )


def _original_unchanged_chain(
    sources: list[StoredSource],
    normalized: dict[str, NormalizedSource],
    names: dict[str, str],
    confirmations: list[FinalTextConfirmation],
    draft_bill_number: str,
    next_index: int,
) -> tuple[ResolvedFinalText | None, list[Issue]]:
    """`ORIGINAL_UNCHANGED_CHAIN_V2` 여섯 조건을 **모두** 확인한다.

    하나라도 어긋나면 파생하지 않는다. 이 예외는 수정가결·위원회 대안에 절대
    적용하지 않는다.
    """

    def block(
        subject: str, message: str, question: str, ids: list[str]
    ) -> tuple[None, list[Issue]]:
        return None, [
            _issue(
                next_index,
                subject=subject,
                message=message,
                question=question,
                source_ids=ids,
            )
        ]

    # 조건 1) 세 역할이 각각 정확히 1개인가
    introduced = _only(sources, SourceRole.INTRODUCED_TEXT)
    info = _only(sources, SourceRole.BILL_INFORMATION)
    vote = _only(sources, SourceRole.PLENARY_VOTE_RESULT)
    missing = [
        SOURCE_ROLE_LABELS[role]
        for role, found in (
            (SourceRole.INTRODUCED_TEXT, introduced),
            (SourceRole.BILL_INFORMATION, info),
            (SourceRole.PLENARY_VOTE_RESULT, vote),
        )
        if found is None
    ]
    if missing:
        return block(
            FINAL_TEXT_UNSAFE_SUBJECT,
            "최종 의결문을 확정할 공식 자료가 부족합니다. "
            f"각각 1개씩 필요한 자료: {', '.join(missing)}.",
            "본회의 최종 의결문을 넣거나, 발의안·의안정보·본회의 표결 결과를 "
            "각각 1개씩 넣어 새 작업으로 다시 시도해 주세요.",
            [s.source_id for s in sources],
        )
    assert introduced is not None and info is not None and vote is not None

    chain = (introduced, info, vote)
    texts = {s.source_id: normalized[s.source_id].normalized_text for s in chain}

    # 조건 6) 저장된 해시와 다시 계산한 해시가 자료마다 같은가
    for source in chain:
        if source.normalized_sha256 and _sha256(texts[source.source_id]) != source.normalized_sha256:
            return block(
                FINAL_TEXT_UNSAFE_SUBJECT,
                f"‘{names[source.source_id]}’의 원문이 처음 넣은 내용과 달라졌습니다.",
                "자료를 다시 넣어 새 작업으로 시도해 주세요.",
                [source.source_id],
            )

    # 조건 2) 세 자료의 의안번호를 각각 정확히 1개씩 읽고 모두 같은가
    numbers: dict[str, str] = {}
    for source in chain:
        found = read_bill_numbers(texts[source.source_id])
        if len(found) != 1:
            return block(
                FINAL_TEXT_UNSAFE_SUBJECT,
                f"‘{names[source.source_id]}’에서 의안번호를 정확히 하나 읽지 "
                f"못했습니다 ({len(found)}개 발견).",
                "의안번호가 한 번만 분명히 적힌 공식 자료를 넣어 주세요.",
                [source.source_id],
            )
        numbers[source.source_id] = next(iter(found))

    distinct = set(numbers.values())
    if draft_bill_number:
        distinct.add(draft_bill_number)
    if len(distinct) != 1:
        detail = ", ".join(f"{names[sid]} {num}" for sid, num in numbers.items())
        if draft_bill_number:
            detail += f", 보도 대상 의안 {draft_bill_number}"
        return block(
            FINAL_TEXT_UNSAFE_SUBJECT,
            f"자료마다 의안번호가 다릅니다: {detail}.",
            "같은 의안의 공식 자료만 넣어 새 작업으로 다시 시도해 주세요.",
            list(numbers),
        )

    # 조건 3) 소관위·법사위·본회의 처리결과가 모두 정확히 원안가결인가
    info_text = texts[info.source_id]
    stages: dict[str, Disposition | None] = {
        "소관위": _stage_disposition(info_text, COMMITTEE_LINE),
        "법사위": _stage_disposition(info_text, LEGISLATION_COMMITTEE_LINE),
        "본회의": _plenary_disposition(texts[vote.source_id]),
    }
    unknown = [label for label, value in stages.items() if value is None]
    if unknown:
        return block(
            FINAL_TEXT_UNSAFE_SUBJECT,
            f"심사 단계의 처리결과를 확인하지 못했습니다: {', '.join(unknown)}.",
            "소관위·법사위 처리결과가 모두 적힌 전체 심사이력과 본회의 표결 "
            "결과를 넣어 새 작업으로 다시 시도해 주세요.",
            [info.source_id, vote.source_id],
        )
    not_original = {
        label: value
        for label, value in stages.items()
        if value is not Disposition.ORIGINAL_PASSED
    }
    if not_original:
        detail = ", ".join(f"{label} {v.value}" for label, v in not_original.items())
        return block(
            FINAL_TEXT_UNSAFE_SUBJECT,
            f"발의안 그대로 통과한 경우가 아닙니다: {detail}. "
            "발의안을 최종 의결 내용으로 대신 쓸 수 없습니다.",
            "이 단계의 공식 최종 의결문(위원회 최종문 또는 본회의 최종문)을 "
            "넣어 새 작업으로 다시 시도해 주세요.",
            [info.source_id, vote.source_id],
        )

    # 조건 4) 그 사이 수정·대체 근거가 하나도 없는가
    for source in sources:
        for line, disposition in _dispositions_in(normalized[source.source_id].normalized_text):
            if disposition in UNSAFE_CHAIN_DISPOSITIONS:
                return block(
                    FINAL_TEXT_UNSAFE_SUBJECT,
                    f"‘{names.get(source.source_id, source.source_id)}’에 수정·대체 "
                    f"근거가 있습니다: “{line}”. 발의안을 최종 내용으로 쓸 수 없습니다.",
                    "이 단계의 공식 최종 의결문을 넣어 새 작업으로 다시 시도해 주세요.",
                    [source.source_id],
                )

    # 조건 5) 발의안에 개정문과 부칙이 끝까지 있고, 사용자가 원문을 보고 확인했는가
    introduced_text = texts[introduced.source_id]
    span = _body_span(introduced_text)
    if span is None:
        return block(
            BOUNDARY_SUBJECT,
            f"‘{names[introduced.source_id]}’에서 개정문이 어디서 시작해 어디서 "
            "끝나는지 확인하지 못했습니다.",
            "‘… 일부를 다음과 같이 개정한다.’부터 부칙까지 빠짐없이 담긴 발의안 "
            "원문을 새 작업으로 넣어 주세요.",
            [introduced.source_id],
        )

    confirmed = any(
        c.source_id == introduced.source_id and c.confirmed for c in confirmations
    )
    if not confirmed:
        return None, [
            _issue(
                next_index,
                subject=FINAL_TEXT_CONFIRMATION_SUBJECT,
                code=IssueCode.REQUIRED_INPUT_MISSING,
                resolution=ResolutionKind.ANSWER_IN_SAME_RUN,
                message=(
                    f"이 작업은 ‘{names[introduced.source_id]}’를 최종 의결 내용으로 "
                    "대신 씁니다. 그러려면 그 자료에 개정문과 부칙이 처음부터 끝까지 "
                    "들어 있는지 사람이 원문을 보고 확인해야 합니다."
                ),
                question=(
                    f"‘{names[introduced.source_id]}’에 ‘… 일부를 다음과 같이 "
                    "개정한다.’부터 모든 개정 지시문과 부칙 끝까지 들어 있습니까?"
                ),
                source_ids=[introduced.source_id],
            )
        ]

    start, end = span
    rule = RULE_ORIGINAL_UNCHANGED_CHAIN
    bill_number = next(iter(distinct))
    return (
        ResolvedFinalText(
            derivation_id=derivation_id(
                rule,
                [
                    introduced.source_id,
                    _sha256(introduced_text),
                    info.source_id,
                    _sha256(info_text),
                    vote.source_id,
                    _sha256(texts[vote.source_id]),
                    bill_number,
                ],
            ),
            rule=rule,
            # 실제 개정문은 발의안의 확인된 구간만 쓴다.
            # 표결·심사이력 문장을 개정문에 합치지 않는다 (§2.16.2).
            source_id=introduced.source_id,
            source_name=names[introduced.source_id],
            input_source_ids=[introduced.source_id, info.source_id, vote.source_id],
            normalized_sha256=_sha256(introduced_text),
            bill_number=bill_number,
            body_start=start,
            body_end=end,
            text=introduced_text,
        ),
        [],
    )
