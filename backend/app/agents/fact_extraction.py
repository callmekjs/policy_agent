"""근거 정리 Agent (README §2.14).

Harness가 준 읽기 전용 입력으로 **사실 후보만** 만든다. 상태를 바꾸거나 초안을
쓰지 않고, 다른 Agent를 부르지 않는다.

Agent는 위치·해시·출처·보호 여부를 정하지 않는다. 그것은 Harness가 근거를
원문에서 직접 찾아 붙인다.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.harness.contracts import (
    SOURCE_ROLE_LABELS,
    Disclosure,
    SourceRole,
    SourceUseScope,
    StoredSource,
)
from app.harness.fact_contracts import (
    FACT_RESULT_SCHEMA_VERSION,
    FactExtractionEnvelope,
    FactExtractionResult,
)
from app.harness.source_normalizer import NormalizedSource
from app.infrastructure.model_gateway import ModelCallRequest, ModelCallResult

AGENT_NAME = "FactExtractionAgent"
PROMPT_VERSION = "fact_extraction_v1"

#: README §2.12에서 확정한 사실 추출 출력 상한.
MAX_OUTPUT_TOKENS = 12_000

#: 이 Agent가 볼 수 없는 자료 범위. 참고 사례에서는 사실을 만들지 않는다.
FORBIDDEN_SCOPES = frozenset({SourceUseScope.STYLE_ONLY})


class AgentResultError(RuntimeError):
    """Agent 응답이 정해진 형식을 만족하지 못했을 때."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def build_request(
    *,
    purpose: str,
    disclosure: Disclosure,
    basis_date: str,
    procedure_stage_label: str,
    effect_status_label: str,
    sources: list[StoredSource],
    normalized: dict[str, NormalizedSource],
) -> ModelCallRequest:
    """이 Agent에게 필요한 최소 입력만 담는다.

    `STYLE_ONLY` 자료는 넣지 않는다. 원문은 `normalized_text`만 보낸다.
    """
    payload_sources: list[dict[str, Any]] = []
    for source in sources:
        if source.use_scope in FORBIDDEN_SCOPES:
            continue
        text = normalized[source.source_id].normalized_text
        payload_sources.append(
            {
                "source_id": source.source_id,
                "display_name": source.display_name,
                "role": source.role.value,
                "role_label": SOURCE_ROLE_LABELS[source.role],
                "use_scope": source.use_scope.value,
                "text": text,
            }
        )

    return ModelCallRequest(
        agent_name=AGENT_NAME,
        prompt_version=PROMPT_VERSION,
        payload={
            "schema_version": FACT_RESULT_SCHEMA_VERSION,
            "purpose": purpose,
            "disclosure": disclosure.value,
            "basis_date": basis_date,
            "procedure_stage": procedure_stage_label,
            "effect_status": effect_status_label,
            "role_options": [
                label
                for role, label in SOURCE_ROLE_LABELS.items()
                if role is not SourceRole.UNKNOWN
            ],
            "sources": payload_sources,
        },
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def parse_result(call: ModelCallResult) -> FactExtractionResult:
    """Agent 응답을 검사한다. 형식이 어긋나면 부분 결과를 쓰지 않는다."""
    try:
        envelope = FactExtractionEnvelope.model_validate(call.result)
    except ValidationError as exc:
        raise AgentResultError(
            "AGENT_SCHEMA_INVALID",
            f"AI 응답이 정해진 형식과 다릅니다 ({exc.error_count()}건).",
        ) from exc

    result = envelope.result

    if result.result_status == "FACT_SCOPE_TOO_LARGE":
        detail = result.scope_error.reason if result.scope_error else "자료가 너무 많습니다."
        raise AgentResultError("FACT_SCOPE_TOO_LARGE", detail)

    if result.scope_error is not None:
        raise AgentResultError(
            "AGENT_SCHEMA_INVALID",
            "정상 결과인데 범위 초과 정보가 함께 왔습니다.",
        )

    _check_references(result)
    return result


def _check_references(result: FactExtractionResult) -> None:
    """항목들이 서로 있는 것만 가리키는지 확인한다."""
    evidence_ids = {e.evidence_id for e in result.evidence}
    candidate_ids = {c.candidate_id for c in result.source_role_candidates}

    if len(evidence_ids) != len(result.evidence):
        raise AgentResultError("AGENT_SCHEMA_INVALID", "근거 ID가 중복됩니다.")

    def require_evidence(evidence_id: str, where: str) -> None:
        if evidence_id not in evidence_ids:
            raise AgentResultError(
                "AGENT_SCHEMA_INVALID", f"{where}가 없는 근거를 가리킵니다."
            )

    def require_candidates(ids: list[str], where: str) -> None:
        missing = [i for i in ids if i not in candidate_ids]
        if missing:
            raise AgentResultError(
                "AGENT_SCHEMA_INVALID", f"{where}가 없는 역할 후보를 가리킵니다."
            )

    for fact in result.facts:
        require_evidence(fact.evidence_id, "사실")
        require_candidates(fact.valid_source_role_candidate_ids, "사실")
    for event in result.legislative_events:
        require_evidence(event.evidence_id, "입법 사건")
        require_candidates(event.valid_source_role_candidate_ids, "입법 사건")
    for rule in result.supplementary_rules:
        require_evidence(rule.evidence_id, "부칙")
        require_candidates(rule.valid_source_role_candidate_ids, "부칙")
    for candidate in result.source_role_candidates:
        for evidence_id in candidate.evidence_ids:
            require_evidence(evidence_id, "역할 후보")
