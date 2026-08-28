"""국정감사형 초안을 Markdown으로 내보낸다.

내보내기는 화면보다 위험하다. 화면 글은 이 프로그램 안에 머물지만, 내려받은
파일은 메일로 가고 메신저로 가고 다른 사람 손에 들어간다. 받은 사람은
**그 파일만 보고** 판단한다.

그래서 여기서 **글을 새로 만들지 않는다.** 초안과 원장에 있는 글자만 옮기고
정해진 머리글만 붙인다. 만들어 내는 자리가 있으면 그 자리가 새 고장점이 된다.

`DRAFT` 표시는 초안이 아니라 **상수**에서 가져와 위·아래 두 곳에 붙인다.
초안에서 표시를 떼어도 파일에는 남는다. 기존 본회의형 `draft_export.py`가
같은 방식이고, 5일차 검토가 그 설계를 확인했다.
"""

from __future__ import annotations

from app.audit.contracts import (
    DRAFT_LABEL,
    SLOT_LABELS,
    AuditDraft,
    AuditLedger,
)


def to_markdown(draft: AuditDraft, ledger: AuditLedger) -> str:
    """검사를 통과한 초안을 사람이 읽을 파일로 옮긴다."""
    lines: list[str] = []

    # 표시를 맨 위에 둔다. 받은 사람이 첫 줄에서 보게 한다.
    lines.append(f"> **{DRAFT_LABEL}**")
    lines.append("")

    # 제목은 재료에 있던 그대로다. 두 줄이면 두 줄로 둔다.
    for line in draft.headline.splitlines():
        if line.strip():
            lines.append(f"# {line.strip()}")
    lines.append("")

    for sub in draft.subheads:
        if sub.strip():
            lines.append(f"- {sub.strip()}")
    if draft.subheads:
        lines.append("")

    # 본문. **못 채운 칸도 지우지 않는다.** 빈 칸을 조용히 빼면 받은 사람은
    # 빠진 것이 있다는 사실 자체를 모른다.
    for entry in draft.slots:
        label = SLOT_LABELS[entry.slot]
        if entry.filled:
            lines.append(f"○ ({label}) {entry.text.strip()}")
        else:
            lines.append(f"⚠ ({label}) 못 채움 — 필요한 자료: {entry.note.strip()}")
        lines.append("")

    lines.append("〈끝〉")
    lines.append("")

    # 근거. **모든 값이 어디서 왔는지** 붙인다. 보좌관이 원문에서 바로
    # 확인할 수 있어야 한다. 이 표가 없으면 초안을 믿을 근거가 없다.
    if ledger.facts:
        lines.append("## 근거")
        lines.append("")
        lines.append("| 사실 | 값 | 출처 | 줄 | 원문 |")
        lines.append("|---|---|---|---|---|")
        for fact in ledger.facts:
            # 표 한 칸에 줄바꿈이 들어가면 Markdown 표가 깨진다. 재료는 사람이
            # 만든 문서라 문장 한가운데 줄바꿈이 흔하다.
            quote = " ".join(fact.evidence.quote.split())
            lines.append(
                f"| {fact.fact_id} | {fact.value} | {fact.evidence.source_name} "
                f"| {fact.evidence.line} | {quote} |"
            )
        lines.append("")

    # 표시를 아래에도 한 번 더 둔다. 위쪽만 잘라 내고 돌리는 일을 막는다.
    lines.append("---")
    lines.append("")
    lines.append(f"**{DRAFT_LABEL}** · 사람이 확인해야 하는 초안입니다.")
    lines.append("")
    return "\n".join(lines)


def file_name(run_id: str, version: int = 1) -> str:
    """내려받을 파일 이름. 판 번호를 넣어 어느 것이 나중인지 알게 한다."""
    return f"국정감사보도자료초안_{run_id}_v{version}.md"
