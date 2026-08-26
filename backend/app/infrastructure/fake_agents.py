"""개발용 가짜 Agent 응답 (README §3.8의 가짜 ModelGateway).

진짜 AI를 부르지 않고, 자료 원문에서 눈에 띄는 표현을 규칙으로 찾아 사실 후보를
만든다. 목적은 **흐름과 Gate가 제대로 도는지 확인하는 것**이지 좋은 추출이
아니다. 실제 추출 품질은 6일차에 진짜 AI로 확인한다.

여기서 만드는 근거 문구는 항상 원문에서 그대로 잘라 쓴다. 그래야 Harness의
근거 대조 Gate가 실제로 동작하는지 볼 수 있다.
"""

from __future__ import annotations

import re
from typing import Any

from app.harness.fact_contracts import (
    COMMITTEE_KIND_PREFIX,
    FACT_RESULT_SCHEMA_VERSION,
)
from app.infrastructure.fake_draft import fake_draft_writing
from app.infrastructure.fake_revision import fake_revision

#: 본회의 사건에만 해당하는 사실 종류.
#: 위원회 심사에도 표결 수와 처리일이 적히므로, 어느 회의 것인지 확인하지 않으면
#: 서로 다른 사건의 값을 같은 항목으로 보고 거짓 충돌을 만든다.
PLENARY_SCOPED_KINDS = frozenset(
    {
        "PLENARY_DECIDED_ON",
        "VOTE_PRESENT_COUNT",
        "VOTE_YES_COUNT",
        "VOTE_NO_COUNT",
    }
)

#: 다른 회의를 가리키는 표현. 이 말이 붙은 자리의 값은 본회의 사실로 쓰지 않는다.
OTHER_BODY_PATTERN = re.compile(r"소관위|법사위|위원회|소위")

#: 사용자가 화면에서 직접 고른 역할. 본문 표기보다 확실한 근거이므로 먼저 본다.
PLENARY_ROLES = frozenset(
    {"PLENARY_VOTE_RESULT", "PLENARY_FINAL_TEXT", "PLENARY_AGENDA_TEXT"}
)
COMMITTEE_ROLES = frozenset({"COMMITTEE_FINAL_TEXT"})

#: 자료 원문에서 찾을 표현들. (사실 종류, 비교 항목, 정규식)
FACT_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("BILL_IDENTITY", "bill_number", re.compile(r"의안\s*번호[:\s]*([0-9A-Z\-]+)")),
    ("BILL_TITLE", "bill_title", re.compile(r"「([^」]{2,60})」")),
    (
        "PLENARY_RESULT",
        "plenary_disposition",
        re.compile(r"(원안가결|수정가결|부결|대안반영폐기)"),
    ),
    (
        # `본회의 심의: 2025. 9. 25.`와 `- 의결일: 2026. 8. 20.(목요일)`을 모두 읽는다.
        # 고정 시험자료는 `의결일:` 표기를 쓰므로 그것도 읽지 못하면
        # 날짜 충돌·요일 검사가 실제 자료에서 한 번도 발동하지 않는다.
        "PLENARY_DECIDED_ON",
        "plenary_decided_on",
        re.compile(
            r"(?:본회의|의결일|의결\s*일자)[^\n]{0,20}?"
            r"(\d{4}\s*[.\-년]\s*\d{1,2}\s*[.\-월]\s*\d{1,2})"
        ),
    ),
    ("VOTE_PRESENT_COUNT", "vote_present_count", re.compile(r"재석[:\s]*(\d+)\s*명")),
    ("VOTE_YES_COUNT", "vote_yes_count", re.compile(r"찬성[:\s]*(\d+)\s*명")),
    ("VOTE_NO_COUNT", "vote_no_count", re.compile(r"반대[:\s]*(\d+)\s*명")),
    ("PROVISION_CHANGE", "changed_article", re.compile(r"(제\s*\d+\s*조(?:의\s*\d+)?)")),
)

#: 역할을 짐작하게 하는 표현.
ROLE_HINTS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("의안정보", "의안번호와 심사 경과가 적혀 있습니다.", re.compile(r"의안\s*번호")),
    ("본회의 표결 결과", "본회의 표결 결과가 적혀 있습니다.", re.compile(r"본회의[^\n]{0,30}(가결|부결)")),
    ("현행 조문", "현재 시행 중인 조문이 적혀 있습니다.", re.compile(r"현행")),
    ("부칙", "부칙 규정이 적혀 있습니다.", re.compile(r"부칙")),
    ("발의안", "발의 내용이 적혀 있습니다.", re.compile(r"발의")),
)


def _line_of(text: str, index: int) -> str:
    """그 위치가 속한 줄 전체. 근거 문구로 쓴다."""
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return text[start : end if end != -1 else len(text)].strip()


def _heading_of(text: str, index: int) -> str:
    """그 위치 바로 앞의 제목 줄. 어느 회의 이야기인지 판단하는 데 쓴다."""
    for line in reversed(text[:index].splitlines()):
        if line.lstrip().startswith("#"):
            return line.strip()
    return ""


def _is_committee_value(role: str) -> bool:
    """이 값이 위원회 회의의 것인가.

    **사용자가 화면에서 고른 자료 역할만 본다.** 본문 글자로 짐작하지 않는다.

    짐작하면 `문화체육관광위원회 대안 「…법률안」 의결일: …`처럼 의안 *이름*에
    위원회가 들어간 줄에서 본회의 값에 위원회 이름표가 붙는다. 이름표가 붙으면
    비교에서 빠지므로, 값은 화면에 남아 있는데 충돌만 조용히 사라진다.

    더 중요한 이유가 있다. 이름표를 붙이는 것은 **그 값을 비교에서 빼는 권한**이다.
    그 권한을 추출기가 가지면 6일차의 진짜 AI도 그대로 물려받는다. AI가 종류
    이름 하나로 충돌 검사를 조용히 끌 수 있게 된다. 그래서 사람이 확인한 값
    말고는 이 권한을 쓰지 않는다.
    """
    return role in COMMITTEE_ROLES


#: 고정 형식이 정한 배열 상한. 넘으면 잘라내지 않고 범위 초과로 멈춘다.
MAX_FACTS = 30
MAX_EVIDENCE = 40
MAX_ROLE_CANDIDATES = 18


def _over_limit(
    facts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    role_candidates: list[dict[str, Any]],
) -> dict[str, str] | None:
    """상한을 넘었으면 무엇이 넘쳤는지 돌려준다."""
    for name, items, limit in (
        ("FACTS", facts, MAX_FACTS),
        ("EVIDENCE", evidence, MAX_EVIDENCE),
        ("SOURCE_ROLE_CANDIDATES", role_candidates, MAX_ROLE_CANDIDATES),
    ):
        if len(items) > limit:
            return {
                "subject": name,
                "reason": (
                    f"한 번에 담을 수 있는 한도({limit})를 넘었습니다. "
                    f"지금은 {len(items)}건입니다. 자료를 나누어 넣어 주세요."
                ),
            }
    return None


def fake_fact_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    """자료에서 규칙으로 사실 후보를 뽑아 raw 결과 모양으로 돌려준다."""
    evidence: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    role_candidates: list[dict[str, Any]] = []
    seen_quotes: dict[tuple[str, str], str] = {}
    #: 같은 자료에서 같은 종류·같은 값이 여러 번 나오면 한 번만 만든다.
    #: 값을 버리는 것이 아니라 똑같은 값이 겹치는 것을 접는 것이다.
    #: 접지 않으면 표결 자료 몇 장만으로 상한을 넘어 전체가 멈춘다.
    seen_facts: set[tuple[str, str, str]] = set()

    def add_evidence(source_id: str, quote: str) -> str:
        """같은 문구는 근거 하나만 만든다. 중복 근거는 형식 위반이다."""
        key = (source_id, quote)
        if key in seen_quotes:
            return seen_quotes[key]
        evidence_id = f"EV-{len(evidence) + 1:02d}"
        evidence.append(
            {"evidence_id": evidence_id, "source_id": source_id, "quote": quote}
        )
        seen_quotes[key] = evidence_id
        return evidence_id

    for source in payload.get("sources", []):
        source_id = source["source_id"]
        text: str = source["text"]

        # 역할이 `잘 모르겠음`이면 후보를 제안한다.
        if source.get("role") == "UNKNOWN":
            for role_label, reason, pattern in ROLE_HINTS:
                match = pattern.search(text)
                if match is None:
                    continue
                quote = _line_of(text, match.start())
                if not quote:
                    continue
                evidence_id = add_evidence(source_id, quote)
                role_candidates.append(
                    {
                        "candidate_id": f"RC-{len(role_candidates) + 1:02d}",
                        "source_id": source_id,
                        "role": role_label,
                        "label": reason,
                        "evidence_ids": [evidence_id],
                    }
                )
                if len([c for c in role_candidates if c["source_id"] == source_id]) >= 3:
                    break

        candidate_ids = [
            c["candidate_id"] for c in role_candidates if c["source_id"] == source_id
        ]

        for kind, _subject, pattern in FACT_PATTERNS:
            for match in pattern.finditer(text):
                quote = _line_of(text, match.start())
                if not quote:
                    continue
                # 값을 버리지 않는다. 어느 회의 것인지 분명하면 종류에
                # 이름표를 붙여 다른 회의 값과 비교되지 않게만 한다.
                # 버리면 충돌이 조용히 사라져 시스템이 말없이 한쪽을 고른
                # 것과 같아진다. 그것이 이 프로젝트에서 가장 나쁜 실패다.
                fact_kind = kind
                if kind in PLENARY_SCOPED_KINDS and _is_committee_value(
                    source.get("role", "UNKNOWN")
                ):
                    fact_kind = COMMITTEE_KIND_PREFIX + kind

                value = match.group(1).strip()
                signature = (source_id, fact_kind, value)
                if signature in seen_facts:
                    continue
                seen_facts.add(signature)

                evidence_id = add_evidence(source_id, quote)
                facts.append(
                    {
                        "fact_id": f"F-{len(facts) + 1:02d}",
                        "kind": fact_kind,
                        "value": value,
                        "source_id": source_id,
                        "evidence_id": evidence_id,
                        "valid_source_role_candidate_ids": candidate_ids,
                    }
                )

    # 부칙과 조문 비교를 만든다. 둘 다 근거 문구를 원문에서 그대로 잘라 쓴다.
    rules = _supplementary_rules(payload, add_evidence)
    comparisons = _provision_comparisons(payload, add_evidence)
    identities = _bill_identities(payload, add_evidence)

    # 고정 형식의 상한을 넘으면 **잘라내지 않고** 범위 초과로 멈춘다.
    # 잘라내면 뒤에 있던 값이 기록 없이 사라지고, 그 값이 걸려 있던 충돌도
    # 함께 사라진다. 형식이 이 경우를 위해 `FACT_SCOPE_TOO_LARGE`를 두었다.
    over = _over_limit(facts, evidence, role_candidates)
    if over is not None:
        return {
            "schema_version": FACT_RESULT_SCHEMA_VERSION,
            "result": {
                "result_status": "FACT_SCOPE_TOO_LARGE",
                "scope_error": over,
                "source_role_candidates": [],
                "evidence": [],
                "facts": [],
                "bill_identities": [],
                "bill_relations": [],
                "legislative_events": [],
                "provision_comparisons": [],
                "supplementary_rules": [],
            },
        }

    return {
        "schema_version": FACT_RESULT_SCHEMA_VERSION,
        "result": {
            "result_status": "OK",
            "scope_error": None,
            "source_role_candidates": role_candidates,
            "evidence": evidence,
            "facts": facts,
            "bill_identities": identities,
            "bill_relations": [],
            "legislative_events": [],
            "provision_comparisons": comparisons,
            "supplementary_rules": rules,
        },
    }


#: Agent 이름 -> 가짜 응답을 만드는 함수.
FAKE_RESPONDERS = {
    "FactExtractionAgent": fake_fact_extraction,
    "DraftWritingAgent": fake_draft_writing,
    "RevisionAgent": fake_revision,
}


#: 부칙 본문에서 시행일 규칙을 찾는다.
EFFECTIVE_DATE_RULE = re.compile(r"^(이 법은[^\n]{0,120}시행한다\.)", re.MULTILINE)

#: 조문 비교에 쓸 조문 번호. 현행과 개정문 양쪽에서 같은 모양으로 읽는다.
PROVISION_LINE = re.compile(r"^(제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*제\s*\d+\s*항)?)")

#: 개정문을 담을 수 있는 역할. 이 중 하나여야 비교의 `최종` 쪽이 된다.
FINAL_TEXT_ROLES = (
    "PLENARY_FINAL_TEXT",
    "COMMITTEE_FINAL_TEXT",
    "PLENARY_AGENDA_TEXT",
    "INTRODUCED_TEXT",
)


def _supplementary_rules(payload, add_evidence) -> list[dict[str, Any]]:
    """부칙 규칙을 만든다. 근거 문구는 원문 그대로 쓴다."""
    rules: list[dict[str, Any]] = []
    for source in payload.get("sources", []):
        text: str = source["text"]
        for match in EFFECTIVE_DATE_RULE.finditer(text):
            quote = match.group(1).strip()
            rules.append(
                {
                    "rule_id": f"SR-{len(rules) + 1:02d}",
                    "kind": "EFFECTIVE_DATE",
                    "applies_to": quote,
                    "source_id": source["source_id"],
                    "evidence_id": add_evidence(source["source_id"], quote),
                    "valid_source_role_candidate_ids": [],
                }
            )
    return rules


def _provision_lines(text: str) -> dict[str, str]:
    """`제7조제6항`처럼 시작하는 줄을 조문별로 모은다."""
    found: dict[str, str] = {}
    for line in text.split("\n"):
        stripped = line.strip()
        match = PROVISION_LINE.match(stripped)
        if match is None:
            continue
        key = match.group(1).replace(" ", "")
        found.setdefault(key, stripped)
    return found


def _provision_comparisons(payload, add_evidence) -> list[dict[str, Any]]:
    """현행 원문과 개정문에 **둘 다 있는** 조문만 비교로 만든다.

    한쪽만 있으면 비교를 만들지 않는다 (§2.16.3). 한쪽 원문이 없는 비교는
    "무엇이 어떻게 바뀌었다"를 근거 없이 말하는 길이 된다.
    """
    current = [
        s for s in payload.get("sources", []) if s.get("role") == "CURRENT_PROVISION"
    ]
    finals = [s for s in payload.get("sources", []) if s.get("role") in FINAL_TEXT_ROLES]
    if len(current) != 1 or len(finals) != 1:
        return []

    current_lines = _provision_lines(current[0]["text"])
    final_lines = _provision_lines(finals[0]["text"])

    comparisons: list[dict[str, Any]] = []
    for key, current_quote in current_lines.items():
        final_quote = final_lines.get(key)
        if final_quote is None:
            continue
        comparisons.append(
            {
                "comparison_id": f"PC-{len(comparisons) + 1:02d}",
                "provision_id": key,
                "current_source_id": current[0]["source_id"],
                "current_evidence_id": add_evidence(current[0]["source_id"], current_quote),
                "final_source_id": finals[0]["source_id"],
                "final_evidence_id": add_evidence(finals[0]["source_id"], final_quote),
            }
        )
    return comparisons


#: 의안번호를 원문에서 읽는다. 최종 의결문 Gate가 쓰는 것과 같은 모양이다.
BILL_NUMBER_IN_TEXT = [
    re.compile(r"의안\s*번호[^0-9]{0,10}(\d{6,8})"),
    re.compile(r"제\s*(\d{6,8})\s*호"),
]


def _bill_identities(payload, add_evidence) -> list[dict[str, Any]]:
    """자료에서 읽은 의안번호로 의안 신원을 만든다.

    자료들이 **하나의 의안번호로만** 모이면 그것이 이번 보도 대상이다.
    번호가 갈리면 어느 쪽도 보도 대상으로 정하지 않는다. 그래야 Harness가
    "의안번호가 다르다"를 보고 멈출 수 있다.
    """
    found: list[tuple[str, str, str]] = []  # (source_id, number, quote)
    for source in payload.get("sources", []):
        text: str = source["text"]
        for pattern in BILL_NUMBER_IN_TEXT:
            for match in pattern.finditer(text):
                quote = _line_of(text, match.start())
                if quote:
                    found.append((source["source_id"], match.group(1), quote))

    numbers = {number for _, number, _ in found}
    single = len(numbers) == 1

    # 신원은 **의안 하나당 하나**다. 자료마다 만들면 같은 의안이 여러 번
    # 올라가 고정 형식의 상한(2개)을 넘고, 형식 위반으로 전체가 멈춘다.
    identities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_id, number, quote in found:
        if number in seen:
            continue
        seen.add(number)
        identities.append(
            {
                "bill_id": f"B-{len(identities) + 1:02d}",
                "bill_number": number,
                "is_draft_subject": single,
                "source_id": source_id,
                "evidence_ids": [add_evidence(source_id, quote)],
            }
        )
    return identities
