"""Harness가 직접 채우는 문단 (README §2.7, §2.16.2).

`DRAFT_MARK`·`BASIS_AND_STATUS`·`ANNOUNCER_AND_RELEASE`·`CONTACT` 네 자리는
**값이 이미 정해져 있다.** 자료 기준일은 사용자가 넣은 날짜이고, 절차 단계와
효력 상태는 이 버전이 고정한 값이며, 발표 주체는 사용자가 확인한 값이다.

그런데 지금까지는 AI가 이 문장들도 썼다. 그래서 검토가 `자료 기준일`에 다른
날짜를 넣거나 발표 주체 자리에 지어낸 기관을 넣어 통과시킬 수 있었다.

정해진 값을 AI에게 맡길 이유가 없다. 여기서 Harness가 직접 만든다. AI는 이
자리에 손댈 수 없으므로 **거짓을 찾아낼 필요 자체가 없어진다.**
"""

from __future__ import annotations

from app.gates.draft_template import DraftTemplate
from app.harness.draft_contracts import DRAFT_LABEL, DraftParagraph


def build_fixed_sections(
    template: DraftTemplate,
    *,
    basis_date: str,
    procedure_stage_label: str,
    effect_status_label: str,
    announcement_subject: str,
    contact_text: str,
    release_date_text: str,
    internet_notice: str,
) -> list[DraftParagraph]:
    """값이 정해진 네 자리를 만든다. 순서는 계약이 정한 대로다."""
    made: dict[str, str] = {
        "DRAFT_MARK": DRAFT_LABEL,
        "BASIS_AND_STATUS": (
            f"제공된 공식 자료 기준: {basis_date} · "
            f"절차 단계: {procedure_stage_label} · "
            f"효력 상태: {effect_status_label}\n{internet_notice}"
        ),
        "ANNOUNCER_AND_RELEASE": (
            f"발표 주체: {announcement_subject} · 보도 예정일: {release_date_text}"
        ),
        "CONTACT": f"문의처: {contact_text}",
    }

    paragraphs: list[DraftParagraph] = []
    for rank, kind in enumerate(template.section_kinds, start=1):
        text = made.get(kind)
        if text is None:
            continue
        paragraphs.append(
            DraftParagraph(
                paragraph_id=f"HS-{len(paragraphs) + 1:02d}",
                section_kind=kind,
                priority_rank=rank,
                text=text,
                claim_ids=[],
                fact_ids=[],
                supplementary_rule_ids=[],
            )
        )
    return paragraphs
