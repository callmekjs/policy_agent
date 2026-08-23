"""원문 보존·사실 추출·근거 대조·충돌 검사 (README §2.3.1, §2.11, §2.14).

가짜 ModelGateway만 쓴다. 외부 AI 호출은 0회이고 비용은 0달러다.
"""

from __future__ import annotations

import unicodedata

import pytest

from app.agents.fact_extraction import AgentResultError, parse_result
from app.gates.conflict_gate import check_conflicts
from app.gates.evidence_gate import build_fact_ledger, locate_evidence
from app.gates.source_role_gate import check_source_roles
from app.harness.contracts import (
    InputMethod,
    IssueCode,
    SourceRole,
    StoredSource,
)
from app.harness.fact_contracts import (
    FACT_RESULT_SCHEMA_VERSION,
    EvidenceCandidate,
    FactExtractionResult,
    RawFact,
    SourceRoleCandidate,
)
from app.harness.source_normalizer import (
    SourceNormalizationError,
    find_quote_offsets,
    normalize_source,
)
from app.infrastructure.model_gateway import ModelCallResult

SAMPLE = (
    "의안번호: 2207285\n"
    "의안명: 「문화예술진흥법 일부개정법률안」\n"
    "본회의 심의: 2025. 9. 25. 원안가결\n"
)


# ---------------------------------------------------------------------------
# 원문 보존 (§2.3.1)
# ---------------------------------------------------------------------------


def test_허용된_세_가지만_바꾼다() -> None:
    raw = "﻿첫 줄\r\n둘째 줄\r셋째 줄"
    result = normalize_source(raw)
    assert result.normalized_text == "첫 줄\n둘째 줄\n셋째 줄"
    assert result.version == "source_text_v1"


def test_공백과_문장부호를_건드리지_않는다() -> None:
    raw = '  앞뒤   공백  \n\n"따옴표"  30%  30%p  1,000원'
    assert normalize_source(raw).normalized_text == raw


def test_분해된_한글을_합친다() -> None:
    """붙여 넣은 자료의 한글이 분해형이어도 완성형과 같게 다룬다."""
    decomposed = unicodedata.normalize("NFD", "본회의 원안가결")
    result = normalize_source(decomposed)
    assert result.normalized_text == "본회의 원안가결"
    assert result.raw_sha256 != result.normalized_sha256


def test_같은_내용이면_줄바꿈이_달라도_정규화_해시가_같다() -> None:
    a = normalize_source("첫 줄\r\n둘째 줄")
    b = normalize_source("첫 줄\n둘째 줄")
    assert a.normalized_sha256 == b.normalized_sha256
    assert a.raw_sha256 != b.raw_sha256


def test_글자_하나만_달라도_정규화_해시가_다르다() -> None:
    a = normalize_source("본회의 원안가결")
    b = normalize_source("본회의 수정가결")
    assert a.normalized_sha256 != b.normalized_sha256


def test_원문_위치를_되찾는다() -> None:
    result = normalize_source("﻿의안번호: 2207285\r\n본회의 원안가결\r\n")
    quote = "본회의 원안가결"
    start = find_quote_offsets(result.normalized_text, quote)[0]
    span = result.raw_span(start, start + len(quote))
    assert span.start.line == 2
    assert span.start.column == 1
    assert span.excerpt == quote


def test_빈_자료는_정규화_전에_막는다() -> None:
    with pytest.raises(SourceNormalizationError):
        normalize_source("")
    with pytest.raises(SourceNormalizationError):
        normalize_source("   \n\n  ")


def test_근거_찾기는_비슷한_문장을_찾아주지_않는다() -> None:
    text = normalize_source(SAMPLE).normalized_text
    assert find_quote_offsets(text, "본회의 심의: 2025. 9. 25. 원안가결")
    assert find_quote_offsets(text, "본회의  심의: 2025. 9. 25. 원안가결") == []
    assert find_quote_offsets(text, "본회의 심의 : 2025. 9. 25. 원안가결") == []


# ---------------------------------------------------------------------------
# 근거 대조 Gate
# ---------------------------------------------------------------------------


def _result(**overrides) -> FactExtractionResult:
    """정상 결과를 만든다. schema는 모든 배열을 요구하므로 빈 값을 채운다."""
    base = {
        "result_status": "OK",
        "scope_error": None,
        "source_role_candidates": [],
        "evidence": [],
        "facts": [],
        "bill_identities": [],
        "bill_relations": [],
        "legislative_events": [],
        "provision_comparisons": [],
        "supplementary_rules": [],
    }
    base.update(overrides)
    return FactExtractionResult(**base)


def _sources() -> tuple[dict, dict]:
    normalized = {"SRC-01": normalize_source(SAMPLE)}
    names = {"SRC-01": "의안정보"}
    return normalized, names


def test_원문에_있는_근거는_위치까지_계산한다() -> None:
    normalized, names = _sources()
    found = locate_evidence(
        [EvidenceCandidate(evidence_id="EV-01", source_id="SRC-01", quote="의안번호: 2207285")],
        normalized,
        names,
    )
    assert not found.problems
    location = found.locations["EV-01"]
    assert location.raw_start_line == 1
    assert location.occurrence_count == 1
    assert location.source_name == "의안정보"


def test_원문에_없는_근거는_사실을_버린다() -> None:
    """AI가 지어낸 근거로는 사실이 원장에 들어가지 못한다."""
    normalized, names = _sources()
    raw = _result(
        evidence=[
            EvidenceCandidate(evidence_id="EV-01", source_id="SRC-01", quote="본회의 부결")
        ],
        facts=[
            RawFact(
                fact_id="F-01",
                kind="PLENARY_RESULT",
                value="부결",
                source_id="SRC-01",
                evidence_id="EV-01",
            )
        ],
    )
    ledger, evidence = build_fact_ledger(raw, normalized, names)
    assert ledger.facts == []
    assert ledger.rejected_fact_ids == ["F-01"]
    assert any(p.kind == "NOT_FOUND" for p in evidence.problems)


def test_여러_곳에_반복되는_근거는_고위험_사실을_막는다() -> None:
    text = "원안가결\n원안가결\n"
    normalized = {"SRC-01": normalize_source(text)}
    raw = _result(
        evidence=[
            EvidenceCandidate(evidence_id="EV-01", source_id="SRC-01", quote="원안가결")
        ],
        facts=[
            RawFact(
                fact_id="F-01",
                kind="PLENARY_RESULT",
                value="원안가결",
                source_id="SRC-01",
                evidence_id="EV-01",
            )
        ],
    )
    ledger, evidence = build_fact_ledger(raw, normalized, {"SRC-01": "표결"})
    assert ledger.facts == []
    assert any(p.kind == "AMBIGUOUS" for p in evidence.problems)


def test_사실과_근거가_다른_자료를_가리키면_버린다() -> None:
    normalized, names = _sources()
    normalized["SRC-02"] = normalize_source("다른 자료")
    names["SRC-02"] = "현행 조문"
    raw = _result(
        evidence=[
            EvidenceCandidate(evidence_id="EV-01", source_id="SRC-01", quote="의안번호: 2207285")
        ],
        facts=[
            RawFact(
                fact_id="F-01",
                kind="BILL_IDENTITY",
                value="2207285",
                source_id="SRC-02",
                evidence_id="EV-01",
            )
        ],
    )
    ledger, _ = build_fact_ledger(raw, normalized, names)
    assert ledger.rejected_fact_ids == ["F-01"]


# ---------------------------------------------------------------------------
# 충돌 검사 (P1-FR-02)
# ---------------------------------------------------------------------------


def _two_source_ledger(value_a: str, value_b: str, kind: str = "PLENARY_DECIDED_ON"):
    text_a = f"본회의 심의: {value_a}\n"
    text_b = f"본회의 심의: {value_b}\n"
    normalized = {"SRC-01": normalize_source(text_a), "SRC-02": normalize_source(text_b)}
    names = {"SRC-01": "의안정보", "SRC-02": "표결 결과"}
    raw = _result(
        evidence=[
            EvidenceCandidate(
                evidence_id="EV-01", source_id="SRC-01", quote=text_a.strip()
            ),
            EvidenceCandidate(
                evidence_id="EV-02", source_id="SRC-02", quote=text_b.strip()
            ),
        ],
        facts=[
            RawFact(
                fact_id="F-01",
                kind=kind,
                value=value_a,
                source_id="SRC-01",
                evidence_id="EV-01",
            ),
            RawFact(
                fact_id="F-02",
                kind=kind,
                value=value_b,
                source_id="SRC-02",
                evidence_id="EV-02",
            ),
        ],
    )
    ledger, _ = build_fact_ledger(raw, normalized, names)
    return ledger


def test_값이_다르면_어느_쪽도_고르지_않고_차단한다() -> None:
    issues = check_conflicts(_two_source_ledger("2025. 9. 25.", "2025. 9. 26."))
    assert [i.code for i in issues] == [IssueCode.FACT_CONFLICT]
    assert issues[0].subject == "plenary_decided_on"
    # 두 값과 각 자료명이 모두 보여야 한다.
    assert "2025. 9. 25." in issues[0].message
    assert "2025. 9. 26." in issues[0].message
    assert "의안정보" in issues[0].message
    assert "표결 결과" in issues[0].message


def test_같은_값이면_충돌이_아니다() -> None:
    assert check_conflicts(_two_source_ledger("2025. 9. 25.", "2025. 9. 25.")) == []


def test_날짜와_요일이_다르면_자동으로_고치지_않는다() -> None:
    """2025-09-25는 목요일이다. 자료에 금요일로 적혀 있으면 물어본다."""
    ledger = _two_source_ledger("2025. 9. 25.(금)", "2025. 9. 25.(금)")
    issues = check_conflicts(ledger)
    codes = [i.code for i in issues]
    assert IssueCode.DATE_WEEKDAY_MISMATCH in codes
    assert "목" in issues[0].message


def test_요일이_맞으면_문제_없다() -> None:
    ledger = _two_source_ledger("2025. 9. 25.(목)", "2025. 9. 25.(목)")
    assert check_conflicts(ledger) == []


# ---------------------------------------------------------------------------
# 자료 역할 확인
# ---------------------------------------------------------------------------


def _source(role: SourceRole) -> StoredSource:
    return StoredSource(
        source_id="SRC-01",
        display_name="붙여넣기 자료 1",
        role=role,
        input_method=InputMethod.PASTED,
        char_count=len(SAMPLE),
        raw_text=SAMPLE,
        raw_sha256="x" * 64,
    )


def test_역할이_정해지면_묻지_않는다() -> None:
    assert check_source_roles([_source(SourceRole.BILL_INFORMATION)], [], {}) == []


def test_잘_모르겠음이면_후보를_보여주고_같은_작업에서_고르게_한다() -> None:
    normalized, names = _sources()
    found = locate_evidence(
        [EvidenceCandidate(evidence_id="EV-01", source_id="SRC-01", quote="의안번호: 2207285")],
        normalized,
        names,
    )
    candidates = [
        SourceRoleCandidate(
            candidate_id="RC-01",
            source_id="SRC-01",
            role="의안정보",
            label="의안번호가 적혀 있습니다.",
            evidence_ids=["EV-01"],
        )
    ]
    issues = check_source_roles([_source(SourceRole.UNKNOWN)], candidates, found.locations)
    assert len(issues) == 1
    assert issues[0].resolution_kind.value == "ANSWER_IN_SAME_RUN"
    assert issues[0].requires_new_run is False
    assert "의안정보" in issues[0].message
    assert "의안번호: 2207285" in issues[0].message


def test_후보가_없으면_새_작업을_요구한다() -> None:
    issues = check_source_roles([_source(SourceRole.UNKNOWN)], [], {})
    assert issues[0].requires_new_run is True


# ---------------------------------------------------------------------------
# Agent 응답 검사
# ---------------------------------------------------------------------------


def _call(result: dict) -> ModelCallResult:
    return ModelCallResult(
        agent_name="FactExtractionAgent",
        requested_model="gpt-5.6-terra",
        actual_model="gpt-5.6-terra",
        result=result,
    )


def test_형식이_어긋난_응답은_부분_결과도_쓰지_않는다() -> None:
    with pytest.raises(AgentResultError) as exc:
        parse_result(_call({"schema_version": "1.2.1", "result": {"result_status": "OK"}}))
    assert exc.value.code == "AGENT_SCHEMA_INVALID"


def test_없는_근거를_가리키면_거부한다() -> None:
    with pytest.raises(AgentResultError):
        parse_result(
            _call(
                {
                    "schema_version": FACT_RESULT_SCHEMA_VERSION,
                    "result": {
                        "result_status": "OK",
                        "scope_error": None,
                        "source_role_candidates": [],
                        "evidence": [],
                        "facts": [
                            {
                                "fact_id": "F-01",
                                "kind": "BILL_IDENTITY",
                                "value": "2207285",
                                "source_id": "SRC-01",
                                "evidence_id": "EV-없음",
                                "valid_source_role_candidate_ids": [],
                            }
                        ],
                        "bill_identities": [],
                        "bill_relations": [],
                        "legislative_events": [],
                        "provision_comparisons": [],
                        "supplementary_rules": [],
                    },
                }
            )
        )


def test_자료가_너무_크면_빈_결과로_멈춘다() -> None:
    with pytest.raises(AgentResultError) as exc:
        parse_result(
            _call(
                {
                    "schema_version": FACT_RESULT_SCHEMA_VERSION,
                    "result": {
                        "result_status": "FACT_SCOPE_TOO_LARGE",
                        "scope_error": {"subject": "SOURCES", "reason": "자료가 너무 많습니다."},
                        "source_role_candidates": [],
                        "evidence": [],
                        "facts": [],
                        "bill_identities": [],
                        "bill_relations": [],
                        "legislative_events": [],
                        "provision_comparisons": [],
                        "supplementary_rules": [],
                    },
                }
            )
        )
    assert exc.value.code == "FACT_SCOPE_TOO_LARGE"


def test_고정_시험자료의_응답_형식을_그대로_읽는다() -> None:
    """`test_sets`에 고정된 실제 모양이 계약과 맞는지 확인한다."""
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "test_sets"
        / "SYN-RISK-003"
        / "candidates"
        / "baseline_fact_extraction_result.json"
    )
    result = parse_result(_call(json.loads(path.read_text(encoding="utf-8"))))
    assert len(result.facts) == 10
    assert len(result.evidence) == 15
    assert len(result.supplementary_rules) == 4


# ---------------------------------------------------------------------------
# 고정 시험자료로 끝까지 (공허하게 통과하지 않도록 실제 자료를 쓴다)
# ---------------------------------------------------------------------------


def _fixture_dir():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / "test_sets"


def _apply_mutation(text: str, mutation_name: str) -> str:
    """고정 mutation 파일의 문자열 교체를 그대로 적용한다."""
    import json

    data = json.loads(
        (_fixture_dir() / "SYN-RISK-001" / "mutations" / f"{mutation_name}.json").read_text(
            encoding="utf-8"
        )
    )
    target = data["selector"]["exact_text"]
    assert target in text, f"고정 자료에 `{target}`가 없습니다."
    return text.replace(target, data["replacement"])


async def _run_flow(sources: list[tuple[str, str, SourceRole]]):
    """가짜 게이트웨이로 전체 흐름을 돌리고 Run을 돌려준다."""
    from datetime import date

    from app.harness.contracts import (
        EXTERNAL_AI_POLICY_VERSION,
        CreateRunRequest,
        Disclosure,
        SourceInput,
    )
    from app.harness.orchestrator import Orchestrator
    from app.infrastructure.model_gateway import FakeModelGateway
    from app.infrastructure.run_store import RunStore

    store = RunStore()
    orchestrator = Orchestrator(store, FakeModelGateway())
    request = CreateRunRequest(
        client_request_id="fixture-run",
        purpose="본회의 의결 결과를 알리려고 합니다. 고정 시험자료로 확인합니다.",
        disclosure=Disclosure.PUBLIC,
        basis_date=date(2026, 8, 23),
        sources=[
            SourceInput(display_name=name, text=text, role=role)
            for name, text, role in sources
        ],
        external_ai_policy_version=EXTERNAL_AI_POLICY_VERSION,
        external_ai_transfer_confirmed=True,
    )
    run = orchestrator.create_run(request)
    await orchestrator.process(run.run_id, request, date(2026, 8, 23))
    return store.get(run.run_id)


def _vote_source(mutation: str | None = None) -> tuple[str, str, SourceRole]:
    text = (_fixture_dir() / "SYN-RISK-001" / "sources" / "03_plenary_vote.md").read_text(
        encoding="utf-8"
    )
    if mutation:
        text = _apply_mutation(text, mutation)
    return ("본회의 표결 결과", text, SourceRole.PLENARY_VOTE_RESULT)


def _other_vote_source() -> tuple[str, str, SourceRole]:
    """같은 표결을 적은 다른 자료. 충돌 비교 상대로 쓴다."""
    text = (
        _fixture_dir() / "SYN-RISK-001" / "sources" / "05_independent_vote_notice.md"
    ).read_text(encoding="utf-8")
    return ("표결 안내", text, SourceRole.BILL_INFORMATION)


@pytest.mark.asyncio
async def test_고정_자료의_요일_불일치를_실제로_잡는다() -> None:
    """`(목요일)` 표기를 읽지 못하면 이 시험이 실패한다."""
    run = await _run_flow([_vote_source("weekday_mismatch")])
    codes = [i.code.value for i in run.issues]
    assert "DATE_WEEKDAY_MISMATCH" in codes, f"요일 검사가 발동하지 않았습니다: {codes}"
    assert run.state == "NEEDS_INPUT"
    assert run.draft_version == 0


@pytest.mark.asyncio
async def test_고정_자료의_날짜_충돌을_실제로_잡는다() -> None:
    run = await _run_flow([_vote_source("date_conflict"), _other_vote_source()])
    conflicts = [i for i in run.issues if i.code.value == "FACT_CONFLICT"]
    assert conflicts, f"날짜 충돌을 잡지 못했습니다: {[i.code.value for i in run.issues]}"
    assert "2026. 8. 21" in conflicts[0].message
    assert "2026. 8. 20" in conflicts[0].message
    assert run.draft_version == 0


@pytest.mark.asyncio
async def test_고정_자료의_찬성_수_충돌을_실제로_잡는다() -> None:
    run = await _run_flow([_vote_source("count_conflict"), _other_vote_source()])
    conflicts = [i for i in run.issues if i.code.value == "FACT_CONFLICT"]
    subjects = [i.subject for i in conflicts]
    assert "vote_yes_count" in subjects, f"찬성 수 충돌을 잡지 못했습니다: {subjects}"
    assert run.draft_version == 0


@pytest.mark.asyncio
async def test_변조하지_않은_고정_자료는_충돌_없이_지나간다() -> None:
    """정상 자료까지 막으면 안 된다."""
    run = await _run_flow([_vote_source(), _other_vote_source()])
    blocking = [i for i in run.issues if i.severity.value == "BLOCKING"]
    assert blocking == [], f"정상 자료를 막았습니다: {[i.message for i in blocking]}"
    assert run.fact_ledger and run.fact_ledger.facts


@pytest.mark.asyncio
async def test_근거가_없는_입법_사건은_원장에_남지_않는다() -> None:
    """사실뿐 아니라 사건·부칙도 근거를 확인한다. 초안 Agent가 읽기 전에 막는다."""
    from app.harness.fact_contracts import RawLegislativeEvent

    normalized, names = _sources()
    raw = _result(
        legislative_events=[
            RawLegislativeEvent(
                event_id="E-01",
                bill_id="B-01",
                procedure_stage="PLENARY_DECIDED",
                disposition="REJECTED",
                occurred_on="2099-01-01",
                source_id="SRC-01",
                evidence_id="EV-없음",
                valid_source_role_candidate_ids=[],
            )
        ],
    )
    ledger, evidence = build_fact_ledger(raw, normalized, names)
    assert ledger.legislative_events == []
    assert any(p.fact_id == "E-01" for p in evidence.problems)


COMMITTEE_ROWS = "- 의결일: 2025. 9. 18.\n- 재석: 24명\n- 찬성: 24명\n- 반대: 0명\n"
PLENARY_ROWS = "- 의결일: 2025. 9. 25.\n- 재석: 205명\n- 찬성: 201명\n- 반대: 3명\n"


@pytest.mark.asyncio
async def test_회의가_분명하면_표결_수를_섞지_않는다() -> None:
    """어느 회의 것인지 분명하면 종류를 갈라 비교하지 않는다.

    분명한 근거는 두 가지다. 사용자가 고른 자료 역할, 그리고 같은 줄의 표기.
    """
    run = await _run_flow(
        [
            ("위원회 최종문", COMMITTEE_ROWS, SourceRole.COMMITTEE_FINAL_TEXT),
            ("본회의 표결 결과", PLENARY_ROWS, SourceRole.PLENARY_VOTE_RESULT),
        ]
    )
    blocking = [i for i in run.issues if i.severity.value == "BLOCKING"]
    assert blocking == [], (
        f"서로 다른 회의의 값을 충돌로 봤습니다: {[i.subject for i in blocking]}"
    )


@pytest.mark.asyncio
async def test_어느_회의인지_애매하면_버리지_않고_물어본다() -> None:
    """이 프로젝트의 방침을 고정한다.

    애매한 값을 **버리면** 충돌이 함께 사라져 시스템이 말없이 한쪽을 고른 것과
    같아진다(1급 실패). 반대로 **남겨서 생기는 거짓 충돌**은 두 값과 자료명을
    보여 주고 사람에게 묻는다(3급). 그래서 애매하면 남기는 쪽을 고른다.

    이 시험은 "정상 자료가 막혔다"를 확인하는 것이 아니라, **값이 조용히
    사라지지 않는다**를 확인한다.
    """
    committee = "# 소관위 심사 결과\n\n" + COMMITTEE_ROWS
    run = await _run_flow(
        [
            ("소관위 심사 결과", committee, SourceRole.BILL_INFORMATION),
            ("본회의 표결 결과", PLENARY_ROWS, SourceRole.PLENARY_VOTE_RESULT),
        ]
    )

    # 값이 하나도 사라지지 않아야 한다.
    assert run.fact_ledger is not None
    values = {f.value for f in run.fact_ledger.facts}
    for expected in ("24", "201", "2025. 9. 18", "2025. 9. 25"):
        assert expected in values, f"`{expected}`가 조용히 사라졌습니다: {sorted(values)}"

    # 애매한 값은 차단 Issue로 사람에게 보여 준다. 초안은 만들지 않는다.
    conflicts = [i for i in run.issues if i.code.value == "FACT_CONFLICT"]
    assert conflicts, "애매한 값을 묻지도 않고 지나갔습니다."
    shown = "\n".join(i.message for i in conflicts)
    for expected in ("24", "201", "소관위 심사 결과", "본회의 표결 결과"):
        assert expected in shown, f"`{expected}`를 사람에게 보여 주지 않았습니다."
    assert run.draft_version == 0


@pytest.mark.asyncio
async def test_제목이_없어도_충돌이_사라지지_않는다() -> None:
    """README §2.3의 기본 입력은 제목 없는 평문 붙여넣기다.

    값이 조용히 빠지면 두 자료의 충돌도 함께 사라져, 시스템이 말없이 한쪽을
    고른 것과 같아진다. 거짓 충돌보다 훨씬 나쁘다.
    """
    a = "의결일: 2026. 8. 20.\n결과: 원안가결\n찬성: 201명\n"
    b = "의결일: 2026. 8. 21.\n결과: 원안가결\n찬성: 202명\n"
    run = await _run_flow(
        [
            ("표결 결과", a, SourceRole.PLENARY_VOTE_RESULT),
            ("표결 안내", b, SourceRole.BILL_INFORMATION),
        ]
    )
    subjects = [i.subject for i in run.issues if i.code.value == "FACT_CONFLICT"]
    assert "plenary_decided_on" in subjects, f"날짜 충돌이 사라졌습니다: {subjects}"
    assert "vote_yes_count" in subjects, f"찬성 수 충돌이 사라졌습니다: {subjects}"
    assert run.draft_version == 0


@pytest.mark.asyncio
async def test_고정_자료의_제목을_지워도_충돌을_잡는다() -> None:
    """검증에서 제목 한 줄만 지우면 충돌 3종이 모두 사라지는 문제가 나왔다."""
    vote_name, vote_text, vote_role = _vote_source("date_conflict")
    other_name, other_text, other_role = _other_vote_source()
    without_heading = "\n".join(
        line for line in other_text.splitlines() if not line.lstrip().startswith("#")
    )
    run = await _run_flow(
        [(vote_name, vote_text, vote_role), (other_name, without_heading, other_role)]
    )
    conflicts = [i for i in run.issues if i.code.value == "FACT_CONFLICT"]
    assert conflicts, "제목을 지우자 충돌이 사라졌습니다."
    assert "2026. 8. 20" in conflicts[0].message
    assert "2026. 8. 21" in conflicts[0].message


@pytest.mark.asyncio
async def test_사용자가_위원회_자료라고_표시하면_본회의_값으로_쓰지_않는다() -> None:
    """화면에서 이미 확인받은 역할은 본문 표기보다 확실한 근거다."""
    text = "의결일: 2025. 9. 18.\n재석: 24명\n찬성: 24명\n"
    run = await _run_flow(
        [
            ("위원회 최종문", text, SourceRole.COMMITTEE_FINAL_TEXT),
            (
                "본회의 표결 결과",
                "의결일: 2025. 9. 25.\n재석: 205명\n찬성: 201명\n",
                SourceRole.PLENARY_VOTE_RESULT,
            ),
        ]
    )
    blocking = [i for i in run.issues if i.severity.value == "BLOCKING"]
    assert blocking == [], f"위원회 값을 본회의 것과 섞었습니다: {[i.subject for i in blocking]}"


@pytest.mark.asyncio
async def test_사용자가_고른_역할이_제목보다_우선한다() -> None:
    """검증에서 나온 경우.

    사용자가 `본회의 표결 결과`라고 직접 골랐는데도 제목에 위원회 이름이
    있다는 이유로 값이 통째로 사라졌다. 값이 사라지면 충돌도 사라진다.
    """
    a = "# 문화체육관광위원회 소관 의안 처리 결과\n\n- 의결일: 2026. 8. 20.\n- 찬성: 201명\n"
    b = "# 문화체육관광위원회 소관 의안 처리 결과\n\n- 의결일: 2026. 8. 21.\n- 찬성: 202명\n"
    run = await _run_flow(
        [
            ("표결 결과 갑", a, SourceRole.PLENARY_VOTE_RESULT),
            ("표결 결과 을", b, SourceRole.PLENARY_VOTE_RESULT),
        ]
    )
    subjects = [i.subject for i in run.issues if i.code.value == "FACT_CONFLICT"]
    assert "plenary_decided_on" in subjects, f"제목 때문에 충돌이 사라졌습니다: {subjects}"
    assert "vote_yes_count" in subjects, f"제목 때문에 충돌이 사라졌습니다: {subjects}"
    assert run.draft_version == 0


@pytest.mark.asyncio
async def test_본회의_줄이_섞인_문서는_제목만으로_버리지_않는다() -> None:
    """위원회 제목 아래에 본회의 줄이 함께 있는 자료가 실제로 있다."""
    mixed = (
        "# 문화체육관광위원회 소관\n\n"
        "- 소관위 심사: 처리일 2025. 9. 18., 처리결과 원안가결\n"
        "- 본회의 심의: 2025. 9. 25. 원안가결\n"
    )
    other = "- 본회의 심의: 2025. 9. 26. 원안가결\n"
    run = await _run_flow(
        [
            ("의안정보", mixed, SourceRole.BILL_INFORMATION),
            ("표결 결과", other, SourceRole.PLENARY_VOTE_RESULT),
        ]
    )
    subjects = [i.subject for i in run.issues if i.code.value == "FACT_CONFLICT"]
    assert "plenary_decided_on" in subjects, (
        f"위원회 제목 때문에 본회의 값이 사라졌습니다: {subjects}"
    )


@pytest.mark.asyncio
async def test_상한을_넘으면_잘라내지_않고_멈춘다() -> None:
    """검증에서 나온 경우.

    자료가 많아 상한을 넘으면 뒤쪽 값을 조용히 잘라냈고, 그 값에 걸려 있던
    충돌도 함께 사라졌다. 고정 형식이 이 경우를 위해 `FACT_SCOPE_TOO_LARGE`를
    두었으므로 잘라내지 않고 멈춘다.
    """
    big = "\n".join(f"의안번호: {2000 + i}\n제{i}조를 고친다." for i in range(40))
    run = await _run_flow([("큰 자료", big, SourceRole.BILL_INFORMATION)])

    # 잘라내지 않고 멈춘다. 다만 프로그램 고장이 아니라 지원 범위를 넘은
    # 자료이므로, 기술 오류가 아니라 사람에게 묻는 자리로 보낸다.
    assert run.state == "NEEDS_INPUT", (
        f"상한을 넘겼는데 멈추지 않았습니다: {run.state} / {run.failure_code}"
    )
    assert run.failure_kind is None, "지원 범위 초과를 기술 오류로 보여 줬습니다."
    assert run.issues and run.issues[0].subject == "UNSUPPORTED_SCOPE"
    assert "범위" in run.issues[0].message
    assert run.draft_version == 0
    assert run.fact_ledger is None or run.fact_ledger.facts == []


@pytest.mark.asyncio
async def test_의안_이름의_위원회_때문에_비교에서_빠지지_않는다() -> None:
    """검증에서 나온 경우.

    `문화체육관광위원회 대안 「…법률안」 의결일: …`처럼 의안 이름에 위원회가
    들어가면 본회의 값에 위원회 이름표가 붙어 비교에서 빠졌다. 값은 화면에
    남아 있는데 충돌만 조용히 사라진다.

    이름표를 붙이는 것은 그 값을 비교에서 빼는 권한이다. 그 권한은 사용자가
    고른 역할로만 쓰고, 본문 글자로 짐작하지 않는다.
    """
    a = "- 문화체육관광위원회 대안 「문화예술진흥법 일부개정법률안」 의결일: 2026. 8. 20.\n"
    b = "- 문화체육관광위원회 대안 「문화예술진흥법 일부개정법률안」 의결일: 2026. 8. 21.\n"
    run = await _run_flow(
        [("갑", a, SourceRole.BILL_INFORMATION), ("을", b, SourceRole.BILL_INFORMATION)]
    )
    kinds = {f.kind for f in run.fact_ledger.facts}
    assert not any(k.startswith("COMMITTEE_") for k in kinds), (
        f"본문 글자만 보고 비교에서 뺐습니다: {sorted(kinds)}"
    )
    conflicts = [i for i in run.issues if i.code.value == "FACT_CONFLICT"]
    assert conflicts, "의안 이름 때문에 충돌이 사라졌습니다."


@pytest.mark.asyncio
async def test_입법_사건도_다른_자료의_근거를_빌리지_못한다() -> None:
    """사실과 같은 기준을 사건·부칙·의안에도 적용한다."""
    from app.harness.fact_contracts import RawLegislativeEvent

    normalized, names = _sources()
    normalized["SRC-02"] = normalize_source("전혀 다른 자료입니다.\n")
    names["SRC-02"] = "다른 자료"
    raw = _result(
        evidence=[
            EvidenceCandidate(
                evidence_id="EV-01", source_id="SRC-01", quote="의안번호: 2207285"
            )
        ],
        legislative_events=[
            RawLegislativeEvent(
                event_id="E-01",
                bill_id="B-01",
                procedure_stage="PLENARY_DECIDED",
                disposition="REJECTED",
                occurred_on="2099-01-01",
                source_id="SRC-02",  # 근거는 SRC-01에 있다
                evidence_id="EV-01",
                valid_source_role_candidate_ids=[],
            )
        ],
    )
    ledger, evidence = build_fact_ledger(raw, normalized, names)
    assert ledger.legislative_events == [], "다른 자료의 근거를 빌려 원장에 남았습니다."
    assert any(p.kind == "UNKNOWN_SOURCE" for p in evidence.problems)


# ---------------------------------------------------------------------------
# Gate가 어떤 입력이 와도 값과 충돌을 잃지 않는가
#
# 아래 시험은 가짜 추출기를 거치지 않고 사실 묶음을 직접 넣는다. 추출기가
# 무엇을 뽑든 Gate는 같은 동작을 해야 한다.
# ---------------------------------------------------------------------------


def _fact(fact_id: str, kind: str, value, source_id: str, evidence_id: str) -> RawFact:
    return RawFact(
        fact_id=fact_id,
        kind=kind,
        value=value,
        source_id=source_id,
        evidence_id=evidence_id,
    )


@pytest.mark.asyncio
async def test_지어낸_근거로_만든_사실이_조용히_사라지지_않는다() -> None:
    """AI가 원문에 없는 문장을 지어냈을 때 밟는 길.

    6일차에 진짜 AI를 붙이면 가장 자주 만나는 경우다. 버린 사실을 알리지
    않으면 그 값에 걸려 있던 충돌도 함께 사라져, 시스템이 말없이 한쪽을
    고른 것과 같아진다.
    """
    from app.harness.orchestrator import _rejected_fact_issues

    normalized, names = _sources()
    raw = _result(
        evidence=[
            EvidenceCandidate(
                evidence_id="EV-01", source_id="SRC-01", quote="원문에 없는 문장"
            )
        ],
        facts=[_fact("F-01", "BILL_IDENTITY", "2207285", "SRC-01", "EV-01")],
    )
    ledger, evidence = build_fact_ledger(raw, normalized, names)
    assert ledger.facts == []

    issues = _rejected_fact_issues(evidence)
    assert issues, "지어낸 근거로 만든 사실이 알림 없이 사라졌습니다."
    assert "2207285" in issues[0].message, "어떤 값이 빠졌는지 보여 주지 않았습니다."


@pytest.mark.asyncio
async def test_없는_자료를_가리켜도_알린다() -> None:
    from app.harness.orchestrator import _rejected_fact_issues

    normalized, names = _sources()
    raw = _result(
        evidence=[
            EvidenceCandidate(
                evidence_id="EV-01", source_id="SRC-없음", quote="의안번호: 2207285"
            )
        ],
        facts=[_fact("F-01", "BILL_IDENTITY", "2207285", "SRC-없음", "EV-01")],
    )
    ledger, evidence = build_fact_ledger(raw, normalized, names)
    assert ledger.facts == []
    assert _rejected_fact_issues(evidence), "없는 자료를 가리킨 사실이 조용히 사라졌습니다."


def test_값이_목록이어도_원장이_죽지_않는다() -> None:
    """고정 형식과 Pydantic이 목록 값을 허용한다. Gate도 받아야 한다.

    받지 못하면 Run 전체가 죽으면서 같은 작업의 정상 사실과 진짜 충돌까지
    함께 사라진다.
    """
    normalized, names = _sources()
    raw = _result(
        evidence=[
            EvidenceCandidate(
                evidence_id="EV-01", source_id="SRC-01", quote="의안번호: 2207285"
            )
        ],
        facts=[
            _fact(
                "F-01",
                "SUPPLEMENTARY_EFFECTIVE_DATES",
                ["2026-01-01", "2026-07-01"],
                "SRC-01",
                "EV-01",
            )
        ],
    )
    ledger, _ = build_fact_ledger(raw, normalized, names)
    assert len(ledger.facts) == 1
    assert ledger.facts[0].normalized_value == "2026-01-01, 2026-07-01"


def test_버린_사실은_값과_자료명과_행을_함께_남긴다() -> None:
    """내부 ID만 남기면 화면에서 아무도 알아볼 수 없다."""
    text = "의안번호: 2207285\n의안번호: 2207285\n"
    normalized = {"SRC-01": normalize_source(text)}
    raw = _result(
        evidence=[
            EvidenceCandidate(
                evidence_id="EV-01", source_id="SRC-01", quote="의안번호: 2207285"
            )
        ],
        facts=[_fact("F-01", "BILL_IDENTITY", "2207285", "SRC-01", "EV-01")],
    )
    _, evidence = build_fact_ledger(raw, normalized, {"SRC-01": "의안정보"})
    problem = evidence.problems[-1]
    described = problem.describe()
    for expected in ("2207285", "의안정보", "1행"):
        assert expected in described, f"버린 기록에 `{expected}`가 없습니다: {described}"


def test_부칙과_의안도_다른_자료의_근거를_빌리지_못한다() -> None:
    """사실만이 아니라 다섯 목록 전부에 같은 기준을 적용한다."""
    from app.harness.fact_contracts import RawBillIdentity, RawSupplementaryRule

    normalized, names = _sources()
    normalized["SRC-02"] = normalize_source("다른 자료입니다.\n")
    names["SRC-02"] = "다른 자료"
    evidence_item = EvidenceCandidate(
        evidence_id="EV-01", source_id="SRC-01", quote="의안번호: 2207285"
    )
    raw = _result(
        evidence=[evidence_item],
        supplementary_rules=[
            RawSupplementaryRule(
                rule_id="R-01",
                kind="EFFECTIVE_DATE",
                applies_to="법률 전체",
                source_id="SRC-02",
                evidence_id="EV-01",
                valid_source_role_candidate_ids=[],
            )
        ],
        bill_identities=[
            RawBillIdentity(
                bill_id="B-01",
                bill_number="2207285",
                is_draft_subject=True,
                source_id="SRC-02",
                evidence_ids=["EV-01"],
            )
        ],
    )
    ledger, _ = build_fact_ledger(raw, normalized, names)
    assert ledger.supplementary_rules == [], "부칙이 다른 자료의 근거를 빌렸습니다."
    assert ledger.bill_identities == [], "의안이 다른 자료의 근거를 빌렸습니다."


def test_조문_비교도_현행과_최종_자료를_각각_대조한다() -> None:
    from app.harness.fact_contracts import RawProvisionComparison

    normalized, names = _sources()
    normalized["SRC-02"] = normalize_source("현행 조문입니다.\n")
    names["SRC-02"] = "현행 조문"
    raw = _result(
        evidence=[
            EvidenceCandidate(
                evidence_id="EV-01", source_id="SRC-01", quote="의안번호: 2207285"
            )
        ],
        provision_comparisons=[
            RawProvisionComparison(
                comparison_id="PC-01",
                provision_id="제7조",
                current_source_id="SRC-02",  # 근거는 SRC-01에 있다
                current_evidence_id="EV-01",
                final_source_id="SRC-01",
                final_evidence_id="EV-01",
            )
        ],
    )
    ledger, evidence = build_fact_ledger(raw, normalized, names)
    assert ledger.provision_comparisons == [], "조문 비교가 다른 자료의 근거를 빌렸습니다."
    assert any(p.fact_id == "PC-01" for p in evidence.problems)
