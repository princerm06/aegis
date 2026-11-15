<#
.SYNOPSIS
  Network settings for the hybrid demo: Arduinos on the hotspot, laptops on Tailscale.

.DESCRIPTION
  Run from an Administrator PowerShell on the host laptop. Safe to run again at
  any time; it restarts nothing. Re-run it after a reboot or a hotspot restart.

  1. Hotspot MTU 1400. Some laptops never receive full 1500-byte packets from
     the hotspot, so small replies work and large ones (/state, the dashboard)
     stall. A smaller MTU avoids that.
  2. Firewall: backend (8000) and dashboard (3000) reachable from hotspot
     devices and from Tailscale devices only.
  3. Forwards this laptop's Tailscale address, and localhost:8000, to the
     services, which stay bound to the hotspot address. The dashboard opens its
     live connection on the same host name the page was loaded from, so it must
     find the backend on port 8000 there.

  The hotspot on a single WiFi radio that is also on campus WiFi cannot carry
  camera video for several laptops. So: only the two Arduinos use the hotspot;
  laptops stay on campus WiFi and use the Tailscale address printed below.

.EXAMPLE
  .\hardware\scripts\apply-demo-network.ps1
.EXAMPLE
  .\hardware\scripts\apply-demo-network.ps1 -Remove    # undo the forwards and the Tailscale rule
#>
param(
    [switch]$Remove,
    [string]$HostIp = "192.168.137.1",
    [int]$BackendPort = 8000,
    [int]$DashboardPort = 3000,
    [int]$Mtu = 1400
)

$ErrorActionPreference = "Stop"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Run this from an Administrator PowerShell (right-click PowerShell, Run as administrator)." -ForegroundColor Red
    exit 1
}

function Find-Tailscale {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = "C:\Program Files\Tailscale\tailscale.exe"
    if (Test-Path $default) { return $default }
    return $null
}

$tsIp = $null
$tailscale = Find-Tailscale
if ($tailscale) {
    $out = & $tailscale ip -4 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { $tsIp = ($out | Select-Object -First 1).Trim() }
}

function Remove-Forward($listen, $port) {
    & netsh interface portproxy delete v4tov4 listenaddress=$listen listenport=$port 2>$null | Out-Null
}
function Add-Forward($listen, $port) {
    Remove-Forward $listen $port
    & netsh interface portproxy add v4tov4 listenaddress=$listen listenport=$port connectaddress=$HostIp connectport=$port | Out-Null
    Write-Host "  forward ${listen}:$port -> ${HostIp}:$port"
}

if ($Remove) {
    Remove-Forward "127.0.0.1" $BackendPort
    if ($tsIp) { Remove-Forward $tsIp $BackendPort; Remove-Forward $tsIp $DashboardPort }
    Get-NetFirewallRule -DisplayName "AEGIS demo host (Tailscale)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Write-Host "Removed the forwards and the Tailscale firewall rule."
    exit 0
}

Write-Host "=== AEGIS demo network (SIMULATION ONLY) ===" -ForegroundColor Cyan

# 1. Hotspot MTU (not persistent: Windows resets it when the hotspot restarts).
$hotspot = Get-NetIPAddress -IPAddress $HostIp -ErrorAction SilentlyContinue
if ($hotspot) {
    & netsh interface ipv4 set subinterface "$($hotspot.InterfaceAlias)" mtu=$Mtu store=active | Out-Null
    Write-Host "  hotspot '$($hotspot.InterfaceAlias)' MTU set to $Mtu"
} else {
    Write-Host "  hotspot is not up ($HostIp missing): start it, then run this again." -ForegroundColor Yellow
}

# 2. Firewall.
$ports = @("$BackendPort", "$DashboardPort")
Get-NetFirewallRule -DisplayName "AEGIS demo host" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "AEGIS demo host" -Direction Inbound -Protocol TCP -Action Allow `
    -LocalPort $ports -LocalAddress $HostIp -RemoteAddress "192.168.137.0/24" | Out-Null
Write-Host "  firewall: $($ports -join ', ') from hotspot devices"

# 3. Forwards. localhost:8000 is for the dashboard opened on this laptop.
Add-Forward "127.0.0.1" $BackendPort
if ($tsIp) {
    Add-Forward $tsIp $BackendPort
    Add-Forward $tsIp $DashboardPort
    Get-NetFirewallRule -DisplayName "AEGIS demo host (Tailscale)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName "AEGIS demo host (Tailscale)" -Direction Inbound -Protocol TCP -Action Allow `
        -LocalPort $ports -LocalAddress $tsIp -RemoteAddress "100.64.0.0/10" | Out-Null
    Write-Host "  firewall: $($ports -join ', ') from Tailscale devices"
    Write-Host ""
    Write-Host "Teammates on Tailscale (stay on campus WiFi, not the hotspot):" -ForegroundColor Green
    Write-Host "  dashboard  http://${tsIp}:$DashboardPort/command"
    Write-Host "  camera/CV  --backend-url http://${tsIp}:$BackendPort"
    Write-Host "They must be in this tailnet, or this machine must be shared with them"
    Write-Host "(Tailscale admin console > Machines > ... > Share)."
} else {
    Write-Host "  Tailscale is not installed or not signed in: only hotspot and localhost access was set up." -ForegroundColor Yellow
}
