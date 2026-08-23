"""변경 최상위 조문 계산기 `article_target_parser_v1` (README §2.16.3).

AI가 준 조문 집합을 믿지 않고 **코드가 개정문에서 직접 센다.** 두 집합이 다르면
진행하지 않는다.

이 파서의 성공 조건은 하나뿐이다. **본칙의 비공백 문자를 100% 소비하고
`unparsed_spans`가 비어 있을 때만** 결과를 쓴다. 일부만 센 1~3개를 성공으로
처리하지 않는다. 못 읽은 줄이 하나라도 남으면, 그 줄에 있던 조문이 조용히
빠진 채로 "조문 1개를 고쳤습니다"라는 초안이 나가기 때문이다.

1차가 지원하는 문법은 좁다. 지원하지 않는 문법은 **추정하지 않고 멈춘다.**
"""

from __future__ import annotations

import re

from app.harness.legal_contracts import (
    ChangedArticleSet,
    ProvisionDirective,
    ResolvedFinalText,
    UnparsedSpan,
)

#: 한 번에 다룰 수 있는 변경 조문 수 (§2.16.3).
MAX_CHANGED_ARTICLES = 3

#: 줄 처음에 오는 대상 최상위 조문. `제23조의8` 같은 가지번호를 함께 읽는다.
ARTICLE_TARGET = re.compile(r"^제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")

#: 지시문에 딸린 하위 단위 줄. 바로 앞 조문의 새 본문으로 소비한다.
PAYLOAD_PREFIX = re.compile(r"^(?:다만|단서|[①-⑳]|\d+\.|[가-힣]\.|[-*]\s)")

#: v1이 지원하지 않는 문법. 추정하지 않고 각각 다른 이름으로 멈춘다.
UNSUPPORTED_SYNTAX: list[tuple[re.Pattern[str], str]] = [
    # 최상위 조문 번호 자체를 다른 번호로 옮기는 경우
    (re.compile(r"제\s*\d+\s*조(?:의\s*\d+)?\s*를\s*제\s*\d+\s*조"), "ARTICLE_RENUMBERING"),
    (re.compile(r"동조|동항|동호"), "LITERAL_SAME_ARTICLE_REFERENCE"),
    (re.compile(r"제\s*\d+\s*조\s*부터\s*제\s*\d+\s*조\s*까지"), "ARTICLE_RANGE"),
    (re.compile(r"별표|별지|서식"), "ATTACHED_TABLE_OR_FORM"),
    (re.compile(r"법률\s*중\s*다음과\s*같이|각각\s*다음과\s*같이\s*개정한다"), "BULK_AMENDMENT"),
]


class ArticleParseError(Exception):
    """조문을 셀 수 없을 때. code와 subject를 함께 들고 나온다."""

    def __init__(self, code: str, subject: str, detail: str, unparsed: list[UnparsedSpan] | None = None) -> None:
        self.code = code
        self.subject = subject
        self.detail = detail
        self.unparsed = unparsed or []
        super().__init__(f"{code}/{subject}: {detail}")


UNDETERMINABLE = "CHANGED_PROVISION_COUNT_UNDETERMINABLE"
UNSUPPORTED_COUNT = "UNSUPPORTED_CHANGED_PROVISION_COUNT"


def normalize_article_id(number: str, branch: str | None) -> str:
    """`제 7 조 의 8` 같은 표기를 하나의 이름으로 모은다."""
    return f"제{int(number)}조의{int(branch)}" if branch else f"제{int(number)}조"


def _non_space_count(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def parse_changed_articles(final_text: ResolvedFinalText) -> ChangedArticleSet:
    """본칙에서 변경 최상위 조문을 센다.

    100% 소비하지 못하면 `ArticleParseError`를 낸다. 부분 결과를 돌려주지 않는다.
    """
    body = final_text.body_text
    offset = final_text.body_start

    for pattern, kind in UNSUPPORTED_SYNTAX:
        match = pattern.search(body)
        if match:
            raise ArticleParseError(
                UNDETERMINABLE,
                f"UNSUPPORTED_SYNTAX:{kind}",
                f"1차에서 지원하지 않는 표현입니다: “{match.group(0).strip()}”.",
            )

    directives: list[ProvisionDirective] = []
    unparsed: list[UnparsedSpan] = []
    cursor = 0

    for line in body.split("\n"):
        start = offset + cursor
        end = start + len(line)
        cursor += len(line) + 1  # 줄바꿈 한 칸

        stripped = line.strip()
        if not stripped:
            continue

        target = ARTICLE_TARGET.match(stripped)
        if target:
            directives.append(
                ProvisionDirective(
                    article_id=normalize_article_id(target.group(1), target.group(2)),
                    start=start,
                    end=end,
                    text=stripped,
                )
            )
            continue

        # 바로 앞 지시문에 딸린 새 본문이면 그 조문이 소비한다.
        if directives and PAYLOAD_PREFIX.match(stripped):
            directives[-1].end = end
            directives[-1].text += "\n" + stripped
            continue

        # 알 수 없는 줄. 고아 payload도 여기로 온다.
        unparsed.append(UnparsedSpan(start=start, end=end, text=stripped))

    consumed = sum(_non_space_count(d.text) for d in directives)
    total = _non_space_count(body)

    if unparsed:
        first = unparsed[0]
        raise ArticleParseError(
            UNDETERMINABLE,
            "SOURCE_TEXT:OCR_OR_INCOMPLETE",
            f"개정문에서 읽지 못한 줄이 {len(unparsed)}개 있습니다. "
            f"첫 줄: “{first.text[:40]}”.",
            unparsed,
        )

    if total == 0 or consumed != total:
        raise ArticleParseError(
            UNDETERMINABLE,
            "SOURCE_TEXT:OCR_OR_INCOMPLETE",
            f"개정문 {total}자 중 {consumed}자만 해석했습니다. "
            "일부만 센 결과는 쓰지 않습니다.",
        )

    article_ids: list[str] = []
    for directive in directives:
        if directive.article_id not in article_ids:
            article_ids.append(directive.article_id)

    if not article_ids:
        raise ArticleParseError(
            UNDETERMINABLE,
            "SOURCE_TEXT:BOUNDARY_MISSING_OR_AMBIGUOUS",
            "개정문에서 바뀐 조문을 하나도 찾지 못했습니다.",
        )
    if len(article_ids) > MAX_CHANGED_ARTICLES:
        raise ArticleParseError(
            UNSUPPORTED_COUNT,
            f"CHANGED_ARTICLE_COUNT:{len(article_ids)}",
            f"한 번에 바뀐 조문이 {len(article_ids)}개입니다. "
            f"1차는 {MAX_CHANGED_ARTICLES}개까지만 다룹니다.",
        )

    return ChangedArticleSet(
        final_text_derivation_id=final_text.derivation_id,
        normalized_sha256=final_text.normalized_sha256,
        body_start=final_text.body_start,
        body_end=final_text.body_end,
        article_ids=article_ids,
        directives=directives,
        unparsed_spans=[],
        consumed_non_space=consumed,
        total_non_space=total,
    )


def top_level_article(provision_id: str) -> str:
    """`제7조제6항` 같은 표기에서 최상위 조문만 뽑는다.

    AI가 준 조문 집합을 코드 집합과 견줄 때 쓴다. 같은 조의 여러 항은 1개다.
    """
    match = ARTICLE_TARGET.match(provision_id.strip())
    if not match:
        return provision_id.strip()
    return normalize_article_id(match.group(1), match.group(2))
