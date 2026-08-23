"""초안 안전 검사 Gate (README §2.11 6단계, §4.2, §2.16.1, §2.16.4).

4일차부터 **글이 실제로 나온다.** 3일차까지는 초안이 언제나 0건이라 안전했다.
여기서부터는 AI가 쓴 문장이 사람에게 보인다. 그래서 이 파일이 하는 일은 하나다.

**원장에 없는 값이 초안에 들어가면 막는다.**

검사 결과에는 언제나 `rule_id`, 기준 문서 위치, 영향받은 초안 부분을 함께
남긴다. 셋 중 하나라도 없으면 왜 막혔는지 추적할 수 없고, 그것 자체가 §4.2의
중대한 실패다.
"""

from __future__ import annotations

import re

from app.harness.draft_contracts import (
    DRAFT_LABEL,
    MAX_KEY_POINTS,
    MIN_KEY_POINTS,
    DraftCandidate,
    ValidationFinding,
    ValidationSeverity,
)
from app.harness.fact_contracts import FactLedger
from app.harness.legal_contracts import ChangedArticleSet, ResolvedFinalText
from app.harness.source_normalizer import NormalizedSource

#: 숫자 낱말. 쉼표를 뺀 자릿수만 비교한다.
NUMBER_TOKEN = re.compile(r"\d[\d,]*")

#: 큰따옴표·홑따옴표 안의 인용. 곧은 따옴표와 굽은 따옴표를 함께 본다.
QUOTE_SPANS = [
    re.compile(r"[“\"]([^”\"]{2,})[”\"]"),
    re.compile(r"[‘']([^’']{2,})[’']"),
]

#: 아직 법이 아닌데 법이 된 것처럼 말하는 표현 (§2.16.1, §4.2).
PREMATURE_EFFECT = [
    (re.compile(r"공포(되었|됐|된|하였|했)"), "공포되지 않았는데 공포된 것처럼 썼습니다"),
    (re.compile(r"시행\s*(중|되고|되었|됐)"), "아직 시행되지 않았는데 시행 중인 것처럼 썼습니다"),
    (re.compile(r"개정(되었|됐)"), "아직 개정이 끝나지 않았는데 끝난 것처럼 썼습니다"),
    (re.compile(r"법(률)?이\s*(개정|변경|바뀌)"), "법이 이미 바뀐 것처럼 썼습니다"),
    (re.compile(r"현재\s*시행"), "현재 시행 중인 것처럼 썼습니다"),
    (re.compile(r"최종\s*확정|확정되었|확정됐"), "확정된 것처럼 썼습니다"),
]

#: 초안을 최종·승인·배포본으로 표시하는 표현 (§4.2).
FORBIDDEN_STATUS = [
    (re.compile(r"최종본"), "최종본"),
    (re.compile(r"승인본|승인 완료"), "승인본"),
    (re.compile(r"배포본|배포용|게시용"), "배포본"),
    (re.compile(r"보도자료\s*확정"), "확정본"),
]

#: 시행 이야기를 하는 문단. 부칙 근거가 반드시 붙어야 한다 (§2.16.4).
EFFECTIVE_DATE_WORDS = re.compile(r"시행|공포")

#: 양식 v1의 필수 문단 종류 (§2.7).
REQUIRED_SECTIONS = ("BODY",)

RULE_DOC = "README §"


def _finding(
    index: int,
    rule_id: str,
    doc: str,
    part: str,
    message: str,
    severity: ValidationSeverity = ValidationSeverity.BLOCKING,
    excerpt: str = "",
) -> ValidationFinding:
    return ValidationFinding(
        finding_id=f"VF-{index:03d}",
        rule_id=rule_id,
        rule_document=RULE_DOC + doc,
        affected_part=part,
        severity=severity,
        message=message,
        excerpt=excerpt,
    )


def _numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in NUMBER_TOKEN.finditer(text)}


def _draft_texts(candidate: DraftCandidate) -> list[tuple[str, str]]:
    """(초안 부분 이름, 글) 목록. 검사 결과가 어디를 가리키는지 말하기 위해서다."""
    parts: list[tuple[str, str]] = [
        ("제목", candidate.title.text),
        ("리드", candidate.lead.text),
    ]
    for i, point in enumerate(candidate.key_points, start=1):
        parts.append((f"핵심 요약 {i}", point.text))
    for paragraph in candidate.paragraphs:
        parts.append((f"본문 {paragraph.paragraph_id}", paragraph.text))
    parts.append(("문의처", candidate.contact_text))
    if candidate.quote:
        parts.append(("인용문", str(candidate.quote.get("text", ""))))
    return parts


def check_draft(
    candidate: DraftCandidate,
    ledger: FactLedger,
    final_text: ResolvedFinalText | None,
    article_set: ChangedArticleSet | None,
    normalized: dict[str, NormalizedSource],
    *,
    announcement_subject: str = "",
) -> list[ValidationFinding]:
    """초안 후보를 검사한다. 차단 항목이 하나라도 있으면 초안을 내주지 않는다."""
    findings: list[ValidationFinding] = []
    index = 1

    def add(rule_id: str, doc: str, part: str, message: str, excerpt: str = "",
            severity: ValidationSeverity = ValidationSeverity.BLOCKING) -> None:
        nonlocal index
        findings.append(_finding(index, rule_id, doc, part, message, severity, excerpt))
        index += 1

    texts = _draft_texts(candidate)

    # --- G1. DRAFT 표시 -----------------------------------------------------
    if candidate.draft_label.strip() != DRAFT_LABEL:
        add(
            "DRAFT_LABEL_REQUIRED",
            "4.2",
            "초안 표시",
            f"‘{DRAFT_LABEL}’ 표시가 없습니다. 표시 없이는 초안을 내주지 않습니다.",
            candidate.draft_label,
        )

    # --- G2. 최종·승인·배포본 표시 금지 --------------------------------------
    for part, text in texts:
        for pattern, label in FORBIDDEN_STATUS:
            match = pattern.search(text)
            if match:
                add(
                    "NO_FINAL_OR_APPROVED_LABEL",
                    "4.2",
                    part,
                    f"초안을 {label}처럼 표시했습니다.",
                    match.group(0),
                )

    # --- G3·G4. 필수 양식 ---------------------------------------------------
    if not MIN_KEY_POINTS <= len(candidate.key_points) <= MAX_KEY_POINTS:
        add(
            "KEY_POINT_COUNT",
            "2.7",
            "핵심 요약",
            f"핵심 요약은 {MIN_KEY_POINTS}~{MAX_KEY_POINTS}개여야 하는데 "
            f"{len(candidate.key_points)}개입니다.",
        )
    sections = {p.section_kind for p in candidate.paragraphs}
    for required in REQUIRED_SECTIONS:
        if required not in sections:
            add("REQUIRED_SECTION_MISSING", "2.7", "본문", f"필수 문단 `{required}`이(가) 없습니다.")
    if not candidate.contact_text.strip():
        add("CONTACT_REQUIRED", "2.7", "문의처", "문의처가 비어 있습니다.")
    if not candidate.basis_date.strip():
        add("BASIS_DATE_REQUIRED", "2.7", "자료 기준일", "자료 기준일이 비어 있습니다.")

    # --- F4. 모든 문장이 원장 사실을 가리키는가 ------------------------------
    known_facts = {f.fact_id for f in ledger.facts}
    known_rules = {r.rule_id for r in ledger.supplementary_rules}
    claim_facts = {c.claim_id: c.fact_ids for c in candidate.claims}

    def check_refs(part: str, fact_ids: list[str], claim_ids: list[str], rule_ids: list[str]) -> None:
        for fact_id in fact_ids:
            if fact_id not in known_facts:
                add(
                    "FACT_REFERENCE_UNKNOWN",
                    "2.10",
                    part,
                    f"원장에 없는 사실 `{fact_id}`을(를) 가리킵니다.",
                )
        for claim_id in claim_ids:
            if claim_id not in claim_facts:
                add(
                    "CLAIM_REFERENCE_UNKNOWN",
                    "2.10",
                    part,
                    f"초안에 없는 주장 `{claim_id}`을(를) 가리킵니다.",
                )
        for rule_id in rule_ids:
            if rule_id not in known_rules:
                add(
                    "RULE_REFERENCE_UNKNOWN",
                    "2.16.4",
                    part,
                    f"원장에 없는 부칙 `{rule_id}`을(를) 가리킵니다.",
                )

    check_refs("제목", candidate.title.fact_ids, candidate.title.claim_ids, [])
    check_refs("리드", candidate.lead.fact_ids, candidate.lead.claim_ids, [])
    for i, point in enumerate(candidate.key_points, start=1):
        check_refs(f"핵심 요약 {i}", point.fact_ids, point.claim_ids, [])
    for paragraph in candidate.paragraphs:
        check_refs(
            f"본문 {paragraph.paragraph_id}",
            paragraph.fact_ids,
            paragraph.claim_ids,
            paragraph.supplementary_rule_ids,
        )
    for claim in candidate.claims:
        check_refs(f"주장 {claim.claim_id}", claim.fact_ids, [], [])

    # --- F1. 초안의 숫자가 모두 자료에서 온 것인가 ---------------------------
    allowed = set()
    for fact in ledger.facts:
        allowed |= _numbers(fact.value)
        for item in fact.value_items:
            allowed |= _numbers(item)
    for rule in ledger.supplementary_rules:
        allowed |= _numbers(rule.applies_to)
    for event in ledger.legislative_events:
        allowed |= _numbers(event.occurred_on)
    for identity in ledger.bill_identities:
        allowed |= _numbers(identity.bill_number)
    if final_text is not None:
        allowed |= _numbers(final_text.body_text)
        allowed |= _numbers(final_text.bill_number)
    if article_set is not None:
        for article_id in article_set.article_ids:
            allowed |= _numbers(article_id)
    allowed |= _numbers(candidate.basis_date)
    allowed |= _numbers(announcement_subject)

    for part, text in texts:
        for number in sorted(_numbers(text)):
            if number not in allowed:
                add(
                    "NUMBER_NOT_IN_LEDGER",
                    "4.2",
                    part,
                    f"자료에 없는 숫자 `{number}`이(가) 초안에 있습니다.",
                    text[:60],
                )

    # --- F2. 인용문이 실제 자료에 있는가 ------------------------------------
    haystacks = [n.normalized_text for n in normalized.values()]
    for part, text in texts:
        for pattern in QUOTE_SPANS:
            for match in pattern.finditer(text):
                quoted = match.group(1).strip()
                if not any(quoted in hay for hay in haystacks):
                    add(
                        "QUOTE_NOT_IN_SOURCE",
                        "4.2",
                        part,
                        f"자료에 없는 인용문입니다: “{quoted[:40]}”.",
                        quoted[:60],
                    )

    # --- H1. 절차를 앞질러 말하지 않는가 ------------------------------------
    if candidate.effect_status == "NOT_A_LAW":
        for part, text in texts:
            for pattern, why in PREMATURE_EFFECT:
                match = pattern.search(text)
                if match:
                    add(
                        "PREMATURE_EFFECT_CLAIM",
                        "2.16.1",
                        part,
                        f"{why}: “{match.group(0)}”.",
                        text[:60],
                    )

    # --- H2. 시행 이야기에는 부칙 근거가 붙는가 ------------------------------
    for paragraph in candidate.paragraphs:
        if EFFECTIVE_DATE_WORDS.search(paragraph.text) and not paragraph.supplementary_rule_ids:
            add(
                "EFFECTIVE_DATE_NEEDS_RULE",
                "2.16.4",
                f"본문 {paragraph.paragraph_id}",
                "시행·공포를 말하면서 부칙 근거를 달지 않았습니다.",
                paragraph.text[:60],
            )

    # --- H4. 초안이 말한 조문이 코드가 센 집합 안에 있는가 --------------------
    if article_set is not None:
        counted = set(article_set.article_ids)
        for part, text in texts:
            for match in re.finditer(r"제\s*\d+\s*조(?:\s*의\s*\d+)?", text):
                from app.harness.article_parser import top_level_article

                article = top_level_article(match.group(0))
                if article not in counted:
                    add(
                        "ARTICLE_NOT_IN_CHANGED_SET",
                        "2.16.3",
                        part,
                        f"코드가 센 변경 조문에 없는 `{article}`을(를) 초안이 말합니다. "
                        f"센 조문: {', '.join(sorted(counted))}.",
                        match.group(0),
                    )

    return findings


def blocking(findings: list[ValidationFinding]) -> list[ValidationFinding]:
    return [f for f in findings if f.severity is ValidationSeverity.BLOCKING]
