@echo off
setlocal
chcp 65001 >nul
title 보도자료 초안 작성 Agent

REM 명령어를 몰라도 이 파일만 두 번 누르면 됩니다.
REM 포트 확인 -> 단일 worker 서버 시작 -> /api/health 확인 -> 같은 주소로 브라우저 열기

set "PORT=8765"
set "ROOT=%~dp0"
set "URL=http://127.0.0.1:%PORT%"
set "PIDFILE=%ROOT%.local-server.pid"

echo.
echo   국회 법률 개정·개선 보도자료 초안 작성 Agent
echo   ------------------------------------------------
echo   산출물은 언제나 DRAFT / 내부 검토용입니다.
echo.

REM 1) 포트가 이미 쓰이고 있는지 확인합니다.
netstat -ano | findstr /R /C:"LISTENING" | findstr /C:":%PORT% " >nul
if not errorlevel 1 (
    echo   [멈춤] %PORT% 포트를 이미 다른 프로그램이 쓰고 있습니다.
    echo          stop-local.cmd 를 먼저 실행하거나 그 프로그램을 닫아 주세요.
    pause
    exit /b 1
)

REM 2) 화면이 빌드되어 있는지 확인합니다.
if not exist "%ROOT%frontend\dist\index.html" (
    echo   [안내] 화면을 먼저 만듭니다. 처음 한 번만 시간이 걸립니다.
    pushd "%ROOT%frontend"
    if not exist node_modules ( call npm install --no-audit --no-fund )
    call npm run build
    popd
    if not exist "%ROOT%frontend\dist\index.html" (
        echo   [멈춤] 화면을 만들지 못했습니다.
        pause
        exit /b 1
    )
)

REM 3) 서버를 시작합니다. 메모리 저장소를 쓰므로 worker는 하나만 띄웁니다.
echo   서버를 시작합니다...
pushd "%ROOT%backend"
start "policy-agent-server" /min cmd /c "python -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --workers 1"
popd

REM 4) 서버가 응답할 때까지 기다립니다.
set "READY="
for /l %%i in (1,1,30) do (
    if not defined READY (
        timeout /t 1 /nobreak >nul
        curl -s -o nul -w "%%{http_code}" "%URL%/api/health" 2>nul | findstr /C:"200" >nul
        if not errorlevel 1 set "READY=1"
    )
)

if not defined READY (
    echo   [멈춤] 서버가 응답하지 않습니다.
    echo          backend 폴더에서 python -m uvicorn app.main:app 을 직접 실행해 오류를 확인해 주세요.
    pause
    exit /b 1
)

REM 5) 우리가 띄운 서버의 PID만 기록합니다. stop-local.cmd가 이것만 종료합니다.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:"LISTENING" ^| findstr /C:":%PORT% "') do (
    echo %%p> "%PIDFILE%"
)

echo   준비됐습니다. 브라우저를 엽니다: %URL%
start "" "%URL%"
echo.
echo   서버를 끄려면 stop-local.cmd 를 실행하세요.
echo.
endlocal
