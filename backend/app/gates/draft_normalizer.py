"""초안 검사 전용 글자 정리 (README §4.2).

자료 원문은 `source_text_v1`으로 정규화한다. 그 계약은 **원문을 최대한 그대로
보존**하는 것이 목적이라 손대는 것이 세 가지뿐이다(BOM·줄바꿈·NFC).

초안은 목적이 다르다. 초안은 **검사를 통과해 사람에게 보이는 글**이다. 그래서
검사하기 전에 한 번 더 정리한다.

3차 검토가 이 자리를 뚫었다. 글자 사이에 눈에 보이지 않는 문자를 끼우면
`김​영​수`가 화면에는 `김영수`로 보이는데 검사에서는 한 글자씩 흩어져 낱말
허용 목록이 통째로 꺼졌다. 검사가 보는 글과 사람이 보는 글이 달랐던 것이다.

여기서 하는 일은 둘이다.

1. **보이지 않는 문자가 든 초안은 막는다.** 보도자료 초안에 그런 문자를 쓸
   이유가 없다. 있으면 검사를 피하려는 시도다.
2. 그래도 검사는 **정리한 사본**으로 한다. 1번을 빠져나가는 새 문자가 생겨도
   낱말 검사가 계속 동작하게 하기 위해서다.
"""

from __future__ import annotations

import unicodedata

#: 글에 남겨 둘 제어 문자. 줄바꿈과 탭만 허용한다.
ALLOWED_CONTROL = frozenset({"\n", "\t", "\r"})

#: 보통 공백. 이것만 공백으로 쓴다.
PLAIN_SPACE = " "

#: 사람 눈에 보이지 않지만 글자를 갈라 놓는 문자들. 이름을 붙여 두면 왜 막혔는지
#: 사람에게 설명할 수 있다.
INVISIBLE_NAMES: dict[str, str] = {
    "​": "폭 없는 공백",
    "‌": "폭 없는 비접합자",
    "‍": "폭 없는 접합자",
    "⁠": "낱말 이음표",
    "﻿": "바이트 순서 표시",
    "­": "숨은 붙임표",
    "᠎": "몽골 모음 구분자",
}


def _is_invisible(char: str) -> bool:
    """검사를 피하려고 끼워 넣을 수 있는 문자인가."""
    if char in ALLOWED_CONTROL or char == PLAIN_SPACE:
        return False
    category = unicodedata.category(char)
    # Cf: 서식 문자, Cc: 제어 문자, Co: 사용자 정의, Cs: 대리 문자
    if category in {"Cf", "Cc", "Co", "Cs"}:
        return True
    # Zs: 보통 공백이 아닌 다른 공백(전각 공백·NBSP 등)
    return category in {"Zs", "Zl", "Zp"}


def find_invisible(text: str) -> list[tuple[int, str, str]]:
    """보이지 않는 문자를 모두 찾는다. (위치, 문자, 이름)."""
    found: list[tuple[int, str, str]] = []
    for index, char in enumerate(text):
        if not _is_invisible(char):
            continue
        name = INVISIBLE_NAMES.get(char)
        if name is None:
            try:
                name = unicodedata.name(char)
            except ValueError:
                name = f"U+{ord(char):04X}"
        found.append((index, char, name))
    return found


def sanitize(text: str) -> str:
    """검사용 사본을 만든다.

    - 보이지 않는 문자를 **지운다.** 지우면 `김​영​수`가 `김영수`로 붙어
      낱말 검사가 제대로 동작한다.
    - 다른 종류의 공백은 보통 공백 하나로 바꾼다.
    - 전각 숫자·호환 문자를 보통 모양으로 맞추고(NFKC) 다시 NFC로 모은다.
      자모가 분해된 `ㄱㅣㅁ`도 여기서 `김`으로 합쳐진다.

    **자료 쪽 글에도 같은 정리를 적용해야 한다.** 한쪽만 정리하면 같은 글자가
    서로 다르게 보여 멀쩡한 초안이 막힌다.
    """
    cleaned: list[str] = []
    for char in text:
        if char in ALLOWED_CONTROL:
            cleaned.append(char)
            continue
        category = unicodedata.category(char)
        if category in {"Cf", "Cc", "Co", "Cs"}:
            continue  # 지운다
        if category in {"Zs", "Zl", "Zp"}:
            cleaned.append(PLAIN_SPACE)
            continue
        cleaned.append(char)
    joined = "".join(cleaned)
    return unicodedata.normalize("NFC", unicodedata.normalize("NFKC", joined))
