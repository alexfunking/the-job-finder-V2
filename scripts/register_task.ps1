# Creates (or replaces) the Task Scheduler task that runs scripts\daily.cmd. Run it from any folder:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_task.ps1
# The task starts at logon, at unlock and on waking from sleep; daily.cmd itself makes it run once a day.
# The delays let Ollama, which starts at logon, and the network come up first.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$user = "$env:USERDOMAIN\$env:USERNAME"
$taskScheduler = 'Root/Microsoft/Windows/TaskScheduler'

$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$logon.Delay = 'PT2M'
$unlock = New-CimInstance -ClientOnly `
    -CimClass (Get-CimClass -Namespace $taskScheduler -ClassName MSFT_TaskSessionStateChangeTrigger) `
    -Property @{ StateChange = [uint32]8; UserId = $user; Enabled = $true; Delay = 'PT1M' }  # 8 = unlock
$wakeQuery = '<QueryList><Query Id="0" Path="System"><Select Path="System">' +
    "*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]" +
    '</Select></Query></QueryList>'
$wake = New-CimInstance -ClientOnly `
    -CimClass (Get-CimClass -Namespace $taskScheduler -ClassName MSFT_TaskEventTrigger) `
    -Property @{ Subscription = $wakeQuery; Enabled = $true; Delay = 'PT2M' }

Register-ScheduledTask -TaskName 'job-finder daily scan' -Force `
    -Action (New-ScheduledTaskAction -Execute cmd.exe -Argument "/c `"$repo\scripts\daily.cmd`"" -WorkingDirectory $repo) `
    -Trigger @($logon, $unlock, $wake) `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)) |
    Out-Null
Write-Output "Task 'job-finder daily scan' registered for $user."
