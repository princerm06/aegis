<#
.SYNOPSIS
  Start the AEGIS responder bridge, reaching the backend over Tailscale.

.DESCRIPTION
  University WiFi is crowded, blocks device-to-device traffic, and the Arduino
  cannot join eduroam (WPA2-Enterprise) or click through a guest portal. So the
  Arduino stays on USB to this laptop, and this laptop reaches the backend
  laptop over Tailscale, which works on campus WiFi, phone hotspots or any mix.

  This script checks Tailscale, finds the backend, waits until it is healthy,
  auto-detects the Arduino, and runs the bridge. If the bridge ever exits with
  an error it is restarted automatically; type `quit` in the bridge to stop.

.EXAMPLE
  .\hardware\scripts\start-bridge.ps1 -Backend prince-laptop
.EXAMPLE
  .\hardware\scripts\start-bridge.ps1 -Backend 100.101.102.103 -Port COM13
.EXAMPLE
  .\hardware\scripts\start-bridge.ps1 -Local          # backend on this laptop
.EXAMPLE
  .\hardware\scripts\start-bridge.ps1 -Wifi -Local -Device 2   # second Arduino, in a second terminal
.EXAMPLE
  .\hardware\scripts\start-bridge.ps1 -Backend prince-laptop -CheckOnly
#>
param(
    [string]$Backend,                   # Tailscale machine name or 100.x address of the backend laptop
    [int]$BackendPort = 8000,
    [string]$Port,                      # Arduino COM port; auto-detected when omitted
    [switch]$Sim,                       # simulated device instead of the Arduino
    [switch]$Wifi,                      # Arduino carried by the responder, on this laptop's hotspot
    [string]$WifiListen = "192.168.137.1:7777",  # for a phone hotspot: this laptop's IP there, e.g. 172.20.10.4:7777
    [switch]$Local,                     # backend runs on this laptop (no Tailscale needed)
    [ValidateRange(1, 4)][int]$Device = 1,       # second Arduino: -Device 2 (port 7778, responder-002)
    [string]$Responder,                 # default: responder-00<Device>
    [double]$HttpTimeout = 3.0,         # generous for crowded WiFi
    [switch]$CheckOnly                  # run the checks, do not start the bridge
)

$ErrorActionPreference = "Stop"
if (-not $Responder) { $Responder = "responder-{0:d3}" -f $Device }
if ($Device -gt 1 -and -not $PSBoundParameters.ContainsKey("WifiListen")) {
    $WifiListen = "192.168.137.1:{0}" -f (7777 + $Device - 1)
}
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Fail($text) { Say "ERROR: $text" "Red"; exit 1 }

function Find-Tailscale {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = "C:\Program Files\Tailscale\tailscale.exe"
    if (Test-Path $default) { return $default }
    return $null
}

function Resolve-Backend($name) {
    # Prefer the stable 100.x Tailscale address so campus DNS never gets involved.
    if ($name -match '^\d{1,3}(\.\d{1,3}){3}$') { return $name }
    $ip = & $script:tailscale ip -4 $name 2>$null
    if ($LASTEXITCODE -eq 0 -and $ip) { return ($ip | Select-Object -First 1).Trim() }
    Fail "Tailscale cannot find a machine named '$name'. Run 'tailscale status' to see the exact names."
}

function Wait-Healthy($url, $attempts = 20) {
    for ($i = 1; $i -le $attempts; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 4
            if ($health.status -eq "ok") { return $true }
        } catch { }
        Say "  backend not answering yet ($i/$attempts), retrying in 3 s..." "DarkYellow"
        Start-Sleep -Seconds 3
    }
    return $false
}

function Find-Arduino {
    try {
        $list = arduino-cli board list --format json 2>$null | ConvertFrom-Json
        foreach ($p in $list.detected_ports) {
            foreach ($b in $p.matching_boards) {
                if ($b.fqbn -like "arduino:renesas_uno:*") { return $p.port.address }
            }
        }
    } catch { }
    # Fallback: Arduino's USB vendor ID 2341.
    $dev = Get-PnpDevice -PresentOnly -Class Ports -ErrorAction SilentlyContinue |
        Where-Object { $_.InstanceId -match "VID_2341" } | Select-Object -First 1
    if ($dev -and $dev.FriendlyName -match "\((COM\d+)\)") { return $Matches[1] }
    return $null
}

Say "=== AEGIS responder bridge launcher (SIMULATION ONLY) ===" "Cyan"

# 1. Where is the backend?
if ($Local) {
    $backendUrl = "http://127.0.0.1:$BackendPort"
} else {
    if (-not $Backend) { Fail "Pass -Backend <tailscale-machine-name or 100.x IP>, or -Local." }
    $script:tailscale = Find-Tailscale
    if (-not $script:tailscale) {
        Fail "Tailscale is not installed. Install it with:  winget install --id tailscale.tailscale   then run:  tailscale up"
    }
    $status = & $script:tailscale status --json 2>$null | ConvertFrom-Json
    if (-not $status -or $status.BackendState -ne "Running") {
        Fail "Tailscale is not connected. Run:  tailscale up   (log in with the team account), then retry."
    }
    Say "Tailscale: connected as $($status.Self.HostName) ($($status.Self.TailscaleIPs[0]))" "Green"
    $ip = Resolve-Backend $Backend
    Say "Backend machine '$Backend' -> $ip"
    $ping = & $script:tailscale ping -c 1 --timeout 5s $ip 2>&1
    Say "  tailscale ping: $($ping | Select-Object -Last 1)"
    $backendUrl = "http://${ip}:$BackendPort"
}

# 2. Is the backend healthy?
Say "Checking $backendUrl/health ..."
if (-not (Wait-Healthy $backendUrl)) {
    Fail ("Backend at $backendUrl is not responding. On the backend laptop run " +
          "hardware\scripts\host-backend.ps1 and allow Python through Windows Firewall.")
}
Say "Backend healthy." "Green"

# 3. Which device?
if ($Sim) {
    $deviceArgs = @("--sim")
    Say "Device: simulated (type 'press' / 'long' for the button)"
} elseif ($Wifi) {
    if ($WifiListen -like "192.168.137.1:*" -and -not $CheckOnly) {
        & (Join-Path $PSScriptRoot "start-hotspot.ps1")
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { Fail "Hotspot did not start." }
    }
    $deviceArgs = @("--wifi", $WifiListen)
    Say "Device: Arduino over WiFi; bridge listens on $WifiListen" "Green"
} else {
    if (-not $Port) { $Port = Find-Arduino }
    if (-not $Port) { Fail "No Arduino found. Plug it in by USB, or pass -Port COMx (see 'arduino-cli board list')." }
    $deviceArgs = @("--port", $Port)
    Say "Device: Arduino on $Port" "Green"
}

if ($CheckOnly) { Say "All checks passed; not starting the bridge (-CheckOnly)." "Green"; exit 0 }

# 4. Run the bridge; restart it if it ever exits with an error.
$bridgeArgs = @("run", "--with", "pyserial", "python", "-m", "hardware.bridge") + $deviceArgs + @(
    "--backend-url", $backendUrl, "--responder", $Responder, "--http-timeout", "$HttpTimeout")
if ($Device -gt 1) { $bridgeArgs += @("--log", "hardware/logs/device-events-$Responder.jsonl") }
Push-Location $repoRoot
try {
    while ($true) {
        Say "Starting bridge -> $backendUrl (type 'status' for health, 'quit' to stop)" "Cyan"
        & uv @bridgeArgs
        if ($LASTEXITCODE -eq 0) { break }
        Say "Bridge exited with code $LASTEXITCODE; restarting in 3 s (Ctrl+C to stop)..." "DarkYellow"
        Start-Sleep -Seconds 3
    }
} finally {
    Pop-Location
}
