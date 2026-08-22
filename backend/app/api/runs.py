"""실행 API (README §3.10).

1일차 범위는 bootstrap·생성·조회·삭제다. answers·revisions·fact-review·
prepare-draft·export는 2~5일차에 같은 파일에 이어서 붙인다.

모든 상태 변경 요청은 localhost Host·정확한 Origin·현재 요청 쿠키·멱등 키를
검사한다. 하나라도 어긋나면 외부 AI 호출 전에 거부한다.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.harness.contracts import (
    ACTIVE_CONTRACT_ID,
    EXTERNAL_AI_POLICY_VERSION,
    MAX_SOURCES,
    MAX_TOTAL_CHARS,
    PURPOSE_MAX_CHARS,
    PURPOSE_MIN_CHARS,
    SOURCE_ROLE_LABELS,
    SUPPORTED_PROCEDURE_STAGE,
    SUPPORTED_PROCEDURE_STAGE_LABEL,
    ApiError,
    CreateRunRequest,
    Run,
)
from app.harness.runtime import BusyError
from app.harness.states import DELETABLE_STATES, RunState, user_facing_status
from app.infrastructure.model_gateway import CONFIGURED_MODEL, CONFIGURED_PROVIDER

router = APIRouter(prefix="/api", tags=["runs"])

#: 서버가 시작할 때마다 새로 만드는 요청 토큰 쿠키 이름.
REQUEST_TOKEN_COOKIE = "policy_agent_request_token"


def error_response(
    status_code: int,
    error_code: str,
    message: str,
    next_action: str,
    run_id: str | None = None,
) -> JSONResponse:
    """모든 오류를 같은 모양으로 돌려준다."""
    body = ApiError(
        error_code=error_code,
        message=message,
        next_action=next_action,
        run_id=run_id,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _is_local_host(request: Request) -> bool:
    host = (request.headers.get("host") or "").split(":")[0].strip("[]")
    return host in {"127.0.0.1", "localhost", "::1"}


def _origin_ok(request: Request) -> bool:
    """Origin이 없거나(같은 주소 요청) 이 서버와 같은 주소여야 한다."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    allowed = {f"{request.url.scheme}://{request.headers.get('host', '')}"}
    allowed |= set(request.app.state.allowed_origins)
    return origin in allowed


def guard_state_change(request: Request) -> JSONResponse | None:
    """상태 변경 요청의 공통 검사. 통과하면 None."""
    if not _is_local_host(request):
        return error_response(
            403,
            "NON_LOCAL_HOST",
            "이 프로그램은 이 컴퓨터에서만 사용할 수 있습니다.",
            "127.0.0.1 주소로 다시 열어 주세요.",
        )
    if not _origin_ok(request):
        return error_response(
            403,
            "ORIGIN_NOT_ALLOWED",
            "허용되지 않은 곳에서 온 요청입니다.",
            "프로그램 화면에서 다시 시도해 주세요.",
        )

    expected = request.app.state.request_token
    presented = request.cookies.get(REQUEST_TOKEN_COOKIE)
    if presented is None or not secrets.compare_digest(presented, expected):
        return error_response(
            403,
            "REQUEST_TOKEN_INVALID",
            "서버가 다시 시작되어 화면 정보가 오래됐습니다.",
            "화면을 새로 고친 뒤 다시 시도해 주세요.",
        )

    if request.app.state.runtime.is_shutting_down:
        return error_response(
            503,
            "SERVER_SHUTTING_DOWN",
            "서버가 종료 중입니다.",
            "서버를 다시 시작한 뒤 시도해 주세요.",
        )
    return None


def run_view(run: Run) -> dict[str, Any]:
    """화면에 보낼 값. 원문 전체와 비밀값은 담지 않는다."""
    state = RunState(run.state)
    return {
        "run_id": run.run_id,
        "state": run.state,
        "status_label": user_facing_status(state),
        "contract_id": run.contract_id,
        "procedure_stage": run.procedure_stage.value,
        "procedure_stage_label": SUPPORTED_PROCEDURE_STAGE_LABEL,
        "effect_status_label": "아직 법률 아님",
        "basis_date": run.basis_date.isoformat(),
        "purpose": run.purpose,
        "disclosure": run.disclosure.value,
        "announcement_subject_input": run.announcement_subject_input,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "draft_version": run.draft_version,
        "actual_model_calls": run.actual_model_calls,
        "max_model_calls": 7,
        "estimated_cost_usd": round(run.estimated_cost_usd, 6),
        "cost_limit_usd": 1.10,
        "sources": [
            {
                "source_id": s.source_id,
                "display_name": s.display_name,
                "role": s.role.value,
                "role_label": SOURCE_ROLE_LABELS[s.role],
                "char_count": s.char_count,
            }
            for s in run.sources
        ],
        "issues": [i.model_dump(mode="json") for i in run.issues],
        "failure": (
            {
                "kind": run.failure_kind,
                "code": run.failure_code,
                "message": run.failure_message,
                "next_action": run.next_action,
            }
            if run.failure_kind
            else None
        ),
    }


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """실행 바로가기와 검증 도구가 서버가 떴는지 확인하는 곳."""
    contract = request.app.state.writing_contract
    return {
        "status": "ok",
        "contract_id": contract.full_id,
        "writing_contract_files": sorted(contract.source_paths.values()),
        "model_gateway": "fake" if request.app.state.gateway.is_fake else "live",
        "external_api_calls": 0 if request.app.state.gateway.is_fake else None,
    }


@router.get("/bootstrap")
async def bootstrap(request: Request, response: Response) -> dict[str, Any]:
    """공개 앱 설정을 주고 요청 토큰 쿠키를 설정한다.

    API 키·원문·토큰 본문은 응답에 담지 않는다.
    """
    response.set_cookie(
        REQUEST_TOKEN_COOKIE,
        request.app.state.request_token,
        httponly=True,
        samesite="strict",
        path="/",
    )
    contract = request.app.state.writing_contract
    return {
        "app_title": "국회 법률 개정·개선 보도자료 초안 작성 Agent",
        "notice": "공개·합성 자료로 내부 검토용 초안을 만드는 도구입니다.",
        "contract_id": contract.full_id,
        "procedure_stage": SUPPORTED_PROCEDURE_STAGE.value,
        "procedure_stage_label": SUPPORTED_PROCEDURE_STAGE_LABEL,
        "external_ai": {
            "policy_version": EXTERNAL_AI_POLICY_VERSION,
            "provider": CONFIGURED_PROVIDER,
            "model": CONFIGURED_MODEL,
            "sent_items": [
                "공개로 확인한 공식 자료 원문",
                "보도 목적",
                "자료 이름·역할 같은 메타데이터",
            ],
            "notice": (
                "확인하면 위 자료가 인터넷을 통해 OpenAI API로 전송됩니다. "
                "응답 저장은 끄지만 공급자의 기본 안전 기록은 보통 최대 30일 남을 수 있습니다."
            ),
        },
        "limits": {
            "max_sources": MAX_SOURCES,
            "max_total_chars": MAX_TOTAL_CHARS,
            "purpose_min_chars": PURPOSE_MIN_CHARS,
            "purpose_max_chars": PURPOSE_MAX_CHARS,
        },
        "source_roles": [
            {"value": role.value, "label": label}
            for role, label in SOURCE_ROLE_LABELS.items()
        ],
        "model_gateway": "fake" if request.app.state.gateway.is_fake else "live",
    }


@router.post("/runs", status_code=202)
async def create_run(request: Request, body: CreateRunRequest) -> Any:
    """새 작업을 만들고 202와 실행 ID를 먼저 돌려준다."""
    guard = guard_state_change(request)
    if guard is not None:
        return guard

    store = request.app.state.store
    runtime = request.app.state.runtime
    orchestrator = request.app.state.orchestrator

    async with store.lock:
        # 같은 멱등 키면 기존 응답을 재사용한다. 중복 클릭이 두 번 과금되지 않는다.
        existing = store.find_by_client_request_id(body.client_request_id)
        if existing is not None:
            return run_view(existing)

        if runtime.busy_run_id() is not None:
            return error_response(
                409,
                "BUSY",
                "다른 작업이 처리 중입니다.",
                "지금 작업이 끝난 뒤 새 작업을 시작해 주세요.",
            )

        if store.active_runs():
            return error_response(
                409,
                "RUN_ALREADY_EXISTS",
                "이미 진행 중인 작업이 있습니다. 한 번에 한 건만 처리합니다.",
                "현재 작업을 삭제한 뒤 새 작업을 시작해 주세요.",
            )

        run = orchestrator.create_run(body)

    try:
        runtime.spawn(
            run.run_id,
            orchestrator.process(run.run_id, body, datetime.now(UTC).date()),
        )
    except BusyError:
        async with store.lock:
            store.delete(run.run_id)
        return error_response(
            409,
            "BUSY",
            "다른 작업이 처리 중입니다.",
            "지금 작업이 끝난 뒤 새 작업을 시작해 주세요.",
        )

    return run_view(run)


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> Any:
    """현재 상태를 돌려준다. 이 조회는 2시간 만료 시간을 늘리지 않는다."""
    store = request.app.state.store
    async with store.lock:
        run = store.get(run_id)
        if run is None:
            return error_response(
                404,
                "RUN_NOT_FOUND",
                "작업이 삭제됐거나 2시간 동안 사용하지 않았거나 "
                "서버가 다시 시작되어 더 이상 불러올 수 없습니다.",
                "새 작업으로 다시 시작해 주세요.",
                run_id,
            )
        return run_view(run)


@router.delete("/runs/{run_id}")
async def delete_run(request: Request, run_id: str) -> Any:
    """현재 작업의 임시 데이터를 지운다. 처리 중에는 취소로 쓰지 않는다."""
    guard = guard_state_change(request)
    if guard is not None:
        return guard

    store = request.app.state.store
    async with store.lock:
        run = store.get(run_id)
        if run is None:
            return error_response(
                404,
                "RUN_NOT_FOUND",
                "이미 삭제됐거나 만료된 작업입니다.",
                "새 작업으로 다시 시작해 주세요.",
                run_id,
            )
        if RunState(run.state) not in DELETABLE_STATES:
            return error_response(
                409,
                "INVALID_RUN_STATE",
                "처리 중에는 삭제할 수 없습니다.",
                "처리가 끝난 뒤 삭제해 주세요.",
                run_id,
            )
        store.delete(run_id)
    return {"run_id": run_id, "deleted": True}


@router.get("/contract")
async def contract_info(request: Request) -> dict[str, Any]:
    """서버가 실제로 읽은 Writing Contract를 보여준다 (검증용)."""
    contract = request.app.state.writing_contract
    return {
        "contract_id": ACTIVE_CONTRACT_ID,
        "loaded_id": contract.full_id,
        "components": {
            name: contract.component_full_id(name)
            for name in ("profile", "template", "style", "validation")
        },
        "files": contract.source_paths,
        "rule_count": len(contract.manifest.get("rules", [])),
        "excluded_rule_count": len(contract.manifest.get("excluded_rules", [])),
        "required_sections": contract.template["required_sections"],
    }
