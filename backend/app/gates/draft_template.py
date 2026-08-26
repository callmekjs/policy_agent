"""보도자료 양식 (README §2.7, Writing Contract `template.yaml`).

**이 파일은 값을 스스로 정하지 않는다.** 계약 파일에서 읽어 온다.

여섯 번의 검토 중 네 번이 같은 지적을 했다. 코드에 `SECTION_KINDS`를 손으로
박아 놓아 계약과 이름이 달랐고(`KEY_POINT` ↔ `KEY_POINTS`), 계약이 요구하는
필수 표시 다섯 개는 아무도 확인하지 않았다. 계약을 읽지 않고 코드에 옮겨
적으면 언제나 이렇게 갈라진다.

그리고 더 중요한 이유가 있다.

지금까지 초안은 **AI가 문장을 통째로 쓰고 Harness가 뒤에서 거짓말을 찾는**
구조였다. 그 방식은 여섯 번 다 졌다. 방어를 하나 만들 때마다 공격면이 하나
늘었기 때문이다.

양식은 그 구조를 바꾼다. **자료 기준·절차·효력·발표 주체·문의처처럼 값이
이미 정해진 자리는 Harness가 직접 채운다.** AI는 그 자리에 손댈 수 없다.
거짓을 찾아내는 것이 아니라 **넣을 수 없게** 만드는 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Harness가 직접 채우는 자리. AI가 쓰지 못한다.
#: 값이 이미 정해져 있으므로 AI에게 맡길 이유가 없고, 맡기면 거짓이 들어갈 수 있다.
#:
#: `SUPPLEMENTARY`(부칙)는 11차 검토 뒤에 들어왔다. 부칙은 자료에 적힌 글
#: 그대로이고 고를 것이 없는데 AI가 썼다. 그래서 `시행한다`를 `시행되었다`로
#: 바꿔 쓸 수 있었고, 검사기는 자료와 겹치는 길이로 그 둘을 가르지 못했다.
#: 자리를 Harness가 가져오면 **가릴 일 자체가 없어진다.**
HARNESS_OWNED = (
    "DRAFT_MARK",
    "BASIS_AND_STATUS",
    "ANNOUNCER_AND_RELEASE",
    "SUPPLEMENTARY",
    "CONTACT",
)

#: Harness가 만든 문단의 이름표 접두어. AI가 보낸 초안에서 이 접두어는 걷어낸다.
HARNESS_ID_PREFIX = "HS-"


@dataclass(frozen=True)
class DraftTemplate:
    """계약이 정한 출력 양식."""

    template_id: str
    version: str
    #: 순서대로의 문단 종류.
    section_kinds: tuple[str, ...]
    #: 반드시 있어야 하는 문단 종류.
    required_sections: frozenset[str]
    #: 근거가 없으면 생략하는 문단 종류.
    conditional_sections: frozenset[str]
    #: 만들지 않는 문단 종류.
    forbidden_sections: frozenset[str]
    #: 초안에 반드시 들어가야 하는 표시. `{basis_date}` 같은 자리는 값으로 바꾼다.
    required_marks: tuple[str, ...]
    #: 사람이 채워야 하는 자리.
    placeholders: dict[str, str]
    #: 핵심 요약 개수.
    min_key_points: int
    max_key_points: int

    @property
    def agent_sections(self) -> frozenset[str]:
        """AI가 쓸 수 있는 문단 종류."""
        return frozenset(self.section_kinds) - frozenset(HARNESS_OWNED)

    def marks_for(self, basis_date: str) -> tuple[str, ...]:
        """자리를 채운 필수 표시."""
        return tuple(m.replace("{basis_date}", basis_date) for m in self.required_marks)


def _key_point_limits(sections: list[dict[str, Any]]) -> tuple[int, int]:
    for section in sections:
        if section.get("kind") == "KEY_POINTS":
            return int(section.get("min_items", 2)), int(section.get("max_items", 3))
    return 2, 3


def load_template(template: dict[str, Any]) -> DraftTemplate:
    """계약의 `template.yaml` 내용을 읽어 양식을 만든다."""
    sections = list(template.get("sections") or [])
    minimum, maximum = _key_point_limits(sections)
    return DraftTemplate(
        template_id=str(template.get("template_id", "")),
        version=str(template.get("version", "")),
        section_kinds=tuple(str(s["kind"]) for s in sections),
        required_sections=frozenset(
            str(k) for k in (template.get("required_sections") or [])
        ),
        conditional_sections=frozenset(
            str(k) for k in (template.get("conditional_sections") or [])
        ),
        forbidden_sections=frozenset(
            str(k) for k in (template.get("forbidden_sections") or [])
        ),
        required_marks=tuple(str(m) for m in (template.get("required_marks") or [])),
        placeholders={
            str(k): str(v) for k, v in (template.get("placeholders") or {}).items()
        },
        min_key_points=minimum,
        max_key_points=maximum,
    )
