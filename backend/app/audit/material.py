"""재료를 제목과 부제로 가른다.

사람이 만든 문서는 **한 줄이 화면 폭에서 갈린다.** gold가 그렇다 — 제목이 두
줄이고, 부제 하나도 두 줄로 갈려 있다. `-`로 시작하는 줄만 부제로 보면 이어진
줄이 제목으로 새고 부제는 잘린다. 처음 돌렸을 때 실제로 그랬다.

그래서 **빈 줄을 경계로** 본다. `-`로 시작한 뒤 빈 줄이 나올 때까지가 한 부제다.
"""

from __future__ import annotations

#: 부제가 시작되는 표시. 사람이 쓰는 여러 모양을 받는다.
_BULLETS = ("-", "–", "—", "·", "○", "▲")


def _is_bullet(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and stripped.startswith(_BULLETS)


def split_material(material: str) -> tuple[str, list[str]]:
    """재료를 `(제목, 부제 목록)`으로 가른다.

    제목은 첫 부제 앞까지다. 부제는 표시로 시작해 **빈 줄이 나올 때까지**
    이어진 줄을 한 덩이로 붙인다.
    """
    lines = material.splitlines()

    first_bullet = next((i for i, line in enumerate(lines) if _is_bullet(line)), None)
    if first_bullet is None:
        return material.strip(), []

    headline = "\n".join(lines[:first_bullet]).strip()

    subheads: list[str] = []
    current: list[str] = []
    for line in lines[first_bullet:]:
        if not line.strip():
            # 빈 줄이 한 부제의 끝이다.
            if current:
                subheads.append(" ".join(current))
                current = []
            continue
        if _is_bullet(line) and current:
            subheads.append(" ".join(current))
            current = []
        current.append(line.strip().lstrip("".join(_BULLETS)).strip())
    if current:
        subheads.append(" ".join(current))

    return headline, subheads
