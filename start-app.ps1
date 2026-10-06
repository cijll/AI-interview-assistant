<#
    AI 模拟面试系统 —— 一键启动 / 停止 / 状态

    常用用法：
        双击  ..\启动应用.cmd              # 一键启动（推荐）
        双击  ..\停止应用.cmd              # 一键停止

    命令行用法：
        .\start-app.ps1                    启动前后端并打开浏览器
        .\start-app.ps1 -Stop              停止本脚本启动过的服务
        .\start-app.ps1 -Status            查看运行状态
        .\start-app.ps1 -Reload            后端开启热重载（改 .py 自动重启）
        .\start-app.ps1 -BackendOnly       只启动后端
        .\start-app.ps1 -FrontendOnly      只启动前端
        .\start-app.ps1 -NoBrowser         启动但不打开浏览器

    说明：
        - 前后端都在后台运行，不占用额外窗口，输出写入项目根目录的 logs\ 。
        - 如果端口已经在监听，会跳过对应服务，直接复用（不会重复启动）。
        - 脚本退出不会关掉服务；要停服务请用 -Stop 或 停止应用.cmd。
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Status,
    [switch]$Reload,
    [switch]$NoBrowser,
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------- 路径与常量
$ProjectRoot  = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir   = Join-Path $ProjectRoot "backend"
$FrontendDir  = Join-Path $ProjectRoot "frontend"
$PythonExe    = Join-Path $BackendDir ".venv\Scripts\python.exe"
$EnvFile      = Join-Path $BackendDir ".env"
$LogDir       = Join-Path $ProjectRoot "logs"
$StateFile    = Join-Path $LogDir "services.json"

$BackendPort  = 8000
$FrontendPort = 5173
$BackendUrl   = "http://127.0.0.1:$BackendPort"
$HealthUrl    = "$BackendUrl/api/health"
$FrontendUrl  = "http://127.0.0.1:$FrontendPort/"

# ---------------------------------------------------------------- 输出辅助
function Say-Ok    { param([string]$m) Write-Host "  [OK]   $m" -ForegroundColor Green }
function Say-Warn  { param([string]$m) Write-Host "  [注意] $m" -ForegroundColor Yellow }
function Say-Err   { param([string]$m) Write-Host "  [错误] $m" -ForegroundColor Red }
function Say-Info  { param([string]$m) Write-Host "  $m" }
function Say-Dim   { param([string]$m) Write-Host "  $m" -ForegroundColor DarkGray }

# ---------------------------------------------------------------- 工具函数
function Test-PortOpen {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(400, $false)) {
            $client.EndConnect($iar)
            return $true
        }
        return $false
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-State {
    if (Test-Path -LiteralPath $StateFile) {
        try { return (Get-Content -Raw -Encoding UTF8 -LiteralPath $StateFile | ConvertFrom-Json) }
        catch { return $null }
    }
    return $null
}

function Save-State {
    param($State)
    if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
    $State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StateFile -Encoding UTF8
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    # 用 taskkill /T 连同子进程一起结束（cmd.exe -> node 这种父子关系，
    # 单纯 Stop-Process 会留下孤儿的 node 进程继续占用端口）。
    & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null
    return $LASTEXITCODE -eq 0
}

# ---------------------------------------------------------------- 停止
function Invoke-Stop {
    Write-Host ""
    Write-Host "停止服务" -ForegroundColor Cyan
    $stopped = 0

    $state = Get-State
    if ($state -and $state.services) {
        foreach ($svc in $state.services) {
            $proc = Get-Process -Id $svc.pid -ErrorAction SilentlyContinue
            if ($proc) {
                if (Stop-ProcessTree -ProcessId $svc.pid) {
                    Say-Ok "已停止 $($svc.name) (pid=$($svc.pid))"
                    $stopped++
                } else {
                    Say-Warn "$($svc.name) (pid=$($svc.pid)) 停止失败，可能已经退出"
                }
            }
        }
    }

    # 兜底：清掉仍占着本项目端口的进程（比如上次没用脚本启动的）
    foreach ($port in @($BackendPort, $FrontendPort)) {
        $ownerIds = @()
        try {
            $ownerIds = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
                        Select-Object -ExpandProperty OwningProcess -Unique
        } catch { }
        foreach ($ownerId in $ownerIds) {
            if (-not $ownerId) { continue }
            $proc = Get-Process -Id $ownerId -ErrorAction SilentlyContinue
            if ($proc) {
                if (Stop-ProcessTree -ProcessId $ownerId) {
                    Say-Ok "已停止占用 $port 端口的 $($proc.ProcessName) (pid=$ownerId)"
                    $stopped++
                }
            }
        }
    }

    if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile -Force }

    Write-Host ""
    if ($stopped -eq 0) { Say-Warn "没有发现正在运行的服务。" } else { Say-Ok "共停止 $stopped 个进程。" }
    Write-Host ""
    return 0
}

# ---------------------------------------------------------------- 状态
function Show-Status {
    Write-Host ""
    Write-Host "服务状态" -ForegroundColor Cyan

    $beUp = Test-PortOpen $BackendPort
    $feUp = Test-PortOpen $FrontendPort

    if ($beUp) { Say-Ok   "后端  $BackendPort 运行中   $BackendUrl/docs" }
    else       { Say-Warn "后端  $BackendPort 未运行" }

    if ($feUp) { Say-Ok   "前端  $FrontendPort 运行中   $FrontendUrl" }
    else       { Say-Warn "前端  $FrontendPort 未运行" }

    if ($beUp) {
        try {
            $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 5
            Say-Info ("健康检查: " + ($health | ConvertTo-Json -Compress))
        } catch {
            Say-Warn "后端端口在监听，但 /api/health 无响应。"
        }
    }

    $state = Get-State
    if ($state -and $state.services) {
        Write-Host ""
        Say-Dim "本次启动记录的进程："
        foreach ($svc in $state.services) {
            $alive = [bool](Get-Process -Id $svc.pid -ErrorAction SilentlyContinue)
            $mark = "已退出"
            if ($alive) { $mark = "运行中" }
            Say-Dim ("  {0,-9} pid={1,-7} {2}   日志: {3}" -f $svc.name, $svc.pid, $mark, $svc.log)
        }
    }
    Write-Host ""
}

# ---------------------------------------------------------------- 启动
# 关键点：这里刻意 *不* 使用 Start-Process 的 -RedirectStandardOutput/-RedirectStandardError。
# 一旦使用那两个参数，Start-Process 会切到 UseShellExecute=$false，子进程就会继承
# 启动者的控制台；启动窗口一关，子进程会被 CTRL_CLOSE_EVENT 一起带走。
# 改成「独立隐藏控制台 + 由 cmd.exe 自己做 > 重定向」后，服务不会随启动窗口退出而终止。
function Start-BackendService {
    if (Test-PortOpen $BackendPort) {
        Say-Warn "后端 $BackendPort 已在监听，跳过启动。"
        return $null
    }
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        throw "找不到后端虚拟环境：$PythonExe`n请先按 README 安装后端依赖。"
    }

    $errLog = Join-Path $LogDir "backend.err.log"
    $reloadFlag = ""
    if ($Reload) { $reloadFlag = " --reload" }

    # 用相对路径，避免项目路径里出现空格时的引号问题（工作目录已由 -WorkingDirectory 指定）
    $inner = ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort$reloadFlag > ..\logs\backend.out.log 2> ..\logs\backend.err.log"

    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $inner) `
        -WorkingDirectory $BackendDir -WindowStyle Hidden -PassThru

    Say-Ok "后端已启动 (pid=$($proc.Id))"
    return [pscustomobject]@{ name = "backend"; pid = $proc.Id; log = $errLog }
}

function Start-FrontendService {
    if (Test-PortOpen $FrontendPort) {
        Say-Warn "前端 $FrontendPort 已在监听，跳过启动。"
        return $null
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "找不到 npm，请先安装 Node.js 并确保它在 PATH 中。"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir "node_modules"))) {
        throw "找不到前端依赖，请先在 frontend 目录执行 npm install。"
    }

    $errLog = Join-Path $LogDir "frontend.err.log"

    # npm 是 .cmd 脚本，必须经由 cmd.exe 启动；同样让 cmd 负责重定向。
    $inner = "npm run dev -- --host 127.0.0.1 > ..\logs\frontend.out.log 2> ..\logs\frontend.err.log"

    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $inner) `
        -WorkingDirectory $FrontendDir -WindowStyle Hidden -PassThru

    Say-Ok "前端已启动 (pid=$($proc.Id))"
    return [pscustomobject]@{ name = "frontend"; pid = $proc.Id; log = $errLog }
}

function Wait-Ready {
    param(
        [string]$Url,
        [int]$TimeoutSeconds,
        [string]$Label,
        [string]$ErrLog
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 700
    }

    Say-Err "$Label 在 $TimeoutSeconds 秒内没有就绪。"
    if ($ErrLog -and (Test-Path -LiteralPath $ErrLog)) {
        Say-Dim "---- $([System.IO.Path]::GetFileName($ErrLog)) 末尾 15 行 ----"
        Get-Content -LiteralPath $ErrLog -Tail 15 | ForEach-Object { Say-Dim "    $_" }
    }
    return $false
}

# ---------------------------------------------------------------- 主流程
Write-Host ""
Write-Host "AI 模拟面试系统" -ForegroundColor Cyan
Say-Dim "项目目录：$ProjectRoot"
Write-Host ""

if ($Stop)   { exit (Invoke-Stop) }
if ($Status) { Show-Status; exit 0 }

if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Say-Warn "没有找到 backend\.env，将使用本地降级模式，无法调用百炼真实模型。"
}

$started  = @()
$failed   = @()

if (-not $FrontendOnly) {
    try {
        $svc = Start-BackendService
        if ($svc) { $started += $svc }
    } catch {
        Say-Err $_.Exception.Message
        $failed += "backend"
    }
}

if (-not $BackendOnly) {
    try {
        $svc = Start-FrontendService
        if ($svc) { $started += $svc }
    } catch {
        Say-Err $_.Exception.Message
        $failed += "frontend"
    }
}

if ($started.Count -gt 0) {
    Save-State -State ([pscustomobject]@{
        started_at = (Get-Date).ToString("s")
        services   = $started
    })
}

# 等待就绪（只等本次真正启动的服务；已在运行的直接视为就绪）
if ($started.Count -gt 0) {
    Write-Host ""
    Write-Host "等待服务就绪..." -ForegroundColor Cyan

    $beStarted = @($started | Where-Object { $_.name -eq "backend" }).Count -gt 0
    $feStarted = @($started | Where-Object { $_.name -eq "frontend" }).Count -gt 0

    if ($beStarted) {
        if (Wait-Ready -Url $HealthUrl -TimeoutSeconds 60 -Label "后端" `
                      -ErrLog (Join-Path $LogDir "backend.err.log")) {
            Say-Ok "后端就绪  $HealthUrl"
        } else {
            $failed += "backend"
        }
    } elseif (-not $FrontendOnly) {
        Say-Ok "后端就绪  $HealthUrl"
    }

    if ($feStarted) {
        if (Wait-Ready -Url $FrontendUrl -TimeoutSeconds 90 -Label "前端" `
                      -ErrLog (Join-Path $LogDir "frontend.err.log")) {
            Say-Ok "前端就绪  $FrontendUrl"
        } else {
            $failed += "frontend"
        }
    } elseif (-not $BackendOnly) {
        Say-Ok "前端就绪  $FrontendUrl"
    }
}

$browseTarget = $FrontendUrl
if ($BackendOnly) { $browseTarget = "$BackendUrl/docs" }

Write-Host ""
$failed = @($failed | Select-Object -Unique)

if ($failed.Count -gt 0) {
    Say-Err "启动未成功：$($failed -join '、')"
    Write-Host ""
    Say-Dim "日志目录：$LogDir"
    Say-Dim "查看后端日志：Get-Content '$LogDir\backend.err.log' -Wait"
    Say-Dim "查看前端日志：Get-Content '$LogDir\frontend.err.log' -Wait"
    Write-Host ""
    exit 1
}

if (-not $NoBrowser) {
    Start-Process $browseTarget
    Say-Ok "已在浏览器打开 $browseTarget"
}

Write-Host ""
Write-Host "全部就绪，服务在后台运行。" -ForegroundColor Green
if ($BackendOnly) {
    Say-Dim "接口文档：$BackendUrl/docs"
} else {
    Say-Dim "页面地址：$FrontendUrl"
    Say-Dim "接口文档：$BackendUrl/docs"
}
Say-Dim "停止服务：双击 停止应用.cmd，或执行 .\start-app.ps1 -Stop"
Write-Host ""
exit 0
