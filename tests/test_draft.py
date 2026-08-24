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


# ---------------------------------------------------------------------------
# 2차 검토가 뚫었던 공격과, "지키는 시험이 없다"고 지적한 자리들
# ---------------------------------------------------------------------------

ATTACKS_V3 = [
    (
        "글자마다 띄어 쓴 통짜 거짓말",
        lambda d: _append(
            d,
            "국 회 는 본 회 의 에 서 재 석 2 6 인 중 찬 성 2 6 인 으 로 "
            "이 안 을 의 결 했 다.",
        ),
    ),
    ("흩어 쓴 인명", lambda d: _lead(d, "김 영 수 장 관 이 이 번 결 과 를 알 렸 다.")),
    ("흩어 쓴 기관명", lambda d: _lead(d, "문 화 체 육 관 광 부 가 후 속 조 치 를 맡 는 다.")),
    ("흩어 쓴 한글 수사 조문", lambda d: _point(d, "바 뀐 조 문 은 제 십 이 조 이 다.")),
    ("한 글자로 깎이는 이름", lambda d: _lead(d, "이지은 의원이 이번 결과를 발표했다.")),
    ("한 글자로 깎이는 이름 2", lambda d: _lead(d, "박서은 의원이 이번 결과를 알린다.")),
    (
        "실존 인물에 없는 발언",
        lambda d: _lead(d, "조계원 의원은 “실무 현장의 혼선을 방지”한다고 발표했다."),
    ),
    ("~고 한다", lambda d: _lead(d, "의원실은 이번 결과를 알린다고 한다.")),
    ("적용된다", lambda d: _lead(d, "이 법은 곧 적용된다.")),
    (
        "문단 번호에 거짓",
        lambda d: d["paragraphs"][0].__setitem__(
            "paragraph_id", "재석 250인 중 찬성 249인, 김영수 장관 발표"
        ),
    ),
    ("초안 번호에 거짓", lambda d: d.__setitem__("candidate_id", "재석 250인")),
    (
        "문단 종류에 거짓",
        lambda d: d["paragraphs"][0].__setitem__("section_kind", "김영수 장관 발표"),
    ),
    (
        "발표 주체 근거 위조",
        lambda d: d.__setitem__("announcement_subject_fact_id", "F-없음"),
    ),
    ("보도일 근거 위조", lambda d: d.__setitem__("release_date_fact_id", "F-없음")),
    ("주장 번호에 거짓", lambda d: d["claims"][0].__setitem__("claim_id", "재석 250인")),
]


@pytest.mark.parametrize(
    ("name", "mutate"), ATTACKS_V3, ids=[a[0] for a in ATTACKS_V3]
)
def test_2차_검토가_뚫었던_공격은_이제_막힌다(good_draft, name, mutate) -> None:
    run = asyncio.run(_run(canned_draft=_spoil(good_draft, mutate)))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    assert run.draft is None
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )


# --- 방어 하나하나를 겨눈 시험 ------------------------------------------------


def test_수를_표기법과_상관없이_읽는다() -> None:
    """`numeral_reader`만 겨눈다. 낱말 검사가 대신 막아 주지 않는지 확인한다."""
    from app.gates.numeral_reader import read_numbers, read_numeral_word

    assert read_numeral_word("이백오십") == 250
    assert read_numeral_word("二百五十") == 250
    assert read_numeral_word("스물다섯") == 25
    # 글에서 읽어 내는 길도 함께 확인한다. 낱말 하나만 읽을 줄 알아도
    # `read_numbers`가 그 길을 안 지나면 표기법 우회가 그대로 통한다.
    assert 250 in read_numbers("재석 이백오십인"), "한글 수사를 글에서 못 읽습니다."
    assert 250 in read_numbers("재석 二百五十인"), "한자 수사를 글에서 못 읽습니다."
    assert 25 in read_numbers("재석 스물다섯명")
    assert 250 in read_numbers("재석 ２５０인")
    assert 250 in read_numbers("재석 250인")
    # 한 글자짜리는 보통 낱말과 구분되지 않아 수로 읽지 않는다.
    assert read_numbers("사실 확인") == set()
    assert read_numbers("공식 자료") == set()


def test_인용_부호_안이_자료에_없으면_막는다(good_draft) -> None:
    """`QUOTE_NOT_IN_SOURCE`만 겨눈다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: d["paragraphs"][1].__setitem__(
                    "text", "개정 문구는 “있지도 않은 문장이다”라고 제안하고 있다."
                ),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "QUOTE_NOT_IN_SOURCE" in rules, rules


def test_부칙에_없는_시점을_시행_이야기에_쓰면_막는다(good_draft) -> None:
    """`EFFECTIVE_DATE_NOT_IN_RULE`만 겨눈다. 부칙 근거는 붙어 있게 둔다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: d["paragraphs"][-1].__setitem__(
                    "text", "부칙은 공포 후 6개월이 지난 날부터 시행하도록 제안하고 있다."
                ),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "EFFECTIVE_DATE_NOT_IN_RULE" in rules, rules


def test_육하원칙_열쇠말이_정해진_것이_아니면_막는다(good_draft) -> None:
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft, lambda d: d.__setitem__("six_w_status", {"누구": "OK"})
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "SIX_W_KEY_UNKNOWN" in rules, rules


def test_로마자_낱말도_자료에_있어야_한다(good_draft) -> None:
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: d["lead"].__setitem__(
                    "text", "Ministry of Culture announced the result."
                ),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "WORD_NOT_IN_LEDGER" in rules, rules


def test_흩어_쓴_글자만_붙여_보고_멀쩡한_말은_그대로_둔다() -> None:
    """`_join_scattered`만 겨눈다. 멀쩡한 띄어쓰기를 붙이면 거짓 차단이 늘어난다."""
    from app.gates.draft_gate import _join_scattered

    assert _join_scattered("김 영 수 장 관") == "김영수장관"
    assert _join_scattered("기부금품의 모집 및 사용에") == "기부금품의 모집 및 사용에"
    assert _join_scattered("자료 기준일은 2025-10-26") == "자료 기준일은 2025-10-26"


def test_덩어리를_조각으로_나눌_수_있는지_본다() -> None:
    """`_is_covered`만 겨눈다. 조사만으로 쪼개지면 지어낸 이름이 통과한다."""
    from app.gates.draft_gate import _is_covered

    haystack = "자료 기준일 본회의 의결"
    assert _is_covered("자료기준일은", haystack)
    assert not _is_covered("이지은", haystack), "조사만으로 쪼개 통과시켰습니다."
    assert not _is_covered("김영수장관", haystack)


@pytest.mark.asyncio
async def test_자료의_부칙을_초안이_빠뜨리면_막는다() -> None:
    """§2.16.4·§4.2. 적용례·경과조치·특례가 조용히 사라지면 안 된다."""
    payload = _draft_dict(await _run())
    # 부칙을 말하는 문단을 통째로 뺀다.
    payload["result"]["paragraphs"] = [
        p for p in payload["result"]["paragraphs"] if not p["supplementary_rule_ids"]
    ]
    run = await _run(canned_draft=payload)
    assert run.draft_version == 0, "부칙이 빠졌는데 초안이 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "SUPPLEMENTARY_RULE_DROPPED" in rules, rules


# ---------------------------------------------------------------------------
# 3차 검토가 뚫었던 공격
# ---------------------------------------------------------------------------

#: 눈에 보이지 않거나 글자를 갈라 놓는 문자들.
HIDDEN_SEPARATORS = [
    ("전각 공백", "　"),
    ("줄바꿈", "\n"),
    ("NBSP", " "),
    ("폭 없는 공백", "​"),
    ("폭 없는 비접합자", "‌"),
    ("낱말 이음표", "⁠"),
    ("가운뎃점", "ㆍ"),
]


@pytest.mark.parametrize(
    ("name", "separator"), HIDDEN_SEPARATORS, ids=[n for n, _ in HIDDEN_SEPARATORS]
)
def test_보이지_않는_문자로_낱말_검사를_끌_수_없다(good_draft, name, separator) -> None:
    """화면에는 `김영수`로 보이는데 검사에서만 흩어지는 것을 막는다."""
    text = f"해당 내용은 김{separator}영{separator}수 의원실이 확인한 사항이다."
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: _point(d, text)))
    )
    assert run.draft_version == 0, f"{name}: 지어낸 이름이 나갔습니다."


def test_자모가_분해된_글자도_붙여_본다(good_draft) -> None:
    import unicodedata

    text = unicodedata.normalize("NFD", "해당 내용은 김영수 의원실이 확인한 사항이다.")
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: _point(d, text)))
    )
    assert run.draft_version == 0


ATTACKS_V4 = [
    ("조각 재조합 국가지원단체", lambda d: _point(d, "국가지원단체가 확인한 내용이다.")),
    ("조각 재조합 전문예술위원회", lambda d: _point(d, "전문예술위원회가 확인한 내용이다.")),
    ("조각 재조합 국가문화진흥원", lambda d: _point(d, "국가문화진흥원이 확인한 내용이다.")),
    ("한자 흩어 쓴 표결 수", lambda d: _point(d, "본회의 표결 결과는 二 百 五 十이다.")),
    ("거짓 날짜", lambda d: _point(d, "본회의 의결일은 2025년 10월 18일이다.")),
    (
        "라고 했다",
        lambda d: _lead(d, "조계원 의원실은 “기부금품을 모집할 수 있다”라고 했다."),
    ),
    (
        "라며",
        lambda d: _lead(d, "조계원 의원실은 “기부금품을 모집할 수 있다”라며 개선을 제안했다."),
    ),
    (
        "설명이다",
        lambda d: _lead(d, "조계원 의원실의 설명이다. “기부금품을 모집할 수 있다”"),
    ),
    ("제목 비움", lambda d: _title(d, "")),
    ("리드 비움", lambda d: _lead(d, "")),
    ("본문 전부 비움", lambda d: [p.__setitem__("text", "") for p in d["paragraphs"]]),
    ("공백만 채운 제목", lambda d: _title(d, "   ")),
    ("제안한…확정됐다", lambda d: _title(d, "제안한 문화예술진흥법 개정이 확정됐다")),
    ("제안한 법률은 공포됐다", lambda d: _lead(d, "제안한 법률은 공포됐다.")),
    ("아직 이 법률은 시행된다", lambda d: _lead(d, "아직 이 법률은 시행된다.")),
    ("제안 내용은 현재 적용된다", lambda d: _point(d, "제안 내용은 현재 적용된다.")),
    ("한자 조문 제九조", lambda d: _point(d, "바뀐 조문은 제九조이다.")),
    ("원장 값 뒤집기 부결", lambda d: _point(d, "의안번호 2207285이(가) 부결로 처리되었다.")),
    ("원장 값 뒤집기 폐기", lambda d: _point(d, "의안번호 2207285이(가) 폐기되었다.")),
    ("원장 값 뒤집기 철회", lambda d: _point(d, "의안번호 2207285이(가) 철회되었다.")),
]


@pytest.mark.parametrize(
    ("name", "mutate"), ATTACKS_V4, ids=[a[0] for a in ATTACKS_V4]
)
def test_3차_검토가_뚫었던_공격은_이제_막힌다(good_draft, name, mutate) -> None:
    run = asyncio.run(_run(canned_draft=_spoil(good_draft, mutate)))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )


def test_근거로_댄_사실의_값이_문장에_있어야_한다(good_draft) -> None:
    """`CLAIM_VALUE_NOT_ANCHORED`만 겨눈다.

    낱말 목록으로는 절대 못 잡는 거짓말을 여기서 잡는다. 낱말도 수도 모두
    자료에 있지만 **자료가 말하는 값과 다른** 문장이다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: _point(d, "의안번호 2207285이(가) 부결로 처리되었다."),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "CLAIM_VALUE_NOT_ANCHORED" in rules, rules


def test_보이지_않는_문자를_찾아_이름을_말한다() -> None:
    """`draft_normalizer`만 겨눈다."""
    from app.gates.draft_normalizer import find_invisible, sanitize

    found = find_invisible("김​영​수")
    assert len(found) == 2
    assert "폭 없는 공백" in {name for _, _, name in found}
    assert sanitize("김​영​수") == "김영수"
    assert sanitize("재석 ２５０인") == "재석 250인"
    assert find_invisible("보통 글자입니다.") == []


def test_이름으로_쓴_개정은_주장이_아니다() -> None:
    """`ASSERTIVE_EFFECT`만 겨눈다. 이름까지 막으면 개정문을 옮길 수 없다."""
    from app.gates.draft_gate import ASSERTIVE_EFFECT

    assert ASSERTIVE_EFFECT.search("공포됐다")
    assert ASSERTIVE_EFFECT.search("시행된다")
    assert ASSERTIVE_EFFECT.search("확정됐다")
    assert not ASSERTIVE_EFFECT.search("개정 문구는"), "이름으로 쓴 것까지 막습니다."
    assert not ASSERTIVE_EFFECT.search("공포일")


def test_헤지는_효력_표현에_붙어_있어야_한다(good_draft) -> None:
    """헤지 규칙만 겨눈다.

    문장 아무 데나 `제안`이 있으면 되게 두면 `제안한 법률은 공포됐다`가
    통과한다. 아래 문장은 근거 값(`2207285`)을 담고 있어 값 대조는 지나가고,
    시행·공포를 말하지 않아 부칙 규칙도 지나간다. 헤지 규칙만 남는다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: _point(d, "제안한 의안번호 2207285 개정이 확정됐다."),
            )
        )
    )
    assert run.draft_version == 0, "먼 곳의 헤지로 통과시켰습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "PREMATURE_EFFECT_CLAIM" in rules, rules


def test_따옴표_앞에_말하는_주체가_있으면_막는다(good_draft) -> None:
    """발언 모양 규칙만 겨눈다.

    따옴표 안 문구는 자료에 그대로 있고, 근거 값도 담겨 있고, 발언 동사도
    없다. 남는 것은 "사람·기관 뒤에 따옴표가 온다"는 모양뿐이다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: _point(
                    d,
                    "의안번호 2207285 관련 조계원 의원실은 "
                    "“기부금품을 모집할 수 있다”",
                ),
            )
        )
    )
    assert run.draft_version == 0, "발언 모양을 놓쳤습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "STATEMENT_WITHOUT_SOURCE" in rules, rules


def test_보이지_않는_문자가_있으면_그_자체로_막는다(good_draft) -> None:
    """`INVISIBLE_CHARACTER`만 겨눈다.

    정리한 사본으로 검사하면 다른 규칙이 대신 잡아 주지만, 그 문자가 든 초안은
    **그 사실만으로도** 막아야 한다. 화면에 보이는 글과 검사하는 글이 달라지는
    것 자체가 위험하기 때문이다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                # 근거 값을 그대로 두고 보이지 않는 문자만 끼운다.
                lambda d: _point(d, "바뀐 조문은 제7​조이다."),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "INVISIBLE_CHARACTER" in rules, rules


# ---------------------------------------------------------------------------
# 4차 검토가 뚫었던 공격
# ---------------------------------------------------------------------------

#: 글자를 갈라 놓는 문자들. 유니코드 분류가 제각각이라 목록으로는 못 따라간다.
FORBIDDEN_CHARS = [
    ("결합 문자", "͏"),
    ("이체자 선택자", "︀"),
    ("결합 악센트", "́"),
    ("한글 채움", "ㅤ"),
    ("초성 채움", "ᅟ"),
    ("점자 빈칸", "⠀"),
]


@pytest.mark.parametrize(
    ("name", "char"), FORBIDDEN_CHARS, ids=[n for n, _ in FORBIDDEN_CHARS]
)
def test_쓸_수_없는_글자로_검사를_끌_수_없다(good_draft, name, char) -> None:
    """글자도 허용 목록으로 본다.

    못 쓸 문자를 세는 방식은 네 번 연속 졌다. 세상의 문자는 15만 자가 넘어
    끝이 없다. 그래서 **쓸 수 있는 글자만** 적고 나머지를 모두 막는다.
    """
    text = f"김{char}영{char}수 장관이 알린다."
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: _point(d, text)))
    )
    assert run.draft_version == 0, f"{name}: 지어낸 이름이 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "CHARACTER_NOT_ALLOWED" in rules, rules


ATTACKS_V5 = [
    ("한자 기관명", lambda d: _point(d, "文化體育觀光部가 함께 알린다.")),
    ("키릴 섞기", lambda d: _point(d, "МОCSТ가 함께 알린다.")),
    (
        "본문을 점자 빈칸으로",
        lambda d: [p.__setitem__("text", "⠀") for p in d["paragraphs"]],
    ),
    ("문의처를 점자 빈칸으로", lambda d: d.__setitem__("contact_text", "⠀")),
    (
        "본문을 한글 채움으로",
        lambda d: [p.__setitem__("text", "ㅤ") for p in d["paragraphs"]],
    ),
    ("공포됨", lambda d: _lead(d, "이 법률은 공포됨. 부칙은 다음과 같다.")),
    ("개정이 완료되었다", lambda d: _lead(d, "제7조 개정이 완료되었다.")),
    ("시행에 들어갔다", lambda d: _lead(d, "개정 내용은 시행에 들어갔다.")),
    ("제 여섯 조", lambda d: _point(d, "바뀐 조문은 제 여섯 조이다.")),
    ("第六條", lambda d: _point(d, "바뀐 조문은 第六條이다.")),
    (
        "〈〉 괄호 인용",
        lambda d: _lead(d, "조계원 의원실은 현재 〈제7조는 원안가결〉이라는 내용을 알린다."),
    ),
    (
        "말하는 이와 따옴표 사이 낱말",
        lambda d: _lead(
            d, "조계원 의원실은 현재 “기부금품 모집 및 접수가 가능하다”는 내용을 알린다."
        ),
    ),
    (
        "말하는 이를 따옴표 뒤에",
        lambda d: _lead(d, "“기부금품 모집 및 접수가 가능하다” — 조계원 의원실"),
    ),
    ("확정 본 (점자 빈칸)", lambda d: _title(d, "확정⠀본 문화예술진흥법")),
    (
        "개정 방향 뒤집기",
        lambda d: d["paragraphs"][1].__setitem__(
            "text",
            "바뀐 조문은 제7조이다. 제7조제6항 중 “모집ㆍ접수할”을 “모집할”로 한다.",
        ),
    ),
    (
        "부칙에 없는 다음 달 시행",
        lambda d: d["paragraphs"][-1].__setitem__(
            "text", "부칙에 따라 이 법은 다음 달 시행 예정이다."
        ),
    ),
    ("조각 재조합 국가지원단체", lambda d: _point(d, "국가지원단체가 함께 알린다.")),
    ("조계원 장관", lambda d: _point(d, "조계원 장관이 알린다.")),
]


@pytest.mark.parametrize(
    ("name", "mutate"), ATTACKS_V5, ids=[a[0] for a in ATTACKS_V5]
)
def test_4차_검토가_뚫었던_공격은_이제_막힌다(good_draft, name, mutate) -> None:
    run = asyncio.run(_run(canned_draft=_spoil(good_draft, mutate)))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )


def test_개정_문구는_통째로_자료에_있어야_한다(good_draft) -> None:
    """`QUOTED_PASSAGE_NOT_IN_SOURCE`만 겨눈다.

    따옴표 하나하나는 자료에 있어도 **순서를 바꾸면** 개정 방향이 뒤집힌다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: d["paragraphs"][1].__setitem__(
                    "text",
                    "바뀐 조문은 제7조이다. "
                    "제7조제6항 중 “모집ㆍ접수할”을 “모집할”로 한다.",
                ),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "QUOTED_PASSAGE_NOT_IN_SOURCE" in rules, rules


def test_부칙에_없는_시점_표현을_막는다(good_draft) -> None:
    """`TIME_WORDS`만 겨눈다. 수가 없는 `다음 달`은 숫자 대조로 안 걸린다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: d["paragraphs"][-1].__setitem__(
                    "text", "부칙에 따라 이 법은 다음 달 시행하도록 제안하고 있다."
                ),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "EFFECTIVE_DATE_NOT_IN_RULE" in rules, rules


def test_자료에_있는_글자는_쓸_수_있다() -> None:
    """글자 허용 목록이 자료를 따라 늘어나는지 본다.

    자료에 한자가 있으면 초안도 쓸 수 있어야 한다. 목록을 손으로 늘리는 것이
    아니라 자료가 정한다.
    """
    from app.gates.draft_charset import allowed_characters, find_forbidden

    allowed = allowed_characters("의안번호 2207285 모집ㆍ접수")
    assert find_forbidden("모집ㆍ접수할 수 있다", allowed) == []
    assert find_forbidden("김͏영수", allowed), "결합 문자를 놓쳤습니다."
    assert find_forbidden("文化", allowed), "자료에 없는 한자를 놓쳤습니다."

    한자_자료 = allowed_characters("法律 제정")
    assert find_forbidden("法律", 한자_자료) == [], "자료에 있는 한자를 막았습니다."


def test_한_글자로_깎인_줄기는_설명이_되지_않는다() -> None:
    """`_is_grounded_word`의 한 글자 규칙만 겨눈다."""
    from app.gates.draft_gate import _is_grounded_word

    haystack = "의안번호 2207285 본회의 의결"
    assert _is_grounded_word("의안번호를", haystack)
    assert not _is_grounded_word("이지은", haystack), "한 글자로 깎아 통과시켰습니다."
    assert not _is_grounded_word("박서은", haystack)


def test_처리_결과_낱말은_허용_목록에_없다() -> None:
    """결과값이 허용 낱말에 있으면 자료와 반대되는 문장이 통과한다."""
    from app.gates.draft_vocabulary import SAFE_WORDS

    for word in ("의결", "가결", "부결", "폐기", "철회", "통과"):
        assert word not in SAFE_WORDS, (
            f"`{word}`은(는) 처리 결과값입니다. 자료에 적혀 있을 때만 쓸 수 있어야 합니다."
        )


def test_따옴표_없이_발언을_옮겨도_막는다(good_draft) -> None:
    """`ATTRIBUTION`만 겨눈다.

    아래 문장은 근거 값(`원안가결`)을 담고 있어 값 대조를 지나가고, 따옴표가
    없어 인용·발언 모양 검사도 지나간다. 남는 것은 발언 낱말 검사뿐이다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: _point(d, "원안가결로 처리되었다고 밝혔다."),
            )
        )
    )
    assert run.draft_version == 0, "발언 옮기기를 놓쳤습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "STATEMENT_WITHOUT_SOURCE" in rules, rules
