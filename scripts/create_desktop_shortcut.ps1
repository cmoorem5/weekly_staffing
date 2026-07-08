# Creates "Crew Hub" on the user's Desktop with the BMF logo icon.
# Run once after clone/move, or re-run if the project folder moves.
#
# The shortcut launches scripts\launch_crew_hub.vbs via wscript, which starts
# the Django server fully hidden and opens the dashboard in an Edge app-mode
# window — no browser chrome, no command-prompt windows, like a native
# Windows app. The server stops itself automatically when that window is
# closed. Server output: output\crew_hub_*.log. Stop_Crew_Hub.bat is still
# there as a manual fallback (e.g. if the window is closed via Task Manager).

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $RepoRoot "scripts\launch_crew_hub.vbs"
$IconPath = Join-Path $RepoRoot "assets\BMF_Staffing.ico"
$BuildIcon = Join-Path $RepoRoot "scripts\build_app_icon.py"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Crew Hub.lnk"
$OldShortcut = Join-Path $Desktop "BMF Staffing.lnk"

if (-not (Test-Path -LiteralPath $Launcher)) {
    Write-Error "Launcher not found: $Launcher"
}

# Build .ico from PNG (Pillow in venv or system Python).
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}
& $python $BuildIcon
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to build app icon. Install dependencies: pip install -r requirements.txt"
}

if (-not (Test-Path -LiteralPath $IconPath)) {
    Write-Error "Icon file missing after build: $IconPath"
}

$Wsh = New-Object -ComObject WScript.Shell
$Shortcut = $Wsh.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "$env:WINDIR\System32\wscript.exe"
$Shortcut.Arguments = """$Launcher"""
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.IconLocation = "$IconPath,0"
$Shortcut.Description = "Boston MedFlight Crew Hub"
$Shortcut.Save()

# Retire the old shortcut (it opened visible command-prompt windows).
if (Test-Path -LiteralPath $OldShortcut) {
    Remove-Item -LiteralPath $OldShortcut
    Write-Host "Removed the old ""BMF Staffing"" shortcut (it opened console windows)."
}

Write-Host ""
Write-Host "Desktop shortcut created:" -ForegroundColor Green
Write-Host "  $ShortcutPath"
Write-Host ""
Write-Host "Double-click ""Crew Hub"" to start the app silently in its own window."
Write-Host "No command windows or browser chrome; server logs go to output\crew_hub_server.log."
Write-Host "The server stops itself when you close the window. Stop_Crew_Hub.bat is a manual fallback."
