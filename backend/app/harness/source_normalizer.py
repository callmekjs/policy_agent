"""원문 보존과 비교용 정규화 (README §2.3.1).

각 Source는 사용자가 준 내용을 그대로 둔 `raw_text`와, Agent에게 보내고 코드가
비교하는 `normalized_text`를 함께 가진다. 정규화 버전은 `source_text_v1`이다.

허용하는 변환은 딱 세 가지다.

1. UTF-8 BOM 제거
2. `CRLF`·`CR` 줄바꿈을 `LF`로 통일
3. Unicode NFC 정규화

공백을 합치거나 앞뒤를 잘라내지 않고, 문장부호·따옴표·숫자·단위·대소문자도
바꾸지 않는다. normalized 위치에서 raw의 line·column을 되찾는 span map을 만들어
화면의 `근거 보기`가 사용자가 붙여 넣은 원문 자리를 가리키게 한다.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass

#: 정규화 계약 버전. 규칙이 바뀌면 이 값도 함께 올린다.
SOURCE_TEXT_VERSION = "source_text_v1"

BOM = "﻿"


class SourceNormalizationError(RuntimeError):
    """정규화·해시·위치 연결에 실패했을 때. AI 호출 전에 차단한다."""

    code = "SOURCE_NORMALIZATION_FAILED"


@dataclass(frozen=True)
class RawPosition:
    """raw_text 안의 한 자리."""

    offset: int
    line: int  # 1부터
    column: int  # 1부터


@dataclass(frozen=True)
class RawSpan:
    """raw_text 안의 한 구간과 그 원문 일부."""

    start: RawPosition
    end: RawPosition
    excerpt: str


@dataclass(frozen=True)
class NormalizedSource:
    """한 Source의 원문·정규화문·해시·위치 지도."""

    version: str
    raw_text: str
    normalized_text: str
    raw_sha256: str
    normalized_sha256: str
    #: normalized의 각 글자가 시작되는 raw 위치. 길이는 len(normalized)+1이며
    #: 마지막 값은 raw 전체 길이(끝 경계)다.
    span_map: tuple[int, ...]
    #: normalized의 각 글자가 끝나는 raw 위치. 한 raw 덩어리가 여러 글자로
    #: 풀리거나 그 반대인 경우에도 구간이 뒤집히지 않게 한다.
    span_map_end: tuple[int, ...] = ()
    original_bytes_sha256: str | None = None

    def raw_span(self, start: int, end: int) -> RawSpan:
        """normalized의 [start, end) 구간에 대응하는 raw 구간을 돌려준다."""
        if not 0 <= start <= end <= len(self.normalized_text):
            raise SourceNormalizationError(
                f"정규화문 범위를 벗어났습니다: [{start}, {end})"
            )
        raw_start = self.span_map[start]
        if end == start:
            raw_end = raw_start
        elif self.span_map_end:
            raw_end = self.span_map_end[end - 1]
        else:
            raw_end = self.span_map[end]
        return RawSpan(
            start=self._position(raw_start),
            end=self._position(raw_end),
            excerpt=self.raw_text[raw_start:raw_end],
        )

    def _position(self, offset: int) -> RawPosition:
        before = self.raw_text[:offset]
        line = before.count("\n") + 1
        column = offset - (before.rfind("\n") + 1) + 1
        return RawPosition(offset=offset, line=line, column=column)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: 한글 자모 중 앞 글자와 합쳐지는 범위(중성 V, 종성 T).
#: 이 글자들은 결합 문자가 아니면서도 앞 글자와 한 글자로 합쳐지므로,
#: 여기서 끊으면 `ᄒ`+`ᅡ`+`ᆫ`이 `한`으로 합쳐지지 않는다.
HANGUL_JAMO_TAIL_START = 0x1160
HANGUL_JAMO_TAIL_END = 0x11FF


def _joins_previous(char: str) -> bool:
    """이 글자가 앞 글자와 한 덩어리로 합쳐질 수 있는가."""
    if unicodedata.combining(char) != 0:
        return True
    return HANGUL_JAMO_TAIL_START <= ord(char) <= HANGUL_JAMO_TAIL_END


def normalize_source(
    raw_text: str,
    original_bytes: bytes | None = None,
) -> NormalizedSource:
    """`source_text_v1`으로 정규화하고 위치 지도를 만든다.

    금지된 변경이 생기면 `SourceNormalizationError`를 올린다.
    """
    if not raw_text:
        raise SourceNormalizationError("자료 본문이 비어 있습니다.")

    # 1) BOM 제거. raw 안에서의 시작 위치를 함께 옮긴다.
    offset = 1 if raw_text.startswith(BOM) else 0
    body = raw_text[offset:]

    # 2) 줄바꿈 통일 + 3) NFC 정규화를 한 번에 훑으면서 위치를 기록한다.
    #    한 글자와 그 뒤에 붙어 합쳐질 수 있는 것들(결합 문자, 한글 중성·종성)을
    #    한 덩어리로 묶어 정규화한다. 이렇게 잘라 정규화한 결과가 통째로 정규화한
    #    결과와 같은지는 아래에서 다시 검사한다.
    pieces: list[str] = []
    span_map: list[int] = []
    span_map_end: list[int] = []
    i = 0
    length = len(body)

    while i < length:
        char = body[i]
        raw_index = offset + i

        # 줄바꿈: CRLF와 CR을 LF 하나로 바꾼다.
        if char == "\r":
            step = 2 if body.startswith("\r\n", i) else 1
            pieces.append("\n")
            span_map.append(raw_index)
            span_map_end.append(raw_index + step)
            i += step
            continue

        # 한 글자와, 그 뒤에 붙어 한 글자로 합쳐질 수 있는 것들을 묶는다.
        end = i + 1
        while end < length and body[end] not in "\r\n" and _joins_previous(body[end]):
            end += 1
        segment = body[i:end]
        composed = unicodedata.normalize("NFC", segment)
        pieces.append(composed)
        span_map.extend([raw_index] * len(composed))
        span_map_end.extend([offset + end] * len(composed))
        i = end

    normalized_text = "".join(pieces)
    span_map.append(len(raw_text))  # 끝 경계

    # 계약을 지켰는지 확인한다. 조각내어 정규화한 결과가 통째로 정규화한 결과와
    # 같아야 하고, 길이 지도도 글자 수와 맞아야 한다.
    expected = unicodedata.normalize(
        "NFC", raw_text[offset:].replace("\r\n", "\n").replace("\r", "\n")
    )
    if normalized_text != expected:
        raise SourceNormalizationError("정규화 결과가 계약과 다릅니다.")
    if len(span_map) != len(normalized_text) + 1:
        raise SourceNormalizationError("원문 위치 지도의 길이가 맞지 않습니다.")
    if span_map != sorted(span_map):
        raise SourceNormalizationError("원문 위치 지도가 순서를 벗어났습니다.")
    if normalized_text.strip() == "":
        raise SourceNormalizationError("자료에 읽을 수 있는 내용이 없습니다.")

    return NormalizedSource(
        version=SOURCE_TEXT_VERSION,
        raw_text=raw_text,
        normalized_text=normalized_text,
        raw_sha256=sha256_text(raw_text),
        normalized_sha256=sha256_text(normalized_text),
        span_map=tuple(span_map),
        span_map_end=tuple(span_map_end),
        original_bytes_sha256=(
            hashlib.sha256(original_bytes).hexdigest() if original_bytes else None
        ),
    )


def find_quote_offsets(normalized_text: str, quote: str) -> list[int]:
    """근거 문구가 정규화문에 완전 일치로 나오는 모든 시작 위치.

    비슷한 문장을 찾아 주지 않는다. 글자가 하나라도 다르면 0건이다.
    """
    if not quote:
        return []
    found: list[int] = []
    start = normalized_text.find(quote)
    while start != -1:
        found.append(start)
        start = normalized_text.find(quote, start + 1)
    return found
