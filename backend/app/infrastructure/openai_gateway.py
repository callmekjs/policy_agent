"""진짜 OpenAI를 부르는 통로 (README §7.2).

가짜 `ModelGateway`와 **같은 모양**으로 만든다. 부르는 쪽은 어느 쪽을 쓰는지
몰라도 된다. 그래야 검사기와 Harness가 그대로 돌아간다.

여기서 지키는 것 셋.

**하나 — 설정을 실행 중에 바꾸지 않는다.** 모델·service tier·저장 여부는
README §7.2가 고정했다. 응답이 다른 모델로 오면 그 사실을 기록하고 다음
호출을 막는다. 더 비싼 모델을 조용히 계속 쓰지 않기 위해서다.

**둘 — 보내기 전에 돈을 센다.** 예약값으로 최대 비용을 미리 계산하고
예산선을 넘으면 **보내지 않는다.** 보낸 뒤에 아는 것은 늦다.

**셋 — 비밀을 남기지 않는다.** API 키는 읽어서 SDK에 넘길 뿐, 로그·오류
문구·보고서 어디에도 담지 않는다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass

from app.infrastructure.model_gateway import (
    CONFIGURED_MODEL,
    CONFIGURED_PROVIDER,
    ModelCallRequest,
    ModelCallResult,
)

#: README §7.2가 고정한 값. 실행 중 바꾸지 않는다.
SERVICE_TIER = "default"
REASONING_EFFORT = "low"
TIMEOUT_SECONDS = 120

#: 호출별 입력 비용 예약값 (README §7.2). 토큰 상한이 아니라 **돈을 미리
#: 잡아 두는 값**이다. 실제 사용량이 오면 그것으로 보정한다.
INPUT_RESERVATION = {
    "FactExtractionAgent": 60_000,
    "DraftWritingAgent": 20_000,
    "RevisionAgent": 12_000,
}

#: 작업 1건 전송 전 예약 예산선 (README §7.2).
RUN_BUDGET_USD = 1.10

#: 2026-08-22 공식 단가 기준 100만 토큰당 달러. 가격이 바뀌면 여기만 고친다.
#: 이 값이 오래되면 `PRICE_REVIEW_REQUIRED`로 멈춘다.
PRICE_PER_MILLION = {"input": 1.25, "output": 10.00}
PRICE_BASIS_DATE = "2026-08-22"


#: Agent별 응답 schema가 있는 곳. 기계가 읽는 원본은 이 파일들이다.
SCHEMA_DIR = Path(__file__).resolve().parents[3] / "test_sets"
SCHEMA_FILES = {
    "FactExtractionAgent": "fact_extraction_result.schema.json",
    "DraftWritingAgent": "draft_candidate.schema.json",
    "RevisionAgent": "draft_candidate.schema.json",
}

#: Agent별 지시문. 공통 한 문단만 주면 AI는 **무엇을 뽑아야 하는지 모른다.**
#: 실제로 그랬다. 발의안 전문을 주고도 사실 3건만 돌려주었고, 의안번호·의결일·
#: 처리결과처럼 초안에 반드시 필요한 값이 빠졌다.
#:
#: 여기 적는 것은 **부탁이지 강제가 아니다.** 강제는 고정 형식(schema)과
#: Harness의 Gate가 한다. 지시문은 "무엇을 찾아 달라"까지만 말하고,
#: "이 값을 써도 된다"는 판단은 절대 맡기지 않는다.
COMMON = (
    "너는 대한민국 국회 의원실의 보도자료 초안을 돕는 도우미다.\n"
    "**주어진 자료에 적혀 있는 값만** 쓴다. 표결 수·날짜·기관명·사람 이름을 "
    "지어내지 않는다. 자료에 없으면 비워 둔다. 빈 값이 틀린 값보다 낫다.\n"
    "반드시 주어진 JSON 형식으로만 답한다."
)

FACT_EXTRACTION = (
    COMMON
    + "\n\n"
    "이번 일은 **사실 뽑기**다. 초안은 쓰지 않는다.\n"
    "\n"
    "찾을 것 (자료에 있는 것만):\n"
    "· BILL_IDENTITY — 의안번호 (예: 2207285)\n"
    "· BILL_TITLE — 개정 대상 법률의 이름\n"
    "· PROPOSER — 대표발의자와 공동발의 인원\n"
    "· PLENARY_RESULT — 본회의 처리 결과 (원안가결·수정가결·부결 등)\n"
    "· PLENARY_DECIDED_ON — 본회의 의결일\n"
    "· VOTE_PRESENT_COUNT / VOTE_YES_COUNT / VOTE_NO_COUNT — 재석·찬성·반대 수\n"
    "· PROVISION_CHANGE — 바뀌는 조문 번호와 바뀌는 문구\n"
    "· PROPOSAL_REASON — 제안이유·주요내용에서 **왜 고치는지**를 말하는 문장\n"
    "· CURRENT_PROBLEM — 지금 무엇이 문제인지 말하는 문장\n"
    "· ANNOUNCEMENT_SUBJECT — 자료를 내는 주체\n"
    "\n"
    "PROPOSAL_REASON과 CURRENT_PROBLEM은 **초안의 배경 문단이 기댈 유일한 "
    "자료다.** 빠뜨리면 초안이 '무엇이 바뀌는지'만 말하고 '왜 바꾸는지'를 한 "
    "줄도 쓰지 못한다.\n"
    "\n"
    "이 둘의 value는 **40자 안쪽의 짧은 구절**로 적는다. 자료에 있는 글자를 "
    "그대로 잘라 쓰되 문단을 통째로 넣지 않는다. 값이 길면 초안이 그 문장을 "
    "통째로 옮겨야만 근거가 되어 쓸 수 없다. evidence의 quote는 그대로 긴 원문 "
    "줄을 쓴다 — 짧게 자르는 것은 value뿐이다.\n"
    "\n"
    "PROPOSAL_REASON과 CURRENT_PROBLEM은 **각각 한 건만** 넣는다. 같은 종류를 "
    "두 건 넣으면 Harness가 '자료마다 값이 다르다'고 보고 멈춘다. 가장 핵심이 "
    "되는 구절 하나를 고른다.\n"
    "\n"
    "**BILL_TITLE은 개정 대상 법률 하나다.** 본문 안에 인용된 다른 법률은 "
    "BILL_TITLE로 만들지 않는다. 「기부금품의 모집 및 사용에 관한 법률」처럼 "
    "괄호 안에 인용된 법은 대상이 아니라 인용이다. 대상 법률은 보통 "
    "제목 줄의 「○○법 일부개정법률안」과 "
    "「○○법 일부를 다음과 같이 개정한다」에 나온다.\n"
    "\n"
    "legislative_event는 어느 회의의 일인지 구분해서 적는다. 위원회 심사는 "
    "COMMITTEE_DECIDED, 본회의 의결은 PLENARY_DECIDED다. 자료에 없는 단계는 "
    "만들지 않는다.\n"
    "\n"
    "evidence의 quote는 **자료 원문에서 그대로 잘라 온 글자**여야 한다. 한 "
    "글자도 고치거나 줄이지 않는다. 요약하거나 띄어쓰기를 고치면 Harness가 "
    "원문에서 찾지 못해 그 사실은 통째로 버려진다.\n"
    "\n"
    "bill_identities에는 이번 보도 대상 의안을 **정확히 하나** `is_draft_subject"
    "=true`로 넣는다. 넣지 않으면 Harness가 어느 의안 이야기인지 확정하지 못해 "
    "초안을 만들지 않는다.\n"
    "\n"
    "provision_comparisons에는 **바뀌는 조문마다 하나씩** 넣는다. 현행 조문 "
    "자료에서 근거 하나, 발의안 자료에서 근거 하나를 짚는다. 조문을 하나라도 "
    "빠뜨리면 Harness가 직접 센 조문 수와 달라서 초안을 만들지 않는다.\n"
    "\n"
    "supplementary_rules에는 부칙을 넣는다. 「이 법은 공포한 날부터 시행한다」는 "
    "kind=EFFECTIVE_DATE다. 부칙이 없으면 넣지 않는다. **applies_to에는 부칙 "
    "문장 전체를 그대로** 적는다. 「이 법」처럼 주어만 적으면 화면에 "
    "「부칙은 “이 법”이라고 제안하고 있습니다」로 나가 뜻이 통하지 않는다.\n"
    "\n"
    "source_id는 입력에 적힌 값을 그대로 쓴다. 새로 짓지 않는다."
)

DRAFT_WRITING = (
    COMMON
    + "\n\n"
    "이번 일은 **보도자료 본문 쓰기**다.\n"
    "\n"
    "쓸 수 있는 값은 **원장(ledger)에 있는 값뿐이다.** 원장에 없는 숫자·날짜·"
    "이름은 한 글자도 쓰지 않는다.\n"
    "\n"
    "아직 공포되지 않은 법이다. **시행·공포·발효·확정·효력**을 뜻하는 말을 "
    "쓰지 않는다. 「본회의를 통과했다」까지가 사실이고 그 다음은 아니다.\n"
    "\n"
    "부칙·근거·배포 주체·연락처는 **쓰지 않는다.** Harness가 원장에서 직접 "
    "채운다. 그 자리에 무엇을 써도 버려진다.\n"
    "\n"
    "**문장을 끝까지 쓴다.** 명사로 끊지 않는다. `…원안가결.`이 아니라 "
    "`…원안가결되었다.`처럼 서술어로 맺는다. 막힐까 봐 낱말만 늘어놓으면 "
    "보도자료가 아니라 메모가 된다.\n"
    "\n"
    "값 뒤에 붙여도 되는 어미가 정해져 있다. 이것들은 안전하다: `이다` `이었다` "
    "`되었다` `된다` `한다` `했다` `하였다` `입니다` `습니다` `이며` `하고` "
    "`하여` `으로` `에서` `까지` `부터` `와` `과` `의` `은` `는` `이` `가` "
    "`을` `를` `에`. 값에 이 어미를 이어 붙이는 것은 막히지 않는다.\n"
    "\n"
    "제목·핵심 요약·리드·본문은 **서로 다른 것을 말한다.** 같은 문장을 두 번 "
    "쓰지 않는다.\n"
    "· 제목: 무엇이 어떻게 됐는지 한 줄\n"
    "· 핵심 요약: 서로 다른 사실 두세 가지\n"
    "· 리드: 언제·무엇이·어떻게 됐는지 한 문장\n"
    "· 본문: **왜 바꾸는지**(제안이유·지금의 문제)와 무엇이 바뀌는지. 원장에 "
    "PROPOSAL_REASON이나 CURRENT_PROBLEM이 있으면 그 내용을 반드시 본문에 담는다.\n"
    "\n"
    "PROPOSAL_REASON·CURRENT_PROBLEM을 옮길 때는 **원장 값의 문장을 글자 그대로 "
    "쓴다.** 어미를 바꾸거나 줄이지 않는다(`구분하고 있는데` → `구분한다`로 "
    "바꾸면 막힌다). 자료에 없는 낱말을 만들지 않으려면 그대로 옮기는 것이 "
    "가장 안전하다. 다만 따옴표는 떼고, 아래 금지 어미가 남으면 그 부분은 "
    "잘라 낸다.\n"
    "\n"
    "쓸 수 있는 낱말이 정해져 있다. **원장 값과 근거 문구에 나온 낱말**, 그리고 "
    "보도자료에 흔히 쓰는 기본 낱말만 쓴다. 자료에 없는 새 낱말을 지어내면 그 "
    "문장은 통째로 막힌다. 영어 낱말은 한 글자도 쓰지 않는다.\n"
    "\n"
    "다음 낱말은 **제목에도 본문에도 쓰지 않는다**: 개정·시행·공포·발효·확정·"
    "효력·통과·제정. 이 낱말이 들어간 긴 말도 안 된다(예: 개정안, 일부개정"
    "법률안). 의안 이름을 부를 때는 원장의 법률 이름과 의안번호를 쓴다.\n"
    "\n"
    "이름표(candidate_id·paragraph_id·claim_id)는 `DC-01` `P-01` `CL-01`처럼 "
    "영문 대문자와 번호로만 적는다. 날짜나 의안번호를 섞지 않는다.\n"
    "\n"
    "fact_id는 **원장에 있는 것만** 가리킨다. 모르면 빈 문자열을 쓴다. "
    "`USER-…` 같은 것을 지어내지 않는다.\n"
    "\n"
    "paragraphs의 section_kind는 정해진 영문 코드다: BODY·NEXT_PROCEDURE·"
    "QUOTE·ATTACHMENT. `본문` 같은 우리말을 쓰지 않는다. **BODY 문단은 반드시 "
    "하나 이상** 있어야 한다.\n"
    "\n"
    "six_w_status는 여섯 칸(who·what·when·where·why·how)을 모두 적고, 값은 "
    "OK·NEEDS_CONFIRMATION·MISSING·NOT_APPLICABLE 중 하나다. 빈 문자열은 안 된다.\n"
    "\n"
    "contact_status는 NEEDS_CONFIRMATION, contact_text는 `[문의처 확인 필요]`로 "
    "적는다. 실제 연락처를 지어내지 않는다.\n"
    "\n"
    "**남의 말을 옮기지 않는다.** `…라고 밝혔다` `…라고 말했다` 같은 모양을 "
    "쓰지 않는다. 공식 발언문 자료가 없으면 발언은 한 줄도 실을 수 없다.\n"
    "\n"
    "다음 어미도 쓰지 않는다: `다는` `라는` `다고` `라고` `다며` `라며` `이라고`. "
    "관형형으로 쓴 것이어도 안 된다(`있다는 사실` → `있다. 그 사실`). 지금 "
    "검사기는 이 어미를 남의 말을 옮긴 신호로 읽는다.\n"
    "\n"
    "**사람·기관 이름(의원·의원실·위원회·장관 등)과 따옴표를 같은 문단에 두지 "
    "않는다.** 검사기는 문단 **전체**를 한 덩어리로 본다. 그 문단 어딘가에 "
    "따옴표가 하나라도 있고 어딘가에 사람 이름이 하나라도 있으면, 두 문장이 "
    "서로 떨어져 있어도 남의 말을 옮긴 것으로 읽힌다.\n"
    "\n"
    "그러므로 문단을 이렇게 나눈다.\n"
    "· 발의자를 말하는 문단 — 따옴표 없음\n"
    "· 조문 변경 문구를 옮기는 문단 — 사람 이름 없음\n"
    "제목·핵심 요약·리드에도 같은 규칙을 지킨다.\n"
    "\n"
    "`발의`라는 낱말을 쓰지 않는다. 자료에 `발의정보`처럼 붙어 있어 낱말 하나로 "
    "떨어지지 않는다. 발의자는 원장의 PROPOSER 값을 그대로 적는다.\n"
    "\n"
    "원장 값 뒤에 조사를 **붙여 쓰지 않는다.** `조계원 의원 등 16인`이면 "
    "`16인의`·`16인이`가 아니라 값을 그대로 적고 조사는 띄어 쓰거나 문장을 "
    "고쳐 쓴다. 값에 조사를 붙이면 자료에 없는 낱말이 된다.\n"
    "\n"
    "draft_label은 정확히 `DRAFT / 내부 검토용`으로 적는다. 다른 말을 쓰면 "
    "초안이 나가지 않는다.\n"
    "\n"
    "보도일·배포일 이야기를 쓰지 않는다. Harness가 직접 채우는 칸이다.\n"
    "\n"
    "claims의 모든 항목에 fact_ids를 **반드시 채운다.** 비우면 근거 없는 "
    "문장으로 막힌다. 댈 사실이 없으면 그 문장을 쓰지 않는다.\n"
    "\n"
    "**따옴표를 쓰지 않는다.** 「」 ‘’ “” 《》 < > 모두 안 된다. 자료의 문장을 "
    "옮길 때도 따옴표를 떼고 쓴다(「기부금품의 모집 및 사용에 관한 법률」 → "
    "기부금품의 모집 및 사용에 관한 법률). 따옴표가 있으면 남의 말을 옮긴 "
    "것으로 읽혀 막힌다. 딱 하나 예외는 원장의 조문 변경 값을 **글자 그대로** "
    "옮길 때다.\n"
    "\n"
    "claims의 fact_ids에 어떤 사실을 대면 **그 사실의 값이 그 문장 안에 글자 "
    "그대로** 들어 있어야 한다. 쓰지 않은 사실을 근거로 대지 않는다.\n"
    "\n"
    "fact_ids를 채우기 전에 **문장을 한 번 눈으로 훑어라.** 그 사실의 값이 "
    "문장 안에 그대로 보이지 않으면 그 fact_id를 **뺀다.** 값을 조금이라도 "
    "줄이거나 어미를 바꾸면 없는 것으로 친다. 배경 문단에서 제안이유를 근거로 "
    "대려면 그 구절을 문장 안에 통째로 넣어야 한다.\n"
    "\n"
    "어떤 항목을 입에 올리면 **그 항목의 원장 값을 같은 문장에 글자 그대로** "
    "적는다. `결과`라고 썼으면 처리결과 값(예: 원안가결)을, `의결일`이라고 "
    "썼으면 그 날짜를, `의안번호`라고 썼으면 그 번호를 함께 적는다. 값 없이 "
    "항목 이름만 쓰면 막힌다. `결과`가 들어간 문장에는 **빠짐없이** 처리결과 "
    "값을 적는다. 제목·핵심 요약·본문 어디든 마찬가지다.\n"
    "\n"
    "의안번호는 원장 값 그대로 숫자만 적는다(예: `의안번호 2207285`). "
    "`제2207285호`처럼 적지 않는다. 그 모양은 **조문 번호 표기**라서 개정문에 "
    "없는 조문을 말한 것으로 읽혀 막힌다. `제○조`·`제○항`·`제○호`는 원장의 "
    "조문 값에 있는 것만 쓴다."
)

REVISION = (
    COMMON
    + "\n\n"
    "이번 일은 **이미 있는 초안 고치기**다.\n"
    "\n"
    "사람이 요청한 부분만 고친다. 요청하지 않은 문장은 글자 그대로 둔다.\n"
    "\n"
    "원장에 있는 값(의안번호·의결일·처리결과·조문)은 **지우거나 바꾸지 "
    "않는다.** 짧게 줄이라는 요청이어도 그 값들은 남긴다."
)

INSTRUCTIONS = {
    "FactExtractionAgent": FACT_EXTRACTION,
    "DraftWritingAgent": DRAFT_WRITING,
    "RevisionAgent": REVISION,
}


def _json_type(value) -> str:
    """이 값이 JSON에서 무슨 종류인가. `const`를 옮길 때 쓴다."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _is_object(node: dict) -> bool:
    """이 칸이 object를 받는가. `["object", "null"]`처럼 적힌 것도 센다."""
    kind = node.get("type")
    if isinstance(kind, list):
        return "object" in kind
    return kind == "object"


def _is_array(node: dict) -> bool:
    """이 칸이 배열을 받는가."""
    kind = node.get("type")
    if isinstance(kind, list):
        return "array" in kind
    return kind == "array"


def _strict(node):
    """OpenAI strict Structured Outputs가 요구하는 모양으로 다듬는다.

    strict 모드는 모든 object에 `additionalProperties: false`와, **속성 전체**가
    `required`에 들어 있기를 요구한다. 우리 schema는 선택 속성을 쓰므로
    그대로는 거절당한다.

    **필드를 지우지 않는다.** 선택 속성은 `null`을 함께 허용해 required에
    넣는다. 그래야 AI가 "없음"을 말할 수 있고, 우리 Pydantic 검사가 그
    다음에 진짜 규칙을 본다. schema를 느슨하게 만드는 것이 아니라 **같은
    뜻을 strict가 받는 모양으로 옮기는 것**이다.
    """
    if isinstance(node, list):
        return [_strict(x) for x in node]
    if not isinstance(node, dict):
        return node

    out = {k: _strict(v) for k, v in node.items()}

    # `const`는 strict가 모르는 낱말이다. 값 하나만 허용하는 `enum`으로 옮긴다.
    # 뜻이 같다. 지우면 AI가 아무 값이나 쓸 수 있게 되므로 **지우지 않는다.**
    if "const" in out:
        value = out.pop("const")
        out["enum"] = [value]
        out.setdefault("type", _json_type(value))

    if _is_object(out) and isinstance(out.get("properties"), dict):
        out["additionalProperties"] = False
        out["required"] = list(out["properties"].keys())
    elif _is_object(out):
        # 모양을 정하지 않은 칸(`dict`)이다. strict는 "아무 object나"를 말하지
        # 못한다. 빈 object만 허용하는 쪽으로 **좁힌다.** 넓히지 않는다.
        # 결과: AI가 이 칸을 채우지 못한다. 원장에 없는 글을 여기로 끼워
        # 넣던 길이 하나 막히는 것이므로 안전 쪽으로 기운다.
        out["properties"] = {}
        out["additionalProperties"] = False
        out["required"] = []

    # 무엇이 들어가는지 정하지 않은 배열도 같은 이유로 좁힌다.
    if _is_array(out) and "items" not in out:
        out["items"] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
            "required": [],
        }
    # strict가 모르는 낱말은 뺀다. 값의 뜻은 Pydantic이 다시 본다.
    for unsupported in ("$schema", "$id", "default", "examples", "format"):
        out.pop(unsupported, None)
    return out


#: 형식 파일이 **알맹이**만 적은 Agent들. 계약은 봉투로 받으므로 씌워 보낸다.
#:
#: `draft_candidate.schema.json`은 이름 그대로 초안 후보 하나의 모양이다
#: (`test_sets/README.md`). 그런데 `DraftEnvelope`는 `{schema_version, result}`를
#: 받는다. 알맹이를 그대로 보내면 AI는 형식을 지키고도 **매번** 거절당한다.
#: 실제로 그랬고, 한 번에 25건씩 "Extra inputs are not permitted"가 났다.
ENVELOPE_AGENTS = frozenset({"DraftWritingAgent", "RevisionAgent"})


def _envelope(candidate: dict) -> dict:
    """알맹이를 `{schema_version, result}` 봉투에 넣는다.

    `$defs`는 **새 루트로 올린다.** 알맹이 안에 남겨 두면 `#/$defs/...` 참조가
    한 칸 어긋나 아무 데도 가리키지 못한다.
    """
    inner = dict(candidate)
    defs = inner.pop("$defs", None)
    version = inner.get("properties", {}).get("schema_version") or {"type": "string"}
    envelope = {
        "type": "object",
        "properties": {"schema_version": version, "result": inner},
        "required": ["schema_version", "result"],
        "additionalProperties": False,
    }
    if defs:
        envelope["$defs"] = defs
    return envelope


def _known_shapes() -> dict[str, dict]:
    """형식 파일은 모양을 안 정했지만 **Gate는 정해진 코드만 받는** 칸들.

    Gate가 이미 거절하는 값을 형식에도 적는다. 그러면 AI가 그 값을 **쓸 수
    없다.** 나중에 거절하는 것보다 아예 못 쓰게 하는 쪽이 낫다.
    """
    from app.harness.draft_contracts import SIX_W_KEYS, STATUS_CODES

    codes = sorted(STATUS_CODES)
    return {
        "six_w_status": {
            "type": "object",
            "properties": {
                key: {"type": "string", "enum": codes} for key in sorted(SIX_W_KEYS)
            },
        }
    }


def _tighten(node, shapes: dict[str, dict]):
    """이름이 같은 칸을 정해진 모양으로 바꾼다."""
    if isinstance(node, list):
        return [_tighten(x, shapes) for x in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            out[key] = {
                name: (
                    json.loads(json.dumps(shapes[name]))
                    if name in shapes
                    else _tighten(child, shapes)
                )
                for name, child in value.items()
            }
        else:
            out[key] = _tighten(value, shapes)
    return out


def response_schema(agent_name: str) -> dict | None:
    """이 Agent가 돌려줘야 하는 모양."""
    name = SCHEMA_FILES.get(agent_name)
    if name is None:
        return None
    path = SCHEMA_DIR / name
    if not path.exists():
        return None
    shape = json.loads(path.read_text(encoding="utf-8"))
    shape = _tighten(shape, _known_shapes())
    if agent_name in ENVELOPE_AGENTS:
        shape = _envelope(shape)
    return _strict(shape)


class ExternalCallBlocked(Exception):
    """보내기 전에 막았다. **호출은 0회다.**"""

    def __init__(self, code: str, message: str, next_action: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_action = next_action


def reserve_usd(agent_name: str, max_output_tokens: int) -> float:
    """이 호출 하나가 쓸 수 있는 **최대** 금액."""
    reserved_input = INPUT_RESERVATION.get(agent_name, 20_000)
    return (
        reserved_input * PRICE_PER_MILLION["input"]
        + max_output_tokens * PRICE_PER_MILLION["output"]
    ) / 1_000_000


def actual_usd(input_tokens: int, output_tokens: int) -> float:
    """응답이 알려 준 실제 사용량으로 다시 센다."""
    return (
        input_tokens * PRICE_PER_MILLION["input"]
        + output_tokens * PRICE_PER_MILLION["output"]
    ) / 1_000_000


@dataclass
class OpenAIModelGateway:
    """진짜 OpenAI를 부른다.

    `budget_usd`는 이 통로 하나가 쓸 수 있는 전체 한도다. 보내기 전에
    예약액을 더해 넘으면 **보내지 않는다.**
    """

    #: **이 통로는 진짜다.** 이 줄이 없으면 화면이 거짓말을 한다.
    #:
    #: 화면은 `getattr(gateway, "is_fake", True)`로 묻는다. 없으면 기본값이
    #: `True`라서 **진짜로 나가는 중에도 "가짜 AI"라고 적힌다.** 실제로
    #: 그랬다. 서버를 진짜로 켰는데 화면은 "인터넷으로 나가지 않고 비용도
    #: 0원"이라고 적혀 있었다. 사람이 그 말을 믿고 자료를 넣는다.
    is_fake: bool = False

    budget_usd: float = RUN_BUDGET_USD
    spent_usd: float = 0.0
    calls: int = 0
    #: 응답이 다른 모델로 오면 여기 남기고 다음 호출을 막는다.
    mismatch: str | None = None

    def _client(self):
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ExternalCallBlocked(
                "API_KEY_MISSING",
                "OpenAI API 키가 없습니다.",
                "`.env`에 `OPENAI_API_KEY`를 넣고 서버를 다시 시작해 주세요.",
            )
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=key, timeout=TIMEOUT_SECONDS)

    async def call(self, request: ModelCallRequest) -> ModelCallResult:
        if self.mismatch is not None:
            raise ExternalCallBlocked(
                "MODEL_CONFIG_MISMATCH",
                f"요청한 모델과 다른 모델이 응답했습니다: {self.mismatch}",
                "설정을 확인한 뒤 새 작업으로 다시 시도해 주세요.",
            )

        reserve = reserve_usd(request.agent_name, request.max_output_tokens)
        if self.spent_usd + reserve > self.budget_usd:
            raise ExternalCallBlocked(
                "BUDGET_EXCEEDED",
                f"예상 비용이 한도를 넘습니다. "
                f"지금까지 {self.spent_usd:.4f}달러를 썼고 이번 호출에 최대 "
                f"{reserve:.4f}달러가 더 듭니다. 한도는 {self.budget_usd:.2f}달러입니다.",
                "자료를 줄이거나 한도를 다시 정해 주세요.",
            )

        client = self._client()
        response = await client.responses.create(
            model=CONFIGURED_MODEL,
            service_tier=SERVICE_TIER,
            # 공급자 쪽에 응답을 남기지 않는다 (README §7.2).
            store=False,
            reasoning={"effort": REASONING_EFFORT},
            max_output_tokens=request.max_output_tokens,
            # 입력이 길면 조용히 잘라내지 않고 오류로 멈춘다.
            truncation="disabled",
            **_format_arg(request.agent_name),
            input=[
                {
                    "role": "system",
                    "content": instructions_for(request.agent_name),
                },
                {
                    "role": "user",
                    "content": json.dumps(request.payload, ensure_ascii=False),
                },
            ],
        )

        actual_model = getattr(response, "model", "") or ""
        if actual_model and not actual_model.startswith(CONFIGURED_MODEL):
            self.mismatch = actual_model

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cost = actual_usd(input_tokens, output_tokens)
        self.spent_usd += cost
        self.calls += 1

        text = getattr(response, "output_text", "") or ""
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            result = {}

        return ModelCallResult(
            agent_name=request.agent_name,
            requested_model=CONFIGURED_MODEL,
            actual_model=actual_model or CONFIGURED_MODEL,
            result=result,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            is_fake=False,
        )


def instructions_for(agent_name: str) -> str:
    """이 Agent에게 줄 지시문. 모르는 이름이면 공통 규칙만 준다."""
    return INSTRUCTIONS.get(agent_name, COMMON)


def _format_arg(agent_name: str) -> dict:
    """응답 형식 지정. schema가 없으면 붙이지 않는다."""
    schema = response_schema(agent_name)
    if schema is None:
        return {}
    return {
        "text": {
            "format": {
                "type": "json_schema",
                "name": agent_name,
                "schema": schema,
                "strict": True,
            }
        }
    }


#: 프로그램이 열쇠를 찾는 자리. 저장소 뿌리에 둔다.
ENV_FILE = SCHEMA_DIR.parent / ".env"

#: `.env`에서 읽어 올 이름. **열쇠 하나뿐이다.**
#:
#: `POLICY_AGENT_LIVE`는 **일부러 뺐다.** 파일로 켤 수 있게 두면 두 가지가
#: 무너진다. 첫째, 시험이 서버를 띄울 때마다 그 파일을 읽어 진짜 AI로 나가고
#: 돈이 든다. 실제로 그렇게 됐고 시험 아홉 개가 한꺼번에 깨졌다. 둘째, 켠
#: 기억이 없는 사람이 자료를 넣는다. 진짜 AI는 **켤 때마다 사람이 손으로**
#: 켜야 한다.
ENV_KEYS = ("OPENAI_API_KEY",)


def load_env_file(path: Path | None = None) -> list[str]:
    """`.env`의 값을 환경에 올린다. **올린 이름만** 돌려준다.

    값은 돌려주지도 기록하지도 않는다. 열쇠가 화면이나 기록에 남으면 안 된다.

    이미 환경에 있는 값은 **덮어쓰지 않는다.** 사람이 명령줄에서 준 값이
    파일보다 세다. 그래야 파일을 고치지 않고도 한 번만 다르게 켤 수 있다.

    이 함수가 없으면 `.env`에 열쇠를 넣어도 서버가 찾지 못한다. 실제로 그랬고,
    오류 메시지는 `.env`에 넣으라고 안내하는데 정작 읽지 않았다.
    """
    target = path if path is not None else ENV_FILE
    if not target.exists():
        return []
    올린_것: list[str] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name not in ENV_KEYS:
            continue
        if os.environ.get(name):
            continue  # 이미 있는 값이 세다
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        os.environ[name] = value
        올린_것.append(name)
    return 올린_것


def live_enabled() -> bool:
    """진짜 AI를 쓸지.

    **기본은 꺼져 있다.** 켜려면 사람이 `POLICY_AGENT_LIVE=1`을 넣어야 한다.
    켜 두면 개발 중 시험을 돌릴 때마다 자료가 나가고 돈이 든다.
    """
    return os.environ.get("POLICY_AGENT_LIVE", "").strip() == "1"


def provider_label() -> str:
    return f"{CONFIGURED_PROVIDER} / {CONFIGURED_MODEL}"
