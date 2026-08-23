"""자료 역할 확인 Gate (README §2.11, §0.3의 5번).

비전공자에게 12가지 자료 역할을 먼저 고르라고 하지 않는다. 기본값은
`잘 모르겠음`이고, AI가 근거와 함께 쉬운 후보 1~3개를 제안하면 사람이 고른다.

역할이 정해지지 않은 자료가 남아 있으면 `SOURCE_ROLE_CONFIRMATION_REQUIRED`로
멈추고 같은 Run의 `/answers`에서 확정한다.
"""

from __future__ import annotations

from app.harness.contracts import (
    SOURCE_ROLE_LABELS,
    Issue,
    IssueCode,
    IssueSeverity,
    ResolutionKind,
    SourceRole,
    StoredSource,
)
from app.harness.fact_contracts import (
    ROLE_LABEL_TO_ENUM,
    EvidenceLocation,
    SourceRoleCandidate,
)

#: 역할 확인이 필요할 때 쓰는 Issue 코드.
SOURCE_ROLE_CONFIRMATION_REQUIRED = IssueCode.SOURCE_ROLE_CONTENT_MISMATCH


def check_source_roles(
    sources: list[StoredSource],
    candidates: list[SourceRoleCandidate],
    locations: dict[str, EvidenceLocation],
    next_index: int = 1,
) -> list[Issue]:
    """역할이 정해지지 않은 자료마다 확인 Issue를 만든다."""
    issues: list[Issue] = []
    index = next_index

    by_source: dict[str, list[SourceRoleCandidate]] = {}
    for candidate in candidates:
        by_source.setdefault(candidate.source_id, []).append(candidate)

    for source in sources:
        if source.role is not SourceRole.UNKNOWN:
            continue

        proposed = by_source.get(source.source_id, [])
        if not proposed:
            issues.append(
                Issue(
                    issue_id=f"ISS-{index:03d}",
                    code=SOURCE_ROLE_CONFIRMATION_REQUIRED,
                    subject=source.source_id,
                    severity=IssueSeverity.BLOCKING,
                    message=(
                        f"‘{source.display_name}’이(가) 어떤 자료인지 알 수 없습니다."
                    ),
                    question="이 자료의 종류를 직접 골라 주세요.",
                    source_ids=[source.source_id],
                    resolution_kind=ResolutionKind.NEW_RUN_WITH_SOURCES,
                    requires_new_run=True,
                )
            )
            index += 1
            continue

        lines = []
        for candidate in proposed[:3]:
            role = ROLE_LABEL_TO_ENUM.get(candidate.role)
            label = SOURCE_ROLE_LABELS.get(role, candidate.role) if role else candidate.role
            quotes = [
                locations[e].quote for e in candidate.evidence_ids if e in locations
            ]
            because = f" — 근거: “{quotes[0]}”" if quotes else ""
            lines.append(f"· {label}{because}")

        issues.append(
            Issue(
                issue_id=f"ISS-{index:03d}",
                code=SOURCE_ROLE_CONFIRMATION_REQUIRED,
                subject=source.source_id,
                severity=IssueSeverity.BLOCKING,
                message=(
                    f"‘{source.display_name}’이(가) 어떤 자료인지 확인해 주세요.\n"
                    + "\n".join(lines)
                ),
                question="맞는 것을 하나 골라 주세요.",
                source_ids=[source.source_id],
                resolution_kind=ResolutionKind.ANSWER_IN_SAME_RUN,
                requires_new_run=False,
            )
        )
        index += 1

    return issues


def candidate_choices(
    candidates: list[SourceRoleCandidate],
    locations: dict[str, EvidenceLocation],
) -> list[dict[str, str]]:
    """화면에서 고를 수 있게 후보를 간단한 목록으로 바꾼다."""
    choices: list[dict[str, str]] = []
    for candidate in candidates:
        role = ROLE_LABEL_TO_ENUM.get(candidate.role)
        quotes = [locations[e].quote for e in candidate.evidence_ids if e in locations]
        choices.append(
            {
                "candidate_id": candidate.candidate_id,
                "source_id": candidate.source_id,
                "role": role.value if role else "",
                "role_label": candidate.role,
                "label": candidate.label,
                "evidence_quote": quotes[0] if quotes else "",
            }
        )
    return choices
