# Silent Crew Hub launcher: starts the server hidden (no console window)
# and opens the dashboard in an Edge "app mode" window (no address bar/tabs,
# its own taskbar icon — feels like a native app, not a browser tab).
# The server is waitress (a production WSGI server), not manage.py
# runserver — the Django dev server isn't meant to face real users.
# Console output goes to output\crew_hub_server*.log; the Django app also
# writes a rotating output\crew_hub_app.log (see LOGGING in settings.py).
# Invoked by scripts\launch_crew_hub.vbs via the desktop shortcut.
#
# Auto-stop: if THIS launch is the one that started the server, the server
# is stopped again as soon as the app window is closed, so it isn't left
# running in the background eating RAM. If the server was already running
# before this launch (e.g. a previous window is still open), it's left
# alone — use Stop_Crew_Hub.bat to force it down.

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

function Get-EdgePath {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    return "msedge.exe"
}

$startedDjango = $false
$djangoProcess = $null

if (-not (Test-Port8000)) {
    $startedDjango = $true
    $python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) { $python = "python" }

    $logDir = Join-Path $RepoRoot "output"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null

    # waitress runs as one clean process, so we can stop it reliably by PID.
    # cwd must be bmf_staffing\ so "bmf_staffing.wsgi" is importable.
    $djangoProcess = Start-Process -FilePath $python `
        -ArgumentList "-m", "waitress", "--listen=127.0.0.1:8000", "--threads=8", "bmf_staffing.wsgi:application" `
        -WorkingDirectory (Join-Path $RepoRoot "bmf_staffing") `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "crew_hub_server.log") `
        -RedirectStandardError (Join-Path $logDir "crew_hub_server_error.log") `
        -PassThru

    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-Port8000) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
}

# Dedicated profile so this app window is its own browser instance (not
# merged into any Edge windows the user already has open) — that's what
# lets us reliably wait for THIS window specifically to close.
$edgeExe = Get-EdgePath
$edgeProfile = Join-Path $env:LOCALAPPDATA "BMFStaffing\CrewHubEdgeProfile"
New-Item -ItemType Directory -Force -Path $edgeProfile | Out-Null

Start-Process -FilePath $edgeExe `
    -ArgumentList "--app=http://127.0.0.1:8000/hub/", "--user-data-dir=""$edgeProfile""" `
    -Wait

if ($startedDjango -and $djangoProcess) {
    Stop-Process -Id $djangoProcess.Id -Force -ErrorAction SilentlyContinue
}
