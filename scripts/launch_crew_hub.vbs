' Crew Hub silent launcher — the desktop shortcut points here (via wscript),
' so no command-prompt windows appear at all. All real work happens in
' launch_crew_hub.ps1, run fully hidden.
Option Explicit
Dim shell, scriptDir
Set shell = CreateObject("WScript.Shell")
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & scriptDir & "launch_crew_hub.ps1""", 0, False
