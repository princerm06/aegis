<#
.SYNOPSIS
  One command to host the whole AEGIS demo on this Windows laptop (SIMULATION ONLY).

.DESCRIPTION
  This laptop runs the AEGIS-RESPONDER hotspot and is always 192.168.137.1 on it.
  Teammates and both Arduinos join that hotspot. This script starts, in order:

    backend     http://192.168.137.1:8000        (hotspot address only)
    voice       http://127.0.0.1:8010            (loopback; reached through the dashboard)
    dashboard   http://192.168.137.1:3000        (teammates)
                http://localhost:3000            (this laptop; the browser microphone needs localhost)
    bridge 1    192.168.137.1:7777 -> responder-001
    bridge 2    192.168.137.1:7778 -> responder-002

  Nothing listens on the campus network: every service is bound to the hotspot
  address or to loopback, never 0.0.0.0. The backend has no authentication.

  Each service writes a log to hardware/logs/demo-host/<name>.log and the script
  finishes with health checks. Run it again with -Status or -Stop at any time.

.EXAMPLE
  .\hardware\scripts\start-demo-host.ps1            # start everything, then check health
.EXAMPLE
  .\hardware\scripts\start-demo-host.ps1 -Status    # health checks only
.EXAMPLE
  .\hardware\scripts\start-demo-host.ps1 -Stop      # stop everything this script started
.EXAMPLE
  .\hardware\scripts\start-demo-host.ps1 -Prepare   # also load the two-actor demo scene (resets the scenario)
#>
param(
    [switch]$Status,
    [switch]$Stop,
    [switch]$Prepare,                 # run scripts.live_demo --prepare-only after the backend is up
    [switch]$Rebuild,                 # force a dashboard rebuild
    [switch]$Neural,                  # enable the neural shadow advisory (needs the weights folder)
    [switch]$NoBridges,
    [switch]$NoDashboard,
    [switch]$NoVoice,
    [string]$HostIp = "192.168.137.1",
    [int]$BackendPort = 8000,
    [int]$DashboardPort = 3000,
    [int]$VoicePort = 8010
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$logDir = Join-Path $repoRoot "hardware\logs\demo-host"
$pidFile = Join-Path $logDir "pids.json"
$backendUrl = "http://${HostIp}:$BackendPort"
$failed = 0

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Ok($text) { Say "  [ok]   $text" "Green" }
function Bad($text) { Say "  [FAIL] $text" "Red"; $script:failed++ }
function Note($text) { Say "  [--]   $text" "DarkYellow" }

function Read-Pids {
    if (-not (Test-Path $pidFile)) { return @() }
    $parsed = Get-Content $pidFile -Raw | ConvertFrom-Json
    return @($parsed | ForEach-Object { $_ })
}

function Test-Url($url, $timeout = 4) {
    try {
        $r = Invoke-WebRequest -Uri $url -TimeoutSec $timeout -UseBasicParsing
        return $r.StatusCode -ge 200 -and $r.StatusCode -lt 400
    } catch { return $false }
}

function Wait-Url($url, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Url $url 3) { return $true }
        Start-Sleep -Milliseconds 800
    }
    return $false
}

function Port-Owner($address, $port) {
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @($address, "0.0.0.0", "::") } | Select-Object -First 1
}

function Start-Service-Logged($name, $commandLine, $workDir, $envVars) {
    # cmd /c so stdout and stderr land in one log; the PID is the cmd wrapper and
    # -Stop kills its whole process tree.
    $log = Join-Path $logDir "$name.log"
    foreach ($key in $envVars.Keys) { Set-Item -Path "env:$key" -Value $envVars[$key] }
    try {
        $proc = Start-Process -FilePath "cmd.exe" -WorkingDirectory $workDir -WindowStyle Hidden -PassThru `
            -ArgumentList "/c", "$commandLine > `"$log`" 2>&1 < NUL"
    } finally {
        foreach ($key in $envVars.Keys) { Remove-Item -Path "env:$key" -ErrorAction SilentlyContinue }
    }
    $script:started += [pscustomobject]@{ name = $name; pid = $proc.Id; log = $log }
    Say "  started $name (pid $($proc.Id)), log: $log"
}

function Stop-All {
    $entries = Read-Pids
    if (-not $entries) { Say "Nothing recorded as running."; return }
    foreach ($e in $entries) {
        if (Get-Process -Id $e.pid -ErrorAction SilentlyContinue) {
            & taskkill /PID $e.pid /T /F 2>$null | Out-Null
            Say "  stopped $($e.name) (pid $($e.pid))"
        }
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

function Show-Health {
    Say ""
    Say "=== Health (SIMULATION ONLY) ===" "Cyan"
    if (Get-NetIPAddress -IPAddress $HostIp -ErrorAction SilentlyContinue) { Ok "hotspot address $HostIp is up" }
    else { Bad "hotspot address $HostIp is not up: run hardware\scripts\start-hotspot.ps1" }

    if (Test-Url "$backendUrl/health") { Ok "backend    $backendUrl" } else { Bad "backend    $backendUrl not answering (backend.log)" }
    if (-not $NoVoice) {
        if (Test-Url "http://127.0.0.1:$VoicePort/health") { Ok "voice      http://127.0.0.1:$VoicePort" }
        else { Bad "voice      http://127.0.0.1:$VoicePort not answering (voice.log)" }
    }
    if (-not $NoDashboard) {
        foreach ($h in @($HostIp, "127.0.0.1")) {
            if (Test-Url "http://${h}:$DashboardPort/command" 8) { Ok "dashboard  http://${h}:$DashboardPort/command" }
            else { Bad "dashboard  http://${h}:$DashboardPort not answering (dashboard-*.log)" }
        }
        if (Test-Url "http://${HostIp}:$DashboardPort/aegis-api/health" 8) { Ok "dashboard -> backend proxy" }
        else { Bad "dashboard cannot reach the backend through /aegis-api" }
    }
    if (-not $NoBridges) {
        foreach ($n in 1, 2) {
            $port = 7777 + $n - 1
            if (-not (Port-Owner $HostIp $port)) { Bad "bridge $n   not listening on ${HostIp}:$port (bridge-$n.log)"; continue }
            $link = Get-NetTCPConnection -State Established -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($link) { Ok "bridge $n   ${HostIp}:$port, device connected from $($link.RemoteAddress)" }
            else { Note "bridge $n   ${HostIp}:$port listening, no device connected yet (device shows '--')" }
        }
    }
    if (-not $NoDashboard) {
        if (Test-Url "http://127.0.0.1:$BackendPort/health") { Ok "localhost:$BackendPort -> backend (live updates on this laptop)" }
        else { Note "localhost:$BackendPort not forwarded: live updates will not work on http://localhost:$DashboardPort (run apply-demo-network.ps1 as Administrator)" }
    }
    $tsExe = "C:\Program Files\Tailscale\tailscale.exe"
    $tsIp = if (Test-Path $tsExe) { (& $tsExe ip -4 2>$null | Select-Object -First 1) } else { $null }
    if ($tsIp) {
        $tsIp = $tsIp.Trim()
        if ((Test-Url "http://${tsIp}:$BackendPort/health") -and (Test-Url "http://${tsIp}:$DashboardPort/command" 8)) {
            Ok "Tailscale  http://${tsIp}:$DashboardPort/command and :$BackendPort (laptops on campus WiFi)"
        } else { Note "Tailscale address $tsIp is not forwarded (run apply-demo-network.ps1 as Administrator)" }
    }
    $mtu = (Get-NetIPInterface -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceIndex -eq (Get-NetIPAddress -IPAddress $HostIp -ErrorAction SilentlyContinue).InterfaceIndex }).NlMtu
    if ($mtu -and $mtu -gt 1400) { Note "hotspot MTU is ${mtu}: large replies may stall for some laptops (run apply-demo-network.ps1 as Administrator)" }
    $rule = Get-NetFirewallRule -DisplayName "AEGIS demo host" -ErrorAction SilentlyContinue
    if ($rule) { Ok "firewall rule 'AEGIS demo host' present" }
    else { Note "firewall rule 'AEGIS demo host' missing: other laptops may be blocked (run once as Administrator)" }

    Say ""
    if ($tsIp) {
        Say "Teammates' laptops (campus WiFi + Tailscale; keep them off the hotspot):" "Cyan"
        Say "  dashboard  http://${tsIp}:$DashboardPort/command"
        Say "  camera/CV  --backend-url http://${tsIp}:$BackendPort"
    } else {
        Say "Teammates (joined to the hotspot):" "Cyan"
        Say "  dashboard  http://${HostIp}:$DashboardPort/command"
        Say "  camera/CV  --backend-url $backendUrl"
    }
    Say "This laptop: http://localhost:$DashboardPort/command (push-to-talk works here)"
    if ($failed) { Say "$failed check(s) failed." "Red" } else { Say "All checks passed." "Green" }
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if ($Stop) { Stop-All; exit 0 }
if ($Status) { Show-Health; exit ([int]($failed -gt 0)) }

Say "=== AEGIS demo host (SIMULATION ONLY) ===" "Cyan"

# 0. Anything from a previous run is stopped first, so a re-run is always clean.
if (Read-Pids) { Say "Stopping the previous run..."; Stop-All }

# 1. Hotspot.
if (-not (Get-NetIPAddress -IPAddress $HostIp -ErrorAction SilentlyContinue)) {
    Say "Starting the hotspot..."
    & (Join-Path $PSScriptRoot "start-hotspot.ps1")
}
if (-not (Get-NetIPAddress -IPAddress $HostIp -ErrorAction SilentlyContinue)) {
    Say "ERROR: $HostIp is not up; the hotspot did not start." "Red"; exit 1
}

# 2. Firewall: backend and dashboard reachable from hotspot devices only.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$ports = @("$BackendPort", "$DashboardPort")
$rule = Get-NetFirewallRule -DisplayName "AEGIS demo host" -ErrorAction SilentlyContinue
if ($admin) {
    if ($rule) { $rule | Remove-NetFirewallRule }
    New-NetFirewallRule -DisplayName "AEGIS demo host" -Direction Inbound -Protocol TCP -Action Allow `
        -LocalPort $ports -LocalAddress $HostIp -RemoteAddress "192.168.137.0/24" | Out-Null
    Say "Firewall: TCP $($ports -join ', ') allowed from hotspot devices only."
} elseif (-not $rule) {
    Say "Firewall rule not added (not an Administrator shell). Run this script once as Administrator," "Yellow"
    Say "or other laptops may not reach the backend and dashboard." "Yellow"
}

# 3. Refuse to start on top of something else.
$wanted = @(@{ a = $HostIp; p = $BackendPort; n = "backend" })
if (-not $NoVoice) { $wanted += @{ a = "127.0.0.1"; p = $VoicePort; n = "voice" } }
if (-not $NoDashboard) { $wanted += @{ a = $HostIp; p = $DashboardPort; n = "dashboard" }; $wanted += @{ a = "127.0.0.1"; p = $DashboardPort; n = "dashboard" } }
if (-not $NoBridges) { $wanted += @{ a = $HostIp; p = 7777; n = "bridge 1" }; $wanted += @{ a = $HostIp; p = 7778; n = "bridge 2" } }
$busy = @()
foreach ($w in $wanted) {
    $owner = Port-Owner $w.a $w.p
    if ($owner) { $busy += "$($w.n): $($owner.LocalAddress):$($w.p) is used by pid $($owner.OwningProcess)" }
}
if ($busy) {
    Say "ERROR: these ports are already in use. Close those programs (old bridge or backend windows) and retry:" "Red"
    $busy | ForEach-Object { Say "  $_" "Red" }
    exit 1
}

$started = @()

# 4. Backend.
$backendEnv = @{}
$uvExtra = ""
if ($Neural) {
    if (Test-Path (Join-Path $repoRoot "experiments\neural_assignment\weights")) {
        $backendEnv["AEGIS_NEURAL_SHADOW_ENABLED"] = "true"
        $uvExtra = "--with torch --with numpy "
    } else {
        Say "Neural weights folder is missing; starting without the neural advisory." "Yellow"
    }
}
Start-Service-Logged "backend" "uv run ${uvExtra}uvicorn backend.app.main:app --host $HostIp --port $BackendPort" $repoRoot $backendEnv
if (-not (Wait-Url "$backendUrl/health" 90)) {
    Say "ERROR: the backend did not come up. See $logDir\backend.log" "Red"
    ConvertTo-Json -InputObject @($started) | Set-Content $pidFile -Encoding utf8
    exit 1
}
if ($Prepare) {
    Say "Loading the demo scene (--prepare-only)..."
    Push-Location $repoRoot
    try { & uv run python -m scripts.live_demo --base-url $backendUrl --prepare-only *> (Join-Path $logDir "prepare.log") }
    finally { Pop-Location }
}

# 5. Voice (loopback; teammates reach it through the dashboard's /aegis-voice proxy).
if (-not $NoVoice) {
    if (-not (Test-Path (Join-Path $repoRoot "voice\.env"))) {
        Say "voice\.env is missing: Voice runs in text-only mode until the ElevenLabs key is added." "Yellow"
    }
    Start-Service-Logged "voice" "uv run uvicorn voice.server:app --host 127.0.0.1 --port $VoicePort" $repoRoot @{
        AEGIS_BACKEND_URL = $backendUrl }
}

# 6. Dashboard: production build, served on the hotspot address and on localhost.
if (-not $NoDashboard) {
    $frontend = Join-Path $repoRoot "frontend"
    # No fixed WebSocket URL: the dashboard connects to port 8000 on the host name
    # the page was loaded from (hotspot address, Tailscale address or localhost).
    # apply-demo-network.ps1 makes the backend answer on the last two.
    $dashEnv = @{
        AEGIS_BACKEND_URL = $backendUrl
        AEGIS_VOICE_URL   = "http://127.0.0.1:$VoicePort"
    }
    # The proxy targets are baked in at build time, so rebuild when they or the
    # frontend sources change.
    $head = (& git -C $repoRoot log -1 --format=%H -- frontend 2>$null)
    $stamp = "$backendUrl|$VoicePort|$head|ws-by-page-host"
    $stampFile = Join-Path $frontend ".next\aegis-demo-host.txt"
    $current = if (Test-Path $stampFile) { (Get-Content $stampFile -Raw).Trim() } else { "" }
    if ($Rebuild -or $current -ne $stamp -or -not (Test-Path (Join-Path $frontend ".next\BUILD_ID"))) {
        Say "Building the dashboard (about a minute)..."
        foreach ($key in $dashEnv.Keys) { Set-Item -Path "env:$key" -Value $dashEnv[$key] }
        Push-Location $frontend
        try {
            # Install when packages are missing or package.json / the lock file changed
            # since the last install (a pull can add dependencies).
            $installed = "node_modules\.package-lock.json"
            $needInstall = -not (Test-Path $installed)
            if (-not $needInstall) {
                $at = (Get-Item $installed).LastWriteTimeUtc
                foreach ($f in "package.json", "package-lock.json") {
                    if ((Test-Path $f) -and (Get-Item $f).LastWriteTimeUtc -gt $at) { $needInstall = $true }
                }
            }
            if ($needInstall) {
                Say "Installing dashboard packages..."
                & cmd /c "npm install --no-audit --no-fund > `"$logDir\dashboard-install.log`" 2>&1"
            }
            & cmd /c "npm run build > `"$logDir\dashboard-build.log`" 2>&1"
            if ($LASTEXITCODE -ne 0) { Say "ERROR: dashboard build failed. See $logDir\dashboard-build.log" "Red" }
            else { Set-Content -Path $stampFile -Value $stamp -Encoding utf8 }
        } finally {
            Pop-Location
            foreach ($key in $dashEnv.Keys) { Remove-Item -Path "env:$key" -ErrorAction SilentlyContinue }
        }
    }
    Start-Service-Logged "dashboard-hotspot" "npx next start -H $HostIp -p $DashboardPort" $frontend $dashEnv
    Start-Service-Logged "dashboard-local" "npx next start -H 127.0.0.1 -p $DashboardPort" $frontend $dashEnv
}

# 7. Bridges, one per Arduino.
if (-not $NoBridges) {
    foreach ($n in 1, 2) {
        $responder = "responder-{0:d3}" -f $n
        $port = 7777 + $n - 1
        Start-Service-Logged "bridge-$n" ("uv run --with pyserial python -m hardware.bridge --wifi ${HostIp}:$port " +
            "--backend-url $backendUrl --responder $responder --http-timeout 3 " +
            "--log hardware/logs/device-events-$responder.jsonl -v") $repoRoot @{}
    }
}

ConvertTo-Json -InputObject @($started) | Set-Content $pidFile -Encoding utf8

if (-not $NoDashboard) { [void](Wait-Url "http://${HostIp}:$DashboardPort/command" 60) }
if (-not $NoVoice) { [void](Wait-Url "http://127.0.0.1:$VoicePort/health" 30) }
Start-Sleep -Seconds 3
Show-Health
Say ""
Say "Logs: $logDir    Stop: .\hardware\scripts\start-demo-host.ps1 -Stop"
exit ([int]($failed -gt 0))
