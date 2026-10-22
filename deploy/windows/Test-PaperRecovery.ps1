$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$runtimeDirectory = Join-Path $projectDirectory 'var/forward'
$supervisor = Get-Content -LiteralPath (Join-Path $runtimeDirectory 'supervisor.json') -Raw | ConvertFrom-Json
$worker = Get-Content -LiteralPath (Join-Path $runtimeDirectory 'worker.json') -Raw | ConvertFrom-Json
$workerProcessId = [int]$worker.pid
$supervisorProcessId = [int]$supervisor.pid
if ($supervisor.state -ne 'running' -or $worker.state -ne 'running' -or [int]$supervisor.child_pid -ne $workerProcessId) { throw 'The supervisor does not own the reported live worker.' }
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$workerProcessId"
$parent = Get-CimInstance Win32_Process -Filter "ProcessId=$supervisorProcessId"
$ledgerPath = Join-Path $runtimeDirectory 'ledger.sqlite3'
if (-not $process -or -not $parent -or $process.ParentProcessId -ne $supervisorProcessId -or $process.CommandLine -notlike '*paper-worker*' -or $process.CommandLine -notlike "*$ledgerPath*" -or $process.CommandLine -notlike "*$($supervisor.token)*" -or $parent.CommandLine -notlike '*supervise-paper*') { throw 'Process identity checks failed. No process was interrupted.' }
$pythonExecutable = Join-Path $projectDirectory '.venv/Scripts/python.exe'
Push-Location $projectDirectory
try {
    $audit = & $pythonExecutable -m bitcoin_agent forward-audit | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $audit.passed) { throw 'The ledger must reconcile before the recovery check.' }
    $record = [pscustomobject]@{ at_utc = [DateTime]::UtcNow.ToString('o'); interrupted_worker_pid = $workerProcessId; supervisor_pid = $supervisorProcessId; audit_before = $audit }
    $record | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $runtimeDirectory 'recovery-check-before.json') -Encoding utf8
    Stop-Process -Id $workerProcessId -Force
    $record | ConvertTo-Json -Depth 12
} finally { Pop-Location }
