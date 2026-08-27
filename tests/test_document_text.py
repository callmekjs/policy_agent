"""PDF에서 글자를 뽑을 때 **원문을 몰래 바꾸지 않는지** 본다.

이 파일이 지키는 성질은 하나다.

    프로그램이 저장하는 것은 **사람이 보고 넘긴 그 글**이다.

편하자고 프로그램이 알아서 고치기 시작하면, 자료에 없는 낱말이 만들어진다.
이 프로그램이 막으려는 바로 그것이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.gates.document_text import (
    DocumentReadError,
    extract_pdf_text,
)

#: 실제 의안원문. 국회 의안정보시스템에서 받은 파일과 같은 모양이다.
REAL_PDF = Path(r"C:\Users\anyca\Downloads\2207285_의사국 의안과_의안원문.pdf")


def test_PDF가_아니면_받지_않는다() -> None:
    with pytest.raises(DocumentReadError) as caught:
        extract_pdf_text(b"%PDF-1.4", "의안원문.hwp")
    assert caught.value.code == "FILE_TYPE_NOT_SUPPORTED"
    # 왜 안 되는지와 **다음에 뭘 하면 되는지**를 함께 준다.
    assert caught.value.next_action


def test_깨진_파일은_쉬운_말로_알린다() -> None:
    with pytest.raises(DocumentReadError) as caught:
        extract_pdf_text("PDF가 아닌 아무 바이트".encode(), "이상한.pdf")
    assert caught.value.code == "FILE_READ_FAILED"
    assert not caught.value.message.isascii(), "영어 오류를 그대로 보여줍니다."


def test_너무_큰_파일은_받지_않는다() -> None:
    from app.gates.document_text import MAX_FILE_BYTES

    with pytest.raises(DocumentReadError) as caught:
        extract_pdf_text(b"0" * (MAX_FILE_BYTES + 1), "큰.pdf")
    assert caught.value.code == "FILE_TOO_LARGE"


@pytest.mark.skipif(not REAL_PDF.exists(), reason="실제 의안원문 PDF가 없는 환경")
def test_실제_의안원문에서_글자를_뽑는다() -> None:
    """진짜 파일로 확인한다. 만들어 낸 자료로는 알 수 없는 것이 있다."""
    got = extract_pdf_text(REAL_PDF.read_bytes(), REAL_PDF.name)

    assert got.page_count >= 3
    # 개정문과 부칙이 들어 있어야 초안을 만들 수 있다.
    assert "일부를 다음과 같이 개정한다" in got.text
    assert "이 법은 공포한 날부터 시행한다" in got.text
    assert "2207285" in got.text or "7285" in got.text


@pytest.mark.skipif(not REAL_PDF.exists(), reason="실제 의안원문 PDF가 없는 환경")
def test_줄이_잘린_자리를_사람에게_알린다() -> None:
    """**고치지 않고 알린다.**

    이 PDF는 줄을 바꾸면서 `법률`을 `법 률`로 쪼갠다. 프로그램이 알아서
    붙이면 진짜로 띄어 쓴 곳까지 붙게 되고, 그러면 자료에 없는 낱말이
    만들어진다.

    그래서 짚어만 주고 고치는 것은 사람이 한다.
    """
    got = extract_pdf_text(REAL_PDF.read_bytes(), REAL_PDF.name)

    import re

    assert got.warnings, "줄이 잘린 자리를 알려 주지 않았습니다."
    assert any("잘렸을 수 있습니다" in w for w in got.warnings)
    # **고치지 않았는지** 확인한다. 잘린 자리가 그대로 있어야 한다.
    #
    # 이 PDF는 `법률`을 줄바꿈으로 쪼갠다(`법` 줄 끝, `률` 다음 줄 머리).
    # 사실 원장은 줄바꿈을 공백으로 보므로 `법 률`이라는 값이 만들어지고,
    # 그래서 실제 실행에서 "자료마다 값이 다릅니다"로 멈췄다.
    assert re.search(r"법\s+률", got.text), "프로그램이 원문을 몰래 붙였습니다."


def test_없는_글자를_만들어_넣지_않는다() -> None:
    """쪽 번호나 머리글을 붙이지 않는다.

    뽑은 글에 프로그램이 만든 말이 섞이면, 그 말이 자료가 말한 것처럼
    보인다. 근거를 되짚을 때 원문에 없는 줄이 나온다.
    """
    import io

    canvas = pytest.importorskip(
        "reportlab.pdfgen.canvas", reason="PDF를 만들 도구가 없는 환경"
    )

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(100, 700, "hello")
    pdf.save()

    got = extract_pdf_text(buffer.getvalue(), "한쪽.pdf")
    assert got.text.strip() == "hello", f"만들어 낸 말이 섞였습니다: {got.text!r}"
