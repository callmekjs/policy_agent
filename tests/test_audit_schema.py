"""자료분석형 두 Agent의 고정 형식(strict schema) 시험.

## 왜 있는가

2026-09-01 관문 검토에서 진짜 AI가 **정해진 네 값 중 하나를 넣어야 할 자리에
빈 문자열**을 줘서 초안이 통째로 막히는 일이 실행 네 번 중 두 번 일어났다.
검사가 막은 것은 옳지만, 애초에 못 주게 하는 쪽이 낫다.

그리고 같은 날, 형식을 담아 두던 `test_sets/`가 본회의형과 함께 지워지면서
형식이 **소리 없이 사라졌다.** `response_schema()`는 파일이 없으면 `None`을
돌려주고 끝이라 아무도 몰랐다. 그것을 지키던 시험도 같이 지워졌기 때문이다.

여기 있는 시험들은 그 두 가지가 다시 일어나지 않게 한다.
"""

from __future__ import annotations

import pytest

from app.audit.contracts import AuditFactKind
from app.audit.slots import SLOT_ORDER
from app.infrastructure.openai_gateway import SCHEMA_FILES, response_schema

AGENTS = ("AuditFactAgent", "AuditDraftAgent")


def _walk(node):
    """schema 안의 모든 object 마디를 훑는다."""
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
        return
    if not isinstance(node, dict):
        return
    yield node
    for value in node.values():
        yield from _walk(value)


@pytest.mark.parametrize("agent", AGENTS)
def test_형식이_사라지지_않았다(agent: str) -> None:
    """파일이 없어지면 `response_schema`는 조용히 None을 준다. 그것을 잡는다."""
    assert agent in SCHEMA_FILES, f"{agent}가 형식 목록에서 빠졌습니다."
    assert response_schema(agent) is not None, (
        f"{agent}의 형식 파일을 읽지 못했습니다. "
        f"{SCHEMA_FILES[agent]}가 제자리에 있는지 보세요."
    )


def test_사실_종류는_코드가_정한_목록만_받는다() -> None:
    """AI가 없는 종류를 지어내면 없는 칸이 열린다. 형식에서 막는다."""
    schema = response_schema("AuditFactAgent")
    kind = schema["properties"]["facts"]["items"]["properties"]["kind"]
    assert kind.get("enum") == [k.value for k in AuditFactKind], (
        "사실 종류 목록이 코드와 다릅니다. 형식 파일에 손으로 베껴 적지 말고 "
        "_known_shapes()가 코드에서 끼워 넣게 두세요."
    )


def test_칸_이름은_코드가_정한_목록만_받는다() -> None:
    schema = response_schema("AuditDraftAgent")
    slot = schema["properties"]["slots"]["items"]["properties"]["slot"]
    assert slot.get("enum") == [s.value for s in SLOT_ORDER], (
        "칸 이름 목록이 코드와 다릅니다."
    )


@pytest.mark.parametrize("agent", AGENTS)
def test_빈_칸을_남겨_둘_수_없다(agent: str) -> None:
    """모든 칸이 `required`다.

    `kind`를 안 주는 것과 `kind`에 빈 문자열을 주는 것은 다르다. 전자는 이
    시험이, 후자는 위의 enum이 막는다. 둘 다 있어야 한다.
    """
    for node in _walk(response_schema(agent)):
        properties = node.get("properties")
        if not isinstance(properties, dict) or not properties:
            continue
        assert set(node.get("required") or []) == set(properties), (
            f"{agent}: 빠뜨려도 되는 칸이 있습니다 — "
            f"{sorted(set(properties) - set(node.get('required') or []))}"
        )


@pytest.mark.parametrize("agent", AGENTS)
def test_적어_두지_않은_칸은_보내지_못한다(agent: str) -> None:
    for node in _walk(response_schema(agent)):
        if isinstance(node.get("properties"), dict):
            assert node.get("additionalProperties") is False, (
                f"{agent}: 형식에 없는 칸을 AI가 덧붙일 수 있습니다."
            )
