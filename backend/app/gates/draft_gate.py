"""초안 안전 검사 Gate (README §2.11 6단계, §4.2, §2.16.1, §2.16.4).

4일차부터 **글이 실제로 나온다.** 3일차까지는 초안이 언제나 0건이라 안전했다.
여기서부터는 AI가 쓴 문장이 사람에게 보인다. 그래서 이 파일이 하는 일은 하나다.

**원장에 없는 값이 초안에 들어가면 막는다.**

처음에는 금지 목록으로 만들었다가 검토에서 공격 47종 중 39종이 통과해 실패했다.
`공포되었`을 막으면 `공포되어`로, `250`을 막으면 `이백오십`으로 빠져나갔다.
지금은 **허용 목록**이다. 자료에 있는 말과 `draft_vocabulary`에 적힌 말만 쓸 수
있고, 그 밖의 낱말·수는 어디에 있든 막는다.

검사는 **초안의 모든 칸**을 본다. 본문뿐 아니라 주장·빈칸 표시·인용·붙임까지
본다. 검사하지 않는 칸이 하나라도 있으면 그 칸이 곧 빠져나가는 길이 된다.

검사 결과에는 언제나 `rule_id`, 기준 문서 위치, 영향받은 초안 부분을 함께
남긴다. 셋 중 하나라도 없으면 왜 막혔는지 추적할 수 없고, 그것 자체가 §4.2의
중대한 실패다.
"""

from __future__ import annotations

import re
from typing import Any

from app.gates.draft_charset import allowed_characters, describe, find_forbidden
from app.gates.draft_normalizer import find_invisible, sanitize
from app.gates.draft_template import (
    HARNESS_ID_PREFIX,
    HARNESS_OWNED,
    DraftTemplate,
)
from app.gates.draft_vocabulary import SAFE_WORDS, SUFFIXES
from app.gates.numeral_reader import read_numbers, read_numeral_word
from app.harness.draft_contracts import (
    DRAFT_LABEL,
    MAX_KEY_POINTS,
    MIN_KEY_POINTS,
    SIX_W_KEYS,
    STATUS_CODES,
    DraftCandidate,
    ValidationFinding,
    ValidationSeverity,
)
from app.harness.fact_contracts import FactLedger
from app.harness.legal_contracts import ChangedArticleSet, ResolvedFinalText
from app.harness.source_normalizer import NormalizedSource

#: 검사할 한글 낱말. 한 글자짜리는 조사와 구분되지 않아 보지 않는다.
HANGUL_RUN = re.compile(r"[가-힣]{2,}")

#: 로마자 낱말. 기관 약칭이 이 모양으로 들어올 수 있다.
LATIN_RUN = re.compile(r"[A-Za-z]{2,}")

#: 아직 법이 아닐 때 반드시 조심해서 써야 하는 말 (§2.16.1, §2.16.4).
#: 이 말이 나오면 같은 문장에 아래 `HEDGES` 중 하나가 **반드시** 있어야 한다.
#: 금지 낱말 목록이 아니라 **함께 쓸 말을 요구하는** 규칙이라 어미를 바꿔도 빠져나갈 수 없다.
EFFECT_STEMS = (
    "공포", "시행", "개정", "확정", "효력", "통과", "제정", "발효", "적용",
)

#: 효력 표현을 **서술로** 쓴 모양. 이름으로 쓴 `개정 문구`와 구분한다.
ASSERTIVE_EFFECT = re.compile(
    "(" + "|".join(EFFECT_STEMS) + ")"
    # 어간과 서술 사이에 조사가 낄 수 있다. `개정이 완료되었다`가 그렇다.
    r"(?:[가-힣]{0,3})?"
    r"(되었|되어|되고|된|됐|됨|하였|했|한다|합니다|중이|완료|시켰|시킨|"
    r"이다|입니다|들어갔|들어간|단계|이르렀|마쳤|끝났|마무리)"
)

#: 이 프로그램이 늘 넣는 표시 문구. 시행일 이야기로 세지 않는다.
FIXED_EFFECT_PHRASES = ("효력상태", "절차단계")

#: 효력 표현 뒤 몇 글자 안에서 헤지를 찾을지. 한 어절 남짓이다.
HEDGE_WINDOW = 6

#: 문장을 끝맺는 글자. 이 글자가 바로 뒤에 오면 주장이 완성된 것이다.
TERMINAL_ENDINGS = frozenset({"다", "음", "함", "임", "죠", "네"})

#: 아직 확정되지 않았음을 드러내는 말.
HEDGES = (
    "제안", "아직", "예정", "전이", "전입니다", "않", "아니", "확정 전", "미확정",
    "하도록", "되도록", "법률 아님", "될 예정", "앞두", "남아",
)

#: 문장을 나누는 자리. 규칙을 문장 단위로 적용한다.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

#: 공백을 모두 지운다.
WHITESPACE = re.compile(r"\s+")

#: 글자와 숫자만 남긴다. 창을 셀 때 문장부호가 자리를 먹지 않게 한다.
LETTERS_ONLY = re.compile(r"[^가-힣0-9A-Za-z]")


def _squeeze(text: str) -> str:
    """공백을 없앤 사본.

    `공 포되었다`처럼 낱말 가운데를 띄어 쓰면 한 글자 조각으로 쪼개져 낱말
    검사를 빠져나간다. 절차 표현을 찾을 때는 붙여 놓고 본다.
    """
    return WHITESPACE.sub("", text)


#: 글자마다 띄어 쓴 구간. 세 글자 이상 이어질 때만 본다.
SCATTERED = re.compile(
    r"(?:(?<![가-힣])[가-힣][ 	]+){2,}(?<![가-힣])[가-힣](?![가-힣])"
)


def _join_scattered(text: str) -> str:
    """`김 영 수 장 관`처럼 **글자마다 띄어 쓴 구간만** 붙인다.

    글 전체의 공백을 지우면 `자료 기준일은`까지 한 덩어리가 되어 멀쩡한 말이
    막힌다. 그래서 한 글자씩 흩어 놓은 자리만 골라 붙인다. 그 모양은 사람이
    보통 쓰지 않는 모양이고, 낱말 검사를 끄려는 시도이기 때문이다.
    """
    return SCATTERED.sub(lambda m: WHITESPACE.sub("", m.group(0)), text)

#: 초안을 최종·승인·배포본으로 표시하는 표현 (§4.2).
#: 허용 목록이 이미 대부분을 막지만, 자료에 그 낱말이 있는 경우를 대비해 남긴다.
FORBIDDEN_STATUS = re.compile(
    r"최종본|승인본|승인\s*완료|배포본|배포용|게시용|공개\s*가능|검토\s*완료본|확정본"
)

#: 인용 부호. 종류를 가리지 않고 모두 본다.
QUOTE_SPANS = [
    re.compile(r"[“\"]([^”\"]+)[”\"]"),
    re.compile(r"[‘']([^’']+)[’']"),
    re.compile(r"「([^」]+)」"),
    re.compile(r"『([^』]+)』"),
    re.compile(r"<([^>]+)>"),
    re.compile(r"《([^》]+)》"),
]

#: 남의 말을 옮길 때 쓰는 말. 공식 발언문 자료가 없으면 쓸 수 없다 (§2.16.2).
#: 허용 낱말 검사만으로는 못 막는다. 자료에 있는 낱말만 골라 붙여도 "누가 그렇게
#: 말했다"는 새 사실이 만들어지기 때문이다.
ATTRIBUTION = re.compile(
    r"(말했|말한|말하|밝혔|밝힌|전했|전한|강조|설명했|설명한|덧붙|지적했|"
    r"주장했|언급|평가했|촉구|발표|답했|답변|호소|당부|약속했|시사|반박|해명|설명이|"
    r"입장이|알려졌|알려진|한다고|이라고|라고는|고한다|고밝|고말|고전)"
)

#: 사람·기관을 가리키는 말. 문장 어디에 있든 본다.
SPEAKER_WORD = re.compile(
    r"(의원|의원실|위원장|위원회|장관|차관|청장|처장|총장|이사장|대표|대변인|"
    r"관계자|당국|정부|부처|실장|국장|과장|본부장|단장|"
    r"단체|법인|기관|국가|재단|공사|협회|연맹|조합|본부|사무처)"
)

#: 따옴표 바로 앞의 `X는`·`X가`. 이 자리는 말하는 이를 가리킨다.
#: 앞말이 문서를 가리키면(`부칙은 “…”`) 문서를 옮기는 것이라 괜찮다.
SPEAKER_BEFORE_QUOTE = re.compile(r"([가-힣]{2,})\s*(은|는|이|가)\s*[“\"「『‘]")

#: 남의 말을 옮길 때 쓰는 문법 어미. 닫힌 부류라 늘어나지 않는다.
QUOTATIVE = re.compile(r"(다고|라고|냐고|자고|이라고|다는|라는|다며|라며)")

#: 따옴표 곁에서 "누가 말했다"를 만드는 말. 사람·기관을 가리킨다.
SPEAKER_NEARBY = re.compile(
    r"(의원|의원실|위원장|위원회|장관|차관|청장|대표|대변인|관계자|당국|정부|"
    r"부처|실장|국장|과장|측)\s*(은|는|이|가|의|께서)?\s*$"
)

#: 초안이 말하는 조문. 아라비아 숫자뿐 아니라 한자·한글 수사도 본다.
#: `제九조`·`제구조`를 못 읽으면 코드가 세지 않은 조문이 그대로 나간다.
ARTICLE_MENTION = re.compile(
    r"제([0-9０-９]+|[一二三四五六七八九十百千]+|[일이삼사오육륙칠팔구십백천]+)"
    r"조(?:의([0-9０-９]+|[一二三四五六七八九十百千]+|[일이삼사오육륙칠팔구십백천]+))?"
)

#: 개정 지시문을 옮기는 모양. 조문 번호와 함께 나오면 자료와 대조한다.
AMENDMENT_VERB = re.compile(r"중.{0,40}로한다|로한다|신설한다|삭제한다|본다")

#: 날짜 표기. 해·달·날이 함께 나오면 **통째로** 견준다.
DATE_PATTERN = re.compile(r"(\d{4})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})")

#: 조각 날짜. `6월 7일`·`2025년 10월`처럼 일부만 적어도 자료와 견뎌야 한다.
#: 세 조각을 다 요구했더니 `의결일은 6월 7일이다`가 통과했다.
PARTIAL_DATE = re.compile(r"(\d{1,4})\s*(년|월)\s*(\d{1,2})\s*(월|일)")

#: 세는 수. 단위가 붙으면 단위까지 자료에 있어야 한다.
# 숫자에 **바로 붙은** 단위만 센다. 사이를 띄우면 `2207285 개정`의 `개`처럼
# 엉뚱한 글자를 단위로 읽는다. 단위 뒤 조사는 그대로 둔다 — `26명이`를
# 놓치면 조사 하나로 검사가 꺼진다.
COUNTED_NUMBER = re.compile(
    r"\d+(?:만|억|조|천)?(명|인|표|건|개|차|회|석|점|번|쪽|장|원|퍼센트|%)"
)

#: 자리값이 붙은 수. `26만`은 26이 아니라 260000이다.
# `제7조`의 `조`는 조문이지 1조(兆)가 아니다. 조문 번호는 빼고 본다.
SCALED_NUMBER = re.compile(r"(?<!제)(\d+)\s*(만|억|조|천)")
SCALE_VALUES = {"천": 1000, "만": 10_000, "억": 100_000_000, "조": 1_000_000_000_000}

#: 인용을 문서에 돌리는 말. 이 말이 곁에 없으면 남의 발언으로 읽힌다.
DOCUMENT_WORD = re.compile(r"(부칙|개정문|개정|자료|원문|조문|본문|법률|규정|항)")


def _dates_in(text: str) -> dict[str, str]:
    """글에 쓰인 날짜를 (원래 표기 -> 여덟 자리)로 모은다.

    수를 집합으로만 보면 자료의 `2025`·`6`·`7`을 모아 자료에 없는
    `2025년 6월 7일`을 만들 수 있다. 날짜는 통째로 견줘야 한다.
    """
    found: dict[str, str] = {}
    for match in DATE_PATTERN.finditer(text):
        year, month, day = match.groups()
        found[match.group(0)] = f"{int(year):04d}{int(month):02d}{int(day):02d}"
    return found


#: 조문에 무엇을 했다는 말. 개정문에 그 말이 없으면 지어낸 주장이다.
#: `제7조는 삭제되었다`·`삭제된 조문은 제7조이다` 둘 다 막는다. 순서를
#: 바꿔 빠져나가지 못하게 문장 어디에 있든 본다.
#: `바뀐 조문은 제7조이다`(요약)와 `모집할로 바뀐다`(주장)를 가르기 위해
#: 서술형만 넣는다. 매김꼴(`바뀐`)은 무엇이 바뀌었는지 말할 뿐이다.
ARTICLE_ACTION = re.compile(
    r"(삭제|신설|제외|포함|종료|추가|이동|폐지|변경|개정한다|로한다|본다|"
    r"바뀐다|바뀌었|바뀝니다|바꾼다|바꿨|고쳤|고친다)"
)

#: 사실 종류마다 "그 항목을 말한다"는 신호가 되는 말.
#: 초안이 이 말을 쓰면 **그 항목의 원장 값을 담아야 한다.**
#:
#: 지금까지는 "댄 근거가 문장에 있나"만 봤다. 그래서 짧은 값 하나를 넣고
#: 나머지를 아무 말로나 채울 수 있었다 — `의안번호 2207285은 처리 결과가
#: 없다`처럼. 방향을 뒤집어 **글이 말하는 항목** 쪽에서도 본다.
FACT_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("PLENARY_RESULT", ("처리결과", "의결결과", "회의결과", "심의결과", "처리됐", "처리되")),
    ("PLENARY_DECIDED_ON", ("의결일", "처리일", "의결한날", "가결일")),
    ("BILL_IDENTITY", ("의안번호",)),
    ("VOTE_PRESENT_COUNT", ("재석",)),
    ("VOTE_YES_COUNT", ("찬성",)),
    ("VOTE_NO_COUNT", ("반대",)),
)

#: 시점을 가리키는 말. 시행 이야기에 쓰려면 부칙에도 있어야 한다 (§2.16.4).
TIME_WORDS = (
    "다음달", "이달", "내달", "개월", "즉시", "곧바로", "당장", "이내", "이후",
    "익일", "익월", "내년", "올해", "분기", "상반기", "하반기", "말일",
)

RULE_DOC = "README §"


def _finding(
    index: int,
    rule_id: str,
    doc: str,
    part: str,
    message: str,
    severity: ValidationSeverity = ValidationSeverity.BLOCKING,
    excerpt: str = "",
) -> ValidationFinding:
    return ValidationFinding(
        finding_id=f"VF-{index:03d}",
        rule_id=rule_id,
        rule_document=RULE_DOC + doc,
        affected_part=part,
        severity=severity,
        message=message,
        excerpt=excerpt,
    )


def _strings_in(value: Any) -> list[str]:
    """어떤 모양으로 들어오든 그 안의 글자를 모두 꺼낸다.

    `quote`·`six_w_status`·`attachments`는 자유로운 모양이라 여기서 펼친다.
    펼치지 않으면 그 칸이 검사를 피해 가는 길이 된다.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            out.append(str(key))
            out.extend(_strings_in(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_strings_in(item))
        return out
    if value is None or isinstance(value, bool):
        return []
    return [str(value)]


def draft_text_parts(candidate: DraftCandidate) -> list[tuple[str, str]]:
    """(초안 부분 이름, 글) 목록. **모든 글자 칸**을 담는다."""
    parts: list[tuple[str, str]] = [
        ("제목", candidate.title.text),
        ("리드", candidate.lead.text),
    ]
    for i, point in enumerate(candidate.key_points, start=1):
        parts.append((f"핵심 요약 {i}", point.text))
    for paragraph in candidate.paragraphs:
        parts.append((f"본문 {paragraph.paragraph_id}", paragraph.text))
    for claim in candidate.claims:
        parts.append((f"주장 {claim.claim_id}", claim.text))
    parts.append(("문의처", candidate.contact_text))
    parts.append(("자료 기준일", candidate.basis_date))
    for i, text in enumerate(candidate.placeholders, start=1):
        parts.append((f"빈칸 표시 {i}", text))
    for i, text in enumerate(_strings_in(candidate.quote), start=1):
        parts.append((f"인용문 {i}", text))
    for i, text in enumerate(_strings_in(candidate.attachments), start=1):
        parts.append((f"붙임 {i}", text))

    # 이름표 칸은 글이 아니라 정해진 모양이어야 한다. `check_draft`가
    # `_check_identifiers`로 따로 본다. 사람이 읽는 문장이 아니지만 화면에
    # 그대로 나가므로, 안 보면 문단 번호 자리에 지어낸 표결 수를 적어 내보낼 수 있다.
    # 상태·육하원칙 칸은 사람이 읽는 글이 아니라 정해진 코드다.
    # `check_draft`가 코드 목록으로 따로 검사한다.
    return [(name, text) for name, text in parts if text and text.strip()]


def _strip_suffix(word: str) -> list[str]:
    """조사·어미를 떼어 낸 후보들. 원래 낱말도 함께 돌려준다."""
    stems = [word]
    current = word
    for _ in range(2):  # `되었습니다`처럼 두 번 붙는 경우까지만 본다
        for suffix in SUFFIXES:
            if len(current) > len(suffix) and current.endswith(suffix):
                current = current[: -len(suffix)]
                stems.append(current)
                break
        else:
            break
    return stems


#: 이름표 모양. `P-01`, `CL-03`, `DC-01`처럼 영문 대문자와 번호만 쓴다.
IDENTIFIER = re.compile(r"^[A-Z]{1,4}-\d{1,4}$")

#: 한 조각으로 볼 수 있는 최대 길이. 자료의 긴 낱말까지 담는다.
MAX_PIECE = 24


def _article_number(token: str) -> int | None:
    """조문 번호를 표기법과 상관없이 읽는다. `九`도 `구`도 9다."""
    flattened = sanitize(token)
    if flattened.isdigit():
        return int(flattened)
    return read_numeral_word(flattened)


def _starts_a_word(piece: str, haystack: str) -> bool:
    """이 조각이 자료의 **낱말 시작 자리**에 있는가.

    그냥 부분 문자열로 보면 `조계원 의원실`에서 `계원의원실`을 잘라내 없는
    이름을 만들 수 있다. 낱말 한가운데서 시작하는 조각은 자료가 말한 낱말이
    아니다.
    """
    packed = _squeeze(haystack)
    for probe in (haystack, packed):
        start = 0
        while True:
            index = probe.find(piece, start)
            if index < 0:
                break
            before = probe[index - 1] if index else ""
            if not ("가" <= before <= "힣"):
                return True
            start = index + 1
    return False


def _is_content(piece: str, haystack: str) -> bool:
    """뜻을 담은 조각인가. 조사·어미가 아니라 낱말이어야 한다.

    자료와 견줄 때는 **공백을 지운 사본과도** 견준다. 자료가 `자료 기준일`로
    띄어 썼는데 초안이 `자료기준일`로 붙여 쓰는 것은 같은 말이다. 다만 자료에
    **붙어 있는 자리**여야 한다. `문화예술` + `법인`처럼 따로 떨어진 조각을
    이어 붙이는 것은 여전히 막힌다.
    """
    if len(piece) == 1:
        # 한 글자라도 허용 낱말이면 뜻 조각으로 본다. `제7조이다`의 `조`가 그렇다.
        # 다만 **조사와 겹치는 글자는 안 된다.** `이`를 뜻 조각으로 인정하면
        # `이지은`이 `이`+`지`+`은`으로 쪼개져 지어낸 이름이 통과한다.
        return piece in SAFE_WORDS and piece not in SUFFIXES
    if _starts_a_word(piece, haystack):
        return True
    if piece in SAFE_WORDS:
        return True
    return read_numeral_word(piece) is not None


def _is_covered(run: str, haystack: str) -> bool:
    """붙어 있는 글자 덩어리를 **설명되는 조각들로 나눌 수 있는가.**

    공백을 지우면 `자료기준일은`처럼 멀쩡한 말도 한 덩어리가 된다. 덩어리째
    대조하면 자료에 없다며 막히므로, `자료` + `기준일` + `은`으로 나눌 수 있는지
    본다.

    규칙은 둘이다. **첫 조각은 뜻을 담은 낱말이어야 하고**, 그다음부터만 조사·
    어미가 올 수 있다. 이 규칙이 없으면 `이지은`이 `이`+`지`+`은`처럼 조사만으로
    쪼개져 지어낸 이름이 통과한다.
    """
    length = len(run)
    if length < 2:
        return True  # 한 글자는 조사와 구분되지 않는다

    # reachable[i] = i까지 왔고 뜻을 담은 조각을 하나 이상 지났다
    reachable = [False] * (length + 1)
    fresh = [False] * (length + 1)  # 아직 뜻 조각을 못 지난 상태
    fresh[0] = True

    for i in range(length):
        for j in range(i + 1, min(i + MAX_PIECE, length) + 1):
            piece = run[i:j]
            # **뜻 조각은 맨 앞에 딱 하나만** 올 수 있다. 여러 개를 이어 붙이게
            # 두면 자료의 `문화예술` + `법인`으로 있지도 않은 `문화예술법인`이
            # 만들어진다. 조각이 다 자료에 있어도 그 낱말은 자료에 없다.
            if fresh[i] and _is_content(piece, haystack):
                reachable[j] = True
            elif reachable[i] and piece in SUFFIXES:
                reachable[j] = True
    return reachable[length]


def _is_grounded_word(word: str, haystack: str) -> bool:
    """이 낱말을 자료나 허용 목록으로 설명할 수 있는가.

    한 글자만 남는 조각은 **설명이 되지 않는다.** 예전에는 통과시켰는데,
    `이지은`이 `이지` → `이`로 깎여 지어낸 이름이 그대로 나갔다. 조사를 떼다
    한 글자가 되면 그 후보는 버리고 다음 후보를 본다.
    """
    if word in SUFFIXES:
        # `이며`처럼 조사·어미만 떨어져 있는 경우. 뜻을 담지 않는다.
        return True
    for stem in _strip_suffix(word):
        if len(stem) < 2:
            continue
        # 그냥 부분 문자열로 보면 `조계원`에서 `계원`을 잘라내 없는 이름을
        # 만들 수 있다. 낱말 시작 자리여야 자료가 말한 낱말이다.
        if _starts_a_word(stem, haystack):
            return True
        if stem in SAFE_WORDS:
            return True
        # 수를 적은 말이면 수 검사가 따로 본다. 여기서 두 번 세지 않는다.
        if read_numeral_word(stem) is not None:
            return True
    # 낱말 자체가 한 글자면 조사와 구분할 수 없어 통과시킨다.
    return len(word) < 2


def build_allowed_text(
    ledger: FactLedger,
    final_text: ResolvedFinalText | None,
    article_set: ChangedArticleSet | None,
    *,
    announcement_subject: str = "",
    fixed_labels: tuple[str, ...] = (),
    include_quotes: bool = True,
) -> str:
    """초안이 기댈 수 있는 글 전체.

    자료에서 확인한 사실과 그 근거 문구, 확정된 최종 의결문, 부칙, 그리고
    Harness가 스스로 넣는 정형 표시만 담는다. **자료 원문 전체는 담지 않는다.**
    원문을 통째로 허용하면 자료 아무 데서나 문장을 끌어와도 통과한다.
    """
    pieces: list[str] = []
    for fact in ledger.facts:
        pieces.append(fact.value)
        pieces.extend(fact.value_items)
        pieces.append(fact.unit)
        if include_quotes:
            pieces.append(fact.evidence.quote)
            pieces.append(fact.evidence.source_name)
    for rule in ledger.supplementary_rules:
        pieces.append(rule.applies_to)
    for event in ledger.legislative_events:
        pieces.append(event.occurred_on)
    for identity in ledger.bill_identities:
        pieces.append(identity.bill_number)
    if final_text is not None:
        pieces.append(final_text.body_text)
        pieces.append(final_text.bill_number)
        pieces.append(final_text.source_name)
    if article_set is not None:
        pieces.extend(article_set.article_ids)
    pieces.append(announcement_subject)
    pieces.extend(fixed_labels)
    return "\n".join(p for p in pieces if p)


def check_draft(
    candidate: DraftCandidate,
    ledger: FactLedger,
    final_text: ResolvedFinalText | None,
    article_set: ChangedArticleSet | None,
    normalized: dict[str, NormalizedSource],
    *,
    announcement_subject: str = "",
    fixed_labels: tuple[str, ...] = (),
    has_statement_source: bool = False,
    template: DraftTemplate | None = None,
) -> list[ValidationFinding]:
    """초안 후보를 검사한다. 차단 항목이 하나라도 있으면 초안을 내주지 않는다."""
    findings: list[ValidationFinding] = []
    index = 1

    def add(
        rule_id: str,
        doc: str,
        part: str,
        message: str,
        excerpt: str = "",
        severity: ValidationSeverity = ValidationSeverity.BLOCKING,
    ) -> None:
        nonlocal index
        findings.append(_finding(index, rule_id, doc, part, message, severity, excerpt))
        index += 1

    raw_parts = draft_text_parts(candidate)
    # Harness가 정해진 값으로 만든 문단은 AI가 쓴 글이 아니다. 지어낸 값을
    # 찾을 대상이 아니므로 낱말·수·발언·효력 검사에서 뺀다. 대신 AI는 이
    # 이름표를 쓸 수 없다 — `orchestrator`가 받은 초안에서 걷어낸다.
    harness_parts = {
        f"본문 {p.paragraph_id}"
        for p in candidate.paragraphs
        if p.paragraph_id.startswith(HARNESS_ID_PREFIX)
    }

    # 보이지 않는 문자는 그 자체로 막는다. 보도자료 초안에 쓸 이유가 없고,
    # 있으면 검사를 피하려는 시도다. 화면에는 보이지 않으므로 사람이 알아챌
    # 수도 없다.
    # 글자부터 허용 목록으로 본다. **쓸 수 있는 글자만 쓸 수 있다.**
    # 못 쓸 문자를 세는 방식은 세 번 연속 졌다. 결합 문자·이체자 선택자·
    # 한글 채움 문자·점자 빈칸·키릴처럼 목록 밖의 문자가 끝없이 나왔다.
    allowed_chars = allowed_characters(
        build_allowed_text(
            ledger,
            final_text,
            article_set,
            announcement_subject=announcement_subject,
            fixed_labels=(*fixed_labels, DRAFT_LABEL, candidate.draft_label),
        )
    )
    for part, text in raw_parts:
        forbidden = find_forbidden(text, allowed_chars)
        if forbidden:
            shown = ", ".join(describe(c) for _, c in forbidden[:3])
            add(
                "CHARACTER_NOT_ALLOWED",
                "4.2",
                part,
                f"초안에 쓸 수 없는 글자가 {len(forbidden)}개 있습니다 ({shown}). "
                "자료에 없는 글자는 쓸 수 없습니다. 눈에 보이지 않는 문자를 "
                "끼워 검사를 피하는 것을 막기 위해서입니다.",
                text[:60],
            )

        hidden = find_invisible(text)
        if hidden:
            names = ", ".join(sorted({name for _, _, name in hidden})[:3])
            add(
                "INVISIBLE_CHARACTER",
                "4.2",
                part,
                f"눈에 보이지 않는 문자가 {len(hidden)}개 있습니다 ({names}). "
                "화면에 보이는 글과 검사하는 글이 달라지므로 쓸 수 없습니다.",
                text[:60],
            )

    # 검사는 정리한 사본으로 한다. 위 검사를 빠져나가는 새 문자가 생겨도
    # 낱말 검사가 계속 동작하게 하기 위해서다.
    parts = [(name, sanitize(text)) for name, text in raw_parts]
    agent_parts = [(n, x) for n, x in parts if n not in harness_parts]
    allowed_text = sanitize(
        build_allowed_text(
            ledger,
            final_text,
            article_set,
            announcement_subject=announcement_subject,
            fixed_labels=(*fixed_labels, DRAFT_LABEL, candidate.draft_label),
        )
    )
    # 쓸 수 있는 **수**는 사실 값에서만 온다. 근거 문구를 통째로 넣으면
    # 그 안의 날짜·숫자가 전부 자유롭게 쓸 수 있는 재료가 된다. 원장에 표결 수
    # 사실이 하나도 없는데 근거 문구의 `처리일 2025. 9. 18.`에서 `18`을 빼내
    # `표결 결과는 18표이다`가 통과했다.
    allowed_numbers = read_numbers(
        sanitize(
            build_allowed_text(
                ledger,
                final_text,
                article_set,
                announcement_subject=announcement_subject,
                fixed_labels=(*fixed_labels, DRAFT_LABEL, candidate.draft_label),
                include_quotes=False,
            )
        )
    )

    # --- G1. DRAFT 표시 -----------------------------------------------------
    if candidate.draft_label.strip() != DRAFT_LABEL:
        add(
            "DRAFT_LABEL_REQUIRED",
            "4.2",
            "초안 표시",
            f"‘{DRAFT_LABEL}’ 표시가 없습니다. 표시 없이는 초안을 내주지 않습니다.",
            candidate.draft_label,
        )

    # --- G2. 최종·승인·배포본 표시 금지 --------------------------------------
    for part, text in parts:
        match = FORBIDDEN_STATUS.search(_squeeze(text))
        if match:
            add(
                "NO_FINAL_OR_APPROVED_LABEL",
                "4.2",
                part,
                f"초안을 최종·승인·배포본처럼 표시했습니다: “{match.group(0)}”.",
                match.group(0),
            )

    # --- G3·G4. 필수 양식 ---------------------------------------------------
    minimum = template.min_key_points if template else MIN_KEY_POINTS
    maximum = template.max_key_points if template else MAX_KEY_POINTS
    if not minimum <= len(candidate.key_points) <= maximum:
        add(
            "KEY_POINT_COUNT",
            "2.7",
            "핵심 요약",
            f"핵심 요약은 {minimum}~{maximum}개여야 하는데 "
            f"{len(candidate.key_points)}개입니다.",
        )

    sections = {p.section_kind for p in candidate.paragraphs}
    # 제목·핵심 요약·리드는 고정 형식에서 별도 칸이다. 그 칸이 차 있으면
    # 계약의 같은 이름 문단을 채운 것으로 본다.
    if candidate.title.text.strip():
        sections.add("TITLE")
    if candidate.key_points:
        sections.add("KEY_POINTS")
    if candidate.lead.text.strip():
        sections.add("LEAD")
    if template is not None:
        # 필수 문단·금지 문단·필수 표시를 **계약에서 읽어** 확인한다.
        # 코드에 옮겨 적으면 계약과 갈라지고, 실제로 네 번 연속 갈라져 있었다.
        for required in sorted(template.required_sections):
            if required not in sections:
                add(
                    "REQUIRED_SECTION_MISSING",
                    "2.7",
                    "본문",
                    f"양식이 요구하는 문단 `{required}`이(가) 없습니다.",
                )
        for forbidden in sorted(template.forbidden_sections & sections):
            add(
                "FORBIDDEN_SECTION",
                "2.7",
                "본문",
                f"양식이 만들지 않기로 한 문단 `{forbidden}`이(가) 있습니다.",
            )
        # 값이 정해진 자리는 Harness가 채운다. AI가 쓴 것은 받지 않는다.
        for paragraph in candidate.paragraphs:
            if paragraph.section_kind in HARNESS_OWNED and not (
                paragraph.paragraph_id.startswith("HS-")
            ):
                add(
                    "HARNESS_OWNED_SECTION",
                    "2.7",
                    f"본문 {paragraph.paragraph_id}",
                    f"`{paragraph.section_kind}` 자리는 값이 이미 정해져 있어 "
                    "AI가 쓸 수 없습니다.",
                    paragraph.text[:60],
                )
        # 계약이 요구하는 표시가 초안 어딘가에 그대로 있어야 한다.
        whole = _squeeze(sanitize("\n".join(text for _, text in parts)))
        for mark in template.marks_for(candidate.basis_date):
            if _squeeze(sanitize(mark)) not in whole:
                add(
                    "REQUIRED_MARK_MISSING",
                    "2.16.2",
                    "필수 표시",
                    f"양식이 요구하는 표시가 없습니다: “{mark}”.",
                    mark,
                )
    if not candidate.contact_text.strip():
        add("CONTACT_REQUIRED", "2.7", "문의처", "문의처가 비어 있습니다.")
    if not candidate.basis_date.strip():
        add("BASIS_DATE_REQUIRED", "2.7", "자료 기준일", "자료 기준일이 비어 있습니다.")
    for name, code in (
        ("보도일 상태", candidate.release_date_status),
        ("문의처 상태", candidate.contact_status),
    ):
        if code not in STATUS_CODES:
            add(
                "STATUS_CODE_UNKNOWN",
                "2.10",
                name,
                f"정해지지 않은 상태 코드 `{code}`입니다. "
                f"쓸 수 있는 값: {', '.join(sorted(STATUS_CODES))}.",
                code,
            )
    # 이름표는 정해진 모양이어야 한다. 화면에 그대로 나가는 자리이므로,
    # 여기를 비워 두면 문단 번호 자리에 지어낸 표결 수를 적어 내보낼 수 있다.
    for name, value in (
        ("초안 번호", candidate.candidate_id),
        *((f"문단 번호 {p.paragraph_id}", p.paragraph_id) for p in candidate.paragraphs),
        *((f"주장 번호 {c.claim_id}", c.claim_id) for c in candidate.claims),
    ):
        if not IDENTIFIER.match(value):
            add(
                "IDENTIFIER_SHAPE_INVALID",
                "2.10",
                name,
                f"이름표는 `P-01`처럼 영문과 번호로만 적어야 하는데 `{value[:40]}`입니다.",
                value[:60],
            )
    known_kinds = (
        frozenset(template.section_kinds) if template else frozenset({"BODY"})
    )
    for paragraph in candidate.paragraphs:
        if paragraph.section_kind not in known_kinds:
            add(
                "SECTION_KIND_UNKNOWN",
                "2.7",
                f"문단 종류 {paragraph.paragraph_id}",
                f"정해지지 않은 문단 종류 `{paragraph.section_kind}`입니다.",
                paragraph.section_kind,
            )
    for name, fact_id in (
        ("발표 주체 근거", candidate.announcement_subject_fact_id),
        ("보도일 근거", candidate.release_date_fact_id),
    ):
        if fact_id and fact_id not in {f.fact_id for f in ledger.facts}:
            add(
                "FACT_REFERENCE_UNKNOWN",
                "2.10",
                name,
                f"원장에 없는 사실 `{fact_id}`을(를) 가리킵니다.",
                fact_id[:60],
            )

    for key, value in candidate.six_w_status.items():
        if key not in SIX_W_KEYS:
            add(
                "SIX_W_KEY_UNKNOWN",
                "2.10",
                "육하원칙",
                f"정해지지 않은 항목 `{key}`입니다.",
                str(key),
            )
        if value not in STATUS_CODES:
            add(
                "STATUS_CODE_UNKNOWN",
                "2.10",
                f"육하원칙 {key}",
                f"정해지지 않은 상태 코드 `{value}`입니다.",
                str(value),
            )

    if not announcement_subject.strip():
        add(
            "ANNOUNCEMENT_SUBJECT_REQUIRED",
            "2.11",
            "발표 주체",
            "누가 발표하는지 확인되지 않았습니다. 발표 주체 없이는 초안을 "
            "내주지 않습니다.",
        )

    # --- F4. 모든 문장이 원장 사실을 가리키는가 ------------------------------
    known_facts = {f.fact_id for f in ledger.facts}
    known_rules = {r.rule_id for r in ledger.supplementary_rules}
    known_claims = {c.claim_id for c in candidate.claims}

    def check_refs(
        part: str, fact_ids: list[str], claim_ids: list[str], rule_ids: list[str]
    ) -> None:
        for fact_id in fact_ids:
            if fact_id not in known_facts:
                add(
                    "FACT_REFERENCE_UNKNOWN",
                    "2.10",
                    part,
                    f"원장에 없는 사실 `{fact_id}`을(를) 가리킵니다.",
                )
        for claim_id in claim_ids:
            if claim_id not in known_claims:
                add(
                    "CLAIM_REFERENCE_UNKNOWN",
                    "2.10",
                    part,
                    f"초안에 없는 주장 `{claim_id}`을(를) 가리킵니다.",
                )
        for rule_id in rule_ids:
            if rule_id not in known_rules:
                add(
                    "RULE_REFERENCE_UNKNOWN",
                    "2.16.4",
                    part,
                    f"원장에 없는 부칙 `{rule_id}`을(를) 가리킵니다.",
                )

    check_refs("제목", candidate.title.fact_ids, candidate.title.claim_ids, [])
    check_refs("리드", candidate.lead.fact_ids, candidate.lead.claim_ids, [])
    for i, point in enumerate(candidate.key_points, start=1):
        check_refs(f"핵심 요약 {i}", point.fact_ids, point.claim_ids, [])
    for paragraph in candidate.paragraphs:
        check_refs(
            f"본문 {paragraph.paragraph_id}",
            paragraph.fact_ids,
            paragraph.claim_ids,
            paragraph.supplementary_rule_ids,
        )
    for claim in candidate.claims:
        check_refs(f"주장 {claim.claim_id}", claim.fact_ids, [], [])

    # --- F4. 문장은 자기가 가리키는 사실의 값을 담아야 한다 ------------------
    # 낱말 목록만으로는 절대 못 잡는 거짓말이 있다.
    #
    #     의안번호 2207285이(가) 부결로 처리되었다.
    #
    # 낱말도 자료에 있고 수도 자료에 있다. 그런데 자료에는 `원안가결`이라고
    # 적혀 있다. 낱말을 아무리 걸러도 이런 문장은 걸러지지 않는다.
    #
    # 그래서 **가리키는 사실 쪽에서** 본다. `F-02(원안가결)`를 근거로 대면
    # 그 문장 안에 `원안가결`이 있어야 한다. 근거는 대면서 다른 말을 쓰면 막는다.
    fact_by_id = {f.fact_id: f for f in ledger.facts}

    def check_anchored(part: str, text: str, fact_ids: list[str]) -> None:
        cited = [fact_by_id[i] for i in fact_ids if i in fact_by_id]
        if not cited:
            # 근거가 없으면 검사를 **끄는** 것이 아니라 **막는다.** 예전에는
            # 그냥 넘어가서, 근거를 비우는 것만으로 값 대조를 통째로 끌 수 있었다.
            add(
                "CLAIM_WITHOUT_FACT",
                "2.10",
                part,
                "어느 사실에서 나온 문장인지 근거가 없습니다. 근거 없는 문장은 "
                "초안에 쓸 수 없습니다.",
                text[:60],
            )
            return
        packed = _squeeze(text)
        numbers = read_numbers(text) | read_numbers(_join_scattered(text))
        missing = []
        for fact in cited:
            value = sanitize(fact.value)
            if _squeeze(value) in packed:
                continue
            values = read_numbers(value)
            if values and values <= numbers:
                continue
            missing.append(value)
        if not missing:
            return
        # **댄 근거는 전부 맞아야 한다.** 하나만 맞으면 되게 두면, 짧은 값
        # 하나를 대 놓고 나머지를 거짓으로 채울 수 있다. 쓰지 않은 사실은
        # 근거로 대지 않으면 된다.
        shown = ", ".join(f"`{v}`" for v in missing[:3])
        add(
            "CLAIM_VALUE_NOT_ANCHORED",
            "2.10",
            part,
            f"근거로 댄 사실의 값이 문장에 없습니다: {shown}"
            + (f" 외 {len(missing) - 3}개" if len(missing) > 3 else "")
            + ". 쓰지 않은 사실은 근거로 대지 마십시오.",
            text[:60],
        )

    # 글이 어떤 항목을 말하면 그 항목의 원장 값을 담아야 한다.
    values_by_kind: dict[str, list[str]] = {}
    for fact in ledger.facts:
        values_by_kind.setdefault(fact.kind, []).append(sanitize(fact.value))

    def check_topics(part: str, text: str) -> None:
        packed = _squeeze(text)
        for kind, words in FACT_TOPICS:
            values = values_by_kind.get(kind)
            if not values:
                continue  # 원장에 그 항목이 없으면 견줄 것이 없다
            if not any(word in packed for word in words):
                continue
            if any(_squeeze(v) in packed for v in values):
                continue
            shown = ", ".join(f"`{v}`" for v in sorted(set(values))[:2])
            add(
                "TOPIC_VALUE_MISMATCH",
                "2.10",
                part,
                f"자료가 말하는 값과 다릅니다. 자료의 값: {shown}.",
                text[:60],
            )

    check_anchored("제목", candidate.title.text, candidate.title.fact_ids)
    check_anchored("리드", candidate.lead.text, candidate.lead.fact_ids)
    for i, point in enumerate(candidate.key_points, start=1):
        check_anchored(f"핵심 요약 {i}", point.text, point.fact_ids)
    for claim in candidate.claims:
        check_anchored(f"주장 {claim.claim_id}", claim.text, claim.fact_ids)
    rule_values = {r.rule_id: r.applies_to for r in ledger.supplementary_rules}
    for paragraph in candidate.paragraphs:
        if paragraph.paragraph_id.startswith("HS-"):
            # Harness가 정해진 값으로 만든 문단이다. AI가 쓴 글이 아니므로
            # 근거를 되짚을 대상이 아니다.
            continue
        if paragraph.supplementary_rule_ids and not paragraph.fact_ids:
            pass  # 부칙만 가리키는 문단은 아래 부칙 대조가 본다
        else:
            check_anchored(
                f"본문 {paragraph.paragraph_id}", paragraph.text, paragraph.fact_ids
            )
        # 부칙을 근거로 댄 문단은 **그 부칙 원문을 담아야 한다.** 담지 않으면
        # 부칙 번호만 붙여 놓고 전혀 다른 시행 이야기를 쓸 수 있다.
        packed_text = _squeeze(paragraph.text)
        for rule_id in paragraph.supplementary_rule_ids:
            value = rule_values.get(rule_id)
            if value is None or _squeeze(sanitize(value)) in packed_text:
                continue
            add(
                "RULE_VALUE_NOT_ANCHORED",
                "2.16.4",
                f"본문 {paragraph.paragraph_id}",
                f"근거로 댄 부칙 원문이 문장에 없습니다: “{value[:40]}”.",
                paragraph.text[:60],
            )

    # --- G3. 필수 항목이 비어 있지 않은가 ------------------------------------
    # 3차 검토가 제목·리드·요약·본문을 전부 빈 글자로 바꿔도 초안이 나가는 것을
    # 찾았다. 비어 있으면 사람이 검토할 것이 없다.
    for part, text in (
        ("제목", candidate.title.text),
        ("리드", candidate.lead.text),
        *((f"핵심 요약 {i}", p.text) for i, p in enumerate(candidate.key_points, 1)),
        *((f"본문 {p.paragraph_id}", p.text) for p in candidate.paragraphs),
    ):
        if not text.strip():
            add("REQUIRED_TEXT_EMPTY", "2.7", part, "내용이 비어 있습니다.")

    # --- F1. 자료에 없는 수를 쓰지 않는가 (표기법 무관) ----------------------
    for part, text in agent_parts:
        check_topics(part, text)

    # 날짜와 세는 수는 조각이 아니라 **통째로** 자료에 있어야 한다.
    allowed_dates = set(_dates_in(allowed_text).values())
    packed_allowed = _squeeze(allowed_text)
    for part, text in agent_parts:
        for match in PARTIAL_DATE.finditer(text):
            if _squeeze(match.group(0)) in packed_allowed:
                continue
            add(
                "DATE_NOT_IN_LEDGER",
                "4.2",
                part,
                f"자료에 없는 날짜 `{match.group(0)}`이(가) 초안에 있습니다.",
                text[:60],
            )
        for shown, value in _dates_in(text).items():
            if value in allowed_dates:
                continue
            add(
                "DATE_NOT_IN_LEDGER",
                "4.2",
                part,
                f"자료에 없는 날짜 `{shown}`이(가) 초안에 있습니다.",
                text[:60],
            )
        for match in COUNTED_NUMBER.finditer(text):
            if _squeeze(match.group(0)) in packed_allowed:
                continue
            add(
                "COUNT_NOT_IN_LEDGER",
                "4.2",
                part,
                f"자료에 없는 셈 `{match.group(0)}`이(가) 초안에 있습니다. "
                "수가 자료에 있다고 그 단위까지 쓸 수 있는 것은 아닙니다.",
                text[:60],
            )

    for part, text in agent_parts:
        # 흩어 쓴 글자도 붙여서 본다. `이 백 오 십`을 그대로 두면 한 글자씩
        # 흩어져 수를 하나도 못 읽는다. 그때 낱말 검사는 "수는 수 검사가
        # 따로 본다"며 넘긴다. 두 검사가 서로 상대를 믿고 둘 다 안 보게 된다.
        probe_numbers = read_numbers(text) | read_numbers(_join_scattered(text))
        # `26만`은 26이 아니라 260000이다. 자리값을 안 읽으면 원장의 `26`
        # 하나로 26만·26억을 만들 수 있다.
        for match in SCALED_NUMBER.finditer(text):
            probe_numbers.add(int(match.group(1)) * SCALE_VALUES[match.group(2)])
        for number in sorted(probe_numbers - allowed_numbers):
            add(
                "NUMBER_NOT_IN_LEDGER",
                "4.2",
                part,
                f"자료에 없는 수 `{number}`이(가) 초안에 있습니다.",
                text[:60],
            )

    # --- F1·F2. 자료에 없는 말을 쓰지 않는가 (허용 목록) ---------------------
    # 지어낸 사람 이름·기관 이름·발언이 여기서 함께 걸린다. 따옴표를 쓰든 안
    # 쓰든 상관없다. 낱말 자체가 자료에도 허용 목록에도 없기 때문이다.
    # **공백을 지운 사본도 함께 본다.** 이것이 없으면 `김 영 수 장 관`처럼
    # 글자마다 띄어 써서 한 글자 조각으로 쪼개는 것만으로 낱말 검사가 통째로
    # 꺼진다. 붙여 놓고 보면 `김영수장관`이 되어 자료에 없는 것이 드러난다.
    for part, text in agent_parts:
        unknown: list[str] = []
        for probe in (text, _join_scattered(text)):
            for match in HANGUL_RUN.finditer(probe):
                word = match.group(0)
                if _is_grounded_word(word, allowed_text):
                    continue
                if _is_covered(word, allowed_text):
                    continue
                if word not in unknown:
                    unknown.append(word)
            for match in LATIN_RUN.finditer(probe):
                word = match.group(0)
                if word not in allowed_text and word not in unknown:
                    unknown.append(word)
        if unknown:
            add(
                "WORD_NOT_IN_LEDGER",
                "4.2",
                part,
                "자료에도 없고 쓸 수 있는 말도 아닌 표현이 있습니다: "
                + ", ".join(f"`{w}`" for w in unknown[:6])
                + (f" 외 {len(unknown) - 6}개" if len(unknown) > 6 else "")
                + ".",
                text[:60],
            )

    # --- F2. 인용과 발언 옮기기 ---------------------------------------------
    # 허용 낱말 검사는 자료에 있는 낱말로 조립한 **가짜 발언**을 막지 못한다.
    # 낱말은 다 자료에 있지만 "누가 그렇게 말했다"는 새 사실이기 때문이다.
    haystacks = [sanitize(n.normalized_text) for n in normalized.values()]
    # 화자로 볼 수 있는 이름. 발표 주체와 자료에 적힌 사람·기관 이름이다.
    speaker_names = [sanitize(announcement_subject)] + [
        sanitize(f.value)
        for f in ledger.facts
        if f.kind in ("ANNOUNCEMENT_SUBJECT", "PROPOSER", "SPEAKER")
    ]
    # 발표 주체가 `조계원 의원실`이면 `조계원`만 써도 같은 사람이다.
    speaker_names += [n.split()[0] for n in speaker_names if n and " " in n]
    speaker_names = [n for n in speaker_names if len(n) >= 2]
    for part, text in agent_parts:
        for pattern in QUOTE_SPANS:
            for match in pattern.finditer(text):
                quoted = match.group(1).strip()
                if len(quoted) < 2:
                    continue
                if not any(quoted in hay for hay in haystacks):
                    add(
                        "QUOTE_NOT_IN_SOURCE",
                        "4.2",
                        part,
                        f"자료에 그대로 있지 않은 인용입니다: “{quoted[:40]}”.",
                        quoted[:60],
                    )
        if has_statement_source:
            continue

        # 개정문·부칙을 그대로 옮기는 것은 오히려 필요하다(§2.16.3). 그래서
        # 인용 부호 자체는 막지 않고, **남의 말로 돌리는 모양**만 막는다.
        #
        # 사람·기관을 가리키는 말이 있고, 같은 문장에 인용 부호나 전언 어미
        # (`다고`·`라고`)가 있으면 그것은 발언을 옮기는 모양이다. 따옴표 바로
        # 앞만 보면 쉼표·콜론 하나로 꺼지고, 낱말 목록만 보면 새 표현이 끝없이
        # 나온다. `다고`·`라고`는 문법이 정한 닫힌 부류라 늘어나지 않는다.
        # 인용은 **어느 문서에서 옮겼는지** 밝혀야 한다. 같은 문단에
        # `부칙`·`개정문`·`자료` 같은 말이 없으면 남의 발언으로 읽힌다.
        # 문장 단위로만 보면 문장을 쪼개 화자를 딴 문단에 두어 빠져나갈 수 있다.
        packed_part = _squeeze(text)
        # 화자는 직함 목록만으로 알 수 없다. `조계원`처럼 자료에 있는 이름을
        # 그대로 쓰면 목록이 꺼진다. 발표 주체와 기관 이름도 함께 본다.
        speaker_here = bool(SPEAKER_WORD.search(packed_part)) or any(
            name and _squeeze(name) in packed_part for name in speaker_names
        )
        # 따옴표 바로 앞의 `X는`도 말하는 이다. 앞말이 문서를 가리키면
        # (`부칙은 “…”`) 문서를 옮기는 것이라 괜찮다.
        for match in SPEAKER_BEFORE_QUOTE.finditer(text):
            if DOCUMENT_WORD.search(match.group(1)):
                continue
            add(
                "STATEMENT_WITHOUT_SOURCE",
                "2.16.2",
                part,
                f"공식 발언문 자료가 없는데 ‘{match.group(1)}’의 말로 인용을 "
                "돌렸습니다. 발언은 공식 발언문에서만 가져올 수 있습니다.",
                text[:60],
            )
            break
        if any(p.search(text) for p in QUOTE_SPANS) and not DOCUMENT_WORD.search(
            packed_part
        ):
            add(
                "QUOTE_WITHOUT_DOCUMENT",
                "2.16.2",
                part,
                "인용을 어느 문서에서 옮겼는지 밝히지 않았습니다. 공식 발언문 "
                "자료가 없으므로 사람의 말로 읽힙니다.",
                text[:60],
            )

        for sentence in SENTENCE_SPLIT.split(text):
            packed_sentence = _squeeze(sentence)
            if not speaker_here:
                continue
            has_quote = any(p.search(text) for p in QUOTE_SPANS)
            has_quotative = QUOTATIVE.search(packed_sentence)
            if not (has_quote or has_quotative):
                continue
            add(
                "STATEMENT_WITHOUT_SOURCE",
                "2.16.2",
                part,
                "공식 발언문 자료가 없는데 사람·기관의 말을 옮기는 모양입니다: "
                f"“{sentence.strip()[:40]}”. 발언은 공식 발언문에서만 "
                "가져올 수 있습니다.",
                sentence.strip()[:60],
            )
            break

        match = ATTRIBUTION.search(_squeeze(text))
        if match:
            add(
                "STATEMENT_WITHOUT_SOURCE",
                "2.16.2",
                part,
                f"‘{match.group(0)}’처럼 남의 말을 옮겼는데 공식 발언문 자료가 "
                "없습니다. 발언은 공식 발언문에서만 가져올 수 있습니다.",
                text[:60],
            )
            continue

        # 낱말 목록만으로는 계속 진다. `라며`·`설명이다`처럼 새 표현이 끝없이
        # 나오기 때문이다. 그래서 **모양**으로도 본다. 따옴표 앞이나 뒤에 사람·
        # 기관을 가리키는 말이 붙어 있으면 그것은 발언을 옮기는 모양이다.
        for pattern in QUOTE_SPANS:
            for quote_match in pattern.finditer(text):
                before = text[max(0, quote_match.start() - 30) : quote_match.start()]
                # 따옴표 **앞**에 사람·기관이 있어야 발언이다. 뒤의 `라고`만
                # 보면 `부칙은 “…”라고 제안하고 있다`처럼 문서를 옮기는 것까지
                # 막힌다. 그것은 오히려 필요한 일이다.
                if SPEAKER_NEARBY.search(before):
                    add(
                        "STATEMENT_WITHOUT_SOURCE",
                        "2.16.2",
                        part,
                        "공식 발언문 자료가 없는데 누군가의 말을 옮기는 모양입니다: "
                        f"“{quote_match.group(1)[:30]}”.",
                        text[:60],
                    )
                    break
            else:
                continue
            break

    # --- F1. 개정 문구는 통째로 자료에 있어야 한다 ---------------------------
    # 따옴표 하나하나는 자료에 있어도 **순서를 바꾸면** 뜻이 뒤집힌다.
    #     자료:  “모집할”을 “모집ㆍ접수할”로 한다
    #     초안:  “모집ㆍ접수할”을 “모집할”로 한다   ← 개정 방향이 거꾸로다
    # 그래서 따옴표가 둘 이상 있는 문장은 **처음 따옴표부터 마지막 따옴표까지**
    # 통째로 자료에 있는지 본다.
    for part, text in agent_parts:
        for sentence in SENTENCE_SPLIT.split(text):
            spans = [
                (m.start(), m.end())
                for pattern in QUOTE_SPANS
                for m in pattern.finditer(sentence)
            ]
            if len(spans) < 2:
                # 따옴표를 빼면 이 검사를 피할 수 있었다. 그래서 따옴표가
                # 없어도 **개정 지시문을 옮기는 모양**이면 문장 전체를 본다.
                packed_sentence = _squeeze(sentence)
                article = ARTICLE_MENTION.search(packed_sentence)
                if article is None:
                    continue
                # 조문에 **무엇을 했다**는 말이 문장 어디에 있든 본다.
                # 앞뒤 자리로 가르면 `삭제된 조문은 제7조이다`처럼 순서를
                # 바꿔 빠져나간다. 대신 그 말이 개정문에 실제로 있으면 넘어간다.
                actions = {
                    m.group(0) for m in ARTICLE_ACTION.finditer(packed_sentence)
                }
                body = _squeeze(sanitize(final_text.body_text)) if final_text else ""
                # 개정문에 없는 말로 조문에 무엇을 했다고 하면 지어낸 주장이다.
                unknown_action = [a for a in actions if a not in body]
                # 개정 지시문을 옮기는 모양이면 문장을 통째로 대조한다.
                looks_like_directive = bool(AMENDMENT_VERB.search(packed_sentence))
                if not unknown_action and not looks_like_directive:
                    continue
                whole = sentence.strip()
            else:
                whole = sentence[
                    min(s for s, _ in spans) : max(e for _, e in spans)
                ]
            packed = _squeeze(whole)
            if any(packed in _squeeze(hay) for hay in haystacks):
                continue
            add(
                "QUOTED_PASSAGE_NOT_IN_SOURCE",
                "2.16.3",
                part,
                f"자료에 그대로 있지 않은 문구입니다: “{whole[:50]}”. "
                "개정 문구는 자료에 적힌 그대로 옮겨야 합니다.",
                whole[:60],
            )

    # --- H1. 절차를 앞질러 말하지 않는가 ------------------------------------
    # 금지 낱말을 세지 않고, **함께 쓸 말을 요구한다.** 어미를 바꿔도 빠져나갈
    # 수 없고, 새 표현이 생겨도 규칙을 고칠 필요가 없다.
    if candidate.effect_status == "NOT_A_LAW":
        for part, text in agent_parts:
            for sentence in SENTENCE_SPLIT.split(text):
                packed = _squeeze(sentence)
                # `개정 문구`, `공포일`처럼 이름으로 쓴 것은 주장이 아니다.
                # `공포됐다`, `시행된다`처럼 **서술로 쓴 것**만 본다.
                #
                # 한 문장에 여러 개가 나오면 **모두** 본다. 첫 것만 보면
                # `적용 완료 예정이며 공포되었다`처럼 앞을 헤지로 덮어
                # 뒤쪽 주장을 통째로 검사에서 빼낼 수 있다.
                for found in ASSERTIVE_EFFECT.finditer(packed):
                    stem = found.group(1)
                    # 헤지는 **효력 표현 바로 뒤에** 붙어 있어야 한다.
                    # 문장 어딘가에 있기만 하면 되게 두면 `제안한 법률은
                    # 공포됐다`가 통과한다. `제안`이 부정하는 것은 `법률`이지
                    # `공포`가 아니다.
                    # 헤지는 **주장 바로 뒤**에 붙어 있어야 한다.
                    #
                    # 전에는 문장이 어떻게 끝나는지를 봤다. 그랬더니
                    # `개정이 완료되어 다음 절차는 예정이다`처럼 뜻은 그대로
                    # 두고 **마지막 네 글자만 바꿔** 검사를 통째로 끌 수
                    # 있었다. `예정`이 부정하는 것은 `절차`이지 `개정 완료`가
                    # 아니다. 붙어 있어야 그 주장을 부정하는 것이다.
                    # 문장부호를 빼고 글자만 센다. `시행한다.”라고 제안`처럼
                    # 따옴표가 끼면 창이 밀려 헤지를 못 본다.
                    rest = LETTERS_ONLY.sub("", packed[found.end() :])
                    # 주장이 이미 끝났으면 뒤에 오는 헤지는 그 주장을
                    # 부정하지 않는다. `개정이 완료되었다, 예정 절차가 있다`의
                    # `예정`이 부정하는 것은 `절차`다.
                    if rest[:1] in TERMINAL_ENDINGS:
                        if not any(h in found.group(0) for h in HEDGES):
                            add(
                                "PREMATURE_EFFECT_CLAIM",
                                "2.16.1",
                                part,
                                f"아직 법률이 아닌데 ‘{stem}’을(를) 확정된 일처럼 "
                                "썼습니다. 제안 내용임을 함께 밝혀야 합니다.",
                                sentence.strip()[:60],
                            )
                        continue
                    if any(h in rest[:HEDGE_WINDOW] for h in HEDGES):
                        continue
                    add(
                        "PREMATURE_EFFECT_CLAIM",
                        "2.16.1",
                        part,
                        f"아직 법률이 아닌데 ‘{stem}’을(를) 확정된 일처럼 "
                        "썼습니다. 제안 내용임을 함께 밝혀야 합니다.",
                        sentence.strip()[:60],
                    )

    # --- H2. 시행 이야기에는 부칙 근거가 붙는가 ------------------------------
    # 본문뿐 아니라 제목·리드·요약도 본다. 한 칸이라도 빠지면 그 칸으로 나간다.
    rule_by_id = {r.rule_id: r for r in ledger.supplementary_rules}
    cited_rules = {
        p.paragraph_id: list(p.supplementary_rule_ids) for p in candidate.paragraphs
    }

    # 적용례·경과조치·특례는 초안에서 빠지면 안 된다 (§2.16.4, §4.2).
    # 원장에 있는데 초안이 한마디도 안 하면, 중요한 제한이 조용히 사라진 것이다.
    cited_everywhere = {
        rule_id
        for paragraph in candidate.paragraphs
        for rule_id in paragraph.supplementary_rule_ids
    }
    for rule in ledger.supplementary_rules:
        if rule.rule_id in cited_everywhere:
            continue
        add(
            "SUPPLEMENTARY_RULE_DROPPED",
            "2.16.4",
            "부칙",
            f"자료의 부칙 `{rule.kind.value}`을(를) 초안이 한마디도 하지 "
            f"않았습니다: “{rule.applies_to[:40]}”.",
            rule.applies_to[:60],
        )

    def _mentions_effect_date(text: str) -> bool:
        packed = _squeeze(text)
        # `효력 상태`는 이 프로그램이 늘 넣는 화면 표시 문구다. 시행일
        # 이야기가 아니므로 세지 않는다.
        for label in FIXED_EFFECT_PHRASES:
            packed = packed.replace(label, "")
        # `시행`·`공포`만 보면 `적용`·`효력`으로 바꿔 부칙 근거 요구를 끌 수 있다.
        return any(w in packed for w in ("시행", "공포", "적용", "효력", "발효"))

    for part, text in agent_parts:
        if not _mentions_effect_date(text):
            continue
        paragraph_id = part.removeprefix("본문 ")
        rule_ids = cited_rules.get(paragraph_id, [])
        if not rule_ids:
            add(
                "EFFECTIVE_DATE_NEEDS_RULE",
                "2.16.4",
                part,
                "시행·공포를 말하면서 부칙 근거를 달지 않았습니다.",
                text[:60],
            )
            continue
        # 붙어 있기만 하면 안 된다. 그 부칙이 실제로 그 내용을 담고 있어야 한다.
        rule_text = " ".join(
            rule_by_id[r].applies_to for r in rule_ids if r in rule_by_id
        )
        # 시점을 가리키는 말도 부칙에 있어야 한다. 수가 없는 `다음 달`,
        # `즉시`는 숫자 대조만으로는 걸리지 않는다.
        packed_rule = _squeeze(rule_text)
        for word in TIME_WORDS:
            if word in _squeeze(text) and word not in packed_rule:
                add(
                    "EFFECTIVE_DATE_NOT_IN_RULE",
                    "2.16.4",
                    part,
                    f"부칙에 없는 시점 표현 `{word}`을(를) 시행 이야기에 "
                    f"썼습니다. 부칙 원문: “{rule_text[:40]}”.",
                    text[:60],
                )

        rule_numbers = read_numbers(rule_text)
        for number in sorted(read_numbers(text) - rule_numbers - {0}):
            if number in allowed_numbers and str(number) in rule_text:
                continue
            add(
                "EFFECTIVE_DATE_NOT_IN_RULE",
                "2.16.4",
                part,
                f"부칙에 없는 시점 `{number}`을(를) 시행 이야기에 썼습니다. "
                f"부칙 원문: “{rule_text[:40]}”.",
                text[:60],
            )

    # --- H4. 초안이 말한 조문이 코드가 센 집합 안에 있는가 --------------------
    if article_set is not None:
        from app.harness.article_parser import top_level_article

        counted = set(article_set.article_ids)
        for part, text in agent_parts:
            for match in ARTICLE_MENTION.finditer(_squeeze(text)):
                # 한자·한글 수사를 아라비아 숫자로 되돌려 코드 집합과 견준다.
                number = _article_number(match.group(1))
                branch = _article_number(match.group(2)) if match.group(2) else None
                if number is None:
                    continue
                article = (
                    f"제{number}조의{branch}" if branch else f"제{number}조"
                )
                if article not in counted:
                    add(
                        "ARTICLE_NOT_IN_CHANGED_SET",
                        "2.16.3",
                        part,
                        f"코드가 센 변경 조문에 없는 `{article}`을(를) 초안이 "
                        f"말합니다. 센 조문: {', '.join(sorted(counted))}.",
                        match.group(0),
                    )

    return findings


def blocking(findings: list[ValidationFinding]) -> list[ValidationFinding]:
    return [f for f in findings if f.severity is ValidationSeverity.BLOCKING]
