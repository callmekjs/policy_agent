"""초안 작성 Agent (README §2.14, §2.11).

이 Agent에게는 **원문 전체를 주지 않는다.** Harness가 확인을 마친 사실 원장과
확정된 최종 의결문 본칙, 코드가 센 변경 조문만 준다. 원문을 통째로 주면 자료
아무 데서나 문장을 끌어와 근거 없는 글을 쓸 수 있기 때문이다.

Agent는 상태를 바꾸지 않고 초안 후보 하나만 돌려준다. 그 후보를 쓸지 말지는
`draft_gate`가 정한다.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.harness.contracts import EffectStatus, ProcedureStage
from app.harness.draft_contracts import (
    DRAFT_LABEL,
    DRAFT_SCHEMA_VERSION,
    DraftCandidate,
    DraftEnvelope,
)
from app.harness.fact_contracts import FactLedger
from app.harness.legal_contracts import ChangedArticleSet, ResolvedFinalText
from app.infrastructure.model_gateway import ModelCallRequest, ModelCallResult

AGENT_NAME = "DraftWritingAgent"
PROMPT_VERSION = "draft_writing_v1"

#: README §2.12에서 확정한 초안 출력 상한.
MAX_OUTPUT_TOKENS = 8_000


class DraftResultError(RuntimeError):
    """초안 응답이 정해진 형식을 만족하지 못했을 때."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def build_request(
    *,
    purpose: str,
    basis_date: str,
    procedure_stage: ProcedureStage,
    effect_status: EffectStatus,
    procedure_stage_label: str,
    effect_status_label: str,
    ledger: FactLedger,
    final_text: ResolvedFinalText,
    article_set: ChangedArticleSet,
    announcement_subject: str,
    contact_text: str,
) -> ModelCallRequest:
    """초안에 쓸 수 있는 재료만 담는다."""
    facts = [
        {
            "fact_id": fact.fact_id,
            "kind": fact.kind,
            "subject": fact.subject,
            "value": fact.value,
            "unit": fact.unit,
            "source_id": fact.source_id,
            "quote": fact.evidence.quote,
        }
        for fact in ledger.facts
    ]
    rules = [
        {
            "rule_id": rule.rule_id,
            "kind": rule.kind.value,
            "applies_to": rule.applies_to,
        }
        for rule in ledger.supplementary_rules
    ]
    return ModelCallRequest(
        agent_name=AGENT_NAME,
        prompt_version=PROMPT_VERSION,
        payload={
            "schema_version": DRAFT_SCHEMA_VERSION,
            "purpose": purpose,
            "basis_date": basis_date,
            "procedure_stage": procedure_stage.value,
            "effect_status": effect_status.value,
            "procedure_stage_label": procedure_stage_label,
            "effect_status_label": effect_status_label,
            "draft_label": DRAFT_LABEL,
            "announcement_subject": announcement_subject,
            "contact_text": contact_text,
            "facts": facts,
            "supplementary_rules": rules,
            # 개정문은 확정된 본칙만 준다. 표결·심사이력 문장은 섞지 않는다.
            "final_text_body": final_text.body_text,
            "final_text_rule": final_text.rule,
            "bill_number": final_text.bill_number,
            # 조문 집합은 코드가 센 값이다. Agent가 덮어쓰지 못한다.
            "changed_article_ids": list(article_set.article_ids),
        },
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def parse_result(call: ModelCallResult) -> DraftCandidate:
    """초안 응답을 검사한다. 형식이 어긋나면 부분 결과를 쓰지 않는다."""
    try:
        envelope = DraftEnvelope.model_validate(call.result)
    except ValidationError as exc:
        raise DraftResultError(
            "DRAFT_SCHEMA_INVALID",
            f"AI 초안이 정해진 형식과 다릅니다 ({exc.error_count()}건).",
        ) from exc
    return envelope.result


def empty_payload() -> dict[str, Any]:
    """시험에서 형식만 맞춘 빈 응답을 만들 때 쓴다."""
    return {"schema_version": DRAFT_SCHEMA_VERSION, "result": {}}
