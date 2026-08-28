"""AI에게 본문을 쓰게 하고, 그 답을 계약 모양으로 받는다.

**막는 것보다 자리를 안 주는 것이 낫다.**

못 채우기로 정한 칸은 AI에게 **아예 알려 주지 않는다.** 알려 주면 AI는
"자료가 없어 못 씁니다"라고 답하는 대신 그럴듯하게 채운다. 연도별 수치가
없어도 `2016년 529ha…`를 만들어 낸다. 자리를 안 주면 채울 자리 자체가 없다.

4일차에서 배운 것과 같다 — *효력을 말하는 자리를 AI에게서 거둬 Harness가
만든다.* 여기서는 한 걸음 더 간다. **자료가 없으면 자리도 없다.**

검사기(`gate.py`)는 그래도 다시 확인한다. 구조로 한 번, 검사로 한 번.
"""

from __future__ import annotations

from typing import Any

from app.audit.contracts import (
    SLOT_LABELS,
    AuditFactKind,
    SLOT_PURPOSE,
    AuditDraft,
    AuditLedger,
    SlotKind,
    SlotPlan,
    SlotText,
)
from app.audit.slots import SLOT_NEEDS, SLOT_ORDER
from app.infrastructure.model_gateway import ModelCallRequest

#: 값을 **나열하는** 칸의 사실 종류. 검사기와 같은 목록을 쓴다.
ENUMERATED_KINDS = frozenset(
    {AuditFactKind.TIME_SERIES, AuditFactKind.BREAKDOWN, AuditFactKind.CASE}
)

#: AI가 문장 안에 두는 자리. Harness가 여기에 목록을 채운다.
LIST_TOKEN = "{목록}"

AGENT_NAME = "AuditDraftAgent"
PROMPT_VERSION = "audit_draft_v1"

#: 본문 출력 상한. 칸이 일곱 개뿐이라 넉넉하다.
MAX_OUTPUT_TOKENS = 3_000

#: AI에게 주는 규칙. **자료 밖으로 나가지 못하게 하는 말만 담는다.**
DRAFTING_RULES = (
    "너는 국회의원실 보좌관이 언론에 배포하기 전 검토할 보도자료 초안의 "
    "**본문만** 쓴다. 제목과 부제는 이미 정해져 있으니 다시 쓰지 않는다.",
    "각 칸에 주어진 사실만 쓴다. **주어지지 않은 수·날짜·기관명·사람 이름은 "
    "절대 쓰지 않는다.** 어림잡거나 계산해서 만든 수도 쓰지 않는다.",
    "문체는 개조식이다. 문장을 `~했음`·`~임`으로 끝낸다.",
    "한 문장은 짧게 쓴다. 연결어미를 두 번 이상 쓰지 않는다.",
    "칸의 순서가 곧 중요도다. 뒤 칸을 지워도 말이 되게 쓴다.",
    "쓸 사실이 모자라면 짧게 쓴다. **모자란 자리를 지어내서 채우지 않는다.**",
)


def _list_text(ledger: AuditLedger, plan: SlotPlan) -> str:
    """이 칸의 목록 문구. `2016년 529ha(314,528그루), 2017년 …`

    요청을 만들 때와 답을 받을 때 **같은 함수**를 쓴다. 두 벌로 만들면
    AI에게 보여 준 것과 파일에 들어가는 것이 어긋난다.
    """
    kinds = set(SLOT_NEEDS[plan.slot])
    if not kinds <= ENUMERATED_KINDS:
        return ""
    order: list[str] = []
    grouped: dict[str, list[str]] = {}
    for fact in ledger.facts:
        if fact.fact_id not in plan.usable_fact_ids or fact.kind not in kinds:
            continue
        if fact.scope not in grouped:
            grouped[fact.scope] = []
            order.append(fact.scope)
        grouped[fact.scope].append(fact.value)

    parts = []
    for scope in order:
        values = grouped[scope]
        rest = f"({', '.join(values[1:])})" if len(values) > 1 else ""
        parts.append(f"{scope} {values[0]}{rest}".strip())
    return ", ".join(parts)


def build_drafting_request(
    *,
    ledger: AuditLedger,
    plans: list[SlotPlan],
    headline: str,
    subheads: list[str],
) -> ModelCallRequest:
    """AI에게 보낼 입력. **채울 수 있는 칸만 담는다.**"""
    facts_by_id = {f.fact_id: f for f in ledger.facts}
    slots: list[dict[str, Any]] = []

    for plan in plans:
        if not plan.fillable:
            # 못 채우는 칸은 이름조차 보내지 않는다.
            continue
        usable = [facts_by_id[i] for i in plan.usable_fact_ids if i in facts_by_id]

        # 같은 시점·지역의 값을 **미리 묶어서** 준다.
        #
        # 납작하게 주면 AI가 `529ha(2016년)`와 `314,528그루(2016년)`를 스스로
        # 짝지어야 한다. 그 부담 때문에 숫자를 통째로 빼고 "연도별 현황이
        # 집계됐음"이라고만 쓴 적이 있다. 묶어서 주면 짝지을 일이 없다.
        #
        # 순서는 **자료에 나온 차례** 그대로다. 정렬하면 `2016 · 2017 · 2018`이
        # 뒤섞일 수 있고, 글자순 정렬은 시간순이 아니다.
        groups: list[dict[str, Any]] = []
        index: dict[str, dict[str, Any]] = {}
        for fact in usable:
            group = index.get(fact.scope)
            if group is None:
                group = {"scope": fact.scope, "values": []}
                index[fact.scope] = group
                groups.append(group)
            group["values"].append(
                {
                    "fact_id": fact.fact_id,
                    "subject": fact.subject,
                    "value": fact.value,
                }
            )

        # **문구를 Harness가 미리 만든다.**
        #
        # `scope`를 문장에 넣으라고 여러 번 부탁했지만 AI는 계속 연도를
        # 빠뜨렸다. `529ha, 1,435ha, 2,443ha 순으로` — 읽는 사람은 그 수가
        # 언제 것인지 알 수 없다.
        #
        # 부탁이 안 통하면 자리를 거둔다. 값이 정해진 자리는 Harness가
        # 채운다는 원칙 그대로다. AI는 이 문구들을 이어 붙이기만 하면 된다.
        for group in groups:
            values = [v["value"] for v in group["values"]]
            head = values[0]
            rest = f"({', '.join(values[1:])})" if len(values) > 1 else ""
            group["phrase"] = f"{group['scope']} {head}{rest}".strip()

        entry: dict[str, Any] = {
                "slot": plan.slot.value,
                "label": SLOT_LABELS[plan.slot],
                "purpose": SLOT_PURPOSE[plan.slot],
                # 이 칸에서 쓸 수 있는 사실만. 다른 칸의 사실은 안 보낸다.
                "facts": [
                    {
                        "fact_id": fact.fact_id,
                        "subject": fact.subject,
                        "value": fact.value,
                        "scope": fact.scope,
                        "quote": fact.evidence.quote,
                    }
                    for fact in usable
                ],
                #: 같은 시점·지역끼리 묶은 것.
                "groups": groups,
        }

        # 나열하는 칸은 **목록을 Harness가 만든다.**
        #
        # 지시문으로 세 번 고쳤는데 세 번 다 다른 데가 망가졌다. 연도를 넣으라
        # 했더니 숫자를 뺐고, 숫자를 넣으라 했더니 연도를 뺐다. 부탁으로 풀
        # 문제가 아니다. AI는 `{목록}` 자리를 둔 이음말만 쓴다.
        # 요청과 답 처리가 **같은 함수**를 쓴다. 두 벌로 만들면 AI에게 보여
        # 준 목록과 파일에 들어가는 목록이 어긋난다. 실제로 한 번 어긋났다.
        composed = _list_text(ledger, plan)
        if composed:
            entry["list_text"] = composed
        slots.append(entry)

    return ModelCallRequest(
        agent_name=AGENT_NAME,
        prompt_version=PROMPT_VERSION,
        payload={
            "rules": list(DRAFTING_RULES),
            "headline": headline,
            "subheads": list(subheads),
            "slots": slots,
            # 쓸 수 있는 값을 한 번 더 모아서 준다. 규칙만으로는 약하다.
            "allowed_values": sorted({f.value for f in ledger.facts}),
        },
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def parse_draft(
    raw: dict[str, Any],
    *,
    plans: list[SlotPlan],
    headline: str,
    subheads: list[str],
    ledger: AuditLedger | None = None,
) -> AuditDraft:
    """AI가 준 본문을 계약 모양으로 받는다.

    **AI가 못 채우는 칸을 채워 보내도 버린다.** 검사기에 오기 전에 걷어낸다.
    그리고 못 채우는 칸은 Harness가 이유와 함께 직접 만들어 넣는다 — AI가
    아무 말 안 해도 빈 칸이 결과물에 남아야 한다.
    """
    by_slot = {p.slot: p for p in plans}
    written: dict[SlotKind, dict[str, Any]] = {}

    for entry in raw.get("slots") or []:
        if not isinstance(entry, dict):
            continue
        try:
            slot = SlotKind(str(entry.get("slot") or ""))
        except ValueError:
            # 계약에 없는 칸 이름은 받지 않는다.
            continue
        plan = by_slot.get(slot)
        if plan is None or not plan.fillable:
            # 못 채우는 칸에 AI가 쓴 글은 통째로 버린다.
            continue
        written[slot] = entry

    slots: list[SlotText] = []
    for slot in SLOT_ORDER:
        plan = by_slot.get(slot)
        if plan is None:
            continue

        if not plan.fillable:
            slots.append(
                SlotText(
                    slot=slot,
                    filled=False,
                    note=plan.needed or plan.reason,
                )
            )
            continue

        entry = written.get(slot)
        if entry is None:
            # 채울 수 있는데 AI가 안 쓴 칸. 지어내지 않고 빈 채로 남긴다.
            slots.append(
                SlotText(
                    slot=slot,
                    filled=False,
                    note="AI가 이 칸을 쓰지 않았습니다. 다시 시도해 주세요.",
                )
            )
            continue

        fact_ids = [str(i) for i in (entry.get("fact_ids") or []) if str(i).strip()]
        text = str(entry.get("text") or "").strip()

        # AI가 둔 `{목록}` 자리를 Harness가 채운다. 값·순서·이름표를 AI가
        # 건드릴 수 없으므로 빠뜨리거나 뒤섞을 길이 없다.
        if ledger is not None and LIST_TOKEN in text:
            text = text.replace(LIST_TOKEN, _list_text(ledger, plan))

        slots.append(
            SlotText(slot=slot, filled=True, text=text, fact_ids=fact_ids)
        )

    # 제목·부제는 **재료에 있던 그대로** 쓴다. AI가 다시 쓰지 못한다.
    return AuditDraft(headline=headline, subheads=list(subheads), slots=slots)
