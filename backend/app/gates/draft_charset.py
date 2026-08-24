"""초안이 쓸 수 있는 **글자** 목록 (README §4.2).

이 파일이 있는 이유를 분명히 적어 둔다.

세 번 연속 같은 실수를 했다. "이런 문자는 막는다"고 목록을 적었고, 검토는
그때마다 **목록 밖의 문자**로 뚫었다.

- 2차: 스페이스·탭만 봤더니 다른 공백으로 뚫림
- 3차: 유니코드 분류 `Cf/Cc/Co/Cs/Zs/Zl/Zp` 일곱 개를 적었더니, 그 밖의
  `Mn`(결합 문자·이체자 선택자)·`Lo`(한글 채움 `ㅤ`)·`So`(점자 빈칸)로 뚫림
- 그때마다 화면에는 `김영수`로 멀쩡히 보이는데 검사에서만 한 글자씩 흩어졌다

세상의 문자는 15만 자가 넘는다. 못 쓸 문자를 세는 일은 끝나지 않는다.
그래서 뒤집는다. **쓸 수 있는 글자만 적는다.**

보도자료 초안에 필요한 글자는 많지 않다. 한글, 숫자, 영문, 그리고 문장 부호
몇 가지다. 여기에 **자료 원문에 실제로 나오는 글자**를 더한다. 자료에 한자가
있으면 초안도 한자를 쓸 수 있고, 자료에 없으면 못 쓴다.

이 규칙 하나가 결합 문자·이체자 선택자·채움 문자·점자·키릴·한자를 한꺼번에
막는다. 새 문자가 생겨도 목록을 고칠 필요가 없다.
"""

from __future__ import annotations

#: 한글 음절. 초안 글의 대부분이다.
HANGUL_SYLLABLES = ("가", "힣")

#: 문장을 이루는 데 꼭 필요한 문자들. 자료에 없어도 쓸 수 있다.
BASE_CHARACTERS: frozenset[str] = frozenset(
    # 공백과 줄바꿈. 다른 종류의 공백은 여기 없다.
    " \n\t"
    # 숫자와 영문
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    # 문장 부호
    ".,!?;:()[]{}<>/-–—~%&＊*+=_·…‥"
    # 따옴표. 어떤 모양이든 인용 검사가 보도록 함께 허용한다.
    "\"'“”‘’「」『』"
    # 한국 공문서에서 흔한 기호
    "○●◦□■△▲▷▶※◇◆★☆"
    "㈜№℃㎡㎢㎏㎖ℓ"
    "\\|@#$^"
)


def allowed_characters(source_text: str) -> frozenset[str]:
    """초안이 쓸 수 있는 글자 전체.

    기본 글자에 **자료 원문에 실제로 나오는 글자**를 더한다. 자료가 정하는
    것이므로 목록을 손으로 늘릴 필요가 없다.
    """
    return BASE_CHARACTERS | frozenset(source_text)


def _is_hangul(char: str) -> bool:
    return HANGUL_SYLLABLES[0] <= char <= HANGUL_SYLLABLES[1]


def find_forbidden(text: str, allowed: frozenset[str]) -> list[tuple[int, str]]:
    """쓸 수 없는 글자를 모두 찾는다. (위치, 글자).

    한글 음절은 언제나 쓸 수 있다. 그 밖의 글자는 기본 목록이나 자료에
    있어야 한다.
    """
    found: list[tuple[int, str]] = []
    for index, char in enumerate(text):
        if _is_hangul(char) or char in allowed:
            continue
        found.append((index, char))
    return found


def describe(char: str) -> str:
    """사람에게 보여 줄 글자 이름. 화면에 안 보이는 글자를 설명하기 위해서다."""
    import unicodedata

    try:
        name = unicodedata.name(char)
    except ValueError:
        name = "이름 없는 문자"
    return f"U+{ord(char):04X}({name})"
