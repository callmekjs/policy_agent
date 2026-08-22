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
    if exist "%PIDFILE%" (
        echo          stop-local.cmd 를 먼저 실행해 주세요.
    ) else (
        echo          이전에 켠 서버가 남아 있을 수 있습니다.
        echo          stop-local.cmd 를 실행하면 남은 서버를 찾아 정리합니다.
    )
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
            exit /b 1
    )
)

REM 3) 서버를 시작하고 곧바로 번호를 적어 둡니다.
REM     번호를 나중에 적으면 그 사이에 이 창이 닫혔을 때 서버가 남아
REM     stop-local.cmd로도 끌 수 없게 됩니다.
echo   서버를 시작합니다...
del "%PIDFILE%" >nul 2>&1
for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "(Start-Process -FilePath 'python' -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','%PORT%','--workers','1' -WorkingDirectory '%ROOT%backend' -WindowStyle Minimized -PassThru).Id"`) do echo %%i> "%PIDFILE%"

if not exist "%PIDFILE%" (
    echo   [멈춤] 서버를 시작하지 못했습니다.
    exit /b 1
)

REM 4) 서버가 응답할 때까지 기다립니다.
set "READY="
for /l %%i in (1,1,30) do (
    if not defined READY (
        ping -n 2 127.0.0.1 >nul
        curl -s -o nul -w "%%{http_code}" "%URL%/api/health" 2>nul | findstr /C:"200" >nul
        if not errorlevel 1 set "READY=1"
    )
)

if not defined READY (
    echo   [멈춤] 서버가 응답하지 않습니다.
    echo          backend 폴더에서 python -m uvicorn app.main:app 을 직접 실행해 오류를 확인해 주세요.
    exit /b 1
)

echo   준비됐습니다. 브라우저를 엽니다: %URL%
start "" "%URL%"
echo.
echo   서버를 끄려면 stop-local.cmd 를 실행하세요.
echo.
endlocal
