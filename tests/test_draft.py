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


#: 고정 자료의 부칙 원문. Harness가 이 글을 **그대로** 옮겨야 한다.
_LEDGER_RULE_TEXTS = ("이 법은 공포한 날부터 시행한다.",)


def _ai_paragraphs(d: dict) -> list[dict]:
    """AI가 쓴 문단만 고른다.

    양식이 정한 `DRAFT_MARK`·`BASIS_AND_STATUS`·`ANNOUNCER_AND_RELEASE`·
    `CONTACT` 네 자리는 Harness가 직접 만든다(`HS-`). 그 문단을 오염시켜도
    Harness가 받은 초안에서 걷어내므로 공격이 되지 않는다. 공격은 **AI가 쓰는
    자리**에 넣어야 뜻이 있다.
    """
    return [p for p in d["paragraphs"] if not p["paragraph_id"].startswith("HS-")]

def _spoil(good: dict, mutate) -> dict:
    payload = copy.deepcopy(good)
    mutate(payload["result"])
    return payload


ATTACKS = [
    (
        "없는 표결 수를 지어낸다",
        lambda d: _ai_paragraphs(d)[0].__setitem__(
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
        # 부칙은 Harness 몫이라 AI에게는 뗄 근거가 없다. 대신 AI가 **직접
        # 부칙 자리를 쓰려는** 것을 찌른다. 걷어내지 못하면 자료에 없는
        # 시행일이 부칙 행세를 하고 나간다.
        "부칙 근거 없이 시행일을 말한다",
        lambda d: _ai_paragraphs(d)[-1].__setitem__(
            "text", "부칙에 따라 공포 후 6개월이 지난 날부터 시행하도록 제안하고 있다."
        ),
        "PREMATURE_EFFECT_CLAIM",
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
    for paragraph in _ai_paragraphs(payload["result"]):
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
    _ai_paragraphs(payload["result"])[-1]["supplementary_rule_ids"] = ["SR-없음"]
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
    ("부칙 ID는 두고 내용만 바꿈", lambda d: _ai_paragraphs(d)[-1].__setitem__("text", "공포 후 6개월이 지난 날부터 시행된다.")),
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


# 공격 문장을 **덧붙인다. 갈아치우지 않는다.**
#
# 전에는 갈아치웠다. 그러면 글은 바뀌는데 근거(`claim_ids`·`fact_ids`)는 그대로
# 남아 글과 근거가 안 맞고, `CLAIM_VALUE_NOT_ANCHORED`가 **무슨 문장을 넣든**
# 걸린다. 그래서 멀쩡한 문장을 넣어도 초안이 안 나오고, `초안이 안 나왔다`만
# 보는 시험은 전부 통과한다 — **아무것도 지키지 못하는 시험**이 된다.
#
# 12차 검토가 이런 시험을 67개 찾았다. 덧붙이면 원래 글과 근거가 그대로 있어서
# 대조군이 통과하고, 막혔다면 그것은 **덧붙인 문장 때문**이다.
#
# 이 성질은 `test_도우미는_멀쩡한_문장을_막지_않는다`가 지킨다.


def _lead(d: dict, text: str) -> None:
    d["lead"]["text"] += " " + text


def _title(d: dict, text: str) -> None:
    d["title"]["text"] += " " + text


def _point(d: dict, text: str) -> None:
    d["key_points"][1]["text"] += " " + text


def _append(d: dict, text: str) -> None:
    _ai_paragraphs(d)[0]["text"] += " " + text


# --- 갈아치우는 도우미 -------------------------------------------------------
#
# **비우기**나 **근거가 말하는 값을 지우기**처럼 갈아치워야만 성립하는 공격이
# 있다. 덧붙이기로는 표현할 수 없다.
#
# 다만 갈아치우면 글과 근거가 어긋나 `CLAIM_VALUE_NOT_ANCHORED`가 무슨 문장을
# 넣든 걸린다. 그래서 **이 도우미를 쓰는 시험은 규칙 이름까지 확인해야 한다.**
# `초안이 안 나왔다`만 보면 아무것도 지키지 못한다.


def _set_title(d: dict, text: str) -> None:
    d["title"]["text"] = text


def _set_lead(d: dict, text: str) -> None:
    d["lead"]["text"] = text


def _set_point(d: dict, text: str) -> None:
    d["key_points"][1]["text"] = text


def _append_body(d: dict, text: str) -> None:
    """마지막 AI 문단에 덧붙인다. 근거는 건드리지 않는다."""
    _ai_paragraphs(d)[-1]["text"] += " " + text


#: 대조군 문장. 자료에 있는 말로만 만들었고 아무 규칙도 어기지 않는다.
#: 이 문장을 넣었는데 초안이 막히면, 그 도우미를 쓰는 시험은 공격이 아니라
#: **도우미 자체**를 재고 있는 것이다.
_HARMLESS = "이번 자료의 내용이다."


@pytest.mark.parametrize(
    "helper",
    (_lead, _title, _point, _append, _append_body),
    ids=("lead", "title", "point", "append", "append_body"),
)
def test_도우미는_멀쩡한_문장을_막지_않는다(good_draft, helper) -> None:
    """공격 시험이 **판별력을 갖는지** 지킨다.

    도우미가 멀쩡한 문장까지 막으면 `초안이 안 나왔다`만 보는 시험은 공격
    문장이 무엇이든 통과한다. 그런 시험은 방어가 죽어도 초록불을 낸다.

    이 시험이 깨지면 그 도우미를 쓰는 공격 시험을 **전부 믿을 수 없다.**
    """
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: helper(d, _HARMLESS)))
    )
    rules = sorted(
        f.rule_id for f in run.validation_findings if f.severity.value == "BLOCKING"
    )
    assert run.draft_version >= 1, (
        f"{helper.__name__}: 멀쩡한 문장인데 막혔습니다 {rules}. "
        "이 도우미를 쓰는 공격 시험은 아무것도 지키지 못합니다."
    )


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
        lambda d: _ai_paragraphs(d)[0].__setitem__(
            "paragraph_id", "재석 250인 중 찬성 249인, 김영수 장관 발표"
        ),
    ),
    ("초안 번호에 거짓", lambda d: d.__setitem__("candidate_id", "재석 250인")),
    (
        "문단 종류에 거짓",
        lambda d: _ai_paragraphs(d)[0].__setitem__("section_kind", "김영수 장관 발표"),
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
                lambda d: _ai_paragraphs(d)[1].__setitem__(
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
                lambda d: _rule_paragraph(d, "부칙은 공포 후 6개월이 지난 날부터 시행하도록 제안하고 있다."),
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
async def test_AI는_자료의_부칙을_빠뜨릴_수_없다() -> None:
    """§2.16.4·§4.2. 적용례·경과조치·특례가 조용히 사라지면 안 된다.

    전에는 AI가 부칙 문단을 썼고, 빠뜨리면 `SUPPLEMENTARY_RULE_DROPPED`가
    **막았다.** 이제는 Harness가 그 자리를 만들므로 AI가 아예 **빠뜨릴 수
    없다.** 막는 것에서 못 하는 것으로 바뀌었다.

    그래서 이 시험은 "막혔는가"가 아니라 "**AI가 지워도 남아 있는가**"를 본다.
    """
    payload = _draft_dict(await _run())
    # AI가 부칙을 말하는 문단을 통째로 뺀다.
    payload["result"]["paragraphs"] = [
        p
        for p in payload["result"]["paragraphs"]
        if not p["supplementary_rule_ids"]
    ]
    run = await _run(canned_draft=payload)
    assert run.draft is not None, "정상 자료인데 초안이 나오지 않았습니다."
    kept = [p for p in run.draft.paragraphs if p.supplementary_rule_ids]
    assert kept, "AI가 지우자 부칙이 사라졌습니다."
    for rule in _LEDGER_RULE_TEXTS:
        assert any(rule in p.text for p in kept), (
            f"부칙 원문이 그대로 남아 있지 않습니다: {rule}"
        )


def test_부칙을_아무도_말하지_않으면_막는다() -> None:
    """`SUPPLEMENTARY_RULE_DROPPED`가 아직 살아 있는지 본다.

    Harness가 부칙 자리를 만들므로 정상 흐름에서는 이 규칙이 걸리지 않는다.
    그래도 계약에 `SUPPLEMENTARY` 자리가 없는 양식이 오면 이 규칙만 남는다.
    """
    from app.gates.draft_template import HARNESS_OWNED

    assert "SUPPLEMENTARY" in HARNESS_OWNED, (
        "부칙이 다시 AI 몫이 되었습니다. 그러면 자료를 베끼고 어미만 바꿀 수 있습니다."
    )


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


#: 낱말 검사가 흩어진 글자를 못 붙이는 자리. 근거 대조가 대신 막는다.
#: 방어가 하나뿐이라 그 하나가 죽으면 뚫린다. `남은 일`(중 / 5일차).
WORD_CHECK_BLIND_SPOTS = {"줄바꿈", "가운뎃점"}


@pytest.mark.parametrize(
    ("name", "separator"), HIDDEN_SEPARATORS, ids=[n for n, _ in HIDDEN_SEPARATORS]
)
def test_보이지_않는_문자로_낱말_검사를_끌_수_없다(good_draft, name, separator) -> None:
    """화면에는 `김영수`로 보이는데 검사에서만 흩어지는 것을 막는다.

    갈아치우는 도우미를 쓰므로 **겨누는 규칙까지** 확인한다. `초안이 안
    나왔다`만 보면 낱말 검사가 죽어도 통과한다 — 실제로 그랬다(12차 검토).

    **낱말 검사가 못 잡는 자리가 둘 있다.** 줄바꿈과 가운뎃점이다. 지금은
    근거 대조(`CLAIM_VALUE_NOT_ANCHORED`)가 대신 받아내서 초안은 나가지
    않는다. 숨기지 않고 여기 적어 둔다. `남은 일`이며 5일차에 다룬다.
    """
    text = f"해당 내용은 김{separator}영{separator}수 의원실이 확인한 사항이다."
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: _set_point(d, text)))
    )
    assert run.draft_version == 0, f"{name}: 지어낸 이름이 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    expected = (
        "CLAIM_VALUE_NOT_ANCHORED"
        if name in WORD_CHECK_BLIND_SPOTS
        else "WORD_NOT_IN_LEDGER"
    )
    assert expected in rules, f"{name}: {rules}"


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
    ("제목 비움", lambda d: _set_title(d, "")),
    ("리드 비움", lambda d: _set_lead(d, "")),
    ("본문 전부 비움", lambda d: [p.__setitem__("text", "") for p in _ai_paragraphs(d)]),
    ("공백만 채운 제목", lambda d: _set_title(d, "   ")),
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
                lambda d: _set_point(d, "의안번호 2207285이(가) 부결로 처리되었다."),
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


def test_이름으로_쓴_개정은_주장이_아니다(good_draft) -> None:
    """이름까지 막으면 개정문을 옮길 수 없다.

    전에는 `ASSERTIVE_EFFECT`라는 어미 목록을 직접 봤다. 그 목록은 이제 문을
    여는 조건이 아니므로, 시험도 **초안이 실제로 나가는지**를 본다. 상수를
    보는 시험은 상수가 죽어도 계속 통과해 아무것도 지키지 못한다.
    """
    # 이름으로 쓴 자리는 정상 초안에 이미 들어 있다. 초안이 나오는 것이 곧
    # 이름을 막지 않는다는 증거다.
    assert asyncio.run(_run()).draft_version >= 1

    for sentence in ("이 법률은 공포됐다.", "이 법률은 시행된다.", "이 내용은 확정됐다."):
        run = asyncio.run(
            _run(canned_draft=_spoil(good_draft, lambda d: _rule_paragraph(d, sentence)))
        )
        assert run.draft_version == 0, f"`{sentence}` 가 그대로 나갔습니다."
        rules = {f.rule_id for f in run.validation_findings}
        assert "PREMATURE_EFFECT_CLAIM" in rules, rules


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
        lambda d: [p.__setitem__("text", "⠀") for p in _ai_paragraphs(d)],
    ),
    ("문의처를 점자 빈칸으로", lambda d: d.__setitem__("contact_text", "⠀")),
    (
        "본문을 한글 채움으로",
        lambda d: [p.__setitem__("text", "ㅤ") for p in _ai_paragraphs(d)],
    ),
    ("공포됨", lambda d: _lead(d, "이 법률은 공포됨. 부칙은 다음과 같다.")),
    ("개정이 완료되었다", lambda d: _lead(d, "제7조 개정이 완료되었다.")),
    ("시행에 들어갔다", lambda d: _lead(d, "개정 내용은 시행에 들어갔다.")),
    ("제 여섯 조", lambda d: _set_point(d, "바뀐 조문은 제 여섯 조이다.")),
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
        lambda d: _ai_paragraphs(d)[1].__setitem__(
            "text",
            "바뀐 조문은 제7조이다. 제7조제6항 중 “모집ㆍ접수할”을 “모집할”로 한다.",
        ),
    ),
    (
        "부칙에 없는 다음 달 시행",
        lambda d: _ai_paragraphs(d)[-1].__setitem__(
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
                lambda d: _ai_paragraphs(d)[1].__setitem__(
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
                lambda d: _rule_paragraph(d, "부칙에 따라 이 법은 다음 달 시행하도록 제안하고 있다."),
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


# ---------------------------------------------------------------------------
# 5차 검토가 뚫었던 공격
# ---------------------------------------------------------------------------

ATTACKS_V6 = [
    (
        "흩어 쓴 한글 수사",
        lambda d: _point(d, "의안번호 2207285 표결 결과는 이 백 오 십 표이다."),
    ),
    (
        "흩어 쓴 한자 수사",
        lambda d: _point(d, "의안번호 2207285 표결 결과는 二 百 五 十 표이다."),
    ),
    ("흩어 쓴 삼 백", lambda d: _point(d, "의안번호 2207285 표결 결과는 삼 백 표이다.")),
    (
        "하이픈 날짜 리드",
        lambda d: _lead(
            d,
            "조계원 의원실은 2025-06-18 의안번호 2207285이(가) 원안가결로 "
            "처리된 사실을 알린다.",
        ),
    ),
    (
        "하이픈 날짜 본문",
        lambda d: _ai_paragraphs(d)[0].__setitem__(
            "text", "의안번호 2207285의 의결일은 2025-09-26이다."
        ),
    ),
    ("하이픈 날짜 기준일", lambda d: d.__setitem__("basis_date", "2025-06-01")),
    (
        "조각 조립 문화예술법인",
        lambda d: _ai_paragraphs(d)[0].__setitem__(
            "text", "문화예술법인은 의안번호 2207285을(를) 알린다."
        ),
    ),
    (
        "조각 조립 전문예술위원회",
        lambda d: _ai_paragraphs(d)[0].__setitem__(
            "text", "전문예술위원회는 의안번호 2207285을(를) 알린다."
        ),
    ),
    (
        "조각 조립 조계원장",
        lambda d: _ai_paragraphs(d)[0].__setitem__(
            "text", "조계원장은 의안번호 2207285을(를) 알린다."
        ),
    ),
    (
        "따옴표 없이 개정 방향 뒤집기",
        lambda d: _ai_paragraphs(d)[1].__setitem__(
            "text", "제7조제6항 중 모집ㆍ접수할을 모집할로 한다."
        ),
    ),
    (
        "따옴표 하나로 뒤집기",
        lambda d: _ai_paragraphs(d)[1].__setitem__(
            "text", "제7조제6항 중 “모집ㆍ접수할”을 모집할로 한다."
        ),
    ),
    (
        "원안ㆍ가결",
        lambda d: _ai_paragraphs(d)[0].__setitem__(
            "text", "의안번호 2207285은(는) 원안ㆍ가결로 처리되었다."
        ),
    ),
    (
        "헤지로 문장 건너뛰기",
        lambda d: _ai_paragraphs(d)[-1].__setitem__("text", "적용 완료 예정이며 공포되었다."),
    ),
    (
        "헤지 앞세우고 시행되었다",
        lambda d: _ai_paragraphs(d)[-1].__setitem__(
            "text", "적용 완료 예정이며 이 법은 시행되었다."
        ),
    ),
    ("시행 단계다", lambda d: _ai_paragraphs(d)[-1].__setitem__("text", "이 법은 시행 단계다.")),
    (
        "공포에 이르렀다",
        lambda d: _ai_paragraphs(d)[-1].__setitem__("text", "이 법은 공포에 이르렀다."),
    ),
]


@pytest.mark.parametrize(
    ("name", "mutate"), ATTACKS_V6, ids=[a[0] for a in ATTACKS_V6]
)
def test_5차_검토가_뚫었던_공격은_이제_막힌다(good_draft, name, mutate) -> None:
    run = asyncio.run(_run(canned_draft=_spoil(good_draft, mutate)))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )


def test_근거로_댄_사실은_전부_문장에_있어야_한다(good_draft) -> None:
    """`CLAIM_VALUE_NOT_ANCHORED`가 **하나만 맞으면 통과**시키지 않는지 본다.

    짧은 값 하나를 대 놓고 나머지를 거짓으로 채우는 우회를 막는다.
    """

    def mutate(d: dict) -> None:
        d["key_points"][1]["text"] = "의안번호 2207285의 처리결과는 제7조이다."
        d["key_points"][1]["fact_ids"] = ["F-06", "F-02"]

    run = asyncio.run(_run(canned_draft=_spoil(good_draft, mutate)))
    assert run.draft_version == 0, "근거 하나만 맞추고 통과했습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "CLAIM_VALUE_NOT_ANCHORED" in rules, rules


def test_부칙을_근거로_대면_그_원문을_담아야_한다(good_draft) -> None:
    """`RULE_VALUE_NOT_ANCHORED`만 겨눈다.

    부칙 번호만 붙여 놓고 전혀 다른 시행 이야기를 쓰는 것을 막는다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: _rule_paragraph(d, "부칙에 따라 곧 적용될 전망이다."),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "RULE_VALUE_NOT_ANCHORED" in rules, rules


def test_흩어_쓴_수사도_수로_읽는다() -> None:
    """수 검사와 낱말 검사가 서로 상대를 믿고 둘 다 안 보던 자리."""
    from app.gates.draft_gate import _join_scattered
    from app.gates.numeral_reader import read_numbers

    assert read_numbers("이 백 오 십") == set(), "붙이지 않으면 못 읽는 것이 맞다"
    assert 250 in read_numbers(_join_scattered("이 백 오 십")), "붙여도 못 읽습니다."


def test_하이픈_날짜를_조각으로_흩지_않는다() -> None:
    from app.gates.numeral_reader import read_numbers

    assert 20250618 in read_numbers("2025-06-18"), "하이픈에서 끊어 읽었습니다."


def test_자료_조각을_이어_붙여_없는_낱말을_만들_수_없다() -> None:
    """`_is_covered`가 뜻 조각을 하나만 허용하는지 본다."""
    from app.gates.draft_gate import _is_covered

    haystack = "문화예술 진흥 전문예술법인 위원회 국가 지원"
    assert _is_covered("문화예술을", haystack)
    assert not _is_covered("문화예술법인", haystack), "조각을 이어 붙였습니다."
    assert not _is_covered("국가지원단체", haystack)


def test_빈칸_표시에_흩어_쓴_수를_넣어도_막는다(good_draft) -> None:
    """수 검사만 겨눈다. 빈칸 표시는 근거 대조를 받지 않는 칸이다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft, lambda d: d.__setitem__("placeholders", ["이 백 오 십 표"])
            )
        )
    )
    assert run.draft_version == 0, "흩어 쓴 수를 놓쳤습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "NUMBER_NOT_IN_LEDGER" in rules, rules


def test_한_문장의_효력_표현을_모두_본다(good_draft) -> None:
    """앞의 표현을 헤지로 덮어 뒤쪽 주장을 빼내는 것을 막는다.

    아래 문장은 근거 값(`2207285`)을 담아 값 대조를 지나가고, `시행`·`공포`가
    없어 부칙 규칙도 지나간다. 남는 것은 효력 표현 전수 검사뿐이다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: _point(d, "의안번호 2207285 적용 완료 예정이며 확정되었다."),
            )
        )
    )
    assert run.draft_version == 0, "뒤쪽 주장을 검사에서 빼냈습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "PREMATURE_EFFECT_CLAIM" in rules, rules


# ---------------------------------------------------------------------------
# 양식(Writing Contract `template.yaml`)을 따르는가
# ---------------------------------------------------------------------------


def _all_text(run) -> str:
    return "\n".join(p.text for p in run.draft.paragraphs)


@pytest.mark.asyncio
async def test_계약이_요구하는_표시가_초안에_모두_있다() -> None:
    """§2.16.2. 화면과 초안에 함께 표시해야 하는 문구들."""
    run = await _run()
    assert run.draft is not None
    whole = _all_text(run)
    for mark in (
        "DRAFT / 내부 검토용",
        "제공된 공식 자료 기준: 2025-10-26",
        "절차 단계",
        "효력 상태: 아직 법률 아님",
        "※ 시스템이 인터넷에서 최신 상태를 별도로 확인한 것은 아닙니다.",
    ):
        assert mark in whole, f"필수 표시가 없습니다: {mark}"


@pytest.mark.asyncio
async def test_문단_종류가_계약과_같은_이름을_쓴다() -> None:
    """코드에 옮겨 적으면 계약과 갈라진다. 실제로 네 번 연속 갈라져 있었다."""
    from app.gates.draft_template import load_template
    from app.harness.contract_loader import load_writing_contract

    template = load_template(load_writing_contract().template)
    run = await _run()
    assert run.draft is not None
    for paragraph in run.draft.paragraphs:
        assert paragraph.section_kind in template.section_kinds, (
            f"계약에 없는 문단 종류입니다: {paragraph.section_kind}"
        )
    kinds = {p.section_kind for p in run.draft.paragraphs}
    assert "KEY_POINT" not in kinds, "계약은 `KEY_POINTS`를 씁니다."
    assert "NEXT_STEP" not in kinds, "계약은 `NEXT_PROCEDURE`를 씁니다."


@pytest.mark.asyncio
async def test_값이_정해진_자리는_AI가_쓸_수_없다() -> None:
    """`CONTACT`처럼 값이 이미 정해진 자리에 AI가 쓴 글은 받지 않는다."""
    payload = _draft_dict(await _run())
    payload["result"]["paragraphs"].append(
        {
            "paragraph_id": "P-09",
            "section_kind": "CONTACT",
            "priority_rank": 9,
            "text": "문의처: 02-1234-5678",
            "claim_ids": [],
            "fact_ids": [],
            "supplementary_rule_ids": [],
        }
    )
    run = await _run(canned_draft=payload)
    assert run.draft is not None, "정상 초안까지 막았습니다."
    assert "02-1234-5678" not in _all_text(run), "AI가 쓴 문의처가 초안에 남았습니다."


@pytest.mark.asyncio
async def test_Harness_이름표를_흉내_낼_수_없다() -> None:
    """`HS-` 이름표를 달아 검사를 건너뛰려는 시도를 막는다."""
    payload = _draft_dict(await _run())
    payload["result"]["paragraphs"].append(
        {
            "paragraph_id": "HS-09",
            "section_kind": "BODY",
            "priority_rank": 9,
            "text": "재석 250인 중 찬성 249인이었다.",
            "claim_ids": [],
            "fact_ids": [],
            "supplementary_rule_ids": [],
        }
    )
    run = await _run(canned_draft=payload)
    assert run.draft is not None
    assert "250인" not in _all_text(run), "흉내 낸 문단이 초안에 남았습니다."


@pytest.mark.asyncio
async def test_양식이_만들지_않기로_한_문단은_막는다() -> None:
    payload = _draft_dict(await _run())
    payload["result"]["paragraphs"].append(
        {
            "paragraph_id": "P-08",
            "section_kind": "SUBTITLE",
            "priority_rank": 8,
            "text": "의안번호 2207285 부제입니다",
            "claim_ids": [],
            "fact_ids": ["F-06"],
            "supplementary_rule_ids": [],
        }
    )
    run = await _run(canned_draft=payload)
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "FORBIDDEN_SECTION" in rules, rules


ATTACKS_V7 = [
    (
        "근거 0개 문단에 거짓",
        lambda d: _ai_paragraphs(d)[0].update(
            {"text": "의안번호 2207285은 원안가결이 아니다.", "fact_ids": [], "claim_ids": []}
        ),
    ),
    (
        "근거 0개 + 거짓 날짜",
        lambda d: _ai_paragraphs(d)[0].update(
            {"text": "본회의 의결일은 2025. 9. 18이다.", "fact_ids": [], "claim_ids": []}
        ),
    ),
    (
        "근거 0개 + 개정 뒤집기",
        lambda d: _ai_paragraphs(d)[0].update(
            {
                "text": "제7조제6항 중 모집ㆍ접수할이 모집할로 바뀐다.",
                "fact_ids": [],
                "claim_ids": [],
            }
        ),
    ),
    (
        "근거 문구에서 뽑은 표결 수",
        lambda d: _ai_paragraphs(d)[0].__setitem__("text", "본회의 표결 결과는 18표이다."),
    ),
    (
        "낱말 경계 자르기",
        lambda d: _ai_paragraphs(d)[0].__setitem__(
            "text", "계원의원실은 이번 법률안을 검토한다."
        ),
    ),
    (
        "콜론 뒤 인용",
        lambda d: _lead(d, "조계원 의원실: “기부금품을 모집할 수 있다”"),
    ),
    (
        "따옴표 없는 전언",
        lambda d: _lead(d, "조계원 의원실은 기부금품을 모집할 수 있다고 알린다."),
    ),
    (
        "헤지로 개정 완료 선언",
        lambda d: _ai_paragraphs(d)[-1].__setitem__(
            "text", "제7조 개정이 완료되어 예정된 절차가 종료되었다."
        ),
    ),
    (
        "효력으로 시행일 말하기",
        lambda d: _ai_paragraphs(d)[0].__setitem__(
            "text", "이 법은 2025. 10. 26부터 효력이 있다."
        ),
    ),
    ("기준일 조작", lambda d: d.__setitem__("basis_date", "2025. 9. 18")),
]


@pytest.mark.parametrize(
    ("name", "mutate"), ATTACKS_V7, ids=[a[0] for a in ATTACKS_V7]
)
def test_6차_검토가_뚫었던_공격은_이제_막힌다(good_draft, name, mutate) -> None:
    run = asyncio.run(_run(canned_draft=_spoil(good_draft, mutate)))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )


# ---------------------------------------------------------------------------
# 7차 검토가 뚫었던 공격
# ---------------------------------------------------------------------------

ATTACKS_V8 = [
    (
        "끝맺음만 바꿔 개정 완료 선언",
        lambda d: _title(d, "의안번호 2207285 개정이 완료되어 다음 절차는 예정이다"),
    ),
    (
        "끝맺음만 바꿔 공포 선언",
        lambda d: _ai_paragraphs(d)[-1].__setitem__(
            "text",
            "부칙은 “이 법은 공포한 날부터 시행한다.”라고 제안하고 있다. "
            "개정이 완료되어 이 법은 공포된 뒤 다음 절차는 예정이다.",
        ),
    ),
    (
        "시행되었다고 제안한다",
        lambda d: _ai_paragraphs(d)[-1].__setitem__(
            "text", "이 법은 공포된 뒤 시행되었다고 제안한다."
        ),
    ),
    (
        "빈칸 표시에 효력 주장",
        lambda d: d.__setitem__(
            "placeholders", ["의안번호 2207285 개정이 완료되어 다음 절차는 예정이다"]
        ),
    ),
    (
        "허용된 수를 모아 만든 날짜",
        lambda d: d["key_points"][0].__setitem__(
            "text", "의안번호 2207285은 2025년 6월 7일 처리되었다."
        ),
    ),
    (
        "허용된 수를 모아 만든 표결 수",
        lambda d: d["key_points"][0].__setitem__(
            "text", "의안번호 2207285은 26명 중 7명이 원안가결로 처리했다."
        ),
    ),
    ("문의처에 거짓 날짜", lambda d: d.__setitem__("contact_text", "[문의처 확인 필요] 2025년 6월 7일")),
    (
        "문장을 쪼개 발언 만들기",
        lambda d: _ai_paragraphs(d)[0].__setitem__(
            "text", "조계원 의원실. “기부금품을 모집할 수 있다”. 원안가결."
        ),
    ),
    (
        "근거는 대되 삭제되었다고 쓰기",
        lambda d: d["key_points"][1].update(
            {"text": "제7조는 삭제되었다.", "fact_ids": ["F-03"]}
        ),
    ),
    (
        "따옴표 한 쌍으로 개정 방향 뒤집기",
        lambda d: _ai_paragraphs(d)[1].__setitem__(
            "text", "제7조제6항의 “모집ㆍ접수할”이 모집할로 바뀐다."
        ),
    ),
]


@pytest.mark.parametrize(
    ("name", "mutate"), ATTACKS_V8, ids=[a[0] for a in ATTACKS_V8]
)
def test_7차_검토가_뚫었던_공격은_이제_막힌다(good_draft, name, mutate) -> None:
    run = asyncio.run(_run(canned_draft=_spoil(good_draft, mutate)))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )


def test_헤지는_주장_바로_뒤에_붙어야_한다(good_draft) -> None:
    """문장 끝맺음만 보면 뜻은 그대로 두고 마지막 네 글자만 바꿔 뚫린다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                # 제목은 부칙을 가리키지 않으므로 부칙 대조가 걸리지 않는다.
                # 근거 값(`2207285`)을 담아 값 대조도 지나간다. 남는 것은
                # 헤지 규칙뿐이다.
                lambda d: _title(
                    d, "의안번호 2207285 개정이 완료되어 다음 절차는 예정이다"
                ),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "PREMATURE_EFFECT_CLAIM" in rules, rules


def test_날짜는_통째로_자료에_있어야_한다(good_draft) -> None:
    """`DATE_NOT_IN_LEDGER`만 겨눈다.

    수를 집합으로만 보면 자료의 `2025`·`6`·`7`을 모아 자료에 없는 날짜를
    만들 수 있다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft, lambda d: d.__setitem__("placeholders", ["2025년 6월 7일"])
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "DATE_NOT_IN_LEDGER" in rules, rules


def test_세는_수는_단위까지_자료에_있어야_한다(good_draft) -> None:
    """`COUNT_NOT_IN_LEDGER`만 겨눈다. `26`이 있다고 `26명`을 쓸 수는 없다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft, lambda d: d.__setitem__("placeholders", ["26명"])
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "COUNT_NOT_IN_LEDGER" in rules, rules


def test_인용은_어느_문서에서_왔는지_밝혀야_한다(good_draft) -> None:
    """`QUOTE_WITHOUT_DOCUMENT`만 겨눈다. 문장을 쪼개 화자를 옮기는 우회를 막는다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: _ai_paragraphs(d)[0].__setitem__(
                    "text", "의안번호 2207285. “기부금품을 모집할 수 있다”."
                ),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "QUOTE_WITHOUT_DOCUMENT" in rules, rules


def test_조문에_무엇을_했다는_주장은_개정문과_대조한다(good_draft) -> None:
    """근거 값만 넣고 딴말을 쓰는 것을 막는다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: d["key_points"][1].update(
                    {"text": "제7조는 신설되었다.", "fact_ids": ["F-03"]}
                ),
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "QUOTED_PASSAGE_NOT_IN_SOURCE" in rules, rules


# ---------------------------------------------------------------------------
# 8차 검토가 뚫었던 공격
# ---------------------------------------------------------------------------


def _with_body(good: dict, text: str, fact_id: str = "F-06") -> dict:
    """AI 문단을 하나 더해 오염시킨다."""
    payload = copy.deepcopy(good)
    payload["result"]["paragraphs"].append(
        {
            "paragraph_id": "P-09",
            "section_kind": "BODY",
            "priority_rank": 9,
            "text": text,
            "claim_ids": [],
            "fact_ids": [fact_id],
            "supplementary_rule_ids": [],
        }
    )
    return payload


ATTACKS_V9 = [
    ("셈에 조사 붙이기", "의안번호 2207285에 따라 의원 26명이 지원한다.", "F-06"),
    ("자리값 26만 명", "의안번호 2207285에 따라 26만 명을 지원한다.", "F-06"),
    ("자리값 26억 원", "의안번호 2207285에 따라 26억 원이다.", "F-06"),
    ("퍼센트", "의안번호 2207285에 따라 26퍼센트가 가능하다.", "F-06"),
    ("부분 날짜 6월 7일", "의안번호 2207285 의결일은 6월 7일이다.", "F-06"),
    ("부분 날짜 2025년 10월", "의안번호 2207285 의결일은 2025년 10월이다.", "F-06"),
    ("이름 조각 계원", "의안번호 2207285은 계원이 확인한다.", "F-06"),
    (
        "자료 문구를 단체 발언으로",
        "의안번호 2207285 개정 자료에서 전문예술단체는 “재정여건 개선에 기여하려는 것임”을 확인.",
        "F-06",
    ),
    (
        "자료 문구를 국가 발언으로",
        "의안번호 2207285 자료 원문에서 국가는 “기부금품을 모집할 수 있다”를 확인한다.",
        "F-06",
    ),
    ("따옴표 없는 전언", "의안번호 2207285에 대하여 국가는 개선에 기여한다는 것을 확인.", "F-06"),
    ("처리 결과가 없다", "의안번호 2207285은 처리 결과가 없다.", "F-06"),
    ("위원회가 종료한다", "의안번호 2207285은 문화체육관광위원회가 종료한다.", "F-06"),
    ("제7조는 제외된다", "제7조는 제외된다.", "F-03"),
    ("삭제된 조문은 제7조", "삭제된 조문은 제7조이다.", "F-03"),
    ("완료 뒤에 예정 절차", "의안번호 2207285 개정이 완료되었다, 예정 절차가 있다.", "F-06"),
    ("완료 뒤에 아직 확인", "의안번호 2207285 개정이 완료되었다 아직 확인 필요.", "F-06"),
    ("확정 뒤에 예정 절차", "의안번호 2207285은 확정되었다, 예정 절차가 있다.", "F-06"),
]


@pytest.mark.parametrize(
    ("name", "text", "fact_id"), ATTACKS_V9, ids=[a[0] for a in ATTACKS_V9]
)
def test_8차_검토가_뚫었던_공격은_이제_막힌다(good_draft, name, text, fact_id) -> None:
    run = asyncio.run(_run(canned_draft=_with_body(good_draft, text, fact_id)))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )


def test_글이_말하는_항목의_값이_자료와_같아야_한다(good_draft) -> None:
    """`TOPIC_VALUE_MISMATCH`만 겨눈다.

    근거 값 하나만 넣고 나머지를 아무 말로나 채우는 것을 막는다. 방향을
    뒤집어 **글이 말하는 항목** 쪽에서도 본다.
    """
    run = asyncio.run(
        _run(canned_draft=_with_body(good_draft, "의안번호 2207285은 처리 결과가 없다."))
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "TOPIC_VALUE_MISMATCH" in rules, rules


def test_주장이_끝났으면_뒤의_헤지는_소용없다(good_draft) -> None:
    """`개정이 완료되었다, 예정 절차가 있다`의 `예정`은 `절차`를 부정한다."""
    run = asyncio.run(
        _with_body(good_draft, "의안번호 2207285 개정이 완료되었다, 예정 절차가 있다.")
        and _run(
            canned_draft=_with_body(
                good_draft, "의안번호 2207285 개정이 완료되었다, 예정 절차가 있다."
            )
        )
    )
    assert run.draft_version == 0
    rules = {f.rule_id for f in run.validation_findings}
    assert "PREMATURE_EFFECT_CLAIM" in rules, rules


def test_자리값이_붙은_수를_제대로_읽는다() -> None:
    """`26만`은 26이 아니라 260000이다. 원장의 `26` 하나로 만들 수 없다."""
    from app.gates.draft_gate import SCALED_NUMBER, SCALE_VALUES

    found = {
        int(m.group(1)) * SCALE_VALUES[m.group(2)]
        for m in SCALED_NUMBER.finditer("26만 명과 26억 원")
    }
    assert found == {260_000, 2_600_000_000}
    # 조문 번호의 `조`는 1조(兆)가 아니다.
    assert not SCALED_NUMBER.search("제7조제6항")


def test_이름_조각을_잘라_쓸_수_없다() -> None:
    """`조계원`에서 `계원`을 잘라내지 못한다."""
    from app.gates.draft_gate import _is_grounded_word

    haystack = "조계원 의원 등 16인"
    assert _is_grounded_word("조계원이", haystack)
    assert not _is_grounded_word("계원이", haystack), "이름 조각을 통과시켰습니다."


def test_따옴표_앞의_말하는_이를_본다(good_draft) -> None:
    """`SPEAKER_BEFORE_QUOTE`만 겨눈다.

    `모집`은 직함 목록에도, 발표 주체에도, 문서 표시에도 없다. 따옴표 앞에서
    `~은` 자리를 차지한 것만으로 말하는 이가 된다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_with_body(
                good_draft,
                "의안번호 2207285 자료에서 모집은 “기부금품을 모집할 수 있다”를 본다.",
            )
        )
    )
    assert run.draft_version == 0, "따옴표 앞의 말하는 이를 놓쳤습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "STATEMENT_WITHOUT_SOURCE" in rules, rules


def test_조각_날짜도_자료와_대조한다(good_draft) -> None:
    """`PARTIAL_DATE`만 겨눈다. 빈칸 표시는 항목 대조를 받지 않는 칸이다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft, lambda d: d.__setitem__("placeholders", ["6월 7일"])
            )
        )
    )
    assert run.draft_version == 0, "조각 날짜를 놓쳤습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "DATE_NOT_IN_LEDGER" in rules, rules


# ---------------------------------------------------------------------------
# 9차 검토가 뚫었던 공격
# ---------------------------------------------------------------------------

ATTACKS_V10 = [
    ("처리한 날은 6일", "의안번호 2207285을 처리한 날은 6일이다.", ("F-06",)),
    ("26년에 심사", "의안번호 2207285은 26년에 심사되었다.", ("F-06",)),
    ("제7조제10항", "바뀐 조문은 제7조제10항이다.", ("F-03",)),
    ("26항이 있다", "제7조에는 26항이 있다.", ("F-03",)),
    ("이름 앞 자르기 조계", "조계가 의안번호 2207285을 확인하였다.", ("F-06",)),
    ("기관 앞 자르기", "문화체육관광위가 의안번호 2207285을 심사하였다.", ("F-06",)),
    ("심사 결과는 없다", "의안번호 2207285의 심사 결과는 없다.", ("F-06",)),
    ("표결 결과는 없다", "의안번호 2207285의 표결 결과는 없다.", ("F-06",)),
    ("원안가결이 아니다", "의안번호 2207285은 원안가결이 아니다.", ("F-06", "F-02")),
    (
        "원안가결이 아닌 결과",
        "의안번호 2207285은 원안가결이 아닌 결과로 처리되었다.",
        ("F-06", "F-02"),
    ),
    (
        "처리된 사실이 없다",
        "의안번호 2207285은 원안가결로 처리된 사실이 없다.",
        ("F-06", "F-02"),
    ),
    ("제7조가 아니다", "바뀐 조문은 제7조가 아니다.", ("F-03",)),
    ("2207285이 아니다", "의안번호는 2207285이 아니다.", ("F-06",)),
]


@pytest.mark.parametrize(
    ("name", "text", "fact_ids"), ATTACKS_V10, ids=[a[0] for a in ATTACKS_V10]
)
def test_9차_검토가_뚫었던_공격은_이제_막힌다(good_draft, name, text, fact_ids) -> None:
    payload = copy.deepcopy(good_draft)
    payload["result"]["paragraphs"].append(
        {
            "paragraph_id": "P-09",
            "section_kind": "BODY",
            "priority_rank": 9,
            "text": text,
            "claim_ids": [],
            "fact_ids": list(fact_ids),
            "supplementary_rule_ids": [],
        }
    )
    run = asyncio.run(_run(canned_draft=payload))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )


CONNECTIVE_ATTACKS = [
    ("공포되어", "이 법률은 공포되어 아직 확인이 필요한 절차가 있다."),
    ("시행되어", "이 법률은 시행되어 아직 확인이 필요한 절차가 있다."),
    ("공포하여", "이 법률을 공포하여 아직 확인이 필요한 절차가 있다."),
]


@pytest.mark.parametrize(
    ("name", "text"), CONNECTIVE_ATTACKS, ids=[a[0] for a in CONNECTIVE_ATTACKS]
)
def test_잇는_어미로_쓴_효력_주장은_헤지를_받지_않는다(good_draft, name, text) -> None:
    """`공포되어 아직 …`의 `아직`은 뒤의 절차를 부정한다."""
    payload = copy.deepcopy(good_draft)
    _ai_paragraphs(payload["result"])[-1]["text"] = (
        "부칙은 “이 법은 공포한 날부터 시행한다.”라고 제안하고 있다. " + text
    )
    run = asyncio.run(_run(canned_draft=payload))
    assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "PREMATURE_EFFECT_CLAIM" in rules, rules


def test_문의처와_빈칸_표시도_자료와_대조한다(good_draft) -> None:
    """두 칸 다 화면에 나간다. 근거 대조를 안 받으므로 다른 검사가 봐야 한다."""
    for name, mutate in (
        ("문의처", lambda d: d.__setitem__("contact_text", "의안번호 2207285의 심사 결과는 없다")),
        (
            "빈칸 표시",
            lambda d: d.__setitem__("placeholders", ["의안번호 2207285은 원안가결이 아니다"]),
        ),
    ):
        run = asyncio.run(_run(canned_draft=_spoil(good_draft, mutate)))
        assert run.draft_version == 0, f"{name}: 오염된 초안이 나갔습니다."


def test_자료가_말한_값을_부정할_수_없다() -> None:
    """`LEDGER_VALUE_NEGATED`만 겨눈다.

    한글은 음절 단위라 `아닌`은 `아니`를 담지 않는다. 활용형을 함께 봐야 한다.
    """
    from app.gates.draft_gate import NEGATIONS

    for form in ("아니다", "아닌 결과", "아님", "없다", "않는다", "못한다"):
        assert any(n in form for n in NEGATIONS), f"부정형을 놓칩니다: {form}"


def test_낱말을_앞뒤_어느_쪽으로도_자를_수_없다() -> None:
    from app.gates.draft_gate import _starts_a_word

    haystack = "조계원 의원 등 16인, 문화체육관광위원회"
    assert _starts_a_word("조계원", haystack)
    assert _starts_a_word("문화체육관광위원회", haystack)
    assert not _starts_a_word("조계", haystack), "앞을 잘라 통과시켰습니다."
    assert not _starts_a_word("계원", haystack), "뒤를 잘라 통과시켰습니다."
    assert not _starts_a_word("문화체육관광위", haystack)


def test_조문_아래_단위도_개정문과_대조한다() -> None:
    """`PROVISION_UNIT`만 겨눈다.

    조는 맞는데 항만 지어내는 길(`제7조제10항`)을 막는다. 지금 고정 자료에서는
    셈 검사가 먼저 잡지만, 자료가 달라지면 이 규칙만 남는다.
    """
    from app.gates.draft_gate import PROVISION_UNIT, _squeeze

    body = _squeeze("제7조제6항 중 “모집할”을 “모집ㆍ접수할”로 한다.")
    found = [m.group(0) for m in PROVISION_UNIT.finditer(_squeeze("제7조제10항이다"))]
    assert found == ["제10항"], found
    assert found[0] not in body, "개정문에 없는 항을 있다고 봤습니다."
    assert [m.group(0) for m in PROVISION_UNIT.finditer(body)] == ["제6항"]


# ---------------------------------------------------------------------------
# 10차 검토가 뚫은 자리
#
# 두 구멍 다 **낱말 목록의 빈칸**이었다. 어미 목록에 `함`이 없었고, 시점 낱말
# 목록에 맨 `다음`이 없었다. 목록을 채우는 대신 규칙의 방향을 뒤집는다 —
# 효력·시점을 말하는 자리는 **자료에 적힌 그대로**여야 하고, 아니면 제안임을
# 밝혀야 한다. 설계는 `docs/superpowers/specs/2026-08-25-h1-h2-ledger-driven-design.md`.
# ---------------------------------------------------------------------------

def _rule_paragraph(d: dict, sentence: str) -> None:
    """AI 문단을 공격 문장 하나로 바꾸고 부칙 근거를 붙인다.

    **부칙 원문을 앞에 붙이지 않는다.** 전에는 붙였다. 그런데 부칙이 Harness
    몫이 된 뒤로는 그 인용문 자체가 AI 자리에서 H1에 걸린다. 그러면 공격
    문장이 비어 있어도 시험이 통과한다 — 아무것도 지키지 못하는 시험이 된다.

    부칙 근거(`SR-01`)는 붙인다. 없으면 `EFFECTIVE_DATE_NEEDS_RULE`이 먼저
    잡아서 **겨누는 규칙까지 도달하지 못한다.**

    공격 문장에 숫자를 넣지 않는다. 부칙 원문에 없는 숫자는 셈 대조가 먼저
    잡으므로, 역시 겨누는 규칙을 확인할 수 없게 된다.
    """
    para = _ai_paragraphs(d)[-1]
    para["text"] = sentence
    para["supplementary_rule_ids"] = ["SR-01"]


def test_목록에_없는_어미로_공포를_앞지를_수_없다(good_draft) -> None:
    """`PREMATURE_EFFECT_CLAIM`만 겨눈다. 10차 검토가 뚫은 입력 그대로다.

    `공포됨`은 어미 목록에 있고 `공포함`은 없었다. 글자 하나 차이로 절차
    앞지르기 검사가 통째로 꺼졌다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: _rule_paragraph(d, "이 법률은 공포함으로써 효력이 있다."),
            )
        )
    )
    assert run.draft_version == 0, "아직 법이 아닌데 공포됐다고 쓴 초안이 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "PREMATURE_EFFECT_CLAIM" in rules, rules


def test_이름꼴_어미로_개정을_앞지를_수_없다(good_draft) -> None:
    """`PREMATURE_EFFECT_CLAIM`만 겨눈다. 10차 검토가 뚫은 입력 그대로다.

    `-함`은 어미 목록에도 없고, 낱말 검사에서는 `개정`(허용 낱말) + `함`(조사)로
    갈라져 둘 다 지나간다. 두 검사가 서로 상대를 믿고 아무도 안 보던 자리다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft, lambda d: _rule_paragraph(d, "이번 조문을 개정함.")
            )
        )
    )
    assert run.draft_version == 0, "아직 법이 아닌데 개정됐다고 쓴 초안이 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "PREMATURE_EFFECT_CLAIM" in rules, rules


def test_부칙에_없는_시점은_숫자가_없어도_막는다(good_draft) -> None:
    """`EFFECTIVE_DATE_NOT_IN_RULE`만 겨눈다. 10차 검토가 뚫은 입력 그대로다.

    부칙은 `공포한 날부터`인데 초안은 `다음 날부터`라고 딴소리를 한다. 숫자가
    없어서 셈 대조를 지나가고, 시점 낱말 목록에는 `다음달`만 있고 맨 `다음`이
    없어서 그 검사도 지나갔다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: _rule_paragraph(
                    d, "조문은 다음 날부터 시행하도록 제안하고 있다."
                ),
            )
        )
    )
    assert run.draft_version == 0, "부칙에 없는 시점을 쓴 초안이 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "EFFECTIVE_DATE_NOT_IN_RULE" in rules, rules


#: `-함` 꼴 변형. 10차 검토가 목록 밖 어미 12종을 시도해 **5종이 통과했고 전부
#: 이 꼴**이었다. `함`과 `으로써`가 둘 다 허용 조사 목록에 있어서, 낱말 검사는
#: `공포`(허용 낱말) + `함`(조사)로 갈라 보고 어미 검사는 목록에 없어 안 본다.
#: 두 검사가 서로 상대를 믿고 아무도 안 보는 자리다.
NAMED_FORM_ATTACKS = [
    "이 법률은 공포함으로써 효력이 있다.",
    "이번 조문을 개정함.",
    "이 법률을 시행함으로써 효력이 있다.",
    "이번 내용을 확정함.",
    "이번 조문을 적용함.",
]


@pytest.mark.parametrize("sentence", NAMED_FORM_ATTACKS, ids=NAMED_FORM_ATTACKS)
def test_이름꼴_어미로는_어느_효력_어간도_앞지를_수_없다(good_draft, sentence) -> None:
    """어간을 바꿔 가며 같은 어미로 찌른다. 하나라도 통과하면 구멍이다."""
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: _rule_paragraph(d, sentence)))
    )
    assert run.draft_version == 0, f"`{sentence}` 가 그대로 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "PREMATURE_EFFECT_CLAIM" in rules, rules


#: 부칙 원문 `이 법은 공포한 날부터 시행한다.`에 없는 시점.
#: 허용 낱말 157개를 전수로 훑어 **시점을 가리킬 수 있는 말**만 골라 찌른다.
NOT_IN_RULE_TIMES = [
    "조문은 다음 날부터 시행하도록 제안하고 있다.",
    "조문은 공포한 날 뒤부터 시행하도록 제안하고 있다.",
    "조문은 공포한 날 앞부터 시행하도록 제안하고 있다.",
    "조문은 공포한 날 전부터 시행하도록 제안하고 있다.",
    "조문은 공포한 날 후부터 시행하도록 제안하고 있다.",
    "조문은 현재부터 시행하도록 제안하고 있다.",
]


@pytest.mark.parametrize("sentence", NOT_IN_RULE_TIMES, ids=NOT_IN_RULE_TIMES)
def test_부칙에_없는_시점은_어느_낱말이든_막는다(good_draft, sentence) -> None:
    """시점 낱말 목록을 없애고 허용 낱말 전수 분류로 바꾸면 전부 막혀야 한다."""
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: _rule_paragraph(d, sentence)))
    )
    assert run.draft_version == 0, f"`{sentence}` 가 그대로 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "EFFECTIVE_DATE_NOT_IN_RULE" in rules, rules


#: 10차를 고치며 **새로 만든 방어**를 겨눈 공격.
#:
#: 아홉 라운드가 가르쳐 준 것 — 되살아난 구멍은 0건이고, 뚫리는 것은 언제나
#: 새로 만든 자리다. 따옴표 예외와 어절 첫머리 판정은 10차를 고치며 그날
#: 만든 것이라 아무도 본 적이 없다. 만든 사람이 먼저 때려 본다.
NEW_GUARD_ATTACKS = [
    # 따옴표 예외: 자료에 있는 짧은 조각만 따옴표로 감싸면 그 안의 어간이
    # 통째로 검사에서 빠진다. 어미는 따옴표 **밖**에 있는데도 안 본다.
    ("따옴표로 어간만 감싸기", "이 법률은 “공포”되었다.", "PREMATURE_EFFECT_CLAIM"),
    ("따옴표로 감싸고 끝맺기", "조문은 “시행”한다.", "PREMATURE_EFFECT_CLAIM"),
    # 어절 첫머리 판정: 띄어쓰기로만 어절을 나눠서 `“다음”`은 `“`로 시작한다고
    # 본다. 따옴표 한 쌍이면 시점 대조가 꺼진다.
    (
        "시점을 따옴표에",
        "조문은 “다음” 날부터 시행하도록 제안하고 있다.",
        "EFFECTIVE_DATE_NOT_IN_RULE",
    ),
]


@pytest.mark.parametrize(
    ("name", "sentence", "rule_id"), NEW_GUARD_ATTACKS, ids=[a[0] for a in NEW_GUARD_ATTACKS]
)
def test_따옴표로_새_방어를_끌_수_없다(good_draft, name, sentence, rule_id) -> None:
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: _rule_paragraph(d, sentence)))
    )
    assert run.draft_version == 0, f"{name}: `{sentence}` 가 그대로 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert rule_id in rules, rules


#: 만든 사람이 두 번째로 때려 본 것. **이 12종은 처음부터 막혔다.**
#:
#: 실패를 못 본 시험이라 "막는 기능이 있다"는 증거는 되지 못한다. 그래도
#: 남긴다. 아홉 라운드가 되살아난 구멍 0건을 지킨 방법이 이것이다 — 한 번
#: 막은 것을 시험으로 굳혀 두면 다시 열리지 않는다.
SELF_ATTACKS_ROUND2 = [
    ("자료 구절을 그대로 주장으로", "제7조는 공포한 날부터 시행한다."),
    (
        "자료 구절 뒤에 주장 붙이기",
        "부칙은 “이 법은 공포한 날부터 시행한다.”라고 제안하고 있다. "
        "따라서 공포한 날부터 효력이 있다.",
    ),
    ("가운뎃점으로 낱말 쪼개기", "이 법률은 공·포되었다."),
    ("글자 사이 띄우기", "이 법률은 공 포 되 었 다."),
    ("어간 뒤 조사 늘리기", "이 법률은 공포되었음이 확인된다."),
    ("헤지를 다음 문장에 두기", "이 법률은 공포되었다. 아직 확정 전이다."),
    ("이름꼴로 시점 만들기", "조문은 시행일 다음부터 적용한다."),
    ("괄호로 시점 감싸기", "조문은 (다음) 날부터 시행하도록 제안하고 있다."),
    ("시점 낱말 이어붙이기", "조문은 그다음날부터 시행하도록 제안하고 있다."),
]


@pytest.mark.parametrize(
    ("name", "sentence"), SELF_ATTACKS_ROUND2, ids=[a[0] for a in SELF_ATTACKS_ROUND2]
)
def test_스스로_때려_본_공격도_막힌다(good_draft, name, sentence) -> None:
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: _rule_paragraph(d, sentence)))
    )
    assert run.draft_version == 0, f"{name}: `{sentence}` 가 그대로 나갔습니다."
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )


# 11차 검토가 뚫은 자리 — 로마자 한 글자.
#
# 두 검사가 서로 상대를 믿었다. 어절 나누는 자리(`WORD_SPLIT`)는 로마자를
# 자르지 않아 `x다음`을 한 낱말로 보고, 낱말 검사(`LATIN_RUN`)는 로마자를
# **두 글자부터** 봐서 `x` 하나를 안 본다. 한글·숫자·따옴표는 다 막히는데
# 로마자 한 글자만 그 사이로 빠진다.
#
# 아래 두 시험은 **방어를 하나씩 따로** 겨눈다. 처음에는 `초안이 안 나왔다`만
# 봤는데, 그러면 두 방어를 되돌려도 시험이 통과했다(12차 검토). 무엇을 겨누는지
# 이름을 대야 그 방어가 죽었을 때 시험이 죽는다.


def test_로마자_한_글자도_자료에_있어야_한다(good_draft) -> None:
    """`LATIN_RUN`만 겨눈다.

    한글은 한 글자짜리가 조사와 겹쳐 못 보지만 로마자는 조사가 아니다.
    한 글자도 자료에 있어야 한다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft, lambda d: _append_body(d, "해당 내용은 x 자료이다.")
            )
        )
    )
    assert run.draft_version == 0, "자료에 없는 로마자가 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "WORD_NOT_IN_LEDGER" in rules, rules


def test_로마자를_끼워_어절_첫머리를_옮길_수_없다(good_draft) -> None:
    """`WORD_SPLIT`의 글자 종류 경계만 겨눈다.

    `x다음`을 한 낱말로 보면 `다음`이 어절 첫머리가 아니게 되어 시점 대조가
    꺼진다. 부칙 근거를 붙여 두어야 `EFFECTIVE_DATE_NOT_IN_RULE`까지 도달한다.
    """

    # 문단을 **통째로 갈아 끼운다.** 덧붙이면 원래 문단의 날짜 숫자와
    # `다음과 같다`가 붙이는 문장과 상관없이 같은 규칙을 걸어서, 이 시험이
    # 겨누는 것을 확인할 수 없다.
    #
    # 갈아 끼우면 다른 규칙도 함께 걸리므로 **규칙 이름으로 판별한다.**
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: _rule_paragraph(
                    d, "조문은 x다음 날부터 시행하도록 제안하고 있다."
                ),
            )
        )
    )
    assert run.draft_version == 0, "부칙에 없는 시점이 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "EFFECTIVE_DATE_NOT_IN_RULE" in rules, rules


#: 11차 검토가 뚫은 자리 — 자료를 그대로 베끼고 어미만 바꾼다.
#:
#: 자료가 `시행한다`(제안)인데 초안이 `시행되었다`(끝남)라고 쓴다. 겹치는
#: 길이로는 **옮긴 것**과 **옮기고 뒤집은 것**을 가를 수 없다. 검사원이
#: `SOURCE_SPAN`을 0부터 13까지 다 넣어 확인했고 어떤 값에서도 공격이 남거나
#: 정상이 죽었다.
#:
#: 그래서 규칙을 더 만들지 않는다. **효력을 말하는 문장을 AI가 못 쓰게** 한다.
#: 그 문장은 Harness가 자료에서 직접 만든다 (`NEXT.md` 넘어가는 조건 4번).
COPY_AND_FLIP_ATTACKS = [
    "이 법은 공포한 날부터 시행되었다.",
    "이 법률은 공포한 날부터 시행되었다.",
    "이 법은 공포한 날부터 시행한다.",
    "공포한 날부터 시행된 내용이다.",
    "공포한 날부터 적용되었다.",
]


@pytest.mark.parametrize("sentence", COPY_AND_FLIP_ATTACKS, ids=COPY_AND_FLIP_ATTACKS)
def test_AI는_효력을_말하는_문장을_쓸_수_없다(good_draft, sentence) -> None:
    """AI가 쓰는 자리에 효력 어간이 나오면 막는다.

    원장 사실 값 안에 든 것만 넘어간다. 의안 이름 `문화예술진흥법
    일부개정법률안`의 `개정`이 그런 것이다. 그 값은 자료가 말한 것이라
    지어낼 수 없다.
    """
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: _rule_paragraph(d, sentence)))
    )
    assert run.draft_version == 0, f"`{sentence}` 가 그대로 나갔습니다."
    rules = {f.rule_id for f in run.validation_findings}
    assert "PREMATURE_EFFECT_CLAIM" in rules, rules


def test_AI는_부칙_자리를_차지할_수_없다(good_draft) -> None:
    """AI가 `SUPPLEMENTARY` 문단을 보내면 Harness가 걷어낸다.

    막는 것이 아니라 **못 하는 것**이다. 초안은 정상으로 나오고, AI가 보낸
    글만 사라진다.
    """
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: d["paragraphs"].append(
                    {
                        "paragraph_id": "P-99",
                        "section_kind": "SUPPLEMENTARY",
                        "priority_rank": 8,
                        "text": "부칙은 “이 법은 즉시 시행한다.”라고 정하고 있다.",
                        "claim_ids": [],
                        "fact_ids": [],
                        "supplementary_rule_ids": ["SR-01"],
                    }
                ),
            )
        )
    )
    assert run.draft is not None, "정상 자료인데 초안이 나오지 않았습니다."
    body = " ".join(p.text for p in run.draft.paragraphs)
    assert "즉시 시행" not in body, "AI가 쓴 부칙이 초안에 실렸습니다."
    assert "이 법은 공포한 날부터 시행한다." in body, "자료의 부칙이 사라졌습니다."


def test_AI가_Harness_이름표를_흉내_내도_걷어낸다(good_draft) -> None:
    """`HS-` 이름표를 달면 검사를 건너뛸 수 있으므로 통째로 버린다."""
    run = asyncio.run(
        _run(
            canned_draft=_spoil(
                good_draft,
                lambda d: d["paragraphs"].append(
                    {
                        "paragraph_id": "HS-99",
                        "section_kind": "BODY",
                        "priority_rank": 7,
                        "text": "이 법은 공포되어 시행되었다.",
                        "claim_ids": [],
                        "fact_ids": [],
                        "supplementary_rule_ids": [],
                    }
                ),
            )
        )
    )
    assert run.draft is not None
    body = " ".join(p.text for p in run.draft.paragraphs)
    assert "공포되어" not in body, "Harness 이름표를 흉내 낸 글이 실렸습니다."


#: 새 구조를 스스로 때려 본 것. 전부 막혔다.
ARCHITECTURE_ATTACKS = [
    ("의안 이름 뒤에 주장 붙이기", "문화예술진흥법 일부개정법률안이 개정되었다."),
    (
        "개정문 본문 뒤에 주장 붙이기",
        "제7조제6항 중 “모집할”을 “모집ㆍ접수할”로 한다. 이로써 개정되었다.",
    ),
    ("한자로 바꿔 쓰기", "이 법은 公布한 날부터 施行되었다."),
    ("글자 흩어 쓰기", "이 법은 공 포 한 날 부 터 시 행 되 었 다."),
    ("헤지를 붙여도 못 쓴다", "이 법은 아직 공포 예정이다."),
]


@pytest.mark.parametrize(
    ("name", "sentence"), ARCHITECTURE_ATTACKS, ids=[a[0] for a in ARCHITECTURE_ATTACKS]
)
def test_새_구조를_때려_본_공격도_막힌다(good_draft, name, sentence) -> None:
    run = asyncio.run(
        _run(canned_draft=_spoil(good_draft, lambda d: _rule_paragraph(d, sentence)))
    )
    assert run.draft_version == 0, f"{name}: `{sentence}` 가 그대로 나갔습니다."
    assert [f for f in run.validation_findings if f.severity.value == "BLOCKING"], (
        f"{name}: 초안은 막혔지만 이유가 기록되지 않았습니다."
    )


def test_AI는_효력_낱말을_어떤_모양으로도_못_쓴다(good_draft) -> None:
    """11~14차 검토가 네 번 뚫은 자리. **예외를 없애서** 닫았다.

    예외는 "그 낱말이 원장 값 안에 있으면 넘어간다"였다. 의안 이름
    `문화예술진흥법 일부개정법률안`의 `개정`을 쓰게 하려던 것이다.

    네 번 고쳤고 네 번 다 뚫렸다. 값의 끝을 막으면 한가운데로, 한가운데를
    막으면 값 뒤로, 값 뒤를 막으면 목록 밖 어미로 돌아왔다. **경계를 어디에
    긋든 거짓말은 그 바깥에 섯다.**

    아래는 열네 라운드가 뚫었던 모양을 모은 것이다. 예외가 없으면
    이것들이 **한 규칙으로** 다 막힌다.
    """
    for sentence in (
        # 12차 — 값이 어간으로 끝남
        "기부금품법 전부개정되었다.",
        # 13차 B1 — 주장이 값 안에 통째로
        "이 법률은 개정되었다.",
        # 13차 B5 — 값이 문장을 가로지름
        "이 법률은 개정 되었다.",
        # 14차 — 목록 밖 어미
        "이 법률은 개정임.",
        "이 법률은 개정이므로 그렇다.",
        # 14차 — 창 밖으로 밀어내기
        "기부금품법 전부개정법률은 이번에 모두 되었다.",
    ):
        run = asyncio.run(
            _run(canned_draft=_spoil(good_draft, lambda d, s=sentence: _rule_paragraph(d, s)))
        )
        assert run.draft_version == 0, f"`{sentence}` 가 그대로 나갔습니다."
        rules = {f.rule_id for f in run.validation_findings}
        assert "PREMATURE_EFFECT_CLAIM" in rules, f"{sentence}: {rules}"


def test_정상_초안은_예외_없이도_나온다() -> None:
    """대조군. 예외를 없앨 대가를 재는 시험이다.

    예외를 없애면 **AI가 의안 이름을 못 쓴다.** `일부개정법률안`에
    `개정`이 들어 있기 때문이다. 그래도 초안은 나와야 한다(`I3`).

    지금 고정 자료의 정상 초안은 의안 이름 대신 의안번호를 쓴다.
    이름이 필요해지면 부칙처럼 **Harness가 넣는 자리**를 만든다.
    """
    run = asyncio.run(_run())
    assert run.draft_version >= 1, "예외를 없애자 정상 초안까지 막혔습니다."
    assert run.state == "REVIEW_READY"




def test_낱말_목록을_글의_id로_기억하지_않는다() -> None:
    """`_PHRASE_CACHE`만 겨눈다.

    파이썬은 **버려진 문자열의 `id`를 새 문자열에 다시 쓴다.** 그래서 글의
    `id`를 열쇠로 쓰면, 앞서 본 글이 사라진 자리에 온 다른 글이 앞 글의
    낱말 목록을 받는다.

    `_starts_a_word`는 원장 대조(`F1`)에도 쓰인다. 목록이 섞이면 지어낸 값이
    자료에 있는 것처럼 보일 수 있다.

    운에 기대지 않고 **열쇠의 모양**을 본다. `id` 재사용은 언제 일어날지
    알 수 없어서 시험 전체를 돌릴 때만 가끔 드러났다.
    """
    from app.gates.draft_gate import _PHRASE_CACHE, _starts_a_word

    _starts_a_word("조계원", "조계원 의원 등 16인")
    assert _PHRASE_CACHE, "낱말 목록을 기억하지 않습니다."
    for key in _PHRASE_CACHE:
        assert isinstance(key, str), (
            f"글의 id를 열쇠로 씁니다: {type(key).__name__}. "
            "버려진 문자열의 id가 다시 쓰이면 다른 글의 답이 나옵니다."
        )
