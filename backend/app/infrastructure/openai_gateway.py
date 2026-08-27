"""진짜 OpenAI를 부르는 통로 (README §7.2).

가짜 `ModelGateway`와 **같은 모양**으로 만든다. 부르는 쪽은 어느 쪽을 쓰는지
몰라도 된다. 그래야 검사기와 Harness가 그대로 돌아간다.

여기서 지키는 것 셋.

**하나 — 설정을 실행 중에 바꾸지 않는다.** 모델·service tier·저장 여부는
README §7.2가 고정했다. 응답이 다른 모델로 오면 그 사실을 기록하고 다음
호출을 막는다. 더 비싼 모델을 조용히 계속 쓰지 않기 위해서다.

**둘 — 보내기 전에 돈을 센다.** 예약값으로 최대 비용을 미리 계산하고
예산선을 넘으면 **보내지 않는다.** 보낸 뒤에 아는 것은 늦다.

**셋 — 비밀을 남기지 않는다.** API 키는 읽어서 SDK에 넘길 뿐, 로그·오류
문구·보고서 어디에도 담지 않는다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from app.infrastructure.model_gateway import (
    CONFIGURED_MODEL,
    CONFIGURED_PROVIDER,
    ModelCallRequest,
    ModelCallResult,
)

#: README §7.2가 고정한 값. 실행 중 바꾸지 않는다.
SERVICE_TIER = "default"
REASONING_EFFORT = "low"
TIMEOUT_SECONDS = 120

#: 호출별 입력 비용 예약값 (README §7.2). 토큰 상한이 아니라 **돈을 미리
#: 잡아 두는 값**이다. 실제 사용량이 오면 그것으로 보정한다.
INPUT_RESERVATION = {
    "FactExtractionAgent": 60_000,
    "DraftWritingAgent": 20_000,
    "RevisionAgent": 12_000,
}

#: 작업 1건 전송 전 예약 예산선 (README §7.2).
RUN_BUDGET_USD = 1.10

#: 2026-08-22 공식 단가 기준 100만 토큰당 달러. 가격이 바뀌면 여기만 고친다.
#: 이 값이 오래되면 `PRICE_REVIEW_REQUIRED`로 멈춘다.
PRICE_PER_MILLION = {"input": 1.25, "output": 10.00}
PRICE_BASIS_DATE = "2026-08-22"


class ExternalCallBlocked(Exception):
    """보내기 전에 막았다. **호출은 0회다.**"""

    def __init__(self, code: str, message: str, next_action: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_action = next_action


def reserve_usd(agent_name: str, max_output_tokens: int) -> float:
    """이 호출 하나가 쓸 수 있는 **최대** 금액."""
    reserved_input = INPUT_RESERVATION.get(agent_name, 20_000)
    return (
        reserved_input * PRICE_PER_MILLION["input"]
        + max_output_tokens * PRICE_PER_MILLION["output"]
    ) / 1_000_000


def actual_usd(input_tokens: int, output_tokens: int) -> float:
    """응답이 알려 준 실제 사용량으로 다시 센다."""
    return (
        input_tokens * PRICE_PER_MILLION["input"]
        + output_tokens * PRICE_PER_MILLION["output"]
    ) / 1_000_000


@dataclass
class OpenAIModelGateway:
    """진짜 OpenAI를 부른다.

    `budget_usd`는 이 통로 하나가 쓸 수 있는 전체 한도다. 보내기 전에
    예약액을 더해 넘으면 **보내지 않는다.**
    """

    budget_usd: float = RUN_BUDGET_USD
    spent_usd: float = 0.0
    calls: int = 0
    #: 응답이 다른 모델로 오면 여기 남기고 다음 호출을 막는다.
    mismatch: str | None = None

    def _client(self):
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ExternalCallBlocked(
                "API_KEY_MISSING",
                "OpenAI API 키가 없습니다.",
                "`.env`에 `OPENAI_API_KEY`를 넣고 서버를 다시 시작해 주세요.",
            )
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=key, timeout=TIMEOUT_SECONDS)

    async def call(self, request: ModelCallRequest) -> ModelCallResult:
        if self.mismatch is not None:
            raise ExternalCallBlocked(
                "MODEL_CONFIG_MISMATCH",
                f"요청한 모델과 다른 모델이 응답했습니다: {self.mismatch}",
                "설정을 확인한 뒤 새 작업으로 다시 시도해 주세요.",
            )

        reserve = reserve_usd(request.agent_name, request.max_output_tokens)
        if self.spent_usd + reserve > self.budget_usd:
            raise ExternalCallBlocked(
                "BUDGET_EXCEEDED",
                f"예상 비용이 한도를 넘습니다. "
                f"지금까지 {self.spent_usd:.4f}달러를 썼고 이번 호출에 최대 "
                f"{reserve:.4f}달러가 더 듭니다. 한도는 {self.budget_usd:.2f}달러입니다.",
                "자료를 줄이거나 한도를 다시 정해 주세요.",
            )

        client = self._client()
        response = await client.responses.create(
            model=CONFIGURED_MODEL,
            service_tier=SERVICE_TIER,
            # 공급자 쪽에 응답을 남기지 않는다 (README §7.2).
            store=False,
            reasoning={"effort": REASONING_EFFORT},
            max_output_tokens=request.max_output_tokens,
            # 입력이 길면 조용히 잘라내지 않고 오류로 멈춘다.
            truncation="disabled",
            input=[
                {
                    "role": "system",
                    "content": (
                        "너는 대한민국 국회 의원실의 보도자료 초안을 쓰는 도우미다. "
                        "**주어진 자료에 적혀 있는 값만** 쓴다. 표결 수·날짜·기관명·"
                        "사람 이름을 지어내지 않는다. 아직 공포되지 않은 법을 "
                        "시행됐다고 쓰지 않는다. 반드시 주어진 JSON 형식으로만 답한다."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(request.payload, ensure_ascii=False),
                },
            ],
        )

        actual_model = getattr(response, "model", "") or ""
        if actual_model and not actual_model.startswith(CONFIGURED_MODEL):
            self.mismatch = actual_model

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cost = actual_usd(input_tokens, output_tokens)
        self.spent_usd += cost
        self.calls += 1

        text = getattr(response, "output_text", "") or ""
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            result = {}

        return ModelCallResult(
            agent_name=request.agent_name,
            requested_model=CONFIGURED_MODEL,
            actual_model=actual_model or CONFIGURED_MODEL,
            result=result,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            is_fake=False,
        )


def live_enabled() -> bool:
    """진짜 AI를 쓸지.

    **기본은 꺼져 있다.** 켜려면 사람이 `POLICY_AGENT_LIVE=1`을 넣어야 한다.
    켜 두면 개발 중 시험을 돌릴 때마다 자료가 나가고 돈이 든다.
    """
    return os.environ.get("POLICY_AGENT_LIVE", "").strip() == "1"


def provider_label() -> str:
    return f"{CONFIGURED_PROVIDER} / {CONFIGURED_MODEL}"
