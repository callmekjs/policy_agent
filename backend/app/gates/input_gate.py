"""기계적 입력 검사 (README §2.11 순서 1).

AI를 부르기 전에 일반 코드로 확인한다. 여기서 막히면 외부 호출은 0회다.
사실 판단은 하지 않는다. 형식·개수·분량·공개 범위·전송 확인만 본다.
"""

from __future__ import annotations

from datetime import date

from app.harness.contracts import (
    EXTERNAL_AI_POLICY_VERSION,
    MAX_SOURCES,
    MAX_TOTAL_CHARS,
    PURPOSE_MAX_CHARS,
    PURPOSE_MIN_CHARS,
    CreateRunRequest,
    Disclosure,
    Issue,
    IssueCode,
    IssueSeverity,
    ResolutionKind,
)


def _issue(
    index: int,
    code: IssueCode,
    subject: str,
    message: str,
    question: str = "",
    resolution: ResolutionKind = ResolutionKind.ANSWER_IN_SAME_RUN,
) -> Issue:
    return Issue(
        issue_id=f"ISS-{index:03d}",
        code=code,
        subject=subject,
        severity=IssueSeverity.BLOCKING,
        message=message,
        question=question,
        resolution_kind=resolution,
        requires_new_run=resolution is not ResolutionKind.ANSWER_IN_SAME_RUN,
    )


def check_input(request: CreateRunRequest, today: date) -> list[Issue]:
    """차단할 문제 목록을 돌려준다. 비어 있으면 통과다."""
    issues: list[Issue] = []
    n = 0

    def add(*args: object, **kwargs: object) -> None:
        nonlocal n
        n += 1
        issues.append(_issue(n, *args, **kwargs))  # type: ignore[arg-type]

    # 보도 목적
    purpose = request.purpose.strip()
    if not purpose:
        add(
            IssueCode.REQUIRED_INPUT_MISSING,
            "PURPOSE",
            "보도 목적이 비어 있습니다.",
            "이번 보도자료로 독자가 무엇을 알아야 하는지 한두 문장으로 적어 주세요.",
        )
    elif len(purpose) < PURPOSE_MIN_CHARS:
        add(
            IssueCode.REQUIRED_INPUT_MISSING,
            "PURPOSE",
            f"보도 목적이 너무 짧습니다. {PURPOSE_MIN_CHARS}자 이상 적어 주세요.",
            "공식 자료에서 확인된 사실 중 무엇을 앞세울지 적어 주세요.",
        )
    elif len(purpose) > PURPOSE_MAX_CHARS:
        add(
            IssueCode.REQUIRED_INPUT_MISSING,
            "PURPOSE",
            f"보도 목적이 너무 깁니다. {PURPOSE_MAX_CHARS}자 이내로 줄여 주세요.",
            "핵심만 한두 문장으로 적어 주세요.",
        )

    # 공개 범위 — 내부·엠바고는 1차 프로토타입에서 쓰지 않는다.
    if request.disclosure is not Disclosure.PUBLIC:
        add(
            IssueCode.DISCLOSURE_NOT_ALLOWED,
            "DISCLOSURE",
            "이 버전은 공개 자료만 처리합니다. 내부·엠바고 자료는 넣을 수 없습니다.",
            "공개된 공식 자료로 다시 시작해 주세요.",
            ResolutionKind.NEW_RUN_WITH_SOURCES,
        )

    # 자료 기준일
    if request.basis_date > today:
        add(
            IssueCode.REQUIRED_INPUT_MISSING,
            "BASIS_DATE",
            "자료 확인 기준일이 오늘보다 뒤입니다.",
            "실제로 자료를 확인한 날짜를 적어 주세요.",
        )

    # 공식 자료
    if not request.sources:
        add(
            IssueCode.REQUIRED_SOURCE_MISSING,
            "OFFICIAL_SOURCE",
            "공식 자료가 없습니다.",
            "국회 의안정보·표결 결과 같은 공식 자료를 붙여 넣어 주세요.",
        )
    else:
        if len(request.sources) > MAX_SOURCES:
            add(
                IssueCode.REQUIRED_SOURCE_MISSING,
                "SOURCE_COUNT",
                f"자료는 최대 {MAX_SOURCES}개까지 넣을 수 있습니다. "
                f"지금은 {len(request.sources)}개입니다.",
                "가장 중요한 공식 자료만 남겨 주세요.",
            )

        total_chars = sum(len(s.text) for s in request.sources)
        if total_chars > MAX_TOTAL_CHARS:
            add(
                IssueCode.REQUIRED_SOURCE_MISSING,
                "SOURCE_TOTAL_CHARS",
                f"자료 분량이 너무 많습니다. 합계 {MAX_TOTAL_CHARS:,}자 이내여야 하는데 "
                f"지금은 {total_chars:,}자입니다.",
                "필요한 본문만 남기고 줄여 주세요.",
            )

        for i, source in enumerate(request.sources, start=1):
            if not source.text.strip():
                add(
                    IssueCode.REQUIRED_SOURCE_MISSING,
                    f"SOURCE_EMPTY:{i}",
                    f"{i}번째 자료의 내용이 비어 있습니다.",
                    "본문을 붙여 넣거나 그 자료를 빼 주세요.",
                )

    # 외부 AI 전송 확인 — 확인 전에는 외부 호출을 0회로 둔다.
    if request.external_ai_policy_version != EXTERNAL_AI_POLICY_VERSION:
        add(
            IssueCode.REQUIRED_INPUT_MISSING,
            "EXTERNAL_AI_POLICY_VERSION",
            "외부 AI 전송 안내가 바뀌었습니다. 화면을 새로 고친 뒤 다시 확인해 주세요.",
            "새 안내를 읽고 다시 확인해 주세요.",
            ResolutionKind.NEW_RUN_WITH_SOURCES,
        )
    elif not request.external_ai_transfer_confirmed:
        add(
            IssueCode.REQUIRED_INPUT_MISSING,
            "EXTERNAL_AI_TRANSFER_CONFIRMED",
            "외부 AI로 자료를 보내도 되는지 아직 확인하지 않았습니다.",
            "화면의 전송 안내를 읽고 확인에 표시해 주세요.",
        )

    return issues
