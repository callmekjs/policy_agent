# 개발 1일차 -> 2일차 전환의 결정형 검사 (README §2.14.1).
#
# 이 script는 코드를 고치지 않는다. 인터넷과 OpenAI API를 부르지 않고
# .env를 열지 않는다. 가짜 ModelGateway만 쓰므로 API 비용은 0달러다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\verify-day1.ps1

param(
    [int]$Port = 8799,
    [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReportDir = Join-Path $Root "verification\reports\day1-to-day2"
$BaseUrl = "http://127.0.0.1:$Port"

if ([string]::IsNullOrWhiteSpace($RunId)) {
    $RunId = "day1-" + (Get-Date -Format "yyyyMMdd-HHmmss")
}

$StartedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$Checks = New-Object System.Collections.ArrayList
$Blockers = New-Object System.Collections.ArrayList
$Warnings = New-Object System.Collections.ArrayList
$ServerProcess = $null

function Write-Head($text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Cyan
}

function New-Check($id, $title) {
    return [ordered]@{
        check_id = $id
        title    = $title
        status   = "PASS"
        commands = New-Object System.Collections.ArrayList
        evidence = New-Object System.Collections.ArrayList
    }
}

function Add-Evidence($check, $text) {
    [void]$check.evidence.Add($text)
    Write-Host "    - $text"
}

function Fail-Check($check, $message) {
    $check.status = "FAIL"
    [void]$Blockers.Add([ordered]@{ check_id = $check.check_id; message = $message })
    Write-Host "    [FAIL] $message" -ForegroundColor Red
}

function Invoke-Step($check, $label, $workDir, $exe, $exeArgs) {
    Push-Location $workDir
    try {
        $output = & $exe @exeArgs 2>&1 | Out-String
        $code = $LASTEXITCODE
    } catch {
        $output = $_.Exception.Message
        $code = 1
    } finally {
        Pop-Location
    }
    $tail = ($output -split "`r?`n" | Where-Object { $_.Trim() -ne "" } | Select-Object -Last 1)
    [void]$check.commands.Add([ordered]@{
        command   = "$exe $($exeArgs -join ' ')"
        exit_code = $code
        summary   = "$label -> $tail"
    })
    if ($code -ne 0) { Fail-Check $check "$label 실패 (exit $code)" }
    else { Add-Evidence $check "$label exit 0" }
    return $code
}

# ---------------------------------------------------------------------------
# D1G-01 필수 파일과 실행 진입점
# ---------------------------------------------------------------------------
Write-Head "D1G-01 필수 파일과 실행 진입점"
$c1 = New-Check "D1G-01" "1일차 필수 파일과 실행 진입점"
$required = @(
    "start-local.cmd", "stop-local.cmd",
    "scripts\verify-day1.ps1",
    "verification\day-gate-verifier.md",
    "verification\day-gate-result.schema.json",
    "backend\pyproject.toml", "backend\.env.example",
    "backend\app\main.py", "backend\app\api\runs.py",
    "backend\app\harness\states.py", "backend\app\harness\contracts.py",
    "backend\app\harness\orchestrator.py", "backend\app\harness\runtime.py",
    "backend\app\harness\contract_loader.py",
    "backend\app\gates\input_gate.py",
    "backend\app\infrastructure\model_gateway.py",
    "backend\app\infrastructure\run_store.py",
    "frontend\package.json", "frontend\vite.config.ts", "frontend\index.html",
    "frontend\src\main.tsx", "frontend\src\App.tsx", "frontend\src\types.ts",
    "frontend\src\api\client.ts",
    "frontend\src\screens\NewRunScreen.tsx",
    "frontend\src\screens\RunStatusScreen.tsx",
    "tests\test_harness_flow.py", "tests\test_day1_gate.py"
)
$missing = @()
foreach ($rel in $required) {
    if (-not (Test-Path (Join-Path $Root $rel))) { $missing += $rel }
}
if ($missing.Count -gt 0) { Fail-Check $c1 ("없는 파일: " + ($missing -join ", ")) }
else { Add-Evidence $c1 "필수 파일 $($required.Count)개 모두 존재" }

Invoke-Step $c1 "백엔드 모듈 로드" $Backend "python" @("-c", "import app.main, app.api.runs, app.harness.orchestrator; print('modules ok')") | Out-Null
[void]$Checks.Add($c1)

# ---------------------------------------------------------------------------
# D1G-05 Writing Contract (서버 시작 전에 파일부터 확인)
# ---------------------------------------------------------------------------
Write-Head "D1G-05 Writing Contract manifest와 설정"
$c5 = New-Check "D1G-05" "Writing Contract manifest 1개 + 설정 4개"
$contractDir = Join-Path $Backend "app\writing_contracts\assembly_law_amendment_v1"
$contractFiles = @("contract.yaml", "profile.yaml", "template.yaml", "style.yaml", "validation.yaml")
$missingContract = @()
foreach ($f in $contractFiles) {
    if (-not (Test-Path (Join-Path $contractDir $f))) { $missingContract += $f }
}
if ($missingContract.Count -gt 0) { Fail-Check $c5 ("없는 설정 파일: " + ($missingContract -join ", ")) }
else { Add-Evidence $c5 "물리적 파일 5개 존재" }
Invoke-Step $c5 "서버가 Contract를 parse/schema 검사해 읽음" $Backend "python" @("-c", "from app.harness.contract_loader import load_writing_contract as l; c=l(); print(c.full_id)") | Out-Null
[void]$Checks.Add($c5)

# ---------------------------------------------------------------------------
# D1G-06 반복 가능한 코드 검사
# ---------------------------------------------------------------------------
Write-Head "D1G-06 반복 가능한 코드 검사"
$c6 = New-Check "D1G-06" "백엔드 시험 + 프런트 typecheck/test/build"
Invoke-Step $c6 "백엔드 pytest" $Backend "python" @("-m", "pytest", "../tests", "-q") | Out-Null
Invoke-Step $c6 "프런트 typecheck" $Frontend "npx" @("tsc", "--noEmit", "-p", "tsconfig.app.json") | Out-Null
Invoke-Step $c6 "프런트 vitest" $Frontend "npm" @("test", "--silent") | Out-Null
Invoke-Step $c6 "프런트 production build" $Frontend "npm" @("run", "build", "--silent") | Out-Null
[void]$Checks.Add($c6)

# ---------------------------------------------------------------------------
# D1G-02 한 주소에서 시작·종료
# ---------------------------------------------------------------------------
Write-Head "D1G-02 한 주소에서 시작·종료"
$c2 = New-Check "D1G-02" "127.0.0.1 한 주소의 health와 첫 화면"
$c3 = New-Check "D1G-03" "사용자 입력 네 가지"
$c4 = New-Check "D1G-04" "Harness 상태와 가짜 AI"
# D1G-07은 실행 중인 서버에서도 검사하므로 여기서 미리 만든다.
$c7 = New-Check "D1G-07" "비밀값·네트워크·도메인 청정성"

# 문서화된 실행법(start-local.cmd)이 실제로 도는지 확인한다.
# 비전공자가 쓰는 유일한 진입점이므로 여기서 깨지면 나머지가 다 소용없다.
#
# 실행기 프로세스가 콘솔을 붙잡고 안 끝나는 환경이 있으므로, 이 검사는
# 시간 제한이 걸린 별도 작업에서 돌린다. 검사기 자체가 멈추면 안 된다.
$startCmd = Join-Path $Root "start-local.cmd"
$stopCmd = Join-Path $Root "stop-local.cmd"

$cmdBytes = [System.IO.File]::ReadAllBytes($startCmd)
$crlf = 0
for ($i = 0; $i -lt $cmdBytes.Length - 1; $i++) {
    if ($cmdBytes[$i] -eq 13 -and $cmdBytes[$i + 1] -eq 10) { $crlf++ }
}
$lfTotal = ($cmdBytes | Where-Object { $_ -eq 10 }).Count
$lfOnly = $lfTotal - $crlf
if ($lfOnly -gt 0) {
    Fail-Check $c2 "start-local.cmd에 CRLF가 아닌 줄이 $($lfOnly)개 있습니다. cmd.exe가 한글 줄을 잘라 읽습니다."
} else {
    Add-Evidence $c2 "start-local.cmd 줄바꿈 CRLF $($crlf)개, LF 단독 0개"
}

$shortcutJob = Start-Job -ScriptBlock {
    param($root, $startCmd, $stopCmd, $logDir)
    $log = Join-Path $logDir "shortcut-out.log"
    $err = Join-Path $logDir "shortcut-err.log"
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$startCmd`"" `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError $err
    $ready = $false
    for ($k = 0; $k -lt 60; $k++) {
        try {
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2
            if ($h.status -eq "ok") { $ready = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 1000
    }
    $text = ""
    foreach ($f in @($log, $err)) {
        if (Test-Path $f) { $text += (Get-Content $f -Raw -Encoding UTF8 -ErrorAction SilentlyContinue) }
    }
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }

    # 문서화된 방법으로만 끈다.
    $stopProc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$stopCmd`"" `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru
    $stopProc.WaitForExit(30000) | Out-Null
    Start-Sleep -Milliseconds 1500
    $left = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue).Count

    [pscustomobject]@{ Ready = $ready; Output = $text; PortLeft = $left }
} -ArgumentList $Root, $startCmd, $stopCmd, $env:TEMP

$shortcut = $null
if (Wait-Job -Job $shortcutJob -Timeout 180) {
    $shortcut = Receive-Job -Job $shortcutJob
}
Remove-Job -Job $shortcutJob -Force -ErrorAction SilentlyContinue

if ($shortcut -eq $null) {
    Fail-Check $c2 "start-local.cmd 검사가 180초 안에 끝나지 않았습니다."
} else {
    [void]$c2.commands.Add([ordered]@{
        command   = "cmd /c start-local.cmd; cmd /c stop-local.cmd"
        exit_code = $(if ($shortcut.Ready) { 0 } else { 1 })
        summary   = ($shortcut.Output -split "`r?`n" | Where-Object { $_.Trim() -ne "" } | Select-Object -Last 1)
    })
    if ($shortcut.Output -match "is not recognized|\[멈춤\]") {
        Fail-Check $c2 "start-local.cmd 실행 중 오류 메시지가 나왔습니다."
    } elseif (-not $shortcut.Ready) {
        Fail-Check $c2 "start-local.cmd 뒤에도 8765가 응답하지 않습니다."
    } else {
        Add-Evidence $c2 "실행 바로가기로 8765의 /api/health 200"
    }
    if ($shortcut.PortLeft -gt 0) {
        Fail-Check $c2 "stop-local.cmd 뒤에도 8765 포트가 열려 있습니다."
    } else {
        Add-Evidence $c2 "stop-local.cmd로 자신이 시작한 서버만 정상 종료"
    }
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Fail-Check $c2 "$($Port) 포트를 이미 다른 프로그램이 쓰고 있습니다."
} else {
    $ServerProcess = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port", "--workers", "1") `
        -WorkingDirectory $Backend -PassThru -WindowStyle Hidden
    Add-Evidence $c2 "검증용 서버 시작 (PID $($ServerProcess.Id))"

    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 3
            if ($health.status -eq "ok") { $ready = $true; break }
        } catch { }
    }

    if (-not $ready) {
        Fail-Check $c2 "/api/health가 200을 돌려주지 않았습니다."
    } else {
        Add-Evidence $c2 "/api/health 200, contract=$($health.contract_id)"
        [void]$c2.commands.Add([ordered]@{ command = "GET $BaseUrl/api/health"; exit_code = 0; summary = "status=ok" })

        # 첫 화면이 같은 주소에서 로드되는지
        try {
            $index = Invoke-WebRequest -Uri "$BaseUrl/" -TimeoutSec 5 -UseBasicParsing
            if ($index.StatusCode -eq 200 -and $index.Content -match '<div id="root"') {
                Add-Evidence $c2 "첫 화면 200, #root 포함"
            } else {
                Fail-Check $c2 "첫 화면이 올바르게 로드되지 않았습니다."
            }
        } catch { Fail-Check $c2 "첫 화면 요청 실패: $($_.Exception.Message)" }

        # -------------------------------------------------------------------
        # D1G-03 / D1G-04 — 실제 API로 확인
        # -------------------------------------------------------------------
        Write-Head "D1G-03 사용자 입력 네 가지 / D1G-04 Harness와 가짜 AI"
        $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
        $boot = Invoke-RestMethod -Uri "$BaseUrl/api/bootstrap" -WebSession $session -TimeoutSec 5

        if ($health.model_gateway -ne "fake") { Fail-Check $c4 "가짜 ModelGateway가 아닙니다: $($health.model_gateway)" }
        else { Add-Evidence $c4 "가짜 ModelGateway 사용, 외부 호출 0회" }

        # 화면 소스에 네 입력이 있는지
        # UTF-8로 읽어야 한글 라벨이 깨지지 않는다.
        $newRunSrc = Get-Content (Join-Path $Frontend "src\screens\NewRunScreen.tsx") -Raw -Encoding UTF8
        $uiLabels = @("보도 목적", "공식 자료", "공개 범위", "자료 확인 기준일")
        $missingUi = @()
        foreach ($label in $uiLabels) { if ($newRunSrc -notmatch [regex]::Escape($label)) { $missingUi += $label } }
        if ($missingUi.Count -gt 0) { Fail-Check $c3 ("화면에 없는 입력: " + ($missingUi -join ", ")) }
        else { Add-Evidence $c3 "화면에 네 가지 입력 모두 존재" }

        # 잘못된 값은 거짓 성공 없이 막히는지
        $badBody = @{
            client_request_id = "verify-bad-$RunId"
            purpose = ""
            disclosure = "PUBLIC"
            basis_date = (Get-Date -Format "yyyy-MM-dd")
            sources = @()
            external_ai_policy_version = $boot.external_ai.policy_version
            external_ai_transfer_confirmed = $true
        } | ConvertTo-Json -Depth 6

        $badRun = Invoke-RestMethod -Uri "$BaseUrl/api/runs" -Method Post -Body $badBody `
            -ContentType "application/json" -WebSession $session -TimeoutSec 10
        $badId = $badRun.run_id
        $badState = $null
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Milliseconds 300
            $view = Invoke-RestMethod -Uri "$BaseUrl/api/runs/$badId" -WebSession $session -TimeoutSec 5
            if ($view.state -ne "CREATED" -and $view.state -ne "VALIDATING_INPUT") { $badState = $view.state; break }
        }
        if ($badState -ne "NEEDS_INPUT") { Fail-Check $c3 "빈 입력이 NEEDS_INPUT으로 막히지 않았습니다: $badState" }
        elseif ($view.draft_version -ne 0) { Fail-Check $c3 "빈 입력인데 초안이 생겼습니다." }
        else { Add-Evidence $c3 "빈 입력 -> NEEDS_INPUT, 초안 0건, 쉬운 안내 $($view.issues.Count)건" }

        if ($view.actual_model_calls -ne 0) { Fail-Check $c4 "입력 검사 단계에서 AI를 불렀습니다." }
        else { Add-Evidence $c4 "입력 Gate 단계 AI 호출 0회" }

        # 새 Run ID와 상태 계약
        if ($badId -notmatch "^RUN-[A-Z0-9]{12}$") { Fail-Check $c4 "실행 ID 형식이 계약과 다릅니다: $badId" }
        else { Add-Evidence $c4 "실행 ID 형식 확인: $badId" }

        $allowed = @("CREATED","VALIDATING_INPUT","NEEDS_INPUT","EXTRACTING_FACTS","DRAFTING",
                     "CHECKING_DRAFT","REVIEW_READY","REVISING","CHECKING_REVISION","DRAFT_READY","FAILED")
        if ($allowed -notcontains $view.state) { Fail-Check $c4 "계약에 없는 상태입니다: $($view.state)" }
        else { Add-Evidence $c4 "상태가 계약 안에 있음: $($view.state)" }

        # 멱등: 같은 키로 두 번 눌러도 Run은 하나
        $again = Invoke-RestMethod -Uri "$BaseUrl/api/runs" -Method Post -Body $badBody `
            -ContentType "application/json" -WebSession $session -TimeoutSec 10
        if ($again.run_id -ne $badId) { Fail-Check $c4 "같은 멱등 키로 Run이 두 개 생겼습니다." }
        else { Add-Evidence $c4 "같은 멱등 키는 기존 Run 재사용" }

        Invoke-RestMethod -Uri "$BaseUrl/api/runs/$badId" -Method Delete -WebSession $session -TimeoutSec 5 | Out-Null

        # 요청 쿠키 없이는 상태 변경이 거부되는지. 403이 아닌 오류는 통과로 보지 않는다.
        try {
            Invoke-RestMethod -Uri "$BaseUrl/api/runs" -Method Post -Body $badBody -ContentType "application/json" -TimeoutSec 5 | Out-Null
            Fail-Check $c3 "요청 쿠키 없이도 작업이 만들어졌습니다."
        } catch {
            $status = $null
            if ($_.Exception.Response -ne $null) { $status = [int]$_.Exception.Response.StatusCode }
            if ($status -eq 403) { Add-Evidence $c3 "쿠키 없는 요청은 403으로 거부됨" }
            else { Fail-Check $c3 "쿠키 없는 요청이 403이 아닌 결과로 끝났습니다: $status" }
        }

        # 실행 중인 서버에서 경로 탈출로 파일이 새어 나가는지 (정적 검사로는 못 잡는다)
        $indexLength = $index.Content.Length
        $escapePaths = @(
            "/../../.env", "/../../.gitignore", "/../../backend/app/main.py",
            "/%2e%2e/%2e%2e/.env", "/..%2f..%2f.env", "/assets/../../../.env"
        )
        $leaked = @()
        foreach ($p in $escapePaths) {
            try {
                $r = Invoke-WebRequest -Uri "$BaseUrl$p" -TimeoutSec 5 -UseBasicParsing
                # 첫 화면으로 되돌려 보내면 안전하다. 다른 내용이면 파일이 새어 나온 것이다.
                if ($r.StatusCode -eq 200 -and $r.Content -notmatch '<div id="root"') { $leaked += $p }
            } catch { }  # 404·403은 안전
        }
        if ($leaked.Count -gt 0) {
            Fail-Check $c7 ("경로 탈출로 빌드 폴더 밖 파일이 노출됩니다: " + ($leaked -join ", "))
        } else {
            Add-Evidence $c7 "경로 탈출 $($escapePaths.Count)종 모두 차단 (첫 화면 $indexLength bytes로 되돌림)"
        }
    }

    # 자신이 시작한 프로세스만 종료
    if ($ServerProcess -ne $null -and -not $ServerProcess.HasExited) {
        Stop-Process -Id $ServerProcess.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 800
        $still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($still) { Fail-Check $c2 "검증 서버 종료 뒤에도 $Port 포트가 열려 있습니다." }
        else { Add-Evidence $c2 "자신이 시작한 프로세스만 정상 종료" }
    }
}
[void]$Checks.Add($c2)
[void]$Checks.Add($c3)
[void]$Checks.Add($c4)

# ---------------------------------------------------------------------------
# D1G-07 비밀·네트워크·기록
# ---------------------------------------------------------------------------
Write-Head "D1G-07 비밀·네트워크·기록"

$scanRoots = @("backend\app", "frontend\src", "frontend\dist", "tests", "scripts", "verification")
$scanFiles = @()
foreach ($rel in $scanRoots) {
    $full = Join-Path $Root $rel
    if (Test-Path $full) {
        $scanFiles += Get-ChildItem -Path $full -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch "node_modules|__pycache__|\.venv" }
    }
}

# 빌드 결과물이 없으면 bundle을 검사하지 못한 것이므로 차단한다.
if (-not (Test-Path (Join-Path $Frontend "dist\index.html"))) {
    Fail-Check $c7 "frontend\dist가 없어 브라우저 bundle의 비밀값을 검사하지 못했습니다."
} else {
    Add-Evidence $c7 "브라우저 bundle 포함 검사"
}

# API 키 패턴
$keyHits = $scanFiles | Select-String -Pattern "sk-[A-Za-z0-9_\-]{20,}", "gho_[A-Za-z0-9]{20,}" -List -ErrorAction SilentlyContinue
if ($keyHits) { Fail-Check $c7 ("API 키 패턴 발견: " + (($keyHits | ForEach-Object { $_.Path }) -join ", ")) }
else { Add-Evidence $c7 "소스·bundle·보고서의 API 키 패턴 0건" }

# .env 를 코드가 읽지 않는지 (예시 파일과 이름 안내는 허용)
$envReaders = $scanFiles | Where-Object { $_.Extension -in @(".py", ".ts", ".tsx") } |
    Select-String -Pattern "load_dotenv|dotenv" -List -ErrorAction SilentlyContinue
if ($envReaders) { Fail-Check $c7 ("코드가 .env를 직접 읽습니다: " + (($envReaders | ForEach-Object { $_.Path }) -join ", ")) }
else { Add-Evidence $c7 "1일차 코드는 .env를 열지 않음" }

# wildcard CORS
$corsHits = $scanFiles | Where-Object { $_.Extension -eq ".py" } |
    Select-String -Pattern 'allow_origins\s*=\s*\[\s*"\*"' -List -ErrorAction SilentlyContinue
if ($corsHits) { Fail-Check $c7 "wildcard CORS가 있습니다." }
else { Add-Evidence $c7 "wildcard CORS 0건" }

# 제품 코드만 본다. 검증 시험 파일은 금지어 목록 자체를 담고 있으므로 제외한다.
$productFiles = $scanFiles | Where-Object {
    $_.FullName -match "\\backend\\app\\|\\frontend\\src\\|\\frontend\\dist\\"
}

# 실제 OpenAI 호출 경로가 없는지 (1일차는 가짜만 사용)
$sdkHits = $productFiles | Where-Object { $_.Extension -eq ".py" } |
    Select-String -Pattern "from openai|import openai|api\.openai\.com" -List -ErrorAction SilentlyContinue
if ($sdkHits) { Fail-Check $c7 ("1일차 제품 코드에 실제 OpenAI 호출 경로: " + (($sdkHits | ForEach-Object { $_.Path }) -join ", ")) }
else { Add-Evidence $c7 "실제 OpenAI 호출 경로 0건, 비용 0달러" }

# 생명과학 전용 잔재. fastapi 안의 fasta 같은 부분일치를 막으려고 경계를 둔다.
$bioPattern = "pubmed|pubtator|scispacy|biopython|pyrosetta|wet-?lab|\bassay\b|\bfasta\b|mesh_term"
$bioHits = $productFiles | Where-Object { $_.Extension -in @(".py", ".ts", ".tsx", ".yaml", ".json") } |
    Select-String -Pattern $bioPattern -List -ErrorAction SilentlyContinue
if ($bioHits) { Fail-Check $c7 ("생명과학 전용 잔재: " + (($bioHits | ForEach-Object { $_.Path }) -join ", ")) }
else { Add-Evidence $c7 "생명과학 전용 요소 0건" }

# dangerouslySetInnerHTML 금지
$xssHits = $scanFiles | Where-Object { $_.Extension -in @(".ts", ".tsx") } |
    Select-String -Pattern "dangerouslySetInnerHTML" -List -ErrorAction SilentlyContinue
if ($xssHits) { Fail-Check $c7 "dangerouslySetInnerHTML을 사용했습니다." }
else { Add-Evidence $c7 "dangerouslySetInnerHTML 0건" }

[void]$Checks.Add($c7)

# ---------------------------------------------------------------------------
# 소스 snapshot (.env·비밀·node_modules·build·보고서 제외)
# ---------------------------------------------------------------------------
Write-Head "소스 snapshot"
$snapRoots = @("backend\app", "frontend\src", "tests", "scripts", "verification\day-gate-verifier.md",
               "verification\day-gate-result.schema.json", "start-local.cmd", "stop-local.cmd")
$snapFiles = @()
foreach ($rel in $snapRoots) {
    $full = Join-Path $Root $rel
    if (Test-Path $full -PathType Leaf) { $snapFiles += Get-Item $full }
    elseif (Test-Path $full) {
        $snapFiles += Get-ChildItem -Path $full -Recurse -File |
            Where-Object { $_.FullName -notmatch "node_modules|__pycache__|\.venv|\\reports\\|\.env" }
    }
}
$snapEntries = New-Object System.Collections.ArrayList
$combined = New-Object System.Text.StringBuilder
foreach ($f in ($snapFiles | Sort-Object FullName)) {
    $hash = (Get-FileHash -Path $f.FullName -Algorithm SHA256).Hash.ToLower()
    $rel = $f.FullName.Substring($Root.Length + 1).Replace("\", "/")
    [void]$snapEntries.Add([ordered]@{ path = $rel; sha256 = $hash })
    [void]$combined.Append("$rel`:$hash`n")
}
$combinedBytes = [System.Text.Encoding]::UTF8.GetBytes($combined.ToString())
$sha = [System.Security.Cryptography.SHA256]::Create()
$combinedHash = -join ($sha.ComputeHash($combinedBytes) | ForEach-Object { $_.ToString("x2") })
Write-Host "    - 소스 파일 $($snapEntries.Count)개, 통합 해시 $($combinedHash.Substring(0,16))..."

# ---------------------------------------------------------------------------
# 판정과 보고서
# ---------------------------------------------------------------------------
$verdict = "PASS"
foreach ($c in $Checks) { if ($c.status -ne "PASS") { $verdict = "BLOCKED" } }
if ($Blockers.Count -gt 0) { $verdict = "BLOCKED" }
if ($Checks.Count -lt 7) { $verdict = "BLOCKED" }

$readmeVersion = "unknown"
$readmeHead = Get-Content (Join-Path $Root "README.md") -TotalCount 5 -Encoding UTF8
foreach ($line in $readmeHead) {
    if ($line -match "문서 버전:\s*(\S+)") { $readmeVersion = $Matches[1] }
}

$report = [ordered]@{
    gate_id            = "DEMO-DAY1-GATE"
    run_id             = $RunId
    readme_version     = $readmeVersion
    started_at         = $StartedAt
    completed_at       = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    verifier           = [ordered]@{
        context = "scripts/verify-day1.ps1 결정형 검사"
        is_separate_from_implementer = $false
    }
    source_snapshot    = [ordered]@{
        file_count      = $snapEntries.Count
        combined_sha256 = $combinedHash
        files           = @($snapEntries)
    }
    verdict            = $verdict
    checks             = @($Checks | ForEach-Object {
        [ordered]@{
            check_id = $_.check_id
            title    = $_.title
            status   = $_.status
            commands = @($_.commands)
            evidence = @($_.evidence)
        }
    })
    blockers           = @($Blockers)
    warnings           = @($Warnings)
    external_api_calls = 0
    estimated_cost_usd = 0
}

if (-not (Test-Path $ReportDir)) { New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null }
$jsonPath = Join-Path $ReportDir "$RunId.json"
$mdPath = Join-Path $ReportDir "$RunId.md"
if (Test-Path $jsonPath) { throw "같은 run ID의 보고서가 이미 있습니다. 기존 보고서를 덮어쓰지 않습니다: $jsonPath" }

$report | ConvertTo-Json -Depth 8 | Out-File -FilePath $jsonPath -Encoding utf8

$md = New-Object System.Collections.ArrayList
[void]$md.Add("# 개발 1일차 -> 2일차 검증 보고서")
[void]$md.Add("")
[void]$md.Add("- 관문: ``DEMO-DAY1-GATE``")
[void]$md.Add("- run ID: ``$RunId``")
[void]$md.Add("- README 버전: $readmeVersion")
[void]$md.Add("- 시작: $StartedAt / 종료: $($report.completed_at)")
[void]$md.Add("- 소스 파일 $($snapEntries.Count)개, 통합 해시 ``$combinedHash``")
[void]$md.Add("- 외부 AI 호출 0회, API 비용 0달러")
[void]$md.Add("")
[void]$md.Add("## 판정: $verdict")
[void]$md.Add("")
[void]$md.Add("| 검사 | 내용 | 결과 |")
[void]$md.Add("|---|---|---|")
foreach ($c in $Checks) {
    $mark = "PASS"
    if ($c.status -ne "PASS") { $mark = $c.status }
    [void]$md.Add("| ``$($c.check_id)`` | $($c.title) | $mark |")
}
[void]$md.Add("")
foreach ($c in $Checks) {
    [void]$md.Add("### $($c.check_id) $($c.title) — $($c.status)")
    [void]$md.Add("")
    foreach ($e in $c.evidence) { [void]$md.Add("- $e") }
    foreach ($cmd in $c.commands) { [void]$md.Add("- ``$($cmd.command)`` -> exit $($cmd.exit_code)") }
    [void]$md.Add("")
}
if ($Blockers.Count -gt 0) {
    [void]$md.Add("## 차단 문제")
    [void]$md.Add("")
    foreach ($b in $Blockers) { [void]$md.Add("- ``$($b.check_id)``: $($b.message)") }
    [void]$md.Add("")
}
if ($Warnings.Count -gt 0) {
    [void]$md.Add("## 경고")
    [void]$md.Add("")
    foreach ($w in $Warnings) { [void]$md.Add("- $w") }
    [void]$md.Add("")
}
[void]$md.Add("---")
[void]$md.Add("")
[void]$md.Add("이 보고서는 결정형 script의 결과다. README 2.14.1에 따라 **구현 담당자와 다른 새 문맥의**")
[void]$md.Add("검사원이 이 결과를 직접 확인하고 최종 판정을 남겨야 개발 2일차를 시작할 수 있다.")
$md -join "`n" | Out-File -FilePath $mdPath -Encoding utf8

Write-Head "판정: $verdict"
Write-Host "  보고서: $jsonPath"
Write-Host "  보고서: $mdPath"
if ($verdict -ne "PASS") { exit 1 }
exit 0
