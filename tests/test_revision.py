"""누적 5일차 — 사람이 확인하고 고칠 때 값이 사라지지 않는지 본다.

합격선은 `verification/day5-pass-bar.md`의 K~O다. 이 파일은 그중 **K(고치는
과정에서 값이 사라지지 않는가)**를 겨눈다.

4일차와 위험이 다르다. 4일차는 **없는 사실이 들어오는 것**이 위험했다.
5일차는 **있던 사실이 조용히 나가는 것**이 위험하다. 사람이 "이 문장 좀
다듬어 줘"라고 했는데 AI가 시행일이나 표결 수를 슬쩍 빼먹는 경우다.

그래서 검사 방향이 반대다 — 새로 들어온 것이 아니라 **없어진 것**을 센다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.gates.protection import apply_reviews, is_protected_candidate
from app.gates.revision_gate import check_revision
from app.harness.draft_contracts import DraftCandidate
from app.harness.review_contracts import FactReview, FactVerdict

from test_draft import _draft_dict, _run


def _confirm_all(ledger, verdict=FactVerdict.OK) -> list[FactReview]:
    """체크리스트에서 모든 사실에 같은 판정을 누른 것처럼 만든다."""
    now = datetime.now(UTC)
    return [
        FactReview(fact_id=f.fact_id, verdict=verdict, reviewed_at=now)
        for f in ledger.facts
    ]


@pytest.fixture(scope="module")
def base_run():
    """정상 초안 한 판. 사람이 모든 사실을 확인한 상태다.

    확인을 거쳐야 보호 사실이 생긴다. **사람이 안 본 값은 보호하지 않는다** —
    보호한다고 말해 봐야 틀린 값을 지키게 될 뿐이다(README §4.3).
    """
    run = asyncio.run(_run())
    assert run.draft is not None, "기준 초안을 만들지 못했습니다."
    return run.model_copy(
        update={"fact_ledger": apply_reviews(run.fact_ledger, _confirm_all(run.fact_ledger))}
    )


def test_사람이_확인해야_보호가_된다() -> None:
    """README §4.3 — Agent는 보호 여부를 정하지 않는다."""
    run = asyncio.run(_run())
    ledger = run.fact_ledger

    # 확인 전에는 보호가 하나도 없다.
    assert not [f for f in ledger.facts if f.protected]

    # 후보가 있어야 확인이 뜻을 갖는다.
    candidates = [f for f in ledger.facts if is_protected_candidate(f)]
    assert candidates, "보호 후보가 하나도 없습니다."

    # "맞다"를 누르면 후보만 보호가 된다.
    confirmed = apply_reviews(ledger, _confirm_all(ledger))
    assert {f.fact_id for f in confirmed.facts if f.protected} == {
        f.fact_id for f in candidates
    }

    # "틀렸다"를 누른 것은 보호하지 않는다. 틀린 값을 지킬 이유가 없다.
    denied = apply_reviews(ledger, _confirm_all(ledger, FactVerdict.WRONG))
    assert not [f for f in denied.facts if f.protected]


def _fact_holders(d: dict) -> list[dict]:
    """초안에서 글과 근거를 함께 들고 있는 칸 전부."""
    return [d["title"], d["lead"], *d["key_points"], *d["paragraphs"]]


def _used_protected(base_run):
    """초안이 **실제로 근거로 달고 있는** 보호 사실 하나.

    아무 보호 사실이나 고르면 안 된다. 초안이 안 쓰는 사실을 지워 봐야
    아무 일도 일어나지 않고, 시험은 통과하는데 아무것도 못 지킨다.
    """
    used = {i for p in base_run.draft.paragraphs for i in p.fact_ids}
    used |= set(base_run.draft.title.fact_ids) | set(base_run.draft.lead.fact_ids)
    for point in base_run.draft.key_points:
        used |= set(point.fact_ids)
    found = next(
        (f for f in base_run.fact_ledger.facts if f.protected and f.fact_id in used),
        None,
    )
    assert found is not None, "초안이 쓰는 보호 사실이 없습니다. 시험 전제가 깨졌습니다."
    return found


def _revised(base_run, mutate) -> DraftCandidate:
    """이전 초안을 복사해 한 군데만 바꾼다."""
    payload = _draft_dict(base_run)["result"]
    mutate(payload)
    return DraftCandidate.model_validate(payload)


def _rules(base_run, mutate) -> set[str]:
    revised = _revised(base_run, mutate)
    findings = check_revision(
        previous=base_run.draft,
        revised=revised,
        ledger=base_run.fact_ledger,
        fact_reviews=[],
    )
    return {f.rule_id for f in findings}


# ---------------------------------------------------------------------------
# K1 · 보호 사실은 고칠 수 없다
# ---------------------------------------------------------------------------


def test_보호_사실이_빠지면_수정본을_버린다(base_run) -> None:
    """`PROTECTED_FACT_DROPPED`만 겨눈다.

    보호 사실은 자료가 말한 값 중 **틀리면 가장 위험한 것**이다. 표결 수,
    의결일, 의안번호 같은 것이다. 고치는 과정에서 이것이 사라지면 읽는 사람은
    무엇이 빠졌는지 알 수 없다.
    """
    gone = _used_protected(base_run)

    other = next(
        f.fact_id
        for f in base_run.fact_ledger.facts
        if f.fact_id != gone.fact_id
    )

    def drop(d: dict) -> None:
        # 제목·리드·요약·문단 **전부**에서 뗀다. 한 곳이라도 남으면 뗀 것이
        # 아니고, 시험은 통과하는데 아무것도 못 지킨다.
        #
        # 제목·리드·요약은 계약이 **근거를 최소 하나** 요구한다(`ClaimText`).
        # 그래서 통째로 비우지 않고 다른 사실로 갈아 끼운다. 실제 AI가 할 수
        # 있는 모양이 이것이다.
        for holder in _fact_holders(d):
            kept = [i for i in holder["fact_ids"] if i != gone.fact_id]
            holder["fact_ids"] = kept or [other]
            holder["text"] = holder["text"].replace(gone.value, "")

    assert "PROTECTED_FACT_DROPPED" in _rules(base_run, drop)


def test_보호_사실의_값이_바뀌면_수정본을_버린다(base_run) -> None:
    """`PROTECTED_FACT_CHANGED`만 겨눈다.

    빠지는 것보다 바뀌는 것이 더 위험하다. 읽는 사람이 **틀린 값을 사실로**
    믿는다.
    """
    gone = _used_protected(base_run)

    def change(d: dict) -> None:
        # 근거는 **그대로 두고** 글에서 값만 바꾼다. 근거를 달고 다른 값을
        # 말하는 것이 빠뜨리는 것보다 위험하다.
        for holder in _fact_holders(d):
            holder["text"] = holder["text"].replace(gone.value, "다른 값")

    assert "PROTECTED_FACT_CHANGED" in _rules(base_run, change)


# ---------------------------------------------------------------------------
# K2 · 부칙은 고치는 과정에서 사라질 수 없다
# ---------------------------------------------------------------------------


def test_부칙이_빠지면_수정본을_버린다(base_run) -> None:
    """`REVISION_DROPPED_RULE`만 겨눈다.

    부칙은 시행일·적용례·경과조치다. 빠지면 읽는 사람이 **언제부터 적용되는지**
    모르게 된다. §2.16.4가 중대한 실패로 정한 자리다.
    """

    def drop(d: dict) -> None:
        d["paragraphs"] = [
            p for p in d["paragraphs"] if not p["supplementary_rule_ids"]
        ]

    assert "REVISION_DROPPED_RULE" in _rules(base_run, drop)


# ---------------------------------------------------------------------------
# K3 · 발표 주체는 고칠 수 없다
# ---------------------------------------------------------------------------


def test_발표_주체가_바뀌면_수정본을_버린다(base_run) -> None:
    """`ANNOUNCER_CHANGED`만 겨눈다.

    누가 발표하는지는 사용자가 확인한 값이다. AI가 고치면서 바꿀 수 없다.
    """

    def change(d: dict) -> None:
        d["announcement_subject_fact_id"] = "F-지어냄"

    assert "ANNOUNCER_CHANGED" in _rules(base_run, change)


# ---------------------------------------------------------------------------
# K5 · 요청만으로 새 사실을 넣을 수 없다
# ---------------------------------------------------------------------------


def test_원장에_없는_사실을_새로_달_수_없다(base_run) -> None:
    """`REVISION_FACT_NOT_IN_LEDGER`만 겨눈다.

    사용자가 "재석 250인이라고 써 줘"라고 해도 원장에 없으면 못 쓴다.
    사람의 요청은 **자료가 아니다.**
    """

    def add(d: dict) -> None:
        d["paragraphs"][0]["fact_ids"] = [*d["paragraphs"][0]["fact_ids"], "F-없는것"]

    assert "REVISION_FACT_NOT_IN_LEDGER" in _rules(base_run, add)


# ---------------------------------------------------------------------------
# M4 · 사람이 틀렸다고 한 사실은 초안에 남을 수 없다
# ---------------------------------------------------------------------------


def test_사람이_틀렸다고_한_사실은_초안에_남을_수_없다(base_run) -> None:
    """`WRONG_FACT_STILL_USED`만 겨눈다.

    체크리스트에서 "틀렸다"를 눌렀는데 그 사실을 쓴 문장이 그대로 남아 있으면
    확인 절차가 아무 뜻이 없다.
    """
    used = next(
        f
        for f in base_run.fact_ledger.facts
        if any(f.fact_id in p.fact_ids for p in base_run.draft.paragraphs)
    )
    findings = check_revision(
        previous=base_run.draft,
        revised=base_run.draft,
        ledger=base_run.fact_ledger,
        fact_reviews=[
            FactReview(
                fact_id=used.fact_id,
                verdict=FactVerdict.WRONG,
                reviewed_at=datetime.now(UTC),
            )
        ],
    )
    assert "WRONG_FACT_STILL_USED" in {f.rule_id for f in findings}


# ---------------------------------------------------------------------------
# 멀쩡한 수정은 통과해야 한다 — 이 시험이 없으면 위 시험들은 아무것도 못 지킨다
# ---------------------------------------------------------------------------


def test_아무것도_안_바꾼_수정본은_통과한다(base_run) -> None:
    """대조군.

    모든 수정이 막히면 위 시험들은 공격이 아니라 **검사기 자체**를 재고 있는
    것이다. 그런 시험은 방어가 죽어도 초록불을 낸다(12·13차 검토).
    """
    findings = check_revision(
        previous=base_run.draft,
        revised=base_run.draft,
        ledger=base_run.fact_ledger,
        fact_reviews=[],
    )
    assert findings == [], [f.rule_id for f in findings]


# ---------------------------------------------------------------------------
# K4·N1·N2·N3 · 고치기를 실제로 돌려 본다
# ---------------------------------------------------------------------------


def _fresh():
    """새 작업 하나. 사람이 모든 사실을 확인한 상태로 만든다."""
    from app.harness.orchestrator import Orchestrator
    from app.infrastructure.model_gateway import FakeModelGateway
    from app.infrastructure.run_store import RunStore

    store = RunStore()
    orchestrator = Orchestrator(store, FakeModelGateway())
    run = asyncio.run(_run())
    stored = run.model_copy()
    store.put(stored)
    orchestrator.review_facts(stored.run_id, _confirm_all(stored.fact_ledger))
    return orchestrator, store, stored.run_id


def test_안전한_수정은_새_판이_된다() -> None:
    """`N1`·`N3`. 순서만 바꾸는 수정은 값을 안 건드리므로 통과해야 한다."""
    orchestrator, store, run_id = _fresh()
    # 저장소는 **같은 객체를 돌려준다.** 고치기 전 값을 미리 베껴 둬야
    # `before`가 고친 뒤 값으로 바뀌지 않는다.
    before_version = store.get(run_id).draft_version
    before_draft = store.get(run_id).draft.model_copy(deep=True)

    asyncio.run(orchestrator.revise(run_id, client_request_id="r1", instruction="순서를 바꿔 주세요"))
    after = store.get(run_id)

    assert after.draft_version == before_version + 1, "새 판이 되지 않았습니다."
    assert after.state == "REVIEW_READY"
    assert after.draft_history, "이전 판을 되짚을 수 없습니다."
    assert after.draft_history[-1].version == before_draft.version


def test_값이_사라지는_수정은_이전_초안을_덮지_않는다() -> None:
    """`K4`. 가장 중요한 성질이다.

    고치는 데 실패했다고 **멀쩡하던 초안까지 잃으면 안 된다.** 사람은 고쳐
    달라고 했을 뿐인데 있던 것까지 없어지면 프로그램을 믿을 수 없다.
    """
    orchestrator, store, run_id = _fresh()
    before_version = store.get(run_id).draft_version
    before_draft = store.get(run_id).draft.model_copy(deep=True)

    asyncio.run(orchestrator.revise(run_id, client_request_id="r1", instruction="짧게 줄여 주세요"))
    after = store.get(run_id)

    assert after.draft_version == before_version, "실패한 수정이 판을 올렸습니다."
    assert after.draft == before_draft, "실패한 수정이 이전 초안을 덮었습니다."
    assert after.state == "REVIEW_READY", "고치기에 실패했다고 작업이 죽으면 안 됩니다."

    attempt = after.revision_attempts[-1]
    assert attempt.outcome == "REJECTED"
    assert attempt.blocking_rule_ids, "왜 막혔는지 남기지 않았습니다."


def test_같은_키로_두_번_요청해도_한_번만_고친다() -> None:
    """`N2`. 사용자가 버튼을 두 번 누르는 일은 늘 일어난다."""
    orchestrator, store, run_id = _fresh()
    before_version = store.get(run_id).draft_version
    for _ in range(2):
        asyncio.run(
            orchestrator.revise(run_id, client_request_id="같은키", instruction="순서를 바꿔 주세요")
        )
    after = store.get(run_id)
    assert after.draft_version == before_version + 1, (
        f"수정이 두 번 적용됐습니다: {before_version} -> {after.draft_version}"
    )
    assert len(after.revision_attempts) == 1


def test_사실을_다_확인해야_완료할_수_있다() -> None:
    """`M1`. 확인하지 않은 초안은 내려받을 수 없다."""
    from app.harness.orchestrator import Orchestrator
    from app.infrastructure.model_gateway import FakeModelGateway
    from app.infrastructure.run_store import RunStore

    store = RunStore()
    orchestrator = Orchestrator(store, FakeModelGateway())
    run = asyncio.run(_run())
    store.put(run.model_copy())

    with pytest.raises(ValueError):
        orchestrator.finalize(run.run_id)

    orchestrator.review_facts(run.run_id, _confirm_all(run.fact_ledger))
    orchestrator.finalize(run.run_id)
    assert store.get(run.run_id).state == "DRAFT_READY"
