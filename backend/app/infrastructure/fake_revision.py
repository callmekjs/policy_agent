"""가짜 수정기 (`RevisionAgent` 대역).

누적 6일차에 진짜 AI로 바뀐다. 그때까지 **검사기를 시험하기 위한 대역**이다.

이 파일이 무엇을 어떻게 고치는지는 5일차 합격선의 판정 대상이 아니다
(`day5-pass-bar.md`의 "차단이 아닌 것"). 중요한 것은 **고친 결과가 검사를
제대로 받는가**이며, 그 판정은 `revision_gate`와 `draft_gate`가 한다.

그래서 여기서는 일부러 **두 가지 성격**을 다 낸다.

- 사람이 부탁한 대로 문장을 다듬는 **안전한 수정**
- 값이 사라지거나 바뀌는 **위험한 수정**

위험한 쪽을 못 만들면 검사기가 일하는지 알 수 없다. 진짜 AI도 실수하며,
그 실수를 잡는 것이 이 프로그램의 존재 이유다.
"""

from __future__ import annotations

from typing import Any

#: 사람이 이 말을 쓰면 문단을 짧게 만든다. 값은 건드리지 않는다.
SHORTEN_WORDS = ("짧게", "줄여", "간단", "간결")

#: 사람이 이 말을 쓰면 문단 순서를 바꾼다. 값은 그대로다.
REORDER_WORDS = ("순서", "먼저", "앞으로")


def _first_sentence(text: str) -> str:
    """첫 문장만 남긴다. 값이 든 문장이 뒤에 있으면 그 값이 사라진다."""
    for mark in (". ", ".\n"):
        at = text.find(mark)
        if at > 0:
            return text[: at + 1]
    return text


def fake_revision(payload: dict[str, Any]) -> dict[str, Any]:
    """이전 초안과 사람의 요청을 받아 고친 초안을 돌려준다.

    payload는 `{"draft": <DraftCandidate>, "instruction": "<사람이 쓴 글>"}`이다.
    """
    draft = payload.get("draft") or {}
    instruction = str(payload.get("instruction") or "")
    revised = {**draft}

    # AI가 쓰는 자리만 고친다. Harness가 만든 자리(`HS-`)는 손대지 않는다.
    # 손대 봐야 Harness가 다시 만들어 덮으므로 뜻이 없다.
    paragraphs = [dict(p) for p in draft.get("paragraphs") or []]
    agent_paragraphs = [
        p for p in paragraphs if not str(p.get("paragraph_id", "")).startswith("HS-")
    ]

    if any(word in instruction for word in SHORTEN_WORDS):
        # **위험한 수정.** 문장을 잘라 내면 뒤에 있던 값이 함께 사라진다.
        # 검사기가 이것을 잡아야 한다.
        for paragraph in agent_paragraphs:
            paragraph["text"] = _first_sentence(paragraph["text"])
    elif any(word in instruction for word in REORDER_WORDS):
        # **안전한 수정.** 순서만 바꾼다. 값은 하나도 안 건드린다.
        agent_paragraphs.reverse()
        for rank, paragraph in enumerate(agent_paragraphs, start=1):
            paragraph["priority_rank"] = rank
    else:
        # 무엇을 하라는지 모르겠으면 **아무것도 바꾸지 않는다.**
        # 모를 때 지어내는 것이 가장 위험하다.
        pass

    kept = [p for p in paragraphs if str(p.get("paragraph_id", "")).startswith("HS-")]
    revised["paragraphs"] = [*kept, *agent_paragraphs]
    revised["version"] = int(draft.get("version") or 1) + 1
    return {"schema_version": draft.get("schema_version", "1.1.0"), "result": revised}
