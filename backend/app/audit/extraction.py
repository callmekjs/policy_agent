"""재료에서 사실을 뽑고, Harness가 원문과 맞대어 거른다.

AI는 사실 **후보**만 만든다. 위치·줄 번호·확인 여부는 AI가 정하지 않는다.
그건 Harness가 원문에서 직접 찾아 붙인다. 기존 법률안 쪽(`app/agents`,
`app/gates/evidence_gate.py`)과 같은 원칙이고, 재료의 모양만 다르다.

거르는 기준은 둘이고 **따로** 본다.

1. 근거 문구가 원문에 글자 그대로 있는가
2. **값이 그 근거 문구 안에 있는가**

2번이 없으면 1번은 껍데기가 된다. AI가 진짜 문장을 근거로 달고 그 안에 없는
숫자를 붙일 수 있기 때문이다. 근거가 붙어 있으니 사람은 확인했다고 여긴다.
그것이 가장 위험하다.
"""

from __future__ import annotations

import re
from typing import Any

from app.audit.contracts import (
    AUDIT_SCHEMA_VERSION,
    AuditFact,
    AuditFactKind,
    AuditLedger,
    Evidence,
)
from app.harness.source_normalizer import NormalizedSource
from app.infrastructure.model_gateway import ModelCallRequest

AGENT_NAME = "AuditFactAgent"
PROMPT_VERSION = "audit_fact_v1"

#: 재료가 짧아 넉넉하다. 상한은 실행 중 바꾸지 않는다 (README §7.2).
MAX_OUTPUT_TOKENS = 4_000


def build_extraction_request(
    *, material: str, source_id: str = "SRC-01", source_name: str = ""
) -> ModelCallRequest:
    """재료에서 사실을 뽑아 달라는 요청.

    **원문만 보낸다.** 목적이나 원하는 결론을 함께 주면 AI가 그쪽으로 값을
    맞춘다. 사람이 무엇을 쓰고 싶은지는 사실 뽑기에 필요 없다.
    """
    return ModelCallRequest(
        agent_name=AGENT_NAME,
        prompt_version=PROMPT_VERSION,
        payload={
            "source_id": source_id,
            "source_name": source_name,
            "material": material,
        },
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )

#: 공백을 없앤 사본으로 견준다.
#:
#: 재료는 사람이 만든 문서라 **줄바꿈이 문장 한가운데 들어간다.** gold의 제목이
#: 그렇다 — `태양광 3년간 상암축구장` / `6천 개 규모 산림 사라져`. 사람 눈에는
#: 한 문장인데 글자로는 갈려 있다. 공백을 그대로 두고 찾으면 진짜 사실을 놓친다.
#:
#: 공백만 없앤다. 글자·숫자·문장부호는 하나라도 다르면 못 찾는 것이 맞다.
_WHITESPACE = re.compile(r"\s+")


def _packed(text: str) -> str:
    return _WHITESPACE.sub("", text)


def _line_of(raw_text: str, quote: str) -> int:
    """근거 문구가 **시작되는** 줄 번호. 못 찾으면 0.

    공백을 없앤 자리로 찾았으므로 원문에서 다시 세어야 한다.

    전에는 문구의 **앞 8글자**만 보고 줄을 정했고, 그것도 누적한 글에서 처음
    나오는 곳이라 문구가 **끝나는** 줄을 돌려줬다. 재료에 `종합부동산세…`처럼
    같은 말로 시작하는 문장이 여럿이면 엉뚱한 줄을 가리켰다 — 근거를 확인하러
    간 사람이 `초과`를 찾으러 가서 `증가`를 보고 맞다고 넘겼다
    (2026-09-01 관문 검토 `D2`, 근거 표 4건 불일치).

    이제 **문구 전체**로 찾고 시작 자리를 돌려준다. 못 찾으면 0이다.
    **틀린 줄을 가리키느니 없다고 하는 편이 낫다.**
    """
    packed_quote = _packed(quote)
    if not packed_quote:
        return 0

    # 공백을 없앤 전체 글과, 그 글의 각 글자가 몇 번째 줄에서 왔는지를 함께 만든다.
    # 이 표가 있어야 찾은 자리를 원문 줄 번호로 되돌릴 수 있다.
    packed_lines: list[str] = []
    line_of_char: list[int] = []
    for number, line in enumerate(raw_text.splitlines(), start=1):
        packed_line = _packed(line)
        packed_lines.append(packed_line)
        line_of_char.extend([number] * len(packed_line))

    start = "".join(packed_lines).find(packed_quote)
    if start < 0:
        return 0
    return line_of_char[start]


def verify_facts(
    raw: dict[str, Any],
    *,
    normalized: NormalizedSource,
    source_id: str,
    source_name: str = "",
) -> AuditLedger:
    """AI가 준 사실 후보를 원문과 맞대어 원장을 만든다.

    **하나가 나쁘다고 나머지를 버리지 않는다.** 좋은 것은 받고, 버린 것은
    이유와 함께 남긴다. 전부 버리면 사람은 무엇이 문제인지 알 수 없다.
    """
    ledger = AuditLedger(schema_version=AUDIT_SCHEMA_VERSION)
    packed_source = _packed(normalized.normalized_text)

    for candidate in raw.get("facts") or []:
        if not isinstance(candidate, dict):
            ledger.rejected.append("형식이 맞지 않는 사실 후보가 있어 버렸습니다.")
            continue

        value = str(candidate.get("value") or "").strip()
        quote = str(candidate.get("quote") or "").strip()
        kind_text = str(candidate.get("kind") or "").strip()

        try:
            kind = AuditFactKind(kind_text)
        except ValueError:
            # 목록에 없는 종류를 지어내면 받지 않는다. 종류가 곧 칸을 여는
            # 자격이라, 지어낸 종류를 받으면 없는 칸이 열린다.
            ledger.rejected.append(
                f"‘{value or kind_text}’ — 알 수 없는 종류 `{kind_text}`라 버렸습니다."
            )
            continue

        if not value or not quote:
            ledger.rejected.append(
                f"‘{value or '(값 없음)'}’ — 값이나 근거 문구가 비어 버렸습니다."
            )
            continue

        packed_quote = _packed(quote)
        if packed_quote not in packed_source:
            ledger.rejected.append(
                f"‘{value}’ — 근거로 단 문구가 자료 원문에 없어 버렸습니다."
            )
            continue

        # **여기가 핵심이다.** 근거는 진짜인데 값이 그 안에 없는 경우를 막는다.
        if _packed(value) not in packed_quote:
            ledger.rejected.append(
                f"‘{value}’ — 근거 문구 안에 그 값이 없어 버렸습니다. "
                "자료에 없는 값을 진짜 문장에 붙인 것입니다."
            )
            continue

        ledger.facts.append(
            AuditFact(
                fact_id=f"AF-{len(ledger.facts) + 1:02d}",
                kind=kind,
                subject=str(candidate.get("subject") or "").strip(),
                value=value,
                scope=str(candidate.get("scope") or "").strip(),
                evidence=Evidence(
                    source_id=source_id,
                    source_name=source_name,
                    quote=quote,
                    line=_line_of(normalized.raw_text, quote),
                ),
                # **확인은 사람이 한다.** 여기서 붙이지 않는다.
                confirmed=False,
            )
        )

    return ledger
