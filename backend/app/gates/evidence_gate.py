"""근거 대조 Gate (README §2.3.1, §2.11 순서 3).

AI가 "이 문장이 원문에 있다"고 말한 것을 그대로 믿지 않는다. Harness가
`normalized_text`에서 **완전 일치**로 직접 찾고, span map으로 사용자가 붙여 넣은
원문의 줄·칸을 되찾는다.

- 0건이면 그 사실을 버린다.
- 여러 곳에 반복되어 하나로 특정할 수 없으면, 고위험 사실은 차단한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.harness.fact_contracts import (
    HIGH_RISK_FACT_KINDS,
    EvidenceCandidate,
    EvidenceLocation,
    FactExtractionResult,
    FactLedger,
    RawFact,
    VerifiedFact,
    subject_of,
)
from app.harness.source_normalizer import NormalizedSource, find_quote_offsets


@dataclass
class EvidenceProblem:
    """근거를 확인하지 못한 항목 하나."""

    kind: str  # NOT_FOUND | AMBIGUOUS | UNKNOWN_SOURCE | UNKNOWN_EVIDENCE
    fact_id: str
    detail: str


@dataclass
class EvidenceResult:
    locations: dict[str, EvidenceLocation] = field(default_factory=dict)
    problems: list[EvidenceProblem] = field(default_factory=list)


def locate_evidence(
    candidates: list[EvidenceCandidate],
    sources: dict[str, NormalizedSource],
    source_names: dict[str, str],
) -> EvidenceResult:
    """근거 문구를 정규화문에서 찾아 위치를 계산한다."""
    result = EvidenceResult()

    for candidate in candidates:
        normalized = sources.get(candidate.source_id)
        if normalized is None:
            result.problems.append(
                EvidenceProblem(
                    kind="UNKNOWN_SOURCE",
                    fact_id=candidate.evidence_id,
                    detail=f"없는 자료를 가리킵니다: {candidate.source_id}",
                )
            )
            continue

        offsets = find_quote_offsets(normalized.normalized_text, candidate.quote)
        if not offsets:
            result.problems.append(
                EvidenceProblem(
                    kind="NOT_FOUND",
                    fact_id=candidate.evidence_id,
                    detail="제시한 근거 문구가 자료 원문에 없습니다.",
                )
            )
            continue

        start = offsets[0]
        end = start + len(candidate.quote)
        span = normalized.raw_span(start, end)
        result.locations[candidate.evidence_id] = EvidenceLocation(
            evidence_id=candidate.evidence_id,
            source_id=candidate.source_id,
            source_name=source_names.get(candidate.source_id, candidate.source_id),
            quote=candidate.quote,
            normalized_start=start,
            normalized_end=end,
            raw_start_line=span.start.line,
            raw_start_column=span.start.column,
            raw_end_line=span.end.line,
            raw_end_column=span.end.column,
            raw_excerpt=span.excerpt,
            occurrence_count=len(offsets),
        )

    return result


def build_fact_ledger(
    raw: FactExtractionResult,
    sources: dict[str, NormalizedSource],
    source_names: dict[str, str],
) -> tuple[FactLedger, EvidenceResult]:
    """raw 결과를 검증된 Fact 원장으로 바꾼다.

    근거를 확인하지 못한 사실은 원장에 넣지 않고 버린 목록에 적는다.
    """
    evidence = locate_evidence(raw.evidence, sources, source_names)

    # 사실뿐 아니라 입법 사건·부칙·의안·관계·조문 비교도 근거를 확인한다.
    # 근거를 찾지 못한 항목이 원장에 남으면 초안 Agent가 그것을 읽게 된다.
    ledger = FactLedger(
        legislative_events=_keep_with_evidence(
            raw.legislative_events, evidence, "event_id", ("evidence_id",)
        ),
        supplementary_rules=_keep_with_evidence(
            raw.supplementary_rules, evidence, "rule_id", ("evidence_id",)
        ),
        bill_identities=_keep_with_evidence(
            raw.bill_identities, evidence, "bill_id", ("evidence_ids",)
        ),
        bill_relations=_keep_with_evidence(
            raw.bill_relations, evidence, "origin_bill_id", ("evidence_ids",)
        ),
        provision_comparisons=_keep_with_evidence(
            raw.provision_comparisons,
            evidence,
            "comparison_id",
            ("current_evidence_id", "final_evidence_id"),
        ),
    )

    for fact in raw.facts:
        problem = _check_fact(fact, evidence)
        if problem is not None:
            evidence.problems.append(problem)
            ledger.rejected_fact_ids.append(fact.fact_id)
            continue

        location = evidence.locations[fact.evidence_id]
        ledger.facts.append(
            VerifiedFact(
                fact_id=fact.fact_id,
                kind=fact.kind,
                subject=subject_of(fact.kind),
                value=fact.value,
                normalized_value=_normalize_value(fact),
                source_id=fact.source_id,
                valid_source_role_candidate_ids=list(
                    fact.valid_source_role_candidate_ids
                ),
                evidence=location,
            )
        )

    return ledger, evidence


def _keep_with_evidence(
    items: list,
    evidence: EvidenceResult,
    id_field: str,
    evidence_fields: tuple[str, ...],
) -> list:
    """근거가 실제 원문에서 확인된 항목만 남긴다.

    사실과 같은 기준을 적용한다. 근거를 못 찾은 항목은 버리고 이유를 적는다.
    """
    kept = []
    for item in items:
        needed: list[str] = []
        for field in evidence_fields:
            value = getattr(item, field)
            needed.extend(value if isinstance(value, list) else [value])

        missing = [e for e in needed if e not in evidence.locations]
        if not needed or missing:
            evidence.problems.append(
                EvidenceProblem(
                    kind="UNKNOWN_EVIDENCE",
                    fact_id=str(getattr(item, id_field)),
                    detail="근거를 확인하지 못해 쓰지 않았습니다.",
                )
            )
            continue
        kept.append(item)
    return kept


def _check_fact(fact: RawFact, evidence: EvidenceResult) -> EvidenceProblem | None:
    """이 사실을 원장에 넣어도 되는지 본다. 문제가 없으면 None."""
    location = evidence.locations.get(fact.evidence_id)
    if location is None:
        return EvidenceProblem(
            kind="UNKNOWN_EVIDENCE",
            fact_id=fact.fact_id,
            detail="근거를 확인하지 못했습니다.",
        )
    if location.source_id != fact.source_id:
        return EvidenceProblem(
            kind="UNKNOWN_SOURCE",
            fact_id=fact.fact_id,
            detail="사실과 근거가 서로 다른 자료를 가리킵니다.",
        )
    if location.occurrence_count > 1 and fact.kind in HIGH_RISK_FACT_KINDS:
        return EvidenceProblem(
            kind="AMBIGUOUS",
            fact_id=fact.fact_id,
            detail=(
                f"근거 문구가 자료에서 {location.occurrence_count}군데 나와 "
                "어디를 가리키는지 알 수 없습니다."
            ),
        )
    return None


def _normalize_value(fact: RawFact) -> str:
    """값을 비교하기 좋게 다듬는다. 원래 값은 그대로 남긴다.

    반올림하거나 자릿수를 더하지 않는다. 앞뒤 공백만 정리한다.
    """
    return " ".join(fact.value.split())
