"""PDF에서 글자를 뽑는다 (README §2.3 자료 입력).

**뽑기만 하고 고치지는 않는다.** 이 파일이 지키는 규칙은 하나다.

> 프로그램이 원문을 **몰래 바꾸지 않는다.**

PDF는 줄을 바꾸면서 낱말을 쪼갠다. 실제 의안원문에서 이런 일이 있었다.

    원문:    기부금품의 모집 및 사용에 관한 법률
    뽑은 글: 기부금품의 모집 및 사용에 관한 법 률
                                          ↑ 줄바꿈 자리

이것을 프로그램이 알아서 붙이면 안 된다. 진짜로 띄어 쓴 곳까지 붙게 되고,
그러면 자료에 없는 낱말이 만들어진다. 이 프로그램이 막으려는 바로 그것이다.

그래서 **뽑은 글을 사람에게 보여 주고 확인받는다.** 이상한 곳은 사람이
고친다. 프로그램이 저장하는 것은 **사람이 보고 넘긴 그 글**이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 한 번에 올릴 수 있는 파일 크기. 30,000자 자료면 이 안에 들어온다.
MAX_FILE_BYTES = 10 * 1024 * 1024

#: 받는 파일 종류. PDF만 받는다. DOCX·HWP는 아직이다.
ALLOWED_SUFFIXES = (".pdf",)


class DocumentReadError(Exception):
    """파일에서 글자를 뽑지 못했다. 이유를 쉬운 말로 담는다."""

    def __init__(self, code: str, message: str, next_action: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_action = next_action


@dataclass(frozen=True)
class ExtractedDocument:
    """뽑은 결과. **아직 자료가 아니다.** 사람이 확인해야 자료가 된다."""

    file_name: str
    page_count: int
    text: str
    #: 사람이 눈여겨봐야 할 자리. 확인을 도우려는 것이지 고치는 것이 아니다.
    warnings: list[str] = field(default_factory=list)


#: 낱말 가운데가 쪼개졌는지 볼 때 쓰는 모양.
#: 한글 한 글자 + 공백 + 한글 한 글자가 이어지면 줄바꿈에 잘렸을 수 있다.
_SPLIT_HINT = "가나다라마바사아자차카타파하"


def _find_warnings(text: str) -> list[str]:
    """사람이 볼 만한 자리를 짚는다. **고치지는 않는다.**"""
    warnings: list[str] = []

    # 한 글자만 홀로 떨어진 자리. 줄바꿈이 낱말을 자른 흔적이다.
    lonely = [
        token
        for token in text.split()
        if len(token) == 1 and "가" <= token <= "힣"
    ]
    if lonely:
        shown = ", ".join(f"`{t}`" for t in sorted(set(lonely))[:5])
        warnings.append(
            f"한 글자만 떨어져 있는 자리가 {len(lonely)}곳 있습니다 ({shown}). "
            "줄이 바뀌면서 낱말이 잘렸을 수 있습니다. 붙여야 할 곳이 있는지 봐 주세요."
        )

    if not text.strip():
        warnings.append(
            "글자를 하나도 뽑지 못했습니다. 그림으로 된 PDF일 수 있습니다."
        )

    return warnings


def extract_pdf_text(data: bytes, file_name: str) -> ExtractedDocument:
    """PDF에서 글자를 뽑는다.

    쪽마다 뽑아 빈 줄 하나로 잇는다. 쪽 번호나 머리글을 만들어 붙이지 않는다.
    **없는 글자를 넣지 않는 것**이 이 함수의 유일한 약속이다.
    """
    if not file_name.lower().endswith(ALLOWED_SUFFIXES):
        raise DocumentReadError(
            "FILE_TYPE_NOT_SUPPORTED",
            "지금은 PDF만 읽을 수 있습니다.",
            "PDF로 저장해 다시 올리거나, 본문을 복사해 붙여 넣어 주세요.",
        )
    if len(data) > MAX_FILE_BYTES:
        raise DocumentReadError(
            "FILE_TOO_LARGE",
            f"파일이 너무 큽니다. {MAX_FILE_BYTES // (1024 * 1024)}MB 이내여야 합니다.",
            "필요한 쪽만 따로 저장해 올려 주세요.",
        )

    try:
        import io

        import pdfplumber

        pages: list[str] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                pages.append((page.extract_text() or "").strip())
    except DocumentReadError:
        raise
    except Exception as exc:  # 라이브러리가 무엇을 던지든 사람 말로 바꾼다.
        raise DocumentReadError(
            "FILE_READ_FAILED",
            "PDF를 읽지 못했습니다.",
            "파일이 손상되지 않았는지 확인하시거나, 본문을 복사해 붙여 넣어 주세요.",
        ) from exc

    text = "\n\n".join(p for p in pages if p)
    return ExtractedDocument(
        file_name=file_name,
        page_count=len(pages),
        text=text,
        warnings=_find_warnings(text),
    )
