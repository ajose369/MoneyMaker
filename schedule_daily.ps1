# Registers a Windows Task Scheduler job that produces and uploads one video per day.
# Run once from an elevated-or-normal PowerShell:  .\schedule_daily.ps1 [-Time "09:00"]
param(
    [string]$Time = "09:00",
    [string]$TaskName = "ToonPipeDaily"
)

$root = $PSScriptRoot
$log = Join-Path $root "logs"
New-Item -ItemType Directory -Force $log | Out-Null

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" }

$action = "cd /d `"$root`" && `"$python`" -m toonpipe autopilot >> `"$log\autopilot_%DATE:/=-%.log`" 2>&1"

schtasks /Create /F /TN $TaskName /SC DAILY /ST $Time /TR "cmd /c $action"
Write-Host "Scheduled '$TaskName' daily at $Time. Logs: $log"
Write-Host "Remove with: schtasks /Delete /TN $TaskName /F"
