"""진짜 AI를 부를 때 **보내기 전에 막는지** 본다.

여기 시험은 **외부 호출을 한 번도 하지 않는다.** 막는 자리를 재는 것이라
실제로 보낼 필요가 없다. 보내야만 확인되는 것은 사람이 별도 확인 뒤에 한다
(README §7.2 시연 예산).

지키는 성질 셋.

1. 돈이 한도를 넘으면 **보내기 전에** 멈춘다.
2. 다른 모델이 응답하면 **다음 호출을 막는다.** 더 비싼 모델을 조용히
   계속 쓰지 않는다.
3. 진짜 AI는 **기본으로 꺼져 있다.** 사람이 켜야 켜진다.
"""

from __future__ import annotations

import pytest

from app.infrastructure.model_gateway import CONFIGURED_MODEL, ModelCallRequest
from app.infrastructure.openai_gateway import (
    ExternalCallBlocked,
    OpenAIModelGateway,
    actual_usd,
    live_enabled,
    reserve_usd,
)


def _request(agent: str = "DraftWritingAgent", tokens: int = 4500) -> ModelCallRequest:
    return ModelCallRequest(
        agent_name=agent,
        prompt_version="v1",
        payload={"hello": "world"},
        max_output_tokens=tokens,
    )


def test_진짜_AI는_기본으로_꺼져_있다(monkeypatch) -> None:
    """켜 두면 시험을 돌릴 때마다 자료가 나가고 돈이 든다."""
    monkeypatch.delenv("POLICY_AGENT_LIVE", raising=False)
    assert live_enabled() is False

    monkeypatch.setenv("POLICY_AGENT_LIVE", "1")
    assert live_enabled() is True


def test_보내기_전에_최대_비용을_센다() -> None:
    """예약값은 **최대**다. 보낸 뒤에 아는 것은 늦다."""
    draft = reserve_usd("DraftWritingAgent", 4500)
    facts = reserve_usd("FactExtractionAgent", 12_000)
    # 사실 추출이 초안보다 입력·출력이 모두 크므로 더 비싸야 한다.
    assert facts > draft > 0


def test_한도를_넘으면_보내지_않는다() -> None:
    """`BUDGET_EXCEEDED`만 겨눈다. **호출 0회**로 막혀야 한다."""
    import asyncio

    gateway = OpenAIModelGateway(budget_usd=0.001)
    with pytest.raises(ExternalCallBlocked) as caught:
        asyncio.run(gateway.call(_request()))

    assert caught.value.code == "BUDGET_EXCEEDED"
    assert gateway.calls == 0, "막았다면서 실제로 불렀습니다."
    assert gateway.spent_usd == 0.0
    # 사람에게는 쉬운 말로 알린다.
    assert not caught.value.message.isascii()
    assert caught.value.next_action


def test_다른_모델이_오면_다음_호출을_막는다() -> None:
    """`MODEL_CONFIG_MISMATCH`만 겨눈다.

    더 비싸거나 다른 모델을 조용히 계속 쓰지 않는다 (README §7.2).
    """
    import asyncio

    gateway = OpenAIModelGateway(budget_usd=10.0)
    gateway.mismatch = "gpt-다른모델"
    with pytest.raises(ExternalCallBlocked) as caught:
        asyncio.run(gateway.call(_request()))

    assert caught.value.code == "MODEL_CONFIG_MISMATCH"
    assert gateway.calls == 0


def test_키가_없으면_부르지_않는다(monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("OPENAI_API_KEY", "")
    gateway = OpenAIModelGateway(budget_usd=10.0)
    with pytest.raises(ExternalCallBlocked) as caught:
        asyncio.run(gateway.call(_request()))

    assert caught.value.code == "API_KEY_MISSING"
    assert gateway.calls == 0


def test_실제_사용량으로_비용을_다시_센다() -> None:
    """예약은 최대치이고, 응답이 오면 **실제로 쓴 만큼**으로 고친다."""
    reserved = reserve_usd("DraftWritingAgent", 4500)
    real = actual_usd(input_tokens=2_000, output_tokens=800)
    assert real < reserved, "실제 사용량이 예약값보다 크면 예약이 뜻이 없습니다."
    assert real > 0


def test_모델_이름을_실행_중에_바꾸지_않는다() -> None:
    """Run 시작 뒤 자동 모델 변경 금지 (README §7.2)."""
    assert CONFIGURED_MODEL == "gpt-5.6-terra"
