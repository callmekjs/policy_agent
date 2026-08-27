"""개발용 가짜 초안 작성기 (README §3.8).

진짜 AI를 부르지 않고, **Harness가 준 재료만으로** 초안 후보를 조립한다. 목적은
글솜씨가 아니라 흐름과 안전 Gate가 실제로 도는지 확인하는 것이다. 문장 품질은
6일차에 진짜 AI로 바꿔 확인한다.

규칙은 하나다. **받은 재료 밖의 값을 절대 새로 만들지 않는다.** 숫자도, 날짜도,
인용문도 만들지 않는다. 그래서 이 작성기가 만든 초안은 안전 Gate를 그대로
통과해야 하고, 통과하지 못하면 Gate가 아니라 이 파일이 잘못된 것이다.
"""

from __future__ import annotations

from typing import Any

from app.harness.draft_contracts import DRAFT_SCHEMA_VERSION

#: 초안에 먼저 쓰고 싶은 사실 종류. 없으면 있는 것만 쓴다.
PREFERRED_KINDS = (
    "BILL_IDENTITY",
    "PLENARY_RESULT",
    "PLENARY_DECIDED_ON",
    "VOTE_PRESENT_COUNT",
    "VOTE_YES_COUNT",
)


def _pick(facts: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    for fact in facts:
        if fact.get("kind") == kind:
            return fact
    return None


def _claim_text(text: str, claim_ids: list[str], fact_ids: list[str]) -> dict[str, Any]:
    return {"text": text, "claim_ids": claim_ids, "fact_ids": fact_ids}


def fake_draft_writing(payload: dict[str, Any]) -> dict[str, Any]:
    """받은 재료로만 초안 후보 하나를 만든다."""
    facts: list[dict[str, Any]] = list(payload.get("facts") or [])
    rules: list[dict[str, Any]] = list(payload.get("supplementary_rules") or [])
    articles: list[str] = list(payload.get("changed_article_ids") or [])
    body: str = payload.get("final_text_body") or ""
    stage_label: str = payload.get("procedure_stage_label") or ""
    effect_label: str = payload.get("effect_status_label") or ""
    subject: str = payload.get("announcement_subject") or ""
    basis_date: str = payload.get("basis_date") or ""

    # 쓸 사실을 고르고, 고른 것마다 주장을 하나씩 만든다.
    chosen: list[dict[str, Any]] = []
    for kind in PREFERRED_KINDS:
        fact = _pick(facts, kind)
        if fact is not None and fact not in chosen:
            chosen.append(fact)
    for fact in facts:
        if len(chosen) >= 6:
            break
        if fact not in chosen:
            chosen.append(fact)

    claims = [
        {
            "claim_id": f"CL-{i:02d}",
            "text": str(fact.get("value", "")),
            "fact_ids": [fact["fact_id"]],
        }
        for i, fact in enumerate(chosen, start=1)
    ]
    claim_ids = [c["claim_id"] for c in claims]
    fact_ids = [f["fact_id"] for f in chosen]

    if not claims:
        # 쓸 사실이 없으면 초안을 만들지 않는다. Harness가 형식 오류로 막는다.
        return {"schema_version": DRAFT_SCHEMA_VERSION, "result": {}}

    identity = _pick(chosen, "BILL_IDENTITY")
    result = _pick(chosen, "PLENARY_RESULT")
    decided_on = _pick(chosen, "PLENARY_DECIDED_ON")

    bill_label = f"의안번호 {identity['value']}" if identity else "이번 안건"
    result_label = result["value"] if result else stage_label
    when = f"{decided_on['value']} " if decided_on else ""

    # 결과를 말하는 제목이면 **결과를 적는다.** `의결 결과`라고만 하고
    # 무엇으로 의결됐는지 안 쓰면 읽는 사람이 알 수 없다.
    title_text = f"{bill_label} {stage_label}: {result_label}"
    # 효력 상태와 자료 기준일은 Harness가 `BASIS_AND_STATUS` 자리에 직접
    # 적는다. AI가 또 쓰면 같은 값을 두 곳에서 관리하게 되고, 무엇보다
    # **AI가 효력을 말하는 자리**가 생긴다 (11차 검토).
    lead_text = (
        f"{subject or '발표 주체 확인 필요'}은(는) {when}{bill_label}이(가) "
        f"{result_label}로 처리된 사실을 알린다."
    )

    # 근거는 **그 문장이 실제로 말하는 값**을 가진 사실이어야 한다.
    # 조문 이야기를 하면서 의안번호를 근거로 대면, 근거를 되짚어도 그 문장이
    # 맞는지 확인할 수 없다.
    def _fact_for(value: str) -> dict[str, Any] | None:
        for fact in chosen:
            if value and value in str(fact.get("value", "")):
                return fact
        return None

    result_fact = result or _fact_for(result_label) or chosen[0]
    article_fact = _fact_for(articles[0] if articles else "") or chosen[0]

    def _cite(fact: dict[str, Any]) -> tuple[list[str], list[str]]:
        for claim, candidate_fact in zip(claims, chosen):
            if candidate_fact is fact:
                return [claim["claim_id"]], [fact["fact_id"]]
        return claim_ids[:1], fact_ids[:1]

    result_claim_ids, result_fact_ids = _cite(result_fact)
    article_claim_ids, article_fact_ids = _cite(article_fact)

    key_points = [
        _claim_text(
            f"{bill_label}이(가) {result_label}로 처리되었다.",
            result_claim_ids,
            result_fact_ids,
        ),
        _claim_text(
            f"바뀐 조문은 {', '.join(articles) if articles else '확인 필요'}이다.",
            article_claim_ids,
            article_fact_ids,
        ),
    ]

    # 근거는 **글에 실제로 쓴 사실만** 댄다. 쓰지도 않은 사실을 대면
    # 그 문장이 맞는지 근거로 되짚을 수 없다.
    def _used_in(text: str) -> tuple[list[str], list[str]]:
        used_claims: list[str] = []
        used_facts: list[str] = []
        for claim, fact in zip(claims, chosen):
            value = str(fact.get("value", ""))
            if value and value in text:
                used_claims.append(claim["claim_id"])
                used_facts.append(fact["fact_id"])
        if not used_facts:  # 형식은 근거를 하나 이상 요구한다
            return claim_ids[:1], fact_ids[:1]
        return used_claims, used_facts

    lead_claim_ids, lead_fact_ids = _used_in(lead_text)

    paragraphs: list[dict[str, Any]] = [
        {
            "paragraph_id": "P-01",
            "section_kind": "BODY",
            "priority_rank": 1,
            "text": lead_text,
            "claim_ids": lead_claim_ids,
            "fact_ids": lead_fact_ids,
            "supplementary_rule_ids": [],
        }
    ]

    if articles and body.strip():
        paragraphs.append(
            {
                "paragraph_id": "P-02",
                "section_kind": "BODY",
                "priority_rank": 2,
                # 개정문 본칙을 그대로 옮긴다. 요약하면 없는 말이 섞일 수 있다.
                # `개정 문구`·`확정`은 효력을 말하는 낱말이라 AI가 쓸 수
                # 없다. 개정문 본문은 원장이 정한 값이라 그대로 옮길 수 있다.
                "text": (
                    f"바뀐 조문은 {', '.join(articles)}이다. "
                    f"공식 자료가 제안한 문구는 다음과 같다. "
                    f"{body.strip()}"
                ),
                "claim_ids": [],
                "fact_ids": article_fact_ids,
                "supplementary_rule_ids": [],
            }
        )

    # 부칙은 **Harness가 직접 만든다** (`draft_sections.build_fixed_sections`).
    # 자료에 적힌 글 그대로이고 고를 것이 없는데 AI가 쓰면 `시행한다`를
    # `시행되었다`로 바꿔 쓸 수 있다(11차 검토). 여기서 `SUPPLEMENTARY`로
    # 보내면 Harness가 걷어내고 자기가 만든 것으로 갈아 끼운다. 진짜 AI가
    # 이 자리를 채우려 해도 같은 일이 일어난다.
    for i, rule in enumerate(rules, start=3):
        paragraphs.append(
            {
                "paragraph_id": f"P-{i:02d}",
                "section_kind": "SUPPLEMENTARY",
                "priority_rank": i,
                "text": f"부칙은 “{rule['applies_to']}”라고 제안하고 있다.",
                "claim_ids": [],
                "fact_ids": [],
                "supplementary_rule_ids": [rule["rule_id"]],
            }
        )

    return {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "result": {
            "schema_version": DRAFT_SCHEMA_VERSION,
            "candidate_id": "DC-01",
            "version": 1,
            "procedure_stage": payload.get("procedure_stage") or "PLENARY_DECIDED",
            "effect_status": payload.get("effect_status") or "NOT_A_LAW",
            "basis_date": basis_date,
            "announcement_subject_fact_id": "",
            "announcement_subject_provenance": "USER_CONFIRMED",
            "release_date_status": "NEEDS_CONFIRMATION",
            "release_date_fact_id": "",
            "draft_label": payload.get("draft_label") or "",
            "title": _claim_text(title_text, *_cite(identity or chosen[0])),
            "key_points": key_points,
            "lead": _claim_text(lead_text, lead_claim_ids, lead_fact_ids),
            "paragraphs": paragraphs,
            "contact_status": "NEEDS_CONFIRMATION",
            "contact_text": payload.get("contact_text") or "[문의처 확인 필요]",
            "quote": None,
            "attachments": [],
            # 여섯 칸을 모두 적는다. 빠뜨리면 "확인 안 함"과 "해당 없음"을
            # 구분할 수 없고, AI에게 보내는 형식도 여섯 칸을 모두 요구한다.
            "six_w_status": {
                "who": "OK",
                "what": "OK",
                "when": "OK",
                "where": "NOT_APPLICABLE",
                "why": "NEEDS_CONFIRMATION",
                "how": "NEEDS_CONFIRMATION",
            },
            "claims": claims,
            "validation_finding_ids": [],
            "placeholders": ["[보도일 확인 필요]"],
            "generated_at": "",
            "next_procedure_fact_ids": [],
        },
    }
