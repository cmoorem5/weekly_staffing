# Silent Crew Hub launcher: starts the Django server hidden (no console
# window) and opens the browser. Server output goes to output\crew_hub_*.log.
# Invoked by scripts\launch_crew_hub.vbs via the desktop shortcut.

$ErrorActionPreference = "SilentlyContinue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Test-Port8000 {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", 8000)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Port8000)) {
    $python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) { $python = "python" }

    $logDir = Join-Path $RepoRoot "output"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null

    # --noreload: one clean process (no autoreload child), so Stop_Crew_Hub
    # can end it reliably. Logs replace the console you would otherwise see.
    Start-Process -FilePath $python `
        -ArgumentList "bmf_staffing\manage.py", "runserver", "--noreload" `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "crew_hub_server.log") `
        -RedirectStandardError (Join-Path $logDir "crew_hub_server_error.log")

    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-Port8000) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
}

Start-Process "http://127.0.0.1:8000/hub/"
