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

from app.harness.fact_contracts import FACT_RESULT_SCHEMA_VERSION

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

#: 사용자가 위원회 자료라고 표시한 역할. 화면에서 이미 확인받은 값이므로
#: 본문 표기보다 확실한 근거다.
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


def _is_plenary_scope(text: str, index: int, role: str) -> bool:
    """이 자리의 값을 본회의 사실로 쓸 수 있는가.

    **다른 회의 것이라는 근거가 있을 때만 뺀다.** 반대로 하면(본회의라는
    근거가 있을 때만 넣으면) 제목 없는 평문 붙여넣기에서 값이 통째로
    사라진다. 값이 사라지면 두 자료의 충돌도 함께 사라져, 시스템이 말없이
    한쪽을 고른 것과 같아진다. 그것이 거짓 충돌보다 훨씬 나쁘다.
    """
    if role in COMMITTEE_ROLES:
        return False
    line = _line_of(text, index)
    if OTHER_BODY_PATTERN.search(line):
        return False
    if "본회의" in line:
        return True
    heading = _heading_of(text, index)
    if heading and OTHER_BODY_PATTERN.search(heading) and "본회의" not in heading:
        return False
    return True


def fake_fact_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    """자료에서 규칙으로 사실 후보를 뽑아 raw 결과 모양으로 돌려준다."""
    evidence: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    role_candidates: list[dict[str, Any]] = []
    seen_quotes: dict[tuple[str, str], str] = {}

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
                if len(role_candidates) >= 3:
                    break

        candidate_ids = [
            c["candidate_id"] for c in role_candidates if c["source_id"] == source_id
        ]

        for kind, subject, pattern in FACT_PATTERNS:
            for match in list(pattern.finditer(text))[:2]:
                quote = _line_of(text, match.start())
                if not quote:
                    continue
                if kind in PLENARY_SCOPED_KINDS and not _is_plenary_scope(
                    text, match.start(), source.get("role", "UNKNOWN")
                ):
                    continue
                evidence_id = add_evidence(source_id, quote)
                facts.append(
                    {
                        "fact_id": f"F-{len(facts) + 1:02d}",
                        "kind": kind,
                        "subject": subject,
                        "value": match.group(1).strip(),
                        "unit": "",
                        "source_id": source_id,
                        "evidence_id": evidence_id,
                        "valid_source_role_candidate_ids": candidate_ids,
                    }
                )

    return {
        "schema_version": FACT_RESULT_SCHEMA_VERSION,
        "result": {
            "result_status": "OK",
            "scope_error": None,
            "source_role_candidates": role_candidates[:18],
            "evidence": evidence[:40],
            "facts": facts[:30],
            "bill_identities": [],
            "bill_relations": [],
            "legislative_events": [],
            "provision_comparisons": [],
            "supplementary_rules": [],
        },
    }


#: Agent 이름 -> 가짜 응답을 만드는 함수.
FAKE_RESPONDERS = {
    "FactExtractionAgent": fake_fact_extraction,
}
