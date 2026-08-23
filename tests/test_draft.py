"""4일차 안전한 초안 시험 (README §2.16, §4.2).

여기서 재는 것은 글솜씨가 아니라 **막아야 할 것을 막는가**다. 가짜 작성기가
쓴 문장을 일부러 오염시켜 넣고, 초안이 나오지 않는 것을 확인한다.

`verification/day4-pass-bar.md`의 F·G·H·I 항목과 짝을 이룬다.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from app.harness.article_parser import (
    UNDETERMINABLE,
    UNSUPPORTED_COUNT,
    ArticleParseError,
    parse_changed_articles,
    top_level_article,
)
from app.harness.contracts import (
    EXTERNAL_AI_POLICY_VERSION,
    CreateRunRequest,
    Disclosure,
    InputMethod,
    SourceInput,
    SourceRole,
    StoredSource,
)
from app.harness.legal_contracts import FinalTextConfirmation, ResolvedFinalText
from app.harness.orchestrator import Orchestrator
from app.harness.source_normalizer import normalize_source
from app.gates.final_text_gate import resolve_final_text
from app.infrastructure.model_gateway import FakeModelGateway
from app.infrastructure.run_store import RunStore

FIXTURE = Path(__file__).resolve().parents[1] / "test_sets" / "ACTUAL-PASS-001"

#: 고정 자료의 역할. manifest의 한국어 이름과 enum을 잇는다.
PASS_SOURCES = (
    ("의안정보", "01_bill_information.md", SourceRole.BILL_INFORMATION),
    ("현행 조문", "02_current_provision.md", SourceRole.CURRENT_PROVISION),
    ("본회의 표결 결과", "03_plenary_vote_result.md", SourceRole.PLENARY_VOTE_RESULT),
    ("발의안", "04_introduced_text.md", SourceRole.INTRODUCED_TEXT),
)

#: 발의안은 네 번째 자료이므로 SRC-04다.
INTRODUCED_SOURCE_ID = "SRC-04"


def _source_inputs() -> list[SourceInput]:
    return [
        SourceInput(
            display_name=name,
            text=(FIXTURE / "sources" / filename).read_text(encoding="utf-8"),
            role=role,
        )
        for name, filename, role in PASS_SOURCES
    ]


async def _run(
    *,
    confirmed: bool = True,
    canned_draft: dict | None = None,
    sources: list[SourceInput] | None = None,
):
    """고정 정상 자료로 한 번 실행한다."""
    store = RunStore()
    gateway = FakeModelGateway()
    if canned_draft is not None:
        gateway.set_response("DraftWritingAgent", canned_draft)
    orchestrator = Orchestrator(store, gateway)
    request = CreateRunRequest(
        client_request_id="draft-run",
        purpose="문화예술진흥법 일부개정법률안의 본회의 의결 결과를 알리는 초안",
        disclosure=Disclosure.PUBLIC,
        basis_date=date(2025, 10, 26),
        sources=sources if sources is not None else _source_inputs(),
        announcement_subject="조계원 의원실",
        external_ai_policy_version=EXTERNAL_AI_POLICY_VERSION,
        external_ai_transfer_confirmed=True,
        final_text_completeness_confirmations=(
            [FinalTextConfirmation(source_id=INTRODUCED_SOURCE_ID, confirmed=True)]
            if confirmed
            else []
        ),
    )
    run = orchestrator.create_run(request)
    await orchestrator.process(run.run_id, request, date(2025, 10, 26))
    return store.get(run.run_id)


# ---------------------------------------------------------------------------
# I3 · 정상 자료는 검토 가능한 초안이 된다
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_정상_자료는_초안이_된다() -> None:
    run = await _run()
    assert run.state == "REVIEW_READY", (
        f"초안이 나오지 않았습니다: {run.failure_code} "
        f"{[f.describe() for f in run.validation_findings]}"
    )
    assert run.draft_version >= 1
    assert run.draft is not None


@pytest.mark.asyncio
async def test_초안에_DRAFT_표시가_남는다() -> None:
    run = await _run()
    assert run.draft is not None
    assert run.draft.draft_label == "DRAFT / 내부 검토용", run.draft.draft_label


@pytest.mark.asyncio
async def test_초안의_모든_요약과_제목에_근거가_붙는다() -> None:
    """어디서 온 문장인지 되짚을 수 없는 글은 초안에 넣지 않는다."""
    run = await _run()
    assert run.draft is not None
    known = {f.fact_id for f in run.fact_ledger.facts}
    for part in [run.draft.title, run.draft.lead, *run.draft.key_points]:
        assert part.fact_ids, f"근거 없는 문장입니다: {part.text}"
        for fact_id in part.fact_ids:
            assert fact_id in known, f"원장에 없는 근거 {fact_id}: {part.text}"


@pytest.mark.asyncio
async def test_화면에_조문과_부칙이_함께_나온다() -> None:
    """4일차 종료 조건: 근거·조문·부칙이 화면에 보인다."""
    run = await _run()
    assert run.changed_article_set is not None
    assert run.changed_article_set.article_ids == ["제7조"], (
        run.changed_article_set.article_ids
    )
    assert run.fact_ledger.supplementary_rules, "부칙을 하나도 정리하지 못했습니다."


@pytest.mark.asyncio
async def test_초안을_만들어도_외부_AI를_부르지_않는다() -> None:
    run = await _run()
    assert run.actual_model_calls == 0
    assert run.estimated_cost_usd == 0.0


# ---------------------------------------------------------------------------
# H3 · 최종 의결 내용을 근거 없이 고르지 않는다
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_사람이_확인하지_않으면_초안을_만들지_않는다() -> None:
    """발의안을 최종 의결 내용으로 대신 쓰려면 사람이 원문을 보고 확인해야 한다."""
    run = await _run(confirmed=False)
    assert run.draft_version == 0, "확인 없이 초안을 만들었습니다."
    assert run.state == "NEEDS_INPUT"
    subjects = [i.subject for i in run.issues]
    assert "FINAL_TEXT_COMPLETENESS_CONFIRMATION_REQUIRED" in subjects, subjects


def _stored(name: str, text: str, role: SourceRole, index: int) -> StoredSource:
    shape = normalize_source(text)
    return StoredSource(
        source_id=f"SRC-{index:02d}",
        display_name=name,
        role=role,
        input_method=InputMethod.PASTED,
        char_count=len(text),
        raw_text=text,
        raw_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        normalized_sha256=shape.normalized_sha256,
    )


def _chain_setup(replace: tuple[str, str] | None = None):
    """고정 정상 자료를 Gate가 바로 쓸 수 있는 모양으로 만든다."""
    sources, normalized = [], {}
    for index, (name, filename, role) in enumerate(PASS_SOURCES, start=1):
        text = (FIXTURE / "sources" / filename).read_text(encoding="utf-8")
        if replace:
            text = text.replace(*replace)
        source = _stored(name, text, role, index)
        sources.append(source)
        normalized[source.source_id] = normalize_source(text)
    return sources, normalized


def _confirmations() -> list[FinalTextConfirmation]:
    return [FinalTextConfirmation(source_id=INTRODUCED_SOURCE_ID, confirmed=True)]


#: 고정 자료의 보도 대상 의안번호. 실제 흐름에서는 Harness가 원장에서 꺼내 넘긴다.
PASS_BILL_NUMBER = "2207285"


def _resolve(sources, normalized, bill_number: str = PASS_BILL_NUMBER):
    return resolve_final_text(
        sources, normalized, _confirmations(), draft_bill_number=bill_number
    )


def test_소관위가_수정가결이면_발의안을_최종문으로_쓰지_않는다() -> None:
    """§2.16.2 조건 3·4. 중간에 내용이 바뀌었으면 발의안은 최종 내용이 아니다."""
    sources, normalized = _chain_setup(("처리결과 원안가결", "처리결과 수정가결"))
    final_text, issues = _resolve(sources, normalized)
    assert final_text is None, "수정가결인데 발의안을 최종문으로 썼습니다."
    assert issues and issues[0].subject == "FINAL_TEXT_DERIVATION_UNSAFE"


def test_본회의가_부결이면_발의안을_최종문으로_쓰지_않는다() -> None:
    sources, normalized = _chain_setup(("회의결과: 원안가결", "회의결과: 부결"))
    final_text, issues = _resolve(sources, normalized)
    assert final_text is None, "부결인데 발의안을 최종문으로 썼습니다."
    assert issues and issues[0].subject == "FINAL_TEXT_DERIVATION_UNSAFE"


def test_의안번호가_다르면_발의안을_최종문으로_쓰지_않는다() -> None:
    """§2.16.2 조건 2. 서로 다른 의안의 자료를 이어 붙이면 안 된다."""
    sources, normalized = _chain_setup(("대상 의안번호: 2207285", "대상 의안번호: 2209999"))
    final_text, issues = _resolve(sources, normalized)
    assert final_text is None, "의안번호가 다른데 발의안을 최종문으로 썼습니다."
    assert issues and "의안번호가 다릅니다" in issues[0].message


def test_개정문_경계가_없으면_최종문을_만들지_않는다() -> None:
    """§2.16.3. 어디까지가 개정문인지 모르면 조문을 셀 수 없다."""
    sources, normalized = _chain_setup(
        ("문화예술진흥법 일부를 다음과 같이 개정한다.", "[중략]")
    )
    final_text, issues = _resolve(sources, normalized)
    assert final_text is None
    assert issues and issues[0].subject == "SOURCE_TEXT:BOUNDARY_MISSING_OR_AMBIGUOUS"


def test_최종문에는_표결_문장을_섞지_않는다() -> None:
    """개정문은 발의안의 확인된 구간만 쓴다 (§2.16.2)."""
    sources, normalized = _chain_setup()
    final_text, _ = _resolve(sources, normalized)
    assert final_text is not None
    assert final_text.source_id == INTRODUCED_SOURCE_ID
    assert "원안가결" not in final_text.body_text, final_text.body_text
    assert "회의결과" not in final_text.body_text


# ---------------------------------------------------------------------------
# H4 · 조문은 코드가 센다
# ---------------------------------------------------------------------------


def _body(text: str) -> ResolvedFinalText:
    whole = "X 일부를 다음과 같이 개정한다.\n\n" + text + "\n\n부칙\n\n이 법은 공포한 날부터 시행한다.\n"
    start = whole.index("개정한다.") + len("개정한다.")
    end = whole.index("\n부칙")
    return ResolvedFinalText(
        derivation_id="FT-TEST",
        rule="TEST",
        source_id="SRC-01",
        body_start=start,
        body_end=end,
        text=whole,
    )


@pytest.mark.parametrize(
    ("amendment", "expected"),
    [
        ("제7조제6항 중 “모집할”을 “모집ㆍ접수할”로 한다.", ["제7조"]),
        ("제7조제6항 중 “A”를 “B”로 하고, 제7조제7항을 삭제한다.", ["제7조"]),
        ("제8조 중 “A”를 “B”로 한다.\n제12조를 다음과 같이 신설한다.", ["제8조", "제12조"]),
        ("제23조의8 중 “A”를 “B”로 한다.\n제23조의14를 삭제한다.", ["제23조의8", "제23조의14"]),
        (
            "제9조를 다음과 같이 신설한다.\n① 첫째 항이다.\n② 둘째 항이다.\n다만, 예외가 있다.",
            ["제9조"],
        ),
    ],
)
def test_바뀐_조문을_코드가_센다(amendment: str, expected: list[str]) -> None:
    result = parse_changed_articles(_body(amendment))
    assert result.article_ids == expected
    assert result.unparsed_spans == []
    assert result.fully_consumed, (
        f"본칙을 다 읽지 못했습니다: {result.consumed_non_space}/{result.total_non_space}"
    )


@pytest.mark.parametrize(
    ("amendment", "code", "subject_starts"),
    [
        ("제1조 삭제.\n제2조 삭제.\n제3조 삭제.\n제4조 삭제.", UNSUPPORTED_COUNT, "CHANGED_ARTICLE_COUNT"),
        ("제5조를 제6조로 한다.", UNDETERMINABLE, "UNSUPPORTED_SYNTAX"),
        ("제5조부터 제9조까지를 각각 삭제한다.", UNDETERMINABLE, "UNSUPPORTED_SYNTAX"),
        ("별표 1을 다음과 같이 한다.", UNDETERMINABLE, "UNSUPPORTED_SYNTAX"),
        ("제7조를 개정하고 동조 제2항을 삭제한다.", UNDETERMINABLE, "UNSUPPORTED_SYNTAX"),
        ("① 이 항은 앞에 지시문이 없다.", UNDETERMINABLE, "SOURCE_TEXT"),
        ("제7조를 삭제한다.\n알 수 없는 문장이 남아 있다.", UNDETERMINABLE, "SOURCE_TEXT"),
    ],
)
def test_셀_수_없는_개정문은_추측하지_않고_멈춘다(
    amendment: str, code: str, subject_starts: str
) -> None:
    """일부만 센 1~3개를 성공으로 처리하지 않는다 (§2.16.3)."""
    with pytest.raises(ArticleParseError) as exc:
        parse_changed_articles(_body(amendment))
    assert exc.value.code == code, exc.value.subject
    assert exc.value.subject.startswith(subject_starts), exc.value.subject


def test_같은_조의_여러_항은_한_개로_센다() -> None:
    assert top_level_article("제7조제6항") == "제7조"
    assert top_level_article("제23조의8제2항") == "제23조의8"
    assert top_level_article("제 7 조") == "제7조"


# ---------------------------------------------------------------------------
# F·G·H · 오염된 초안은 나가지 않는다
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def good_draft() -> dict:
    """가짜 작성기가 만든 정상 초안. 이것을 오염시켜 Gate를 시험한다."""
    run = asyncio.run(_run())
    assert run.draft is not None, "기준 초안을 만들지 못했습니다."
    return {
        "schema_version": "1.1.0",
        "result": json.loads(run.draft.model_dump_json()),
    }


def _spoil(good: dict, mutate) -> dict:
    payload = copy.deepcopy(good)
    mutate(payload["result"])
    return payload


ATTACKS = [
    (
        "없는 표결 수를 지어낸다",
        lambda d: d["paragraphs"][0].__setitem__(
            "text", d["paragraphs"][0]["text"] + " 재석 250인 중 찬성 249인이었다."
        ),
        "NUMBER_NOT_IN_LEDGER",
    ),
    (
        "없는 인용문을 지어낸다",
        lambda d: d["lead"].__setitem__(
            "text", d["lead"]["text"] + " 의원은 “국민을 위한 법”이라고 말했다."
        ),
        "STATEMENT_WITHOUT_SOURCE",
    ),
    ("DRAFT 표시를 지운다", lambda d: d.__setitem__("draft_label", ""), "DRAFT_LABEL_REQUIRED"),
    (
        "최종본이라고 쓴다",
        lambda d: d["title"].__setitem__("text", "최종본 문화예술진흥법 개정"),
        "NO_FINAL_OR_APPROVED_LABEL",
    ),
    (
        "공포됐다고 쓴다",
        lambda d: d["lead"].__setitem__("text", "이 법은 공포되었다."),
        "PREMATURE_EFFECT_CLAIM",
    ),
    (
        "시행 중이라고 쓴다",
        lambda d: d["lead"].__setitem__("text", "개정 내용은 현재 시행 중이다."),
        "PREMATURE_EFFECT_CLAIM",
    ),
    (
        "법이 개정됐다고 쓴다",
        lambda d: d["lead"].__setitem__("text", "문화예술진흥법이 개정되었다."),
        "PREMATURE_EFFECT_CLAIM",
    ),
    (
        "코드가 세지 않은 조문을 말한다",
        lambda d: d["key_points"][1].__setitem__("text", "바뀐 조문은 제99조이다."),
        "ARTICLE_NOT_IN_CHANGED_SET",
    ),
    (
        "부칙 근거 없이 시행일을 말한다",
        lambda d: d["paragraphs"][-1].__setitem__("supplementary_rule_ids", []),
        "EFFECTIVE_DATE_NEEDS_RULE",
    ),
    (
        "원장에 없는 사실을 가리킨다",
        lambda d: d["title"].__setitem__("fact_ids", ["F-없음"]),
        "FACT_REFERENCE_UNKNOWN",
    ),
    ("문의처를 지운다", lambda d: d.__setitem__("contact_text", "  "), "CONTACT_REQUIRED"),
]


@pytest.mark.parametrize(("name", "mutate", "rule_id"), ATTACKS, ids=[a[0] for a in ATTACKS])
def test_오염된_초안은_나가지_않는다(good_draft, name, mutate, rule_id) -> None:
    run = asyncio.run(_run(canned_draft=_spoil(good_draft, mutate)))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    assert run.draft is None
    rules = {f.rule_id for f in run.validation_findings if f.severity.value == "BLOCKING"}
    assert rule_id in rules, f"{name}: 기대한 규칙이 걸리지 않았습니다. 걸린 것: {rules}"


def test_핵심_요약이_두_개보다_적으면_초안이_나가지_않는다(good_draft) -> None:
    """형식 자체가 어긋나면 검사 이전에 막힌다."""
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: d.__setitem__("key_points", d["key_points"][:1])))
    )
    assert run.draft_version == 0
    assert run.draft is None


def test_막힌_이유에는_규칙과_기준_문서와_초안_위치가_있다(good_draft) -> None:
    """§4.2. 셋 중 하나라도 없으면 왜 막혔는지 되짚을 수 없다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: d["lead"].__setitem__("text", "이 법은 공포되었다."),
            )
        )
    )
    blocked = [f for f in run.validation_findings if f.severity.value == "BLOCKING"]
    assert blocked
    for finding in blocked:
        assert finding.rule_id, finding
        assert finding.rule_document.startswith("README §"), finding
        assert finding.affected_part, finding


# ---------------------------------------------------------------------------
# 검토가 "되돌려도 죽는 시험이 없다"고 지적한 자리들
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_AI가_센_조문이_코드와_다르면_초안을_만들지_않는다() -> None:
    """§2.16.3. 코드 집합과 AI 집합이 정확히 같아야 진행한다."""
    from app.harness.fact_contracts import FACT_RESULT_SCHEMA_VERSION

    store = RunStore()
    gateway = FakeModelGateway()
    # AI가 조문 비교를 하나도 만들지 않은 응답. 코드는 제7조 1개를 센다.
    gateway.set_response(
        "FactExtractionAgent",
        {
            "schema_version": FACT_RESULT_SCHEMA_VERSION,
            "result": {
                "result_status": "OK",
                "scope_error": None,
                "source_role_candidates": [],
                "evidence": [
                    {
                        "evidence_id": "EV-01",
                        "source_id": "SRC-04",
                        "quote": "- 의안번호: 2207285",
                    }
                ],
                "facts": [
                    {
                        "fact_id": "F-01",
                        "kind": "BILL_IDENTITY",
                        "value": "2207285",
                        "source_id": "SRC-04",
                        "evidence_id": "EV-01",
                        "valid_source_role_candidate_ids": [],
                    }
                ],
                "bill_identities": [
                    {
                        "bill_id": "B-01",
                        "bill_number": "2207285",
                        "is_draft_subject": True,
                        "source_id": "SRC-04",
                        "evidence_ids": ["EV-01"],
                    }
                ],
                "bill_relations": [],
                "legislative_events": [],
                "provision_comparisons": [],
                "supplementary_rules": [],
            },
        },
    )
    orchestrator = Orchestrator(store, gateway)
    request = CreateRunRequest(
        client_request_id="mismatch",
        purpose="조문 집합이 어긋날 때 멈추는지 확인합니다.",
        disclosure=Disclosure.PUBLIC,
        basis_date=date(2025, 10, 26),
        sources=_source_inputs(),
        announcement_subject="조계원 의원실",
        external_ai_policy_version=EXTERNAL_AI_POLICY_VERSION,
        external_ai_transfer_confirmed=True,
        final_text_completeness_confirmations=[
            FinalTextConfirmation(source_id=INTRODUCED_SOURCE_ID, confirmed=True)
        ],
    )
    run = orchestrator.create_run(request)
    await orchestrator.process(run.run_id, request, date(2025, 10, 26))
    result = store.get(run.run_id)
    assert result.draft_version == 0, "조문 집합이 어긋나는데 초안을 만들었습니다."
    assert result.failure_code == "PROVISION_SET_MISMATCH", result.failure_code
    assert "제7조" in (result.failure_message or "")


def test_다른_자료에_대안_근거가_있으면_발의안을_최종문으로_쓰지_않는다() -> None:
    """§2.16.2 조건 4. 수정·대체 근거가 **어느 자료에든** 있으면 안 된다."""
    sources, normalized = _chain_setup(
        ("법령 버전:", "이 의안은 대안반영폐기되었다. 법령 버전:")
    )
    final_text, issues = _resolve(sources, normalized)
    assert final_text is None, "대안 근거가 있는데 발의안을 최종문으로 썼습니다."
    assert issues and "수정·대체" in issues[0].message, issues[0].message


def test_자료가_바뀌면_발의안을_최종문으로_쓰지_않는다() -> None:
    """§2.16.2 조건 6. 저장된 해시와 다시 센 해시가 같아야 한다."""
    sources, normalized = _chain_setup()
    # 저장된 해시만 다른 값으로 바꾼다. 원문은 그대로다.
    for source in sources:
        if source.source_id == INTRODUCED_SOURCE_ID:
            source.normalized_sha256 = "0" * 64
    final_text, issues = _resolve(sources, normalized)
    assert final_text is None, "원문이 달라졌는데 최종문을 만들었습니다."
    assert issues and "달라졌습니다" in issues[0].message, issues[0].message


def test_보도_대상_의안을_모르면_발의안을_최종문으로_쓰지_않는다() -> None:
    """§2.16.2 조건 2는 보도 대상 의안과의 대조를 요구한다."""
    sources, normalized = _chain_setup()
    final_text, issues = _resolve(sources, normalized, bill_number="")
    assert final_text is None, "보도 대상을 모르는데 최종문을 만들었습니다."
    assert issues and "어느 의안을" in issues[0].message, issues[0].message


def _draft_dict(run) -> dict:
    return {"schema_version": "1.1.0", "result": json.loads(run.draft.model_dump_json())}


@pytest.mark.asyncio
async def test_자료_기준일이_비면_초안을_만들지_않는다() -> None:
    payload = _draft_dict(await _run())
    payload["result"]["basis_date"] = ""
    run = await _run(canned_draft=payload)
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "BASIS_DATE_REQUIRED" in rules, rules


@pytest.mark.asyncio
async def test_필수_문단이_없으면_초안을_만들지_않는다() -> None:
    payload = _draft_dict(await _run())
    for paragraph in payload["result"]["paragraphs"]:
        paragraph["section_kind"] = "EXTRA"
    run = await _run(canned_draft=payload)
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "REQUIRED_SECTION_MISSING" in rules, rules


@pytest.mark.asyncio
async def test_없는_주장을_가리키면_초안을_만들지_않는다() -> None:
    payload = _draft_dict(await _run())
    payload["result"]["title"]["claim_ids"] = ["CL-없음"]
    run = await _run(canned_draft=payload)
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "CLAIM_REFERENCE_UNKNOWN" in rules, rules


@pytest.mark.asyncio
async def test_없는_부칙을_가리키면_초안을_만들지_않는다() -> None:
    payload = _draft_dict(await _run())
    payload["result"]["paragraphs"][-1]["supplementary_rule_ids"] = ["SR-없음"]
    run = await _run(canned_draft=payload)
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "RULE_REFERENCE_UNKNOWN" in rules, rules


@pytest.mark.asyncio
async def test_발표_주체가_없으면_초안을_만들지_않는다() -> None:
    """§2.11 4단계. 누가 발표하는지 확정되지 않으면 초안을 내주지 않는다."""
    store = RunStore()
    orchestrator = Orchestrator(store, FakeModelGateway())
    request = CreateRunRequest(
        client_request_id="no-subject",
        purpose="발표 주체 없이 초안이 나오는지 확인합니다.",
        disclosure=Disclosure.PUBLIC,
        basis_date=date(2025, 10, 26),
        sources=_source_inputs(),
        announcement_subject=None,
        external_ai_policy_version=EXTERNAL_AI_POLICY_VERSION,
        external_ai_transfer_confirmed=True,
        final_text_completeness_confirmations=[
            FinalTextConfirmation(source_id=INTRODUCED_SOURCE_ID, confirmed=True)
        ],
    )
    run = orchestrator.create_run(request)
    await orchestrator.process(run.run_id, request, date(2025, 10, 26))
    result = store.get(run.run_id)
    assert result.draft_version == 0, "발표 주체 없이 초안을 만들었습니다."
    rules = {f.rule_id for f in result.validation_findings}
    assert "ANNOUNCEMENT_SUBJECT_REQUIRED" in rules, rules


def test_바뀐_조문을_하나도_찾지_못하면_멈춘다() -> None:
    with pytest.raises(ArticleParseError) as exc:
        parse_changed_articles(_body("다만, 지시문이 하나도 없다."))
    assert exc.value.code == UNDETERMINABLE


def test_신설_조문_본문_속_참조는_바뀐_조문으로_세지_않는다() -> None:
    """§2.16.3. 새 조문 본문 안의 단순 참조는 세지 않는다."""
    result = parse_changed_articles(
        _body("제12조를 다음과 같이 신설한다.\n제7조의 규정에도 불구하고 접수할 수 있다.")
    )
    assert result.article_ids == ["제12조"], result.article_ids
    assert result.fully_consumed


# ---------------------------------------------------------------------------
# 검토가 뚫었던 공격들. 다시 뚫리면 여기서 죽는다.
# ---------------------------------------------------------------------------

ATTACKS_V2 = [
    ("한글 숫자", lambda d: _append(d, "재석 이백오십인 중 찬성 이백사십구인이었다.")),
    ("한자 숫자", lambda d: _append(d, "재석 二百五十인이었다.")),
    ("전각 숫자", lambda d: _append(d, "재석 ２５０인이었다.")),
    ("원장 날짜 조각 재사용", lambda d: _append(d, "재석 26인 중 찬성 10인으로 의결됐다.")),
    ("없는 인명", lambda d: _lead(d, "김영수 위원장은 이번 의결을 환영했다.")),
    ("없는 기관", lambda d: _lead(d, "문화체육관광부가 후속 조치를 맡는다.")),
    ("따옴표 없는 인용", lambda d: _lead(d, "조계원 의원은 현장의 오랜 숙원이 풀렸다고 밝혔다.")),
    ("낫표 인용", lambda d: _lead(d, "의원은 「현장의 숙원이 풀렸다」고 말했다.")),
    ("자료 낱말로 조립한 발언", lambda d: _lead(d, "의원은 “기부금품을 모집할 수 있다”고 밝혔다.")),
    ("공포되어", lambda d: _lead(d, "이 법은 공포되어 곧 효력을 갖는다.")),
    ("시행됩니다", lambda d: _lead(d, "이 법은 공포한 날부터 시행됩니다.")),
    ("개정이 완료됐습니다", lambda d: _lead(d, "문화예술진흥법 개정이 완료됐습니다.")),
    ("띄어쓴 공 포되었다", lambda d: _lead(d, "이 법은 공 포되었다.")),
    ("요약에 다른 시행일", lambda d: _point(d, "공포 후 6개월이 지난 날부터 시행된다.")),
    ("제목에 공포 즉시 시행", lambda d: _title(d, "공포 즉시 시행")),
    ("부칙 ID는 두고 내용만 바꿈", lambda d: d["paragraphs"][-1].__setitem__("text", "공포 후 6개월이 지난 날부터 시행된다.")),
    ("빈칸 표시에 지어낸 수", lambda d: d.__setitem__("placeholders", ["재석 250인 중 찬성 249인"])),
    ("주장에 지어낸 인명", lambda d: d["claims"][0].__setitem__("text", "김영수 장관이 발표했다")),
    ("기준일 조작", lambda d: d.__setitem__("basis_date", "2099-12-31")),
    ("육하원칙에 자유 글", lambda d: d.__setitem__("six_w_status", {"who": "김영수 장관"})),
    ("인용 칸에 지어낸 발언", lambda d: d.__setitem__("quote", {"text": "국민을 위한 법이다"})),
    ("붙임에 지어낸 값", lambda d: d.__setitem__("attachments", [{"title": "재석 250인 표결표"}])),
    ("상태 코드에 자유 글", lambda d: d.__setitem__("contact_status", "김영수 장관실")),
    ("대외 공개 가능 문서", lambda d: _title(d, "대외 공개 가능 문서")),
    ("띄어쓴 최 종 본", lambda d: _title(d, "최 종 본 문화예술진흥법")),
    ("띄어쓴 안 센 조문", lambda d: _point(d, "바뀐 조문은 제 99 조이다.")),
]


def _lead(d: dict, text: str) -> None:
    d["lead"]["text"] = text


def _title(d: dict, text: str) -> None:
    d["title"]["text"] = text


def _point(d: dict, text: str) -> None:
    d["key_points"][1]["text"] = text


def _append(d: dict, text: str) -> None:
    d["paragraphs"][0]["text"] += " " + text


@pytest.mark.parametrize(
    ("name", "mutate"), ATTACKS_V2, ids=[a[0] for a in ATTACKS_V2]
)
def test_검토가_뚫었던_공격은_이제_막힌다(good_draft, name, mutate) -> None:
    run = asyncio.run(_run(canned_draft=_spoil(good_draft, mutate)))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    assert run.draft is None
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )
