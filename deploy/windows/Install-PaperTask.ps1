$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$taskName = 'BitcoinPaperAgent-01a129e0'
$pythonExecutable = Join-Path $projectDirectory '.venv/Scripts/pythonw.exe'
$arguments = '-u -m bitcoin_agent supervise-paper'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    $allowedExecutables = @($pythonExecutable, (Join-Path $projectDirectory '.venv/Scripts/python.exe'))
    if ($existing.Actions.Count -ne 1 -or $existing.Actions.Execute -notin $allowedExecutables -or $existing.Actions.Arguments -ne $arguments -or $existing.Actions.WorkingDirectory -ne $projectDirectory) { throw 'A different task already uses this name; it was not changed.' }
}
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonExecutable -Argument $arguments -WorkingDirectory $projectDirectory
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $owner
$recoveryTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 8)
$principal = New-ScheduledTaskPrincipal -UserId $owner -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logonTrigger, $recoveryTrigger) -Principal $principal -Settings $settings -Description 'Local Bitcoin paper experiment. Hidden process, one-minute recovery for eight days, scoped awake request, public data and simulated funds; honors var/forward/STOP.' -Force | Select-Object TaskName,State
