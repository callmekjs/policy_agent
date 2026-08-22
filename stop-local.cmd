@echo off
setlocal
chcp 65001 >nul
title 보도자료 초안 작성 Agent - 종료

REM start-local.cmd가 기록한 PID만 종료합니다.
REM 같은 포트를 쓰는 다른 프로그램은 건드리지 않습니다.

set "ROOT=%~dp0"
set "PIDFILE=%ROOT%.local-server.pid"

if not exist "%PIDFILE%" (
    echo   이 스크립트로 시작한 서버가 없습니다.
    echo   ^(직접 실행한 서버는 그 창에서 Ctrl+C 로 종료해 주세요.^)
    exit /b 0
)

set /p SERVERPID=<"%PIDFILE%"
if "%SERVERPID%"=="" (
    del "%PIDFILE%" >nul 2>&1
    echo   기록된 서버 번호가 비어 있습니다.
    exit /b 0
)

tasklist /fi "PID eq %SERVERPID%" | findstr /C:"%SERVERPID%" >nul
if errorlevel 1 (
    echo   서버가 이미 종료되어 있습니다.
    del "%PIDFILE%" >nul 2>&1
    exit /b 0
)

taskkill /PID %SERVERPID% /T /F >nul 2>&1
if errorlevel 1 (
    echo   [멈춤] 서버를 종료하지 못했습니다. 작업 관리자에서 확인해 주세요.
    exit /b 1
)

del "%PIDFILE%" >nul 2>&1
echo   서버를 종료했습니다. 메모리에 있던 작업 자료도 함께 사라집니다.
endlocal
