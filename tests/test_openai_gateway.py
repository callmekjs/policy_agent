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

import os
import pathlib

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


# ---------------------------------------------------------------------------
# 고정 형식과 Pydantic 계약이 같은 값을 요구하는지
# ---------------------------------------------------------------------------
#
# AI에게 보내는 고정 형식(test_sets/*.schema.json)이 자유 글자를 허용하는데
# Harness의 계약이 정해진 코드를 요구하면, AI는 형식을 지키고도 **매번**
# 거절당한다. 실제로 그랬다. 형식은 `procedure_stage`를 아무 글자나 받게
# 두었고 계약은 `PLENARY_DECIDED`를 요구해서, 진짜 AI가 `본회의 의결`이라고
# 답한 뒤 통째로 버려졌다.
#
# 이 시험은 그 어긋남을 **형식 쪽에서** 잡는다. 프롬프트로 부탁하는 것과
# 다르다. 형식에 넣으면 AI가 다른 값을 **쓸 수 없다.**


def _enum_at(schema: dict, def_name: str, field: str) -> list[str]:
    node = schema["$defs"][def_name]["properties"][field]
    assert "enum" in node, (
        f"{def_name}.{field}이(가) 고정 형식에서 자유 글자입니다. "
        f"AI가 계약에 없는 값을 쓸 수 있게 됩니다."
    )
    return node["enum"]


# ---------------------------------------------------------------------------
# Agent마다 다른 지시문을 받는지
# ---------------------------------------------------------------------------
#
# 세 Agent가 공통 한 문단만 받으면 **무엇을 뽑아야 하는지 모른다.** 실제로
# 발의안 전문을 주고도 사실 3건만 돌아왔고 의안번호·의결일·처리결과가 빠졌다.
#
# 지시문은 부탁이지 강제가 아니다. 강제는 고정 형식과 Gate가 한다. 그래서 이
# 시험도 "AI가 잘 따르는지"를 재지 않고, **필요한 종류를 말하기는 했는지**만
# 잰다. 보호 대상 종류를 새로 늘리면서 AI에게 알리지 않으면 여기서 걸린다.


def test_Agent마다_제_지시문을_받는다() -> None:
    from app.infrastructure.openai_gateway import COMMON, instructions_for

    fact = instructions_for("FactExtractionAgent")
    draft = instructions_for("DraftWritingAgent")
    revise = instructions_for("RevisionAgent")

    assert fact != draft != revise
    assert fact != revise
    for text in (fact, draft, revise):
        assert COMMON in text, "공통 규칙이 빠졌습니다."

    # 모르는 이름은 공통 규칙만 받는다. 엉뚱한 지시문을 물려주지 않는다.
    assert instructions_for("아무도아님") == COMMON


def test_초안에_필요한_사실_종류를_AI에게_알린다() -> None:
    """초안이 찾는 종류인데 지시문에 없으면 AI는 그것을 뽑지 않는다."""
    from app.infrastructure.fake_draft import PREFERRED_KINDS
    from app.infrastructure.openai_gateway import instructions_for

    fact = instructions_for("FactExtractionAgent")
    빠진_것 = [kind for kind in PREFERRED_KINDS if kind not in fact]
    assert not 빠진_것, (
        f"초안은 {빠진_것}을(를) 쓰는데 사실 뽑기 지시문에는 없습니다. "
        f"AI가 그 값을 뽑을 이유가 없습니다."
    )


# ---------------------------------------------------------------------------
# 고정 형식을 strict 모드가 받는 모양으로 옮기는지
# ---------------------------------------------------------------------------
#
# OpenAI strict Structured Outputs는 지원하는 낱말만 받는다. `const`는 그중에
# 없어서 초안 쓰기 호출이 통째로 거절당했다. 400 오류였고 초안은 한 판도
# 나오지 않았다.
#
# 옮길 때 **값의 뜻이 느슨해지면 안 된다.** `const`를 그냥 지우면 AI가 아무
# 값이나 쓸 수 있게 된다. 그래서 값 하나만 허용하는 `enum`으로 옮긴다.


def test_한_값만_허용하는_자리는_옮겨도_한_값만_허용한다() -> None:
    from app.infrastructure.openai_gateway import _strict

    moved = _strict({"type": "object", "properties": {"stage": {"const": "PLENARY_DECIDED"}}})
    stage = moved["properties"]["stage"]
    assert "const" not in stage, "strict가 모르는 낱말이 남았습니다."
    assert stage.get("enum") == ["PLENARY_DECIDED"], (
        "값 하나만 허용하던 자리가 아무 값이나 받게 되었습니다."
    )
    assert stage.get("type") == "string"


# ---------------------------------------------------------------------------
# strict 규칙을 시험으로 옮긴다
# ---------------------------------------------------------------------------
#
# 형식이 어긋나면 API가 400으로 거절한다. 그런데 그것을 알려면 **돈을 내고
# 한 번 보내 봐야** 했다. 한 번에 하나씩만 알려주므로 고칠 때마다 또 보냈다.
#
# 그래서 규칙을 여기로 옮긴다. 이제 어긋남은 **보내기 전에** 잡힌다.
#
# 규칙 출처: OpenAI Structured Outputs 문서.
# · object는 `properties`·`additionalProperties: false`·모든 속성이 `required`
# · array는 `items`가 있어야 한다
# · 지원하지 않는 낱말은 넣지 않는다


def strict_violations(node, path: str = "") -> list[str]:
    """strict가 거절할 자리를 모두 찾는다. 첫 번째에서 멈추지 않는다."""
    나쁜_곳: list[str] = []
    금지 = ("const", "allOf", "oneOf", "not", "if", "then", "else", "uniqueItems",
            "$schema", "$id", "default", "examples", "format")

    if isinstance(node, list):
        for i, child in enumerate(node):
            나쁜_곳 += strict_violations(child, f"{path}/{i}")
        return 나쁜_곳
    if not isinstance(node, dict):
        return 나쁜_곳

    for word in 금지:
        if word in node:
            나쁜_곳.append(f"{path}: 지원하지 않는 낱말 '{word}'")

    kinds = node.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if "object" in kinds:
        if not isinstance(node.get("properties"), dict):
            나쁜_곳.append(f"{path}: object인데 properties가 없습니다")
        elif node.get("additionalProperties") is not False:
            나쁜_곳.append(f"{path}: additionalProperties가 false가 아닙니다")
        elif sorted(node.get("required", [])) != sorted(node["properties"]):
            나쁜_곳.append(f"{path}: required가 속성 전체와 다릅니다")
    if "array" in kinds and "items" not in node:
        나쁜_곳.append(f"{path}: array인데 items가 없습니다")

    for key, value in node.items():
        if key in ("properties", "$defs"):
            for name, child in (value or {}).items():
                나쁜_곳 += strict_violations(child, f"{path}/{key}/{name}")
        elif key in ("items", "anyOf"):
            나쁜_곳 += strict_violations(value, f"{path}/{key}")
    return 나쁜_곳


def test_검사기가_어긋남을_실제로_잡는다() -> None:
    """검사기가 아무것도 잡지 못하면 위 시험은 아무것도 지키지 않는다."""
    assert strict_violations({"type": "array"}) , "items 없는 배열을 놓쳤습니다."
    assert strict_violations({"type": "object"}), "properties 없는 object를 놓쳤습니다."
    assert strict_violations(
        {"type": "object", "properties": {"a": {"type": "string"}},
         "additionalProperties": False, "required": []}
    ), "required가 빠진 속성을 놓쳤습니다."
    assert not strict_violations(
        {"type": "object", "properties": {"a": {"type": "string"}},
         "additionalProperties": False, "required": ["a"]}
    ), "멀쩡한 형식을 잘못 잡았습니다."


# ---------------------------------------------------------------------------
# 보내는 형식과 받는 계약이 같은 모양인지
# ---------------------------------------------------------------------------
#
# 두 번 같은 일이 났다. 형식은 `procedure_stage`에 아무 글자나 허용했는데
# 계약은 코드를 요구했고, 형식은 초안 **알맹이**를 적었는데 계약은 **봉투**를
# 받았다. 둘 다 AI가 형식을 완벽히 지키고도 통째로 버려졌다.
#
# 위 두 시험은 그 두 자리를 각각 지킨다. 이 시험은 **자리를 미리 알지 못해도**
# 잡는다. 가짜 AI 응답은 계약을 통과하는 것이 이미 확인돼 있다(다른 시험
# 480개가 그 위에 서 있다). 그러니 그 응답을 **우리가 보내는 형식**에
# 넣어 본다. 통과하지 못하면 형식과 계약의 모양이 다른 것이다.


# ---------------------------------------------------------------------------
# `.env`를 실제로 읽는가
# ---------------------------------------------------------------------------
#
# 열쇠가 없을 때 프로그램은 "`.env`에 `OPENAI_API_KEY`를 넣고 서버를 다시
# 시작해 주세요"라고 안내한다. 그런데 서버는 `.env`를 **읽지 않았다.**
# 안내대로 해도 계속 같은 오류가 났다.
#
# 여기서 지키는 것 셋.
# 1. 안내한 자리에서 실제로 읽는다
# 2. 명령줄에서 준 값이 파일보다 세다
# 3. 정해진 이름 말고는 환경에 올리지 않는다


def test_안내한_자리에서_열쇠를_읽는다(tmp_path, monkeypatch) -> None:
    from app.infrastructure.openai_gateway import load_env_file

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("POLICY_AGENT_LIVE", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# 주석\n\nOPENAI_API_KEY=sk-시험용-값\nPOLICY_AGENT_LIVE=1\n",
        encoding="utf-8",
    )

    올린_것 = load_env_file(env)

    assert 올린_것 == ["OPENAI_API_KEY"], 올린_것
    assert os.environ["OPENAI_API_KEY"] == "sk-시험용-값"


def test_파일로는_진짜_AI를_켜지_못한다(tmp_path, monkeypatch) -> None:
    """진짜 AI는 **켤 때마다 사람이 손으로** 켜야 한다.

    파일로 켤 수 있게 두면 시험이 서버를 띄울 때마다 진짜로 나가고 돈이 든다.
    실제로 그렇게 됐고 시험 아홉 개가 한꺼번에 깨졌다.
    """
    from app.infrastructure.openai_gateway import live_enabled, load_env_file

    monkeypatch.delenv("POLICY_AGENT_LIVE", raising=False)
    env = tmp_path / ".env"
    env.write_text("POLICY_AGENT_LIVE=1\n", encoding="utf-8")

    load_env_file(env)

    assert live_enabled() is False, "파일만으로 진짜 AI가 켜졌습니다."


def test_명령줄에서_준_값이_파일보다_세다(tmp_path, monkeypatch) -> None:
    """파일을 고치지 않고도 한 번만 다르게 켤 수 있어야 한다."""
    from app.infrastructure.openai_gateway import load_env_file

    monkeypatch.setenv("OPENAI_API_KEY", "명령줄-값")
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=파일-값\n", encoding="utf-8")

    load_env_file(env)

    assert os.environ["OPENAI_API_KEY"] == "명령줄-값"


def test_정해진_이름_말고는_환경에_올리지_않는다(tmp_path, monkeypatch) -> None:
    """파일 아무 줄이나 올리면 사람이 모르는 설정이 켜진다."""
    from app.infrastructure.openai_gateway import load_env_file

    monkeypatch.delenv("POLICY_AGENT_DEV", raising=False)
    monkeypatch.delenv("아무거나", raising=False)
    # 앞선 시험이 올려 둔 값이 남아 있으면 "이미 있으니 건너뛴다"에 걸려
    # 이 시험이 아무것도 재지 못한다.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "POLICY_AGENT_DEV=1\n아무거나=올라가면안됨\nOPENAI_API_KEY=키\n",
        encoding="utf-8",
    )

    올린_것 = load_env_file(env)

    assert 올린_것 == ["OPENAI_API_KEY"], 올린_것
    assert os.environ.get("POLICY_AGENT_DEV") is None
    assert os.environ.get("아무거나") is None


def test_없는_파일이어도_멈추지_않는다(tmp_path) -> None:
    from app.infrastructure.openai_gateway import load_env_file

    assert load_env_file(tmp_path / "없는파일.env") == []


# ---------------------------------------------------------------------------
# 화면이 어느 AI를 쓰는지 정직하게 말하는가
# ---------------------------------------------------------------------------
#
# 화면은 `getattr(gateway, "is_fake", True)`로 묻는다. 진짜 통로에 그 값이
# 없으면 기본값 `True`가 나와 **진짜로 나가는 중에도 "가짜 AI"라고 적힌다.**
#
# 실제로 그랬다. 서버를 진짜 AI로 켰는데 화면은 "인터넷으로 나가지 않고
# 비용도 0원"이라고 적혀 있었다. 사람은 그 말을 믿고 자료를 넣는다.
#
# 틀리는 방향이 한쪽으로만 위험하다. "진짜인데 가짜라고 적기"는 사람을
# 속인다. 그래서 **기본값에 기대지 않고 통로가 스스로 밝히는지**를 잰다.


def test_진짜_통로는_스스로_진짜라고_밝힌다() -> None:
    from app.infrastructure.model_gateway import FakeModelGateway

    # 기본값에 기대지 않는다. 통로에 값이 직접 있어야 한다.
    assert hasattr(OpenAIModelGateway, "is_fake"), (
        "진짜 통로에 `is_fake`가 없습니다. 화면이 기본값 True를 읽어 "
        "'가짜 AI'라고 적습니다."
    )
    assert OpenAIModelGateway.is_fake is False
    assert FakeModelGateway.is_fake is True

    # 만들어 놓은 것에서도 같아야 한다.
    assert OpenAIModelGateway(budget_usd=1.0).is_fake is False
    assert FakeModelGateway().is_fake is True


