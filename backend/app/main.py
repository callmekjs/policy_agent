"""FastAPI 시작점 (README §3.8, §3.10).

React 화면과 API를 **한 주소**에서 연다. 개발 중 Vite를 따로 띄울 때만
정확한 주소 하나를 CORS로 허용하고, 모든 출처를 뜻하는 와일드카드는 쓰지 않는다.

lifespan이 작업 registry·Semaphore(1)·RunStore·TTL sweeper를 소유한다.
서버가 꺼지면 메모리의 Run은 전부 사라진다.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.runs import router as runs_router
from app.harness.contract_loader import load_writing_contract
from app.harness.orchestrator import Orchestrator
from app.harness.runtime import HarnessRuntime
from app.infrastructure.model_gateway import FakeModelGateway
from app.infrastructure.openai_gateway import OpenAIModelGateway, live_enabled
from app.infrastructure.run_store import RunStore

#: 빌드된 React 결과물 위치.
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

#: 개발 중 Vite 주소. 와일드카드는 쓰지 않는다.
DEV_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Writing Contract 다섯 파일을 읽지 못하면 서버를 시작하지 않는다.
    app.state.writing_contract = load_writing_contract()

    app.state.store = RunStore()
    # **기본은 가짜다.** 진짜 AI는 사람이 `POLICY_AGENT_LIVE=1`을 넣어야 켜진다.
    # 켜 두면 시험을 돌릴 때마다 자료가 인터넷으로 나가고 돈이 든다.
    if live_enabled():
        app.state.gateway = OpenAIModelGateway()
    else:
        app.state.gateway = FakeModelGateway()
    app.state.orchestrator = Orchestrator(app.state.store, app.state.gateway)
    app.state.runtime = HarnessRuntime(app.state.store)
    app.state.request_token = secrets.token_urlsafe(32)
    app.state.allowed_origins = DEV_ORIGINS
    app.state.runtime.start_sweeper()
    try:
        yield
    finally:
        await app.state.runtime.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="국회 법률 개정·개선 보도자료 초안 작성 Agent",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    if os.environ.get("POLICY_AGENT_DEV") == "1":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=DEV_ORIGINS,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type"],
        )

    app.include_router(runs_router)

    # 빌드된 화면을 같은 주소에서 서빙한다.
    if FRONTEND_DIST.is_dir():
        assets = FRONTEND_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(FRONTEND_DIST / "index.html")

        @app.get("/{path:path}", include_in_schema=False, response_model=None)
        async def spa(path: str) -> FileResponse | JSONResponse:
            if path.startswith("api/"):
                return JSONResponse(
                    status_code=404,
                    content={
                        "error_code": "NOT_FOUND",
                        "message": "없는 주소입니다.",
                        "next_action": "화면에서 다시 시도해 주세요.",
                        "run_id": None,
                    },
                )
            index = FRONTEND_DIST / "index.html"
            if not path:
                return FileResponse(index)

            # 요청 경로가 빌드 폴더 밖을 가리키면 파일을 읽지 않는다.
            # `..`, 퍼센트 인코딩, 절대 경로로 .env 같은 파일을 가져가지 못하게 막는다.
            dist_root = FRONTEND_DIST.resolve()
            try:
                candidate = (FRONTEND_DIST / path).resolve()
            except (OSError, ValueError):
                return FileResponse(index)
            if not candidate.is_relative_to(dist_root):
                return FileResponse(index)
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

    return app


app = create_app()
