@echo off
setlocal
chcp 65001 >nul
title 보도자료 초안 작성 Agent (진짜 AI)

REM ============================================================
REM  진짜 AI로 켭니다. 자료가 인터넷으로 나가고 돈이 듭니다.
REM
REM  평소에는 start-local.cmd 를 쓰세요. 그쪽은 가짜 AI라
REM  인터넷으로 나가지 않고 비용도 0원입니다.
REM
REM  진짜 AI를 켜는 스위치는 **이 파일에만** 있습니다.
REM  .env 파일로는 켜지지 않습니다. 켠 기억이 없는 사람이
REM  자료를 넣는 일을 막기 위해서입니다.
REM  (.env 에는 OPENAI_API_KEY 만 두세요.)
REM ============================================================

set "PORT=8765"
set "ROOT=%~dp0"
set "URL=http://127.0.0.1:%PORT%"
set "PIDFILE=%ROOT%.local-server.pid"

echo.
echo   국회 법률 개정·개선 보도자료 초안 작성 Agent
echo   ------------------------------------------------
echo   [주의] 진짜 AI로 켭니다.
echo          붙여 넣는 자료가 인터넷을 통해 OpenAI로 전송되고
echo          작업 한 건에 대략 0.03 ~ 0.05 달러가 듭니다.
echo          공개 자료만 넣어 주세요.
echo.
echo   산출물은 언제나 DRAFT / 내부 검토용입니다.
echo.

set /p ANSWER=  진짜 AI로 켤까요? (y 를 누르고 Enter):
if /i not "%ANSWER%"=="y" (
    echo   취소했습니다. 가짜 AI로 켜려면 start-local.cmd 를 실행하세요.
    exit /b 0
)

REM 1) 포트가 이미 쓰이고 있는지 확인합니다.
netstat -ano | findstr /R /C:"LISTENING" | findstr /C:":%PORT% " >nul
if not errorlevel 1 (
    echo   [멈춤] %PORT% 포트를 이미 다른 프로그램이 쓰고 있습니다.
    echo          stop-local.cmd 를 먼저 실행해 주세요.
    exit /b 1
)

REM 2) 열쇠가 있는지 확인합니다. 값은 화면에 찍지 않습니다.
if not exist "%ROOT%.env" (
    echo   [멈춤] .env 파일이 없습니다.
    echo          .env 에 OPENAI_API_KEY=... 한 줄을 넣어 주세요.
    exit /b 1
)
findstr /B /C:"OPENAI_API_KEY=" "%ROOT%.env" >nul
if errorlevel 1 (
    echo   [멈춤] .env 에 OPENAI_API_KEY 가 없습니다.
    exit /b 1
)

REM 3) 화면이 빌드되어 있는지 확인합니다.
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

REM 4) 진짜 AI 스위치를 켜고 서버를 시작합니다.
echo   서버를 시작합니다 (진짜 AI)...
del "%PIDFILE%" >nul 2>&1
for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:POLICY_AGENT_LIVE='1'; (Start-Process -FilePath 'python' -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','%PORT%','--workers','1' -WorkingDirectory '%ROOT%backend' -WindowStyle Minimized -PassThru).Id"`) do echo %%i> "%PIDFILE%"

if not exist "%PIDFILE%" (
    echo   [멈춤] 서버를 시작하지 못했습니다.
    exit /b 1
)

REM 5) 서버가 응답할 때까지 기다립니다.
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
    exit /b 1
)

echo   준비됐습니다. 브라우저를 엽니다: %URL%
echo   화면에 "진짜 AI"라고 적혀 있는지 확인해 주세요.
start "" "%URL%"
echo.
echo   서버를 끄려면 stop-local.cmd 를 실행하세요.
echo.
endlocal
