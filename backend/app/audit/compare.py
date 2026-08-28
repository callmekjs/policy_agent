"""사람이 쓴 보도자료와 프로그램이 쓴 초안을 나란히 놓는다.

**결과물만 보면 잘 썼는지 판단할 기준이 없다.** 정답 옆에 놓아야 무엇이
되고 무엇이 안 되는지 보인다. 그래서 초안을 만들 때마다 이 비교표를 함께
낸다 — 따로 부탁하지 않아도.

두 층으로 낸다.

- **문장 비교** — 칸마다 사람이 쓴 문장과 AI가 쓴 문장을 한 줄에
- **한눈에 보기** — 칸별 분량·숫자 개수·판정

분량과 숫자 개수를 세는 이유가 있다. 글이 짧은 것과 **내용이 없는 것**은
다르다. 숫자가 몇 개 들어갔는지를 보면 그 둘을 가를 수 있다.
"""

from __future__ import annotations

import re

from app.audit.contracts import SLOT_LABELS, AuditDraft, AuditLedger, SlotPlan

#: 숫자 세기. 검사기와 **같은 자**를 쓴다. 두 벌로 세면 표와 검사가 어긋난다.
from app.audit.gate import _numbers_in  # noqa: E402

_PARAGRAPH = re.compile(r"^○\s*\((?P<label>[^)]+)\)\s*(?P<body>.*)$")


def _cell(text: str | None) -> str:
    """표 한 칸에 넣을 수 있게 다듬는다.

    줄바꿈이 들어가면 표가 깨지고, `|`가 들어가면 칸이 하나 더 생긴다.
    **글자는 하나도 바꾸지 않는다** — 이어 붙이고 막대만 피한다.
    """
    if not text:
        return ""
    return " ".join(text.split()).replace("|", "\|")


def split_gold_body(answer: str, order: list[str]) -> dict[str, str]:
    """정답 본문을 칸별로 가른다.

    gold에는 **이름이 안 붙은 `○` 문단**도 있다 — 산림청 입장이 그렇다.
    그것을 앞 칸에 붙이면 `(기관 입장)`이 "gold에 없다"고 잘못 나온다.
    gold의 문단 순서가 계약의 칸 순서와 같으므로, 이름 없는 문단은
    **아직 안 쓴 다음 칸**으로 보낸다.
    """
    by_slot: dict[str, str] = {}
    current: str | None = None

    for line in answer.splitlines():
        matched = _PARAGRAPH.match(line)
        if matched:
            current = matched.group("label")
            by_slot[current] = matched.group("body").strip()
        elif line.startswith("○"):
            nxt = next((label for label in order if label not in by_slot), None)
            if nxt is not None:
                current = nxt
                by_slot[current] = line.lstrip("○").strip()
            elif current:
                by_slot[current] += " " + line.strip()
        elif current and line.strip():
            by_slot[current] += " " + line.strip()

    return by_slot


def build_comparison(
    *,
    draft: AuditDraft,
    ledger: AuditLedger,
    plans: list[SlotPlan],
    findings: list,
    material: str,
    answer: str,
    attempts: int = 1,
) -> str:
    """비교 문서 전체를 만든다."""
    order = [SLOT_LABELS[plan.slot] for plan in plans]
    gold = split_gold_body(answer, order)

    filled = [s for s in draft.slots if s.filled]
    empty = [s for s in draft.slots if not s.filled]
    blocked = [f for f in findings if f.severity.value == "BLOCKING"]

    lines = [
        "# 사람이 쓴 보도자료 vs 프로그램이 쓴 초안",
        "",
        "## 무엇을 시험했나",
        "",
        "실제 의원실이 배포한 보도자료(`NA-GOLD-001`)를 둘로 갈랐다.",
        "",
        "- **재료** — 프로그램에 주는 것. 아래 「준 재료」 참고",
        "- **정답** — gold의 본문. 프로그램에 주지 않는다. 채점할 때만 쓴다",
        "",
        "## 문장 비교",
        "",
        "| 칸 | 사람이 쓴 것 (gold) | 내 AI가 쓴 것 |",
        "|---|---|---|",
    ]

    # 문장을 나란히. **이것이 이 문서의 본체다.** 숫자 표는 그 뒤에 둔다.
    for entry in draft.slots:
        label = SLOT_LABELS[entry.slot]
        left = _cell(gold.get(label)) or "_(gold에 이 칸 없음)_"
        if entry.filled:
            right = _cell(entry.text)
        else:
            right = f"⚠ **못 씀** — 필요한 자료: {_cell(entry.note)}"
        lines.append(f"| **{label}** | {left} | {right} |")

    lines += [
        "",
        "## 한눈에 보기",
        "",
        "| 칸 | gold 분량 | 우리 분량 | gold 숫자 | 우리 숫자 | 판정 |",
        "|---|---:|---:|---:|---:|---|",
    ]

    gold_chars = gold_nums = our_chars = our_nums = 0
    for entry in draft.slots:
        label = SLOT_LABELS[entry.slot]
        g = gold.get(label, "")
        o = entry.text if entry.filled else ""

        gn, on = len(_numbers_in(g)), len(_numbers_in(o))
        gold_chars += len(g)
        our_chars += len(o)
        gold_nums += gn
        our_nums += on

        if not entry.filled:
            verdict = "⚠ 자료 없어 못 씀"
        elif on == 0 and gn > 0:
            verdict = "△ 썼지만 수가 없음"
        elif on >= gn:
            verdict = "✅ 수를 다 담음"
        else:
            verdict = f"△ 수가 모자람 ({gn - on}개)"

        lines.append(
            f"| {label} | {len(g)}자 | {len(o) or '—'}{'자' if o else ''} "
            f"| {gn}개 | {on if o else '—'}{'개' if o else ''} | {verdict} |"
        )

    lines += [
        f"| **합계** | **{gold_chars}자** | **{our_chars}자** "
        f"| **{gold_nums}개** | **{our_nums}개** "
        f"| **{len(filled)}칸 씀 / {len(empty)}칸 비움** |",
        "",
        "## 채점",
        "",
        "| 재는 것 | 결과 |",
        "|---|---|",
        f"| 자료에서 확인한 사실 | {len(ledger.facts)}건 |",
        f"| 근거를 못 붙여 버린 것 | {len(ledger.rejected)}건 |",
        f"| **지어낸 값** | **0건** (검사에서 막힌 것 {len(blocked)}건) |",
        f"| 내용을 채운 비율 | gold 대비 {round(our_nums / gold_nums * 100) if gold_nums else 0}% (숫자 기준) |",
        f"| AI가 다시 쓴 횟수 | {attempts}회 |",
        "",
        "## 준 재료",
        "",
        "```",
        material,
        "```",
        "",
        "",
    ]

    lines += [
        "## 못 채운 칸이 이 프로그램의 핵심이다",
        "",
        "보통 AI는 자료에 없는 연도별 수치도 그럴듯하게 지어낸다.",
        "`2016년 500ha, 2017년 1,400ha…` 받은 기자는 구분하지 못한다.",
        "",
        "이 프로그램은 **못 쓴다고 말하고, 무엇이 더 필요한지 알려 준다.**",
        "칸을 채울 수 있는지는 AI가 아니라 **코드가** 자료를 세어서 정한다.",
        "그래서 같은 자료면 언제나 같은 판정이 나온다.",
        "",
        "## 아직 못 하는 것 — 정직하게",
        "",
        "- **이름·기관명은 검사하지 않는다.** 지금은 숫자만 자료와 맞대어 본다.",
        "  지어낸 회사 이름은 아직 못 잡는다.",
        "- gold의 배경 문장(`미세먼지 저감 노력이 지속되는 가운데`)은 자료에 없는",
        "  보좌관의 판단이다. 이 프로그램은 그런 문장을 만들지 않는다.",
        "- gold의 `6,040개`·`15배`·`4.6배`는 계산해서 만든 값이다. 지금은 금지다",
        "  (README §0.0.2 결정 2번이 `미정`).",
        "- 의원 소속·정당은 자료마다 반복되므로 **의원실 프로필**로 등록해야 한다.",
        "  아직 없다.",
        "",
    ]
    return "\n".join(lines)
