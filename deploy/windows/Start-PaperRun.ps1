param([switch]$Resume)
$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$runtimeDirectory = Join-Path $projectDirectory 'var/forward'
$pythonExecutable = Join-Path $projectDirectory '.venv/Scripts/pythonw.exe'
$stopFile = Join-Path $runtimeDirectory 'STOP'
if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Create the project virtual environment first.' }
if (Test-Path -LiteralPath (Join-Path $runtimeDirectory 'COMPLETE')) { throw 'This seven-day window is closed. Preserve its evidence and create a separate experiment for another run.' }
New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
if (Test-Path -LiteralPath $stopFile) {
    if (-not $Resume) { throw 'The paper run was stopped. Use -Resume to explicitly continue the same account.' }
    Remove-Item -LiteralPath $stopFile
}
# A duplicate launch exits on the supervisor OS lock, without touching its worker.
$logDirectory = Join-Path $runtimeDirectory 'logs'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$launchId = Get-Date -Format 'yyyyMMddTHHmmssfffffff'
$process = Start-Process -FilePath $pythonExecutable -ArgumentList @('-u', '-m', 'bitcoin_agent', 'supervise-paper') -WorkingDirectory $projectDirectory -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "launcher-$launchId.stdout.log") -RedirectStandardError (Join-Path $logDirectory "launcher-$launchId.stderr.log") -PassThru
[pscustomobject]@{ SupervisorProcessId = $process.Id; Runtime = $runtimeDirectory; HealthCommand = '.\.venv\Scripts\python.exe -m bitcoin_agent health' } | ConvertTo-Json
