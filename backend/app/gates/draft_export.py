"""초안을 Markdown 파일로 내보낸다 (누적 5일차 합격선 `M2`·`M3`).

내보내기는 **화면보다 위험하다.** 화면에 있는 글은 이 프로그램 안에 머물지만,
내려받은 파일은 메일로 가고 메신저로 가고 다른 사람 손에 들어간다. 그 파일에
`DRAFT / 내부 검토용` 표시가 없으면 받은 사람은 완성본으로 읽는다.

그래서 여기서 글을 **새로 만들지 않는다.** 이미 검사를 통과한 초안의 글을
그대로 옮기고, 표시만 덧붙인다. 만들어 내는 자리가 있으면 그 자리가 새
고장점이 된다.
"""

from __future__ import annotations

from app.harness.draft_contracts import DRAFT_LABEL, DraftCandidate


def to_markdown(candidate: DraftCandidate) -> str:
    """검사를 통과한 초안을 Markdown으로 옮긴다.

    **글을 새로 쓰지 않는다.** 초안에 있는 글자만 옮긴다.
    """
    lines: list[str] = []

    # 표시를 맨 위에 둔다. 받은 사람이 첫 줄에서 보게 한다 (`M2`).
    lines.append(f"> **{DRAFT_LABEL}**")
    lines.append("")
    lines.append(f"# {candidate.title.text}")
    lines.append("")

    if candidate.key_points:
        lines.append("## 핵심 요약")
        lines.append("")
        for point in candidate.key_points:
            lines.append(f"- {point.text}")
        lines.append("")

    lines.append(candidate.lead.text)
    lines.append("")

    for paragraph in sorted(candidate.paragraphs, key=lambda p: p.priority_rank):
        text = paragraph.text.strip()
        if text:
            lines.append(text)
            lines.append("")

    # 표시를 아래에도 한 번 더 둔다. 위쪽만 잘라 내고 돌리는 일을 막는다.
    lines.append("---")
    lines.append("")
    lines.append(f"**{DRAFT_LABEL}** · 사람이 사실을 확인한 판입니다.")
    lines.append("")
    return "\n".join(lines)


def file_name(run_id: str, version: int) -> str:
    """내려받을 파일 이름.

    판 번호를 넣는다. 여러 판을 내려받았을 때 어느 것이 나중인지 파일 이름만
    보고 알 수 있어야 한다.
    """
    return f"보도자료초안_{run_id}_v{version}.md"
