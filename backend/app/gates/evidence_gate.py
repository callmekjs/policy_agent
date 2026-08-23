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
    """근거를 확인하지 못한 항목 하나.

    사람이 무엇이 빠졌는지 알 수 있어야 한다. 내부 ID만 남기면 화면에서
    `F-01`처럼 보여 아무도 알아볼 수 없고, 그 값에 걸려 있던 충돌이 함께
    사라진 것도 눈치챌 수 없다.
    """

    kind: str  # NOT_FOUND | AMBIGUOUS | UNKNOWN_SOURCE | UNKNOWN_EVIDENCE
    fact_id: str
    detail: str
    fact_kind: str = ""
    value: str = ""
    source_name: str = ""
    quote: str = ""
    raw_line: int = 0

    def describe(self) -> str:
        """화면에 보여줄 한 줄. 값과 자료명을 반드시 담는다."""
        head = f"{self.value}" if self.value else self.fact_id
        where = f" — {self.source_name}" if self.source_name else ""
        line = f" {self.raw_line}행" if self.raw_line else ""
        quote = f": “{self.quote}”" if self.quote else ""
        return f"{head}{where}{line}{quote} · {self.detail}"


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
        provision_comparisons=_keep_comparisons(raw.provision_comparisons, evidence),
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

        item_id = str(getattr(item, id_field))
        problem = _evidence_problem(item, item_id, needed, evidence)
        if problem is not None:
            evidence.problems.append(problem)
            continue
        kept.append(item)
    return kept


def _keep_comparisons(items: list, evidence: EvidenceResult) -> list:
    """조문 비교는 현행·최종 자료를 따로 가리키므로 짝을 맞춰 확인한다.

    `source_id` 하나만 보는 공통 검사로는 이 항목의 자료 대조가 건너뛰어져,
    다른 자료의 근거를 빌려 원장에 남을 수 있다.
    """
    kept = []
    for item in items:
        pairs = (
            (item.current_source_id, item.current_evidence_id),
            (item.final_source_id, item.final_evidence_id),
        )
        problem: EvidenceProblem | None = None
        for source_id, evidence_id in pairs:
            location = evidence.locations.get(evidence_id)
            if location is None:
                problem = EvidenceProblem(
                    kind="UNKNOWN_EVIDENCE",
                    fact_id=item.comparison_id,
                    detail="근거를 확인하지 못해 쓰지 않았습니다.",
                    fact_kind="PROVISION_COMPARISON",
                    value=item.provision_id,
                )
                break
            if location.source_id != source_id:
                problem = EvidenceProblem(
                    kind="UNKNOWN_SOURCE",
                    fact_id=item.comparison_id,
                    detail="조문 비교와 근거가 서로 다른 자료를 가리킵니다.",
                    fact_kind="PROVISION_COMPARISON",
                    value=item.provision_id,
                    source_name=location.source_name,
                    quote=location.quote,
                    raw_line=location.raw_start_line,
                )
                break
            if location.occurrence_count > 1:
                problem = EvidenceProblem(
                    kind="AMBIGUOUS",
                    fact_id=item.comparison_id,
                    detail=(
                        f"근거 문구가 자료에서 {location.occurrence_count}군데 나와 "
                        "어디를 가리키는지 알 수 없습니다."
                    ),
                    fact_kind="PROVISION_COMPARISON",
                    value=item.provision_id,
                    source_name=location.source_name,
                    quote=location.quote,
                    raw_line=location.raw_start_line,
                )
                break
        if problem is not None:
            evidence.problems.append(problem)
            continue
        kept.append(item)
    return kept


def _evidence_problem(
    item: object,
    item_id: str,
    needed: list[str],
    evidence: EvidenceResult,
) -> EvidenceProblem | None:
    """항목의 근거가 쓸 만한지 본다. 사실과 같은 기준을 적용한다."""
    if not needed:
        return EvidenceProblem(
            kind="UNKNOWN_EVIDENCE",
            fact_id=item_id,
            detail="근거를 대지 않아 쓰지 않았습니다.",
        )

    for evidence_id in needed:
        location = evidence.locations.get(evidence_id)
        if location is None:
            return EvidenceProblem(
                kind="UNKNOWN_EVIDENCE",
                fact_id=item_id,
                detail="근거를 확인하지 못해 쓰지 않았습니다.",
            )
        # 항목이 가리키는 자료와 근거가 있는 자료가 달라도 안 된다.
        # 그러지 않으면 다른 자료의 진짜 근거를 빌려 붙일 수 있다.
        own_source = getattr(item, "source_id", None)
        if own_source is not None and location.source_id != own_source:
            return EvidenceProblem(
                kind="UNKNOWN_SOURCE",
                fact_id=item_id,
                detail="항목과 근거가 서로 다른 자료를 가리킵니다.",
            )
        if location.occurrence_count > 1:
            return EvidenceProblem(
                kind="AMBIGUOUS",
                fact_id=item_id,
                detail=(
                    f"근거 문구가 자료에서 {location.occurrence_count}군데 나와 "
                    "어디를 가리키는지 알 수 없습니다."
                ),
            )
    return None


def _check_fact(fact: RawFact, evidence: EvidenceResult) -> EvidenceProblem | None:
    """이 사실을 원장에 넣어도 되는지 본다. 문제가 없으면 None."""
    value = fact.value if isinstance(fact.value, str) else ", ".join(fact.value)
    location = evidence.locations.get(fact.evidence_id)
    if location is None:
        return EvidenceProblem(
            kind="UNKNOWN_EVIDENCE",
            fact_id=fact.fact_id,
            detail="근거를 확인하지 못했습니다.",
            fact_kind=fact.kind,
            value=value,
        )
    if location.source_id != fact.source_id:
        return EvidenceProblem(
            kind="UNKNOWN_SOURCE",
            fact_id=fact.fact_id,
            detail="사실과 근거가 서로 다른 자료를 가리킵니다.",
            fact_kind=fact.kind,
            value=value,
            source_name=location.source_name,
            quote=location.quote,
            raw_line=location.raw_start_line,
        )
    if location.occurrence_count > 1 and fact.kind in HIGH_RISK_FACT_KINDS:
        return EvidenceProblem(
            kind="AMBIGUOUS",
            fact_id=fact.fact_id,
            detail=(
                f"근거 문구가 자료에서 {location.occurrence_count}군데 나와 "
                "어디를 가리키는지 알 수 없습니다."
            ),
            fact_kind=fact.kind,
            value=value,
            source_name=location.source_name,
            quote=location.quote,
            raw_line=location.raw_start_line,
        )
    return None


def _normalize_value(fact: RawFact) -> str:
    """값을 비교하기 좋게 다듬는다. 원래 값은 그대로 남긴다.

    반올림하거나 자릿수를 더하지 않는다. 앞뒤 공백만 정리한다.

    값은 하나일 수도, 목록일 수도 있다. 고정 형식과 Pydantic이 둘 다 허용하므로
    여기서도 둘 다 받아야 한다. 목록을 못 받으면 Run 전체가 죽으면서 같은
    작업의 정상 사실과 진짜 충돌까지 함께 사라진다.
    """
    if isinstance(fact.value, list):
        return ", ".join(" ".join(item.split()) for item in fact.value)
    return " ".join(fact.value.split())
