"""초안 안전 검사 Gate (README §2.11 6단계, §4.2, §2.16.1, §2.16.4).

4일차부터 **글이 실제로 나온다.** 3일차까지는 초안이 언제나 0건이라 안전했다.
여기서부터는 AI가 쓴 문장이 사람에게 보인다. 그래서 이 파일이 하는 일은 하나다.

**원장에 없는 값이 초안에 들어가면 막는다.**

처음에는 금지 목록으로 만들었다가 검토에서 공격 47종 중 39종이 통과해 실패했다.
`공포되었`을 막으면 `공포되어`로, `250`을 막으면 `이백오십`으로 빠져나갔다.
지금은 **허용 목록**이다. 자료에 있는 말과 `draft_vocabulary`에 적힌 말만 쓸 수
있고, 그 밖의 낱말·수는 어디에 있든 막는다.

검사는 **초안의 모든 칸**을 본다. 본문뿐 아니라 주장·빈칸 표시·인용·붙임까지
본다. 검사하지 않는 칸이 하나라도 있으면 그 칸이 곧 빠져나가는 길이 된다.

검사 결과에는 언제나 `rule_id`, 기준 문서 위치, 영향받은 초안 부분을 함께
남긴다. 셋 중 하나라도 없으면 왜 막혔는지 추적할 수 없고, 그것 자체가 §4.2의
중대한 실패다.
"""

from __future__ import annotations

import re
from typing import Any

from app.gates.draft_vocabulary import SAFE_WORDS, SUFFIXES
from app.gates.numeral_reader import read_numbers, read_numeral_word
from app.harness.draft_contracts import (
    DRAFT_LABEL,
    MAX_KEY_POINTS,
    MIN_KEY_POINTS,
    SIX_W_KEYS,
    STATUS_CODES,
    DraftCandidate,
    ValidationFinding,
    ValidationSeverity,
)
from app.harness.fact_contracts import FactLedger
from app.harness.legal_contracts import ChangedArticleSet, ResolvedFinalText
from app.harness.source_normalizer import NormalizedSource

#: 검사할 한글 낱말. 한 글자짜리는 조사와 구분되지 않아 보지 않는다.
HANGUL_RUN = re.compile(r"[가-힣]{2,}")

#: 로마자 낱말. 기관 약칭이 이 모양으로 들어올 수 있다.
LATIN_RUN = re.compile(r"[A-Za-z]{2,}")

#: 아직 법이 아닐 때 반드시 조심해서 써야 하는 말 (§2.16.1, §2.16.4).
#: 이 말이 나오면 같은 문장에 아래 `HEDGES` 중 하나가 **반드시** 있어야 한다.
#: 금지 낱말 목록이 아니라 **함께 쓸 말을 요구하는** 규칙이라 어미를 바꿔도 빠져나갈 수 없다.
EFFECT_STEMS = ("공포", "시행", "개정", "확정", "효력", "통과", "제정", "발효")

#: 아직 확정되지 않았음을 드러내는 말.
HEDGES = (
    "제안", "아직", "예정", "전이", "전입니다", "않", "아니", "확정 전", "미확정",
    "하도록", "되도록", "법률 아님", "될 예정", "앞두", "남아",
)

#: 문장을 나누는 자리. 규칙을 문장 단위로 적용한다.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

#: 공백을 모두 지운다.
WHITESPACE = re.compile(r"\s+")


def _squeeze(text: str) -> str:
    """공백을 없앤 사본.

    `공 포되었다`처럼 낱말 가운데를 띄어 쓰면 한 글자 조각으로 쪼개져 낱말
    검사를 빠져나간다. 절차 표현을 찾을 때는 붙여 놓고 본다.
    """
    return WHITESPACE.sub("", text)

#: 초안을 최종·승인·배포본으로 표시하는 표현 (§4.2).
#: 허용 목록이 이미 대부분을 막지만, 자료에 그 낱말이 있는 경우를 대비해 남긴다.
FORBIDDEN_STATUS = re.compile(
    r"최종본|승인본|승인\s*완료|배포본|배포용|게시용|공개\s*가능|검토\s*완료본|확정본"
)

#: 인용 부호. 종류를 가리지 않고 모두 본다.
QUOTE_SPANS = [
    re.compile(r"[“\"]([^”\"]+)[”\"]"),
    re.compile(r"[‘']([^’']+)[’']"),
    re.compile(r"「([^」]+)」"),
    re.compile(r"『([^』]+)』"),
    re.compile(r"<([^>]+)>"),
    re.compile(r"《([^》]+)》"),
]

#: 남의 말을 옮길 때 쓰는 말. 공식 발언문 자료가 없으면 쓸 수 없다 (§2.16.2).
#: 허용 낱말 검사만으로는 못 막는다. 자료에 있는 낱말만 골라 붙여도 "누가 그렇게
#: 말했다"는 새 사실이 만들어지기 때문이다.
ATTRIBUTION = re.compile(
    r"(말했|밝혔|전했|강조했|설명했|덧붙였|지적했|주장했|언급했|평가했|촉구했)"
    r"|라고\s*(했|말|밝|전)"
)

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


def _strings_in(value: Any) -> list[str]:
    """어떤 모양으로 들어오든 그 안의 글자를 모두 꺼낸다.

    `quote`·`six_w_status`·`attachments`는 자유로운 모양이라 여기서 펼친다.
    펼치지 않으면 그 칸이 검사를 피해 가는 길이 된다.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            out.append(str(key))
            out.extend(_strings_in(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_strings_in(item))
        return out
    if value is None or isinstance(value, bool):
        return []
    return [str(value)]


def draft_text_parts(candidate: DraftCandidate) -> list[tuple[str, str]]:
    """(초안 부분 이름, 글) 목록. **모든 글자 칸**을 담는다."""
    parts: list[tuple[str, str]] = [
        ("제목", candidate.title.text),
        ("리드", candidate.lead.text),
    ]
    for i, point in enumerate(candidate.key_points, start=1):
        parts.append((f"핵심 요약 {i}", point.text))
    for paragraph in candidate.paragraphs:
        parts.append((f"본문 {paragraph.paragraph_id}", paragraph.text))
    for claim in candidate.claims:
        parts.append((f"주장 {claim.claim_id}", claim.text))
    parts.append(("문의처", candidate.contact_text))
    parts.append(("자료 기준일", candidate.basis_date))
    for i, text in enumerate(candidate.placeholders, start=1):
        parts.append((f"빈칸 표시 {i}", text))
    for i, text in enumerate(_strings_in(candidate.quote), start=1):
        parts.append((f"인용문 {i}", text))
    for i, text in enumerate(_strings_in(candidate.attachments), start=1):
        parts.append((f"붙임 {i}", text))
    # 상태·육하원칙 칸은 사람이 읽는 글이 아니라 정해진 코드다.
    # `check_draft`가 코드 목록으로 따로 검사한다.
    return [(name, text) for name, text in parts if text and text.strip()]


def _strip_suffix(word: str) -> list[str]:
    """조사·어미를 떼어 낸 후보들. 원래 낱말도 함께 돌려준다."""
    stems = [word]
    current = word
    for _ in range(2):  # `되었습니다`처럼 두 번 붙는 경우까지만 본다
        for suffix in SUFFIXES:
            if len(current) > len(suffix) and current.endswith(suffix):
                current = current[: -len(suffix)]
                stems.append(current)
                break
        else:
            break
    return stems


def _is_grounded_word(word: str, haystack: str) -> bool:
    """이 낱말을 자료나 허용 목록으로 설명할 수 있는가."""
    for stem in _strip_suffix(word):
        if len(stem) < 2:
            # 한 글자만 남으면 조사와 구분되지 않는다. 통과시킨다.
            return True
        if stem in haystack:
            return True
        if stem in SAFE_WORDS:
            return True
        # 수를 적은 말이면 수 검사가 따로 본다. 여기서 두 번 세지 않는다.
        if read_numeral_word(stem) is not None:
            return True
    return False


def build_allowed_text(
    ledger: FactLedger,
    final_text: ResolvedFinalText | None,
    article_set: ChangedArticleSet | None,
    *,
    announcement_subject: str = "",
    fixed_labels: tuple[str, ...] = (),
) -> str:
    """초안이 기댈 수 있는 글 전체.

    자료에서 확인한 사실과 그 근거 문구, 확정된 최종 의결문, 부칙, 그리고
    Harness가 스스로 넣는 정형 표시만 담는다. **자료 원문 전체는 담지 않는다.**
    원문을 통째로 허용하면 자료 아무 데서나 문장을 끌어와도 통과한다.
    """
    pieces: list[str] = []
    for fact in ledger.facts:
        pieces.append(fact.value)
        pieces.extend(fact.value_items)
        pieces.append(fact.unit)
        pieces.append(fact.evidence.quote)
        pieces.append(fact.evidence.source_name)
    for rule in ledger.supplementary_rules:
        pieces.append(rule.applies_to)
    for event in ledger.legislative_events:
        pieces.append(event.occurred_on)
    for identity in ledger.bill_identities:
        pieces.append(identity.bill_number)
    if final_text is not None:
        pieces.append(final_text.body_text)
        pieces.append(final_text.bill_number)
        pieces.append(final_text.source_name)
    if article_set is not None:
        pieces.extend(article_set.article_ids)
    pieces.append(announcement_subject)
    pieces.extend(fixed_labels)
    return "\n".join(p for p in pieces if p)


def check_draft(
    candidate: DraftCandidate,
    ledger: FactLedger,
    final_text: ResolvedFinalText | None,
    article_set: ChangedArticleSet | None,
    normalized: dict[str, NormalizedSource],
    *,
    announcement_subject: str = "",
    fixed_labels: tuple[str, ...] = (),
    has_statement_source: bool = False,
) -> list[ValidationFinding]:
    """초안 후보를 검사한다. 차단 항목이 하나라도 있으면 초안을 내주지 않는다."""
    findings: list[ValidationFinding] = []
    index = 1

    def add(
        rule_id: str,
        doc: str,
        part: str,
        message: str,
        excerpt: str = "",
        severity: ValidationSeverity = ValidationSeverity.BLOCKING,
    ) -> None:
        nonlocal index
        findings.append(_finding(index, rule_id, doc, part, message, severity, excerpt))
        index += 1

    parts = draft_text_parts(candidate)
    allowed_text = build_allowed_text(
        ledger,
        final_text,
        article_set,
        announcement_subject=announcement_subject,
        fixed_labels=(*fixed_labels, DRAFT_LABEL, candidate.draft_label),
    )
    allowed_numbers = read_numbers(allowed_text)

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
    for part, text in parts:
        match = FORBIDDEN_STATUS.search(_squeeze(text))
        if match:
            add(
                "NO_FINAL_OR_APPROVED_LABEL",
                "4.2",
                part,
                f"초안을 최종·승인·배포본처럼 표시했습니다: “{match.group(0)}”.",
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
            add(
                "REQUIRED_SECTION_MISSING",
                "2.7",
                "본문",
                f"필수 문단 `{required}`이(가) 없습니다.",
            )
    if not candidate.contact_text.strip():
        add("CONTACT_REQUIRED", "2.7", "문의처", "문의처가 비어 있습니다.")
    if not candidate.basis_date.strip():
        add("BASIS_DATE_REQUIRED", "2.7", "자료 기준일", "자료 기준일이 비어 있습니다.")
    for name, code in (
        ("보도일 상태", candidate.release_date_status),
        ("문의처 상태", candidate.contact_status),
    ):
        if code not in STATUS_CODES:
            add(
                "STATUS_CODE_UNKNOWN",
                "2.10",
                name,
                f"정해지지 않은 상태 코드 `{code}`입니다. "
                f"쓸 수 있는 값: {', '.join(sorted(STATUS_CODES))}.",
                code,
            )
    for key, value in candidate.six_w_status.items():
        if key not in SIX_W_KEYS:
            add(
                "SIX_W_KEY_UNKNOWN",
                "2.10",
                "육하원칙",
                f"정해지지 않은 항목 `{key}`입니다.",
                str(key),
            )
        if value not in STATUS_CODES:
            add(
                "STATUS_CODE_UNKNOWN",
                "2.10",
                f"육하원칙 {key}",
                f"정해지지 않은 상태 코드 `{value}`입니다.",
                str(value),
            )

    if not announcement_subject.strip():
        add(
            "ANNOUNCEMENT_SUBJECT_REQUIRED",
            "2.11",
            "발표 주체",
            "누가 발표하는지 확인되지 않았습니다. 발표 주체 없이는 초안을 "
            "내주지 않습니다.",
        )

    # --- F4. 모든 문장이 원장 사실을 가리키는가 ------------------------------
    known_facts = {f.fact_id for f in ledger.facts}
    known_rules = {r.rule_id for r in ledger.supplementary_rules}
    known_claims = {c.claim_id for c in candidate.claims}

    def check_refs(
        part: str, fact_ids: list[str], claim_ids: list[str], rule_ids: list[str]
    ) -> None:
        for fact_id in fact_ids:
            if fact_id not in known_facts:
                add(
                    "FACT_REFERENCE_UNKNOWN",
                    "2.10",
                    part,
                    f"원장에 없는 사실 `{fact_id}`을(를) 가리킵니다.",
                )
        for claim_id in claim_ids:
            if claim_id not in known_claims:
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

    # --- F1. 자료에 없는 수를 쓰지 않는가 (표기법 무관) ----------------------
    for part, text in parts:
        for number in sorted(read_numbers(text) - allowed_numbers):
            add(
                "NUMBER_NOT_IN_LEDGER",
                "4.2",
                part,
                f"자료에 없는 수 `{number}`이(가) 초안에 있습니다.",
                text[:60],
            )

    # --- F1·F2. 자료에 없는 말을 쓰지 않는가 (허용 목록) ---------------------
    # 지어낸 사람 이름·기관 이름·발언이 여기서 함께 걸린다. 따옴표를 쓰든 안
    # 쓰든 상관없다. 낱말 자체가 자료에도 허용 목록에도 없기 때문이다.
    for part, text in parts:
        unknown: list[str] = []
        for match in HANGUL_RUN.finditer(text):
            word = match.group(0)
            if not _is_grounded_word(word, allowed_text) and word not in unknown:
                unknown.append(word)
        for match in LATIN_RUN.finditer(text):
            word = match.group(0)
            if word not in allowed_text and word not in unknown:
                unknown.append(word)
        if unknown:
            add(
                "WORD_NOT_IN_LEDGER",
                "4.2",
                part,
                "자료에도 없고 쓸 수 있는 말도 아닌 표현이 있습니다: "
                + ", ".join(f"`{w}`" for w in unknown[:6])
                + (f" 외 {len(unknown) - 6}개" if len(unknown) > 6 else "")
                + ".",
                text[:60],
            )

    # --- F2. 인용과 발언 옮기기 ---------------------------------------------
    # 허용 낱말 검사는 자료에 있는 낱말로 조립한 **가짜 발언**을 막지 못한다.
    # 낱말은 다 자료에 있지만 "누가 그렇게 말했다"는 새 사실이기 때문이다.
    haystacks = [n.normalized_text for n in normalized.values()]
    for part, text in parts:
        for pattern in QUOTE_SPANS:
            for match in pattern.finditer(text):
                quoted = match.group(1).strip()
                if len(quoted) < 2:
                    continue
                if not any(quoted in hay for hay in haystacks):
                    add(
                        "QUOTE_NOT_IN_SOURCE",
                        "4.2",
                        part,
                        f"자료에 그대로 있지 않은 인용입니다: “{quoted[:40]}”.",
                        quoted[:60],
                    )
        if not has_statement_source:
            match = ATTRIBUTION.search(text)
            if match:
                add(
                    "STATEMENT_WITHOUT_SOURCE",
                    "2.16.2",
                    part,
                    f"‘{match.group(0)}’처럼 남의 말을 옮겼는데 공식 발언문 자료가 "
                    "없습니다. 발언은 공식 발언문에서만 가져올 수 있습니다.",
                    text[:60],
                )

    # --- H1. 절차를 앞질러 말하지 않는가 ------------------------------------
    # 금지 낱말을 세지 않고, **함께 쓸 말을 요구한다.** 어미를 바꿔도 빠져나갈
    # 수 없고, 새 표현이 생겨도 규칙을 고칠 필요가 없다.
    if candidate.effect_status == "NOT_A_LAW":
        for part, text in parts:
            for sentence in SENTENCE_SPLIT.split(text):
                packed = _squeeze(sentence)
                stem = next((s for s in EFFECT_STEMS if s in packed), None)
                if stem is None:
                    continue
                if any(h in sentence or h in packed for h in HEDGES):
                    continue
                add(
                    "PREMATURE_EFFECT_CLAIM",
                    "2.16.1",
                    part,
                    f"아직 법률이 아닌데 ‘{stem}’을(를) 확정된 일처럼 썼습니다. "
                    "제안 내용임을 함께 밝혀야 합니다.",
                    sentence.strip()[:60],
                )

    # --- H2. 시행 이야기에는 부칙 근거가 붙는가 ------------------------------
    # 본문뿐 아니라 제목·리드·요약도 본다. 한 칸이라도 빠지면 그 칸으로 나간다.
    rule_by_id = {r.rule_id: r for r in ledger.supplementary_rules}
    cited_rules = {
        p.paragraph_id: list(p.supplementary_rule_ids) for p in candidate.paragraphs
    }

    def _mentions_effect_date(text: str) -> bool:
        packed = _squeeze(text)
        return "시행" in packed or "공포" in packed

    for part, text in parts:
        if not _mentions_effect_date(text):
            continue
        paragraph_id = part.removeprefix("본문 ")
        rule_ids = cited_rules.get(paragraph_id, [])
        if not rule_ids:
            add(
                "EFFECTIVE_DATE_NEEDS_RULE",
                "2.16.4",
                part,
                "시행·공포를 말하면서 부칙 근거를 달지 않았습니다.",
                text[:60],
            )
            continue
        # 붙어 있기만 하면 안 된다. 그 부칙이 실제로 그 내용을 담고 있어야 한다.
        rule_text = " ".join(
            rule_by_id[r].applies_to for r in rule_ids if r in rule_by_id
        )
        rule_numbers = read_numbers(rule_text)
        for number in sorted(read_numbers(text) - rule_numbers - {0}):
            if number in allowed_numbers and str(number) in rule_text:
                continue
            add(
                "EFFECTIVE_DATE_NOT_IN_RULE",
                "2.16.4",
                part,
                f"부칙에 없는 시점 `{number}`을(를) 시행 이야기에 썼습니다. "
                f"부칙 원문: “{rule_text[:40]}”.",
                text[:60],
            )

    # --- H4. 초안이 말한 조문이 코드가 센 집합 안에 있는가 --------------------
    if article_set is not None:
        from app.harness.article_parser import top_level_article

        counted = set(article_set.article_ids)
        for part, text in parts:
            for match in re.finditer(r"제\s*\d+\s*조(?:\s*의\s*\d+)?", text):
                article = top_level_article(match.group(0).replace(" ", ""))
                if article not in counted:
                    add(
                        "ARTICLE_NOT_IN_CHANGED_SET",
                        "2.16.3",
                        part,
                        f"코드가 센 변경 조문에 없는 `{article}`을(를) 초안이 "
                        f"말합니다. 센 조문: {', '.join(sorted(counted))}.",
                        match.group(0),
                    )

    return findings


def blocking(findings: list[ValidationFinding]) -> list[ValidationFinding]:
    return [f for f in findings if f.severity is ValidationSeverity.BLOCKING]
