"""검증 script·schema·보고서 계약 검사 (README §2.14.1).

검증 도구 자체가 계약대로 만들어졌는지 확인한다. 이 시험은 서버를 띄우지 않고
파일만 읽으므로 외부 호출이 없다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VERIFICATION = ROOT / "verification"
SCHEMA_PATH = VERIFICATION / "day-gate-result.schema.json"
VERIFIER_DOC = VERIFICATION / "day-gate-verifier.md"
SCRIPT_PATH = ROOT / "scripts" / "verify-day1.ps1"

REQUIRED_CHECKS = ["D1G-01", "D1G-02", "D1G-03", "D1G-04", "D1G-05", "D1G-06", "D1G-07"]


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def script_text() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_검증_계약_파일이_모두_있다() -> None:
    assert SCHEMA_PATH.is_file()
    assert VERIFIER_DOC.is_file()
    assert SCRIPT_PATH.is_file()


def test_보고서_schema가_7개_검사를_요구한다(schema: dict) -> None:
    checks = schema["properties"]["checks"]
    assert checks["minItems"] == 7
    assert set(checks["items"]["properties"]["check_id"]["enum"]) == set(REQUIRED_CHECKS)


def test_판정은_PASS_또는_BLOCKED뿐이다(schema: dict) -> None:
    assert set(schema["properties"]["verdict"]["enum"]) == {"PASS", "BLOCKED"}


def test_검사_상태에_SKIP도_기록된다(schema: dict) -> None:
    """SKIP을 숨기지 않고 기록해야 BLOCKED로 판정할 수 있다."""
    status = schema["properties"]["checks"]["items"]["properties"]["status"]["enum"]
    assert set(status) == {"PASS", "FAIL", "ERROR", "SKIP"}


def test_외부_호출과_비용은_0으로_고정된다(schema: dict) -> None:
    assert schema["properties"]["external_api_calls"]["const"] == 0
    assert schema["properties"]["estimated_cost_usd"]["const"] == 0


def test_증거가_없으면_보고서가_통과하지_못한다(schema: dict) -> None:
    evidence = schema["properties"]["checks"]["items"]["properties"]["evidence"]
    assert evidence["minItems"] == 1


def test_script가_7개_검사를_모두_실행한다(script_text: str) -> None:
    for check_id in REQUIRED_CHECKS:
        assert f'"{check_id}"' in script_text, f"{check_id} 검사가 없습니다."


def test_script가_외부_네트워크를_쓰지_않는다(script_text: str) -> None:
    """127.0.0.1 외의 주소를 부르지 않아야 한다."""
    for banned in ("api.openai.com", "https://", "Invoke-WebRequest -Uri \"http://localhost"):
        assert banned not in script_text, f"외부 주소를 부릅니다: {banned}"


def test_script가_env를_읽지_않는다(script_text: str) -> None:
    """`.env`를 열거나 불러오지 않는다.

    제외 목록·검사 대상 이름으로 `.env` 문자열이 나오는 것은 허용한다.
    같은 줄에서 파일을 읽는 명령과 함께 쓰였는지만 본다.
    """
    assert "OPENAI_API_KEY" not in script_text

    read_commands = ("Get-Content", "Import-Csv", "[IO.File]::Read", "gc ")
    for line_no, line in enumerate(script_text.splitlines(), start=1):
        if ".env" not in line or ".env.example" in line:
            continue
        for command in read_commands:
            assert command not in line, (
                f"{line_no}번째 줄에서 .env를 읽습니다: {line.strip()}"
            )


def test_script가_제품_코드를_고치지_않는다(script_text: str) -> None:
    """검사원은 코드를 고칠 수 없다 (README §2.14.1 금지 행동)."""
    forbidden = [
        "Set-Content -Path $Backend",
        "Remove-Item -Recurse",
        "npm install",
        "pip install",
        "git checkout",
        "git commit",
    ]
    for command in forbidden:
        assert command not in script_text, f"금지된 행동이 있습니다: {command}"


def test_script가_기존_보고서를_덮어쓰지_않는다(script_text: str) -> None:
    assert "기존 보고서를 덮어쓰지 않습니다" in script_text
    assert "if (Test-Path $jsonPath) { throw" in script_text


def test_문지기_문서가_금지_행동을_적어_둔다() -> None:
    doc = VERIFIER_DOC.read_text(encoding="utf-8")
    for rule in (
        "제품 소스",
        "테스트 삭제",
        "인터넷",
        "`.env`",
        "스스로 PASS",
    ):
        assert rule in doc, f"문지기 문서에 금지 행동이 없습니다: {rule}"


def test_보고서_폴더가_append_only로_준비된다() -> None:
    reports = VERIFICATION / "reports" / "day1-to-day2"
    assert reports.is_dir(), "보고서 폴더가 없습니다."


def test_스스로_합격시키지_않는다는_표시가_보고서에_들어간다(script_text: str) -> None:
    assert "is_separate_from_implementer" in script_text
    assert "다른 새 문맥의" in script_text
