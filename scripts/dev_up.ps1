# Starts the whole local setup in separate windows: mock Pixxi (seed data), receiver + orchestrator,
# webhook subscriptions, and the live dashboard.
#
#   powershell -ExecutionPolicy Bypass -File scripts\dev_up.ps1
#
# Stop everything by closing the windows (or Ctrl+C in each). New leads created from the dashboard
# make Agent 1 call the LLM (about $0.0008 each).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Old servers keep the ports and cause confusing results (e.g. a receiver without agents).
$busy = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 8001, 8002, 8501 }
if ($busy) {
    Write-Host "These ports are already in use:" -ForegroundColor Yellow
    foreach ($c in $busy) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)"
        Write-Host ("  port {0}  PID {1}  {2}" -f $c.LocalPort, $c.OwningProcess, $p.CommandLine)
    }
    Write-Host "Stop them first, e.g.  taskkill /PID <pid> /T /F   (then rerun this script)." -ForegroundColor Yellow
    exit 1
}

function Start-Window($title, $command) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location '$root'; $command"
}

Start-Window "Mock Pixxi :8001" "`$env:MOCK_FIXTURES_DIR = 'data/synthetic/seed'; uv run uvicorn mock_pixxi.app:app --port 8001"
Start-Window "Receiver + orchestrator :8002" "uv run uvicorn src.main:create_app --factory --port 8002"

Write-Host "Waiting for the mock and the receiver to start..."
$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Seconds 1
    $ready = $true
    foreach ($url in "http://127.0.0.1:8001/docs", "http://127.0.0.1:8002/health") {
        try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2 | Out-Null } catch { $ready = $false }
    }
} until ($ready -or (Get-Date) -gt $deadline)
if (-not $ready) { Write-Host "Servers did not start in 60 s; check their windows." -ForegroundColor Red; exit 1 }

uv run python -m scripts.subscribe_webhooks
Start-Window "Dashboard :8501" "uv run streamlit run ui/dashboard.py"
Write-Host "Dashboard: http://localhost:8501" -ForegroundColor Green
