"""국정감사형 보도자료 초안을 처음부터 끝까지 한 번 만든다.

재료는 gold 파일(`NA-GOLD-001`)의 **제목 + 부제**까지다. 본문은 정답이므로
재료로 주지 않는다. 파일을 첫 `○` 줄에서 자른다.

    재료 (제목 + 부제)  ->  사실 뽑기  ->  칸 판정  ->  본문 쓰기  ->  검사  ->  파일

**기본이 진짜 AI다** (2026-08-28 사용자 결정). 실제 작업은 언제나 진짜로
돌린다. 돈이 들고 재료가 인터넷으로 나간다.

`--fake`는 **시험·디버깅 전용 대역**이다. 결과물을 만들 때 쓰지 않는다.

    python scripts/run_audit_draft.py            # 진짜, 약 $0.05
    python scripts/run_audit_draft.py --fake     # 대역, 0원 (배관 확인용)
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.audit.drafting import build_drafting_request, parse_draft  # noqa: E402
from app.audit.export import to_markdown  # noqa: E402
from app.audit.extraction import build_extraction_request, verify_facts  # noqa: E402
from app.audit.compare import build_comparison  # noqa: E402
from app.audit.gate import blocking, check_draft  # noqa: E402
from app.audit.material import split_material  # noqa: E402
from app.audit.slots import plan_slots  # noqa: E402
from app.harness.source_normalizer import normalize_source  # noqa: E402

GOLD = ROOT / "references" / "보도자료예시" / "01_역피라미드_태양광_산림훼손.txt"


def split_gold(text: str) -> tuple[str, str]:
    """gold를 **재료(제목+부제)**와 **정답(본문)**으로 가른다.

    본문은 `○`로 시작한다. 그 앞까지가 사람이 프로그램에 주는 재료다.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("○"):
            return "\n".join(lines[:index]).strip(), "\n".join(lines[index:]).strip()
    raise SystemExit("gold 파일에서 본문 시작(`○`)을 찾지 못했습니다.")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fake", action="store_true", help="시험용 대역. 결과물을 만들 때는 쓰지 않는다"
    )
    parser.add_argument("--out", default=str(ROOT / "tmp" / "audit-draft.md"))
    parser.add_argument(
        "--model", default="", help="비교 실험용. 비우면 §7.2가 고정한 제품 모델"
    )
    parser.add_argument(
        "--effort", default="", help="비교 실험용. 비우면 제품 기본값(low)"
    )
    parser.add_argument(
        "--tokens-scale",
        type=int,
        default=1,
        help=(
            "출력 토큰 한도 배수. `effort`를 올리면 추론이 출력 토큰을 먹어 "
            "답이 아예 안 나온다. max로 돌릴 때는 4 이상을 준다"
        ),
    )
    parser.add_argument(
        "--material",
        default="",
        help="재료 파일을 따로 준다. 없으면 gold의 제목+부제를 쓴다",
    )
    args = parser.parse_args()

    _, answer = split_gold(GOLD.read_text(encoding="utf-8"))
    if args.material:
        material = Path(args.material).read_text(encoding="utf-8").strip()
    else:
        material, _ = split_gold(GOLD.read_text(encoding="utf-8"))

    print("=" * 70)
    print("재료 (프로그램에 주는 것)")
    print("=" * 70)
    print(material)
    print()

    if not args.fake:
        # 진짜 AI는 **사람이 `--live`를 직접 줄 때만** 켜진다. 파일로 켜지면
        # 켠 기억이 없는 사람이 자료를 넣게 된다(6일차 결정).
        os.environ["POLICY_AGENT_LIVE"] = "1"
        from app.infrastructure.openai_gateway import (
            OpenAIModelGateway,
            load_env_file,
        )

        load_env_file()
        gateway = OpenAIModelGateway()
        # **비교 실험용 덮어쓰기.** 제품 기본값(§7.2)은 그대로 두고 이 스크립트
        # 안에서만 바꾼다. 무엇으로 돌렸는지 반드시 찍는다 — 안 찍으면 어느
        # 설정의 결과인지 모른 채 표를 만들게 된다. 실제로 한 번 그랬다.
        if args.model:
            gateway.model = args.model
        if args.tokens_scale > 1:
            # 실험은 토큰을 크게 쓴다. 제품 한도(§7.2 · Run당 1.10달러)로는
            # 예약 단계에서 막힌다. **실험에서만** 늘린다.
            gateway.budget_usd = 3.00
        if args.effort:
            gateway.reasoning_effort = args.effort
            # effort 를 올리면 훨씬 오래 걸린다. 기본 시간으로는 끊긴다.
            if args.effort in ("high", "max"):
                gateway.timeout_seconds = 900.0
        print(
            f"진짜 AI로 돌립니다. 모델 {gateway.model} · "
            f"effort {gateway.reasoning_effort}. 돈이 듭니다.\n"
        )
    else:
        from app.infrastructure.model_gateway import FakeModelGateway

        gateway = FakeModelGateway()
        print("가짜 AI로 돌립니다. 0원, 외부 호출 없음.\n")

    def scaled(request):
        """출력 토큰 한도를 실험 배수만큼 늘린다.

        `effort`를 올리면 추론이 출력 토큰을 먼저 먹는다. 한도가 모자라면
        답이 **빈 채로** 돌아온다 (실제로 sol/max가 4000/4000을 쓰고 아무것도
        못 냈다). 모델이 나쁜 것이 아니라 자리가 좁았던 것이다.
        """
        if args.tokens_scale == 1:
            return request
        return replace(
            request, max_output_tokens=request.max_output_tokens * args.tokens_scale
        )

    normalized = normalize_source(material)

    # 1) 사실 뽑기 -----------------------------------------------------------
    call = await gateway.call(
        scaled(build_extraction_request(material=material, source_name="국정감사 재료"))
    )
    ledger = verify_facts(
        call.result or {},
        normalized=normalized,
        source_id="SRC-01",
        source_name="국정감사 재료",
    )

    print("=" * 70)
    print(f"확인된 사실 {len(ledger.facts)}건 · 버린 것 {len(ledger.rejected)}건")
    print("=" * 70)
    for fact in ledger.facts:
        print(f"  {fact.fact_id}  [{fact.kind.value:14}] {fact.value:16} ({fact.scope})")
    for reason in ledger.rejected:
        print(f"  [버림] {reason}")
    print()

    # 2) 칸 판정 -------------------------------------------------------------
    plans = plan_slots(ledger)
    print("=" * 70)
    print("칸 판정 — 코드가 정한다. AI에게 묻지 않는다")
    print("=" * 70)
    for plan in plans:
        mark = "채움" if plan.fillable else "못 채움"
        print(f"  [{mark:6}] {plan.slot.value:12} {plan.needed}")
    print()

    # 3) 본문 쓰기 -----------------------------------------------------------
    headline, subheads = split_material(material)

    # 같은 자료로 돌려도 AI의 답은 흔들린다. 막히면 다시 쓰게 한다.
    # **몇 번 만에 됐는지 숨기지 않는다.** 흔들린다는 사실도 결과의 일부다.
    request = build_drafting_request(
        ledger=ledger, plans=plans, headline=headline, subheads=subheads
    )
    attempts = 0
    for attempts in range(1, 4):
        call = await gateway.call(scaled(request))
        draft = parse_draft(
            call.result or {},
            plans=plans,
            headline=headline,
            subheads=subheads,
            ledger=ledger,
        )
        findings = check_draft(draft, ledger, plans)
        blocked = blocking(findings)
        if not blocked:
            break
        print(f"[{attempts}번째 시도 막힘] {[f.rule_id for f in blocked]} — 다시 씁니다")

    # 4) 검사 ---------------------------------------------------------------
    print("=" * 70)
    print(
        f"검사 — 막힌 것 {len(blocked)}건 · 경고 {len(findings) - len(blocked)}건 "
        f"· 시도 {attempts}회"
    )
    print("=" * 70)
    for finding in findings:
        tag = "막힘" if finding.severity.value == "BLOCKING" else "경고"
        print(f"  [{tag}] {finding.rule_id}: {finding.message}")
    print()

    if blocked:
        print("차단된 항목이 있어 초안을 내주지 않습니다.")
        return 1

    # 5) 파일 ---------------------------------------------------------------
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_markdown(draft, ledger), encoding="utf-8")

    # 6) 사람이 쓴 것과 나란히 --------------------------------------------
    #
    # 결과물만 보면 "잘 썼나"를 판단할 기준이 없다. **정답 옆에 놓아야**
    # 무엇이 되고 무엇이 안 되는지 보인다.
    compare = out.with_name(out.stem + "-비교.md")
    compare.write_text(
        build_comparison(
            draft=draft,
            ledger=ledger,
            plans=plans,
            findings=findings,
            material=material,
            answer=answer,
            attempts=attempts,
        ),
        encoding="utf-8",
    )

    print("=" * 70)
    print(f"결과물: {out}")
    print(f"비교표: {compare}")
    print("비용: $0 (대역)" if args.fake else f"비용: ${gateway.spent_usd:.4f}")
    print("=" * 70)
    print()
    print("--- 정답(gold 본문) 첫 400자 — 맞대어 보기 ---")
    print(answer[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
