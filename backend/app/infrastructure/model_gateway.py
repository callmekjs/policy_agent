"""외부 AI 호출의 유일한 경계 (README §2.12, §3.8).

브라우저는 AI를 직접 부르지 않는다. 모든 호출은 이 파일을 지나며,
여기서 모델·저장 설정·토큰·금액 한도를 강제한다.

1일차에는 `FakeModelGateway`만 사용한다. 실제 OpenAI 호출은 0회이고 비용은
0달러다. 진짜 게이트웨이는 2~6일차에 같은 인터페이스로 갈아 끼운다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.infrastructure.fake_agents import FAKE_RESPONDERS

#: README §7.2에서 확정한 1차 모델. 실행 중 자동 변경은 금지한다.
CONFIGURED_PROVIDER = "openai"
CONFIGURED_MODEL = "gpt-5.6-terra"


@dataclass(frozen=True)
class ModelCallRequest:
    """Harness가 Agent 한 번을 부를 때 넘기는 값."""

    agent_name: str
    prompt_version: str
    payload: dict[str, Any]
    max_output_tokens: int


@dataclass(frozen=True)
class ModelCallResult:
    """호출 1회의 결과. 원문·숨은 사고 과정은 담지 않는다."""

    agent_name: str
    requested_model: str
    actual_model: str
    result: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    estimated_cost_usd: float = 0.0
    is_fake: bool = True


class ModelGateway(Protocol):
    """진짜·가짜 게이트웨이가 함께 지키는 인터페이스."""

    @property
    def is_fake(self) -> bool: ...

    async def call(self, request: ModelCallRequest) -> ModelCallResult: ...


class FakeModelGateway:
    """미리 정해둔 답만 돌려주는 개발용 게이트웨이.

    - 인터넷과 OpenAI API를 호출하지 않는다.
    - 부를 때마다 같은 답을 준다. 그래야 버그와 AI의 문장 변화를 구분할 수 있다.
    - 비용은 항상 0달러다.
    """

    is_fake = True

    def __init__(self, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self._responses: dict[str, dict[str, Any]] = responses or {}
        self.calls: list[ModelCallRequest] = []

    def set_response(self, agent_name: str, result: dict[str, Any]) -> None:
        """특정 Agent가 돌려줄 고정 답을 등록한다. 규칙 기반 응답보다 우선한다."""
        self._responses[agent_name] = result

    async def call(self, request: ModelCallRequest) -> ModelCallResult:
        self.calls.append(request)
        return ModelCallResult(
            agent_name=request.agent_name,
            requested_model=CONFIGURED_MODEL,
            actual_model=CONFIGURED_MODEL,
            result=self._build(request),
            estimated_cost_usd=0.0,
            is_fake=True,
        )

    def _build(self, request: ModelCallRequest) -> dict[str, Any]:
        if request.agent_name in self._responses:
            return self._responses[request.agent_name]
        responder = FAKE_RESPONDERS.get(request.agent_name)
        if responder is None:
            return {}
        return responder(request.payload)
