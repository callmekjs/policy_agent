"""누적 5일차 — 화면이 부르는 길이 끝에서 끝까지 도는지 본다.

여기서는 검사기 하나를 겨누지 않는다. **사람이 실제로 하는 순서**를 그대로
밟는다. 자료를 넣고, 초안을 받고, 사실을 확인하고, 고쳐 달라고 하고,
내려받는다.

단위 시험이 다 통과해도 이 길이 안 이어지면 사람은 아무것도 못 한다.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.runs import REQUEST_TOKEN_COOKIE
from app.harness.contracts import EXTERNAL_AI_POLICY_VERSION
from app.main import create_app

from test_draft import FIXTURE, INTRODUCED_SOURCE_ID, PASS_SOURCES

TODAY = date(2025, 10, 26)


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app(), base_url="http://127.0.0.1") as test_client:
        yield test_client


def _sources() -> list[dict[str, str]]:
    """고정 정상 자료를 화면이 보내는 모양으로 만든다.

    **역할을 함께 보낸다.** 역할이 없으면 어느 자료가 발의안인지 코드가 알 수
    없어 초안 전에 사람에게 물으며 멈춘다.
    """
    return [
        {
            "display_name": name,
            "text": (FIXTURE / "sources" / filename).read_text(encoding="utf-8"),
            "role": role.value,
        }
        for name, filename, role in PASS_SOURCES
    ]


def _ready(client: TestClient) -> dict:
    """초안이 나온 작업 하나."""
    client.get("/api/bootstrap")
    assert client.cookies.get(REQUEST_TOKEN_COOKIE)
    created = client.post(
        "/api/runs",
        json={
            "client_request_id": "flow-1",
            "purpose": "문화예술진흥법 일부개정법률안의 본회의 의결 결과를 알리는 초안",
            "disclosure": "PUBLIC",
            "basis_date": TODAY.isoformat(),
            "sources": _sources(),
            "announcement_subject": "조계원 의원실",
            "external_ai_policy_version": EXTERNAL_AI_POLICY_VERSION,
            "external_ai_transfer_confirmed": True,
            "final_text_completeness_confirmations": [
                {"source_id": INTRODUCED_SOURCE_ID, "confirmed": True}
            ],
        },
    ).json()
    run = client.get(f"/api/runs/{created['run_id']}").json()
    assert run["state"] == "REVIEW_READY", run.get("failure") or run["state"]
    return run


def _confirm_all(client: TestClient, run: dict, verdict: str = "OK") -> dict:
    return client.post(
        f"/api/runs/{run['run_id']}/fact-review",
        json={
            "reviews": [
                {"fact_id": f["fact_id"], "verdict": verdict} for f in run["facts"]
            ]
        },
    ).json()


def test_확인하기_전에는_내려받을_수_없다(client: TestClient) -> None:
    """`M1`. 확인 절차가 있는데 건너뛸 수 있으면 없는 것과 같다."""
    run = _ready(client)
    assert run["can_download"] is False
    assert run["unreviewed_fact_ids"], "확인할 사실 목록이 비어 있습니다."

    response = client.get(f"/api/runs/{run['run_id']}/draft.md")
    assert response.status_code == 409
    assert response.json()["error_code"] == "REVIEW_NOT_FINISHED"


def test_다_확인하면_내려받을_수_있다(client: TestClient) -> None:
    """`M1`·`M2`. 내려받은 파일에 `DRAFT` 표시가 반드시 있어야 한다."""
    run = _ready(client)
    after = _confirm_all(client, run)
    assert after["can_download"] is True
    assert after["unreviewed_fact_ids"] == []

    response = client.get(f"/api/runs/{run['run_id']}/draft.md")
    assert response.status_code == 200
    text = response.text
    assert "DRAFT / 내부 검토용" in text, "내려받은 파일에 DRAFT 표시가 없습니다."
    assert after["draft"]["title"]["text"] in text


def test_확인이_보호를_만든다(client: TestClient) -> None:
    """README §4.3 — Agent는 보호 여부를 정하지 않는다."""
    run = _ready(client)
    assert run["protected_candidate_fact_ids"], "보호 후보가 하나도 없습니다."
    after = _confirm_all(client, run)
    assert len(after["fact_reviews"]) == len(run["facts"])


def test_안전한_수정은_새_판이_되고_기록이_남는다(client: TestClient) -> None:
    """`N1`·`N3`."""
    run = _ready(client)
    _confirm_all(client, run)
    before = run["draft_version"]
    after = client.post(
        f"/api/runs/{run['run_id']}/revisions",
        json={"client_request_id": "rev-1", "instruction": "순서를 바꿔 주세요"},
    ).json()

    assert after["draft_version"] == before + 1
    assert after["previous_versions"] == [before]
    assert after["revision_attempts"][-1]["outcome"] == "APPLIED"


def test_값이_사라지는_수정은_막히고_이전_초안이_남는다(client: TestClient) -> None:
    """`K4`. 가장 중요한 성질이다.

    고치는 데 실패했다고 **멀쩡하던 초안까지 잃으면 안 된다.** 그리고 왜
    막혔는지 화면이 보여 줄 수 있어야 한다.
    """
    run = _ready(client)
    _confirm_all(client, run)
    before_version = run["draft_version"]
    before_title = run["draft"]["title"]["text"]

    after = client.post(
        f"/api/runs/{run['run_id']}/revisions",
        json={"client_request_id": "rev-1", "instruction": "짧게 줄여 주세요"},
    ).json()

    assert after["draft_version"] == before_version, "실패한 수정이 판을 올렸습니다."
    assert after["draft"]["title"]["text"] == before_title, "이전 초안이 덮였습니다."
    attempt = after["revision_attempts"][-1]
    assert attempt["outcome"] == "REJECTED"
    assert attempt["blocking_rule_ids"], "왜 막혔는지 화면에 보낼 수 없습니다."


def test_완료하면_상태가_바뀐다(client: TestClient) -> None:
    run = _ready(client)
    _confirm_all(client, run)
    after = client.post(f"/api/runs/{run['run_id']}/complete").json()
    assert after["state"] == "DRAFT_READY"


def test_막힌_이유는_쉬운_말로_보인다(client: TestClient) -> None:
    """직접 써 보고 찾은 문제.

    화면에 `CLAIM_VALUE_NOT_ANCHORED` 같은 **영어 코드**만 보이면 사용자는
    무엇을 고쳐야 할지 알 수 없다. 코드는 되짚을 때만 쓰고, 사람에게는 쉬운
    말을 보여 준다.
    """
    run = _ready(client)
    _confirm_all(client, run)
    after = client.post(
        f"/api/runs/{run['run_id']}/revisions",
        json={"client_request_id": "rev-1", "instruction": "짧게 줄여 주세요"},
    ).json()

    attempt = after["revision_attempts"][-1]
    assert attempt["outcome"] == "REJECTED"
    assert attempt["blocking_messages"], "사람이 읽을 이유가 없습니다."
    for message in attempt["blocking_messages"]:
        assert not message.isascii(), f"영어 코드만 보입니다: {message}"
