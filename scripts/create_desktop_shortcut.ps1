# Creates "BMF Staffing" on the user's Desktop with the BMF logo icon.
# Run once after clone/move, or re-run if the project folder moves.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $RepoRoot "Run_Staffing_Django.bat"
$IconPath = Join-Path $RepoRoot "assets\BMF_Staffing.ico"
$BuildIcon = Join-Path $RepoRoot "scripts\build_app_icon.py"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "BMF Staffing.lnk"

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
$Shortcut.TargetPath = $Launcher
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.IconLocation = "$IconPath,0"
$Shortcut.Description = "Boston MedFlight Staffing Dashboard"
$Shortcut.WindowStyle = 7  # Minimized (backup; launcher also self-minimizes)
$Shortcut.Save()

Write-Host ""
Write-Host "Desktop shortcut created:" -ForegroundColor Green
Write-Host "  $ShortcutPath"
Write-Host ""
Write-Host "Double-click ""BMF Staffing"" on your desktop to start the app and open the browser."
Write-Host "The Django server runs minimized in the taskbar (""BMF Staffing - Django"")."
