$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$runtimeDirectory = Join-Path $projectDirectory 'var/forward'
New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $runtimeDirectory 'STOP') | Out-Null
Write-Output 'Stop requested. The supervisor will finish the current tick, close workers and preserve balances. It may take up to 90 seconds.'
