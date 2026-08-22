"""Harness 상태·멱등·TTL·입력 Gate 검사 (README §2.11, §2.13, §3.10).

가짜 ModelGateway만 쓴다. 외부 AI 호출은 0회이고 비용은 0달러다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.runs import REQUEST_TOKEN_COOKIE
from app.gates.input_gate import check_input
from app.harness.contract_loader import ContractLoadError, load_writing_contract
from app.harness.contracts import (
    EXTERNAL_AI_POLICY_VERSION,
    MAX_SOURCES,
    CreateRunRequest,
    Disclosure,
    IssueCode,
    Run,
    SourceInput,
)
from app.harness.states import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransition,
    RunState,
    assert_transition,
    can_transition,
)
from app.infrastructure.run_store import RunStore
from app.main import create_app

TODAY = date(2026, 8, 22)


# ---------------------------------------------------------------------------
# 상태 머신
# ---------------------------------------------------------------------------


def test_모든_상태가_전이표에_있다() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(RunState)


def test_종료_상태에서는_어떤_전이도_없다() -> None:
    for state in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == frozenset()


def test_허용된_전이만_통과한다() -> None:
    assert can_transition(RunState.CREATED, RunState.VALIDATING_INPUT)
    assert can_transition(RunState.VALIDATING_INPUT, RunState.NEEDS_INPUT)
    assert not can_transition(RunState.CREATED, RunState.DRAFT_READY)
    assert not can_transition(RunState.FAILED, RunState.VALIDATING_INPUT)


def test_허용되지_않은_전이는_거부된다() -> None:
    with pytest.raises(InvalidTransition):
        assert_transition(RunState.CREATED, RunState.REVIEW_READY)


def test_초안_준비_완료는_승인_상태가_아니다() -> None:
    """DRAFT_READY에서 다른 상태로 갈 수 없어야 한다."""
    assert ALLOWED_TRANSITIONS[RunState.DRAFT_READY] == frozenset()


# ---------------------------------------------------------------------------
# 입력 Gate
# ---------------------------------------------------------------------------


def _request(**overrides: object) -> CreateRunRequest:
    payload: dict[str, object] = {
        "client_request_id": "req-1",
        "purpose": "문화예술진흥법 일부개정법률안 본회의 의결 결과를 알리려고 합니다.",
        "disclosure": Disclosure.PUBLIC,
        "basis_date": TODAY,
        "sources": [SourceInput(text="의안번호 2207285 원안가결")],
        "external_ai_policy_version": EXTERNAL_AI_POLICY_VERSION,
        "external_ai_transfer_confirmed": True,
    }
    payload.update(overrides)
    return CreateRunRequest(**payload)  # type: ignore[arg-type]


def test_정상_입력은_통과한다() -> None:
    assert check_input(_request(), TODAY) == []


def test_보도_목적이_비면_차단한다() -> None:
    issues = check_input(_request(purpose="   "), TODAY)
    assert [i.code for i in issues] == [IssueCode.REQUIRED_INPUT_MISSING]
    assert issues[0].subject == "PURPOSE"


def test_공식_자료가_없으면_차단한다() -> None:
    issues = check_input(_request(sources=[]), TODAY)
    assert any(i.code is IssueCode.REQUIRED_SOURCE_MISSING for i in issues)


def test_내부_엠바고는_차단하고_새_작업을_요구한다() -> None:
    issues = check_input(_request(disclosure=Disclosure.EMBARGO), TODAY)
    blocking = [i for i in issues if i.code is IssueCode.DISCLOSURE_NOT_ALLOWED]
    assert len(blocking) == 1
    assert blocking[0].requires_new_run is True


def test_전송_확인_전에는_차단한다() -> None:
    issues = check_input(_request(external_ai_transfer_confirmed=False), TODAY)
    assert any(i.subject == "EXTERNAL_AI_TRANSFER_CONFIRMED" for i in issues)


def test_오래된_정책_버전은_차단한다() -> None:
    issues = check_input(_request(external_ai_policy_version="old_v0"), TODAY)
    assert any(i.subject == "EXTERNAL_AI_POLICY_VERSION" for i in issues)


def test_기준일이_미래면_차단한다() -> None:
    issues = check_input(_request(basis_date=TODAY + timedelta(days=1)), TODAY)
    assert any(i.subject == "BASIS_DATE" for i in issues)


def test_자료_개수_초과를_차단한다() -> None:
    sources = [SourceInput(text=f"자료 {i}") for i in range(MAX_SOURCES + 1)]
    issues = check_input(_request(sources=sources), TODAY)
    assert any(i.subject == "SOURCE_COUNT" for i in issues)


def test_분량_초과를_차단한다() -> None:
    issues = check_input(_request(sources=[SourceInput(text="가" * 30_001)]), TODAY)
    assert any(i.subject == "SOURCE_TOTAL_CHARS" for i in issues)


def test_issue의_code와_subject를_한_문자열로_합치지_않는다() -> None:
    issues = check_input(_request(sources=[]), TODAY)
    for issue in issues:
        assert issue.subject not in issue.code.value


# ---------------------------------------------------------------------------
# Writing Contract
# ---------------------------------------------------------------------------


def test_다섯_파일을_모두_읽는다() -> None:
    contract = load_writing_contract()
    assert contract.full_id == "assembly_member_partial_amendment_plenary_v1@1.0.0"
    for name in ("profile", "template", "style", "validation"):
        assert contract.component_full_id(name).endswith("@1.0.0")


def test_manifest와_실제_버전이_다르면_거부한다(tmp_path) -> None:
    import shutil

    from app.harness.contract_loader import CONTRACT_DIR

    shutil.copytree(CONTRACT_DIR, tmp_path / "c")
    target = tmp_path / "c" / "style.yaml"
    target.write_text(
        target.read_text(encoding="utf-8").replace("version: 1.0.0", "version: 9.9.9", 1),
        encoding="utf-8",
    )
    with pytest.raises(ContractLoadError):
        load_writing_contract(tmp_path / "c")


def test_제외_규칙은_검사기가_실행하지_않는다() -> None:
    contract = load_writing_contract()
    excluded = {r["id"] for r in contract.manifest["excluded_rules"]}
    assert excluded == set(contract.validation["never_execute_rules"])
    executed = {r["id"] for r in contract.manifest["rules"]}
    assert excluded.isdisjoint(executed)


def test_양식에_부제와_기관소개를_만들지_않는다() -> None:
    contract = load_writing_contract()
    assert "SUBTITLE" in contract.template["forbidden_sections"]
    assert "INSTITUTION_INTRO" in contract.template["forbidden_sections"]


def test_금지_표현은_validation이_한_곳에서만_정의한다() -> None:
    contract = load_writing_contract()
    forbidden = contract.validation["forbidden_phrases"]
    assert "최종본" in forbidden["always"]
    # profile과 template은 금지 표현을 다시 정의하지 않는다.
    assert "forbidden_phrases" not in contract.profile
    assert "forbidden_phrases" not in contract.template


# ---------------------------------------------------------------------------
# RunStore와 TTL
# ---------------------------------------------------------------------------


def _run(run_id: str, at: datetime) -> Run:
    return Run(
        run_id=run_id,
        state=RunState.NEEDS_INPUT.value,
        created_at=at,
        updated_at=at,
        last_user_action_at=at,
        client_request_id=f"cid-{run_id}",
        purpose="테스트",
        disclosure=Disclosure.PUBLIC,
        basis_date=TODAY,
    )


def test_2시간이_지나면_조회되지_않는다() -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    current = {"t": now}
    store = RunStore(clock=lambda: current["t"])
    store.put(_run("RUN-A", now))

    current["t"] = now + timedelta(hours=1, minutes=59)
    assert store.get("RUN-A") is not None

    current["t"] = now + timedelta(hours=2)
    assert store.get("RUN-A") is None


def test_같은_멱등키는_기존_Run을_돌려준다() -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    store = RunStore(clock=lambda: now)
    run = _run("RUN-A", now)
    store.put(run)
    assert store.find_by_client_request_id("cid-RUN-A") is run
    assert store.find_by_client_request_id("cid-없음") is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    # 127.0.0.1로 요청해야 한다. 다른 host는 서버가 막는다.
    with TestClient(create_app(), base_url="http://127.0.0.1") as test_client:
        yield test_client


def _bootstrap(client: TestClient) -> dict:
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    assert client.cookies.get(REQUEST_TOKEN_COOKIE)
    return response.json()


def test_health가_가짜_게이트웨이를_보고한다(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["model_gateway"] == "fake"
    assert body["external_api_calls"] == 0
    assert len(body["writing_contract_files"]) == 5


def test_bootstrap은_비밀값을_돌려주지_않는다(client: TestClient) -> None:
    body = _bootstrap(client)
    text = str(body)
    assert "sk-" not in text
    assert "api_key" not in text
    assert body["external_ai"]["model"] == "gpt-5.6-terra"


def test_요청_쿠키가_없으면_생성이_거부된다(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "client_request_id": "req-1",
            "purpose": "본회의 의결 결과를 알리려고 합니다.",
            "disclosure": "PUBLIC",
            "basis_date": TODAY.isoformat(),
            "sources": [{"text": "의안번호 2207285"}],
            "external_ai_policy_version": EXTERNAL_AI_POLICY_VERSION,
            "external_ai_transfer_confirmed": True,
        },
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "REQUEST_TOKEN_INVALID"


def _create(client: TestClient, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "client_request_id": "req-1",
        "purpose": "문화예술진흥법 일부개정법률안 본회의 의결 결과를 알리려고 합니다.",
        "disclosure": "PUBLIC",
        "basis_date": TODAY.isoformat(),
        "sources": [{"text": "의안번호 2207285 원안가결"}],
        "external_ai_policy_version": EXTERNAL_AI_POLICY_VERSION,
        "external_ai_transfer_confirmed": True,
    }
    payload.update(overrides)
    return client.post("/api/runs", json=payload).json()


def test_부족한_입력은_초안_없이_보완_안내로_끝난다(client: TestClient) -> None:
    _bootstrap(client)
    created = _create(client, purpose="")
    run_id = created["run_id"]

    for _ in range(50):
        body = client.get(f"/api/runs/{run_id}").json()
        if body["state"] not in {"CREATED", "VALIDATING_INPUT"}:
            break

    assert body["state"] == "NEEDS_INPUT"
    assert body["status_label"] == "입력 보완 필요"
    assert body["draft_version"] == 0
    assert body["actual_model_calls"] == 0
    assert body["estimated_cost_usd"] == 0
    assert any(i["subject"] == "PURPOSE" for i in body["issues"])


def test_정상_입력은_1일차_범위에서_정직하게_멈춘다(client: TestClient) -> None:
    _bootstrap(client)
    created = _create(client)
    run_id = created["run_id"]

    for _ in range(50):
        body = client.get(f"/api/runs/{run_id}").json()
        if body["state"] in {"NEEDS_INPUT", "FAILED", "REVIEW_READY"}:
            break

    # 아직 만들지 않은 단계를 성공한 것처럼 보여주지 않는다.
    assert body["state"] == "FAILED"
    assert body["failure"]["kind"] == "TECHNICAL"
    assert body["failure"]["code"] == "DAY1_SCOPE_LIMIT"
    assert body["draft_version"] == 0
    assert body["actual_model_calls"] == 0


def test_같은_멱등키_같은_내용은_한_번만_만들어진다(client: TestClient) -> None:
    _bootstrap(client)
    first = _create(client)
    second = _create(client)
    assert first["run_id"] == second["run_id"]


def test_같은_멱등키_다른_내용은_거부한다(client: TestClient) -> None:
    """README §2.13 — 같은 키·다른 payload는 409 IDEMPOTENCY_KEY_REUSED."""
    _bootstrap(client)
    _create(client)
    body = _create(client, purpose="완전히 다른 보도 목적을 적어 같은 키로 다시 보냅니다.")
    assert body["error_code"] == "IDEMPOTENCY_KEY_REUSED"


def test_없는_Run은_404를_돌려준다(client: TestClient) -> None:
    body = client.get("/api/runs/RUN-없음")
    assert body.status_code == 404
    assert body.json()["error_code"] == "RUN_NOT_FOUND"


def test_삭제_뒤_조회는_404다(client: TestClient) -> None:
    _bootstrap(client)
    created = _create(client)
    run_id = created["run_id"]
    for _ in range(50):
        if client.get(f"/api/runs/{run_id}").json()["state"] in {"NEEDS_INPUT", "FAILED"}:
            break
    assert client.delete(f"/api/runs/{run_id}").status_code == 200
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_승인_배포_경로가_없다(client: TestClient) -> None:
    """README §4.2 — 최종본·승인·게시·배포 기능이 있으면 안 된다."""
    def collect(routes: object, found: set[str]) -> set[str]:
        for route in routes:  # type: ignore[attr-defined]
            path = getattr(route, "path", None)
            if isinstance(path, str):
                found.add(path)
            # include_router로 붙인 라우터는 original_router 안에 들어 있다.
            nested = getattr(route, "routes", None) or getattr(
                getattr(route, "original_router", None), "routes", None
            )
            if nested:
                collect(nested, found)
        return found

    paths = collect(client.app.routes, set())  # type: ignore[attr-defined]
    assert "/api/runs" in paths, "실행 API 경로를 찾지 못했습니다."
    for banned in ("approve", "publish", "final", "distribute", "send"):
        assert not any(banned in p for p in paths), f"금지된 경로가 있습니다: {banned}"


@pytest.mark.parametrize(
    "path",
    [
        "/../../.env",
        "/../../.gitignore",
        "/../../backend/app/main.py",
        "/%2e%2e/%2e%2e/.env",
        "/..%2f..%2f.env",
        "/assets/../../../.env",
        "/C:/Windows/win.ini",
    ],
)
def test_빌드_폴더_밖의_파일을_돌려주지_않는다(client: TestClient, path: str) -> None:
    """경로 탈출로 .env·소스 파일이 새어 나가면 안 된다.

    화면이 빌드되어 있지 않으면 catch-all 자체가 없으므로 404가 정상이다.
    빌드되어 있으면 첫 화면(index.html)으로 되돌려 보내야 한다.
    """
    response = client.get(path)
    assert response.status_code in (200, 404, 405)
    if response.status_code != 200:
        return

    body = response.content
    # 디스크의 실제 파일 내용이 그대로 나오면 안 된다.
    for leaked in (b"OPENAI_API_KEY", b"FastAPI", b"node_modules", b"[extensions]"):
        assert leaked not in body, f"{path}에서 파일이 새어 나갔습니다."
    assert b'<div id="root">' in body, f"{path}가 첫 화면으로 되돌아가지 않았습니다."


def test_contract_API가_읽은_설정을_보여준다(client: TestClient) -> None:
    body = client.get("/api/contract").json()
    assert body["loaded_id"] == body["contract_id"]
    assert len(body["files"]) == 5
    assert "DRAFT_MARK" in body["required_sections"]
