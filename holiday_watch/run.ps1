# Task Scheduler's entry point: `run.ps1 pass` every 30 min, `run.ps1 report` once a day.
# Registering the tasks is the arm step's job. Extra arguments pass through (e.g. --dry-run).
$repo = Split-Path -Parent $PSScriptRoot
$python = if ($env:HOLIDAY_WATCH_PYTHON) { $env:HOLIDAY_WATCH_PYTHON } else { Join-Path $repo ".venv\Scripts\python.exe" }
Set-Location $repo
& $python -m holiday_watch @args
exit $LASTEXITCODE
