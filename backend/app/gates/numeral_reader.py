"""초안에 쓰인 수를 **표기법과 상관없이** 읽는다 (README §4.2).

검토에서 `재석 250인`을 막자 `재석 이백오십인`으로 빠져나갔고, 그것을 막자
`二百五十`으로 빠져나갔다. 아라비아 숫자만 보는 검사는 표기만 바꾸면 그대로
뚫린다.

그래서 여기서는 세 가지를 모두 같은 수로 읽는다.

- 아라비아 숫자: `250`, `2,050`, `２５０`(전각), `2 5 0`(사이 공백)
- 한글 수사: `이백오십`, `스물`, `열둘`
- 한자 수사: `二百五十`

읽은 수는 자료에서 온 수의 집합과 비교한다. 집합에 없으면 지어낸 수다.
"""

from __future__ import annotations

import re
import unicodedata

#: 한자리 수. 한글과 한자를 함께 본다.
DIGIT_WORDS: dict[str, int] = {
    "영": 0, "공": 0, "〇": 0, "零": 0,
    "일": 1, "하나": 1, "한": 1, "一": 1,
    "이": 2, "둘": 2, "두": 2, "二": 2,
    "삼": 3, "셋": 3, "세": 3, "三": 3,
    "사": 4, "넷": 4, "네": 4, "四": 4,
    "오": 5, "다섯": 5, "五": 5,
    "육": 6, "륙": 6, "여섯": 6, "六": 6,
    "칠": 7, "일곱": 7, "七": 7,
    "팔": 8, "여덟": 8, "八": 8,
    "구": 9, "아홉": 9, "九": 9,
}

#: 자리값.
UNIT_WORDS: dict[str, int] = {
    "십": 10, "十": 10,
    "백": 100, "百": 100,
    "천": 1000, "千": 1000,
    "만": 10_000, "萬": 10_000, "万": 10_000,
    "억": 100_000_000, "億": 100_000_000,
}

#: 순우리말 십 단위. `스물다섯`처럼 앞에 붙는다.
NATIVE_TENS: dict[str, int] = {
    "열": 10, "스물": 20, "서른": 30, "마흔": 40, "쉰": 50,
    "예순": 60, "일흔": 70, "여든": 80, "아흔": 90,
}

#: 수를 적을 수 있는 글자 전체.
_NUMERAL_CHARS = set("".join(DIGIT_WORDS) + "".join(UNIT_WORDS) + "".join(NATIVE_TENS))

#: 아라비아 숫자 덩어리. 쉼표·마침표·사이 공백을 함께 먹는다.
ARABIC_RUN = re.compile(r"\d[\d,\.\s]*\d|\d")

#: 한글·한자 수사 덩어리.
WORD_RUN = re.compile("[" + re.escape("".join(sorted(_NUMERAL_CHARS))) + "]{1,12}")


def _arabic_values(text: str) -> set[int]:
    """아라비아 숫자를 읽는다. 사이의 쉼표·공백·마침표는 자릿수 구분으로 본다."""
    found: set[int] = set()
    for match in ARABIC_RUN.finditer(text):
        digits = re.sub(r"[^\d]", "", match.group(0))
        if not digits:
            continue
        found.add(int(digits))
        # `2025. 10. 26`처럼 마침표로 나뉜 경우 조각도 각각 수로 읽는다.
        for piece in re.split(r"[^\d]+", match.group(0)):
            if piece:
                found.add(int(piece))
    return found


def read_numeral_word(word: str) -> int | None:
    """한글·한자 수사 하나를 수로 읽는다. 수가 아니면 `None`."""
    if not word:
        return None

    # 순우리말 십 단위가 앞에 오는 경우: 스물다섯, 열둘
    for native, base in NATIVE_TENS.items():
        if word.startswith(native):
            rest = word[len(native) :]
            if not rest:
                return base
            tail = DIGIT_WORDS.get(rest)
            return base + tail if tail is not None else None

    if word in DIGIT_WORDS:
        return DIGIT_WORDS[word]

    total = 0
    section = 0
    current: int | None = None
    saw_unit = False

    for char in word:
        if char in DIGIT_WORDS:
            if current is not None:
                return None  # `이삼`처럼 자리값 없이 붙은 것은 수로 읽지 않는다
            current = DIGIT_WORDS[char]
        elif char in UNIT_WORDS:
            saw_unit = True
            unit = UNIT_WORDS[char]
            if unit >= 10_000:
                section = (section + (current if current is not None else 0)) or 1
                total += section * unit
                section = 0
            else:
                section += (current if current is not None else 1) * unit
            current = None
        else:
            return None

    if not saw_unit:
        return None
    return total + section + (current or 0)


def read_numbers(text: str) -> set[int]:
    """글에 쓰인 수를 표기법과 상관없이 모두 읽는다."""
    # 전각 숫자·기호를 반각으로 맞춘다. **이 검사에서만** 쓰는 사본이다.
    flattened = unicodedata.normalize("NFKC", text)
    found = _arabic_values(flattened)
    for match in WORD_RUN.finditer(flattened):
        value = read_numeral_word(match.group(0))
        if value is not None:
            found.add(value)
    return found
