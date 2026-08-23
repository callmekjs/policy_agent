"""누락·충돌 검사 (README §2.11 순서 4, P1-FR-02).

자료끼리 같은 항목의 값이 다르면 **어느 하나를 대신 고르지 않는다.** 두 값과
각 자료명·원문을 나란히 보여주고 사람에게 묻는다.

날짜와 요일이 맞지 않아도 자동으로 고치지 않는다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from app.harness.contracts import (
    Issue,
    IssueCode,
    IssueSeverity,
    ResolutionKind,
)
from app.harness.fact_contracts import FactLedger, VerifiedFact

#: 자료에 함께 적힌 요일 표기를 찾는다.
#: 고정 시험자료는 `(목요일)` 형태를 쓰고 실제 국회 자료는 `(목)`도 쓴다.
#: 둘 다 읽지 못하면 요일 검사가 실제 자료에서 한 번도 발동하지 않는다.
WEEKDAY_PATTERN = re.compile(r"\(\s*([월화수목금토일])(?:요일)?\s*\)")
WEEKDAY_NAMES = "월화수목금토일"

#: `2025. 9. 25.` `2025-09-25` `2025년 9월 25일`을 모두 읽는다.
DATE_PATTERNS = (
    re.compile(r"(\d{4})\s*[-.년]\s*(\d{1,2})\s*[-.월]\s*(\d{1,2})"),
)


def _parse_date(text: str) -> date | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            year, month, day = (int(g) for g in match.groups())
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def _conflict_key(fact: VerifiedFact) -> str:
    """같은 항목인지 판단하는 기준. 종류와 비교 대상을 함께 본다."""
    return f"{fact.kind}:{fact.subject or fact.kind}"


def check_conflicts(ledger: FactLedger, next_index: int = 1) -> list[Issue]:
    """차단할 충돌 목록을 돌려준다. 비어 있으면 통과다."""
    issues: list[Issue] = []
    index = next_index

    grouped: dict[str, list[VerifiedFact]] = defaultdict(list)
    for fact in ledger.facts:
        grouped[_conflict_key(fact)].append(fact)

    for key, facts in sorted(grouped.items()):
        values = {f.normalized_value for f in facts}
        if len(values) <= 1:
            continue

        lines = []
        for fact in facts:
            lines.append(
                f"· {fact.value} — {fact.evidence.source_name} "
                f"{fact.evidence.raw_start_line}행: “{fact.evidence.quote}”"
            )
        subject = facts[0].subject or facts[0].kind
        issues.append(
            Issue(
                issue_id=f"ISS-{index:03d}",
                code=IssueCode.FACT_CONFLICT,
                subject=subject,
                severity=IssueSeverity.BLOCKING,
                message=(
                    f"자료마다 값이 다릅니다. 어느 값도 대신 고르지 않았습니다.\n"
                    + "\n".join(lines)
                ),
                question="어느 자료의 값이 맞는지 확인해 주세요.",
                source_ids=sorted({f.source_id for f in facts}),
                resolution_kind=ResolutionKind.NEW_RUN_WITH_SOURCES,
                requires_new_run=True,
            )
        )
        index += 1

    # 날짜와 요일이 서로 맞지 않는 경우
    for fact in ledger.facts:
        parsed = _parse_date(fact.value)
        if parsed is None:
            continue
        match = WEEKDAY_PATTERN.search(fact.evidence.quote) or WEEKDAY_PATTERN.search(
            fact.value
        )
        if match is None:
            continue
        written = match.group(1)
        actual = WEEKDAY_NAMES[parsed.weekday()]
        if written == actual:
            continue
        issues.append(
            Issue(
                issue_id=f"ISS-{index:03d}",
                code=IssueCode.DATE_WEEKDAY_MISMATCH,
                subject=fact.subject or fact.kind,
                severity=IssueSeverity.BLOCKING,
                message=(
                    f"{fact.value}은(는) {actual}요일인데 자료에는 "
                    f"{written}요일로 적혀 있습니다. 자동으로 고치지 않았습니다.\n"
                    f"· {fact.evidence.source_name} "
                    f"{fact.evidence.raw_start_line}행: “{fact.evidence.quote}”"
                ),
                question="공식 자료에서 정확한 날짜를 확인해 주세요.",
                source_ids=[fact.source_id],
                resolution_kind=ResolutionKind.NEW_RUN_WITH_SOURCES,
                requires_new_run=True,
            )
        )
        index += 1

    return issues
