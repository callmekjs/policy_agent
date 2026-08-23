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
                subject="plenary_decided_on",
                value=value_a,
                source_id="SRC-01",
                evidence_id="EV-01",
            ),
            RawFact(
                fact_id="F-02",
                kind=kind,
                subject="plenary_decided_on",
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


@pytest.mark.asyncio
async def test_위원회_표결_수를_본회의_것과_섞지_않는다() -> None:
    """서로 다른 회의의 표결 수를 같은 항목으로 보면 정상 자료가 막힌다."""
    committee = (
        "# 소관위 심사 결과\n\n"
        "- 의결일: 2025. 9. 18.\n"
        "- 재석: 24명\n"
        "- 찬성: 24명\n"
        "- 반대: 0명\n"
    )
    plenary = (
        "# 본회의 표결 결과\n\n"
        "- 의결일: 2025. 9. 25.\n"
        "- 재석: 205명\n"
        "- 찬성: 201명\n"
        "- 반대: 3명\n"
    )
    run = await _run_flow(
        [
            ("소관위 심사 결과", committee, SourceRole.BILL_INFORMATION),
            ("본회의 표결 결과", plenary, SourceRole.PLENARY_VOTE_RESULT),
        ]
    )
    blocking = [i for i in run.issues if i.severity.value == "BLOCKING"]
    assert blocking == [], (
        "서로 다른 회의의 값을 충돌로 보고 정상 자료를 막았습니다: "
        f"{[i.subject for i in blocking]}"
    )
