param([int]$MainPid,[long]$MainWindow,[string]$GuardThread,[switch]$Disable,[switch]$WaitForExit)
$ErrorActionPreference='Stop'
$runtime=Join-Path $env:LOCALAPPDATA 'CodexComposerInline'
$null=New-Item -Path $runtime -ItemType Directory -Force
$stop=Join-Path $runtime 'stop'
if($Disable){ $null=New-Item -Path $stop -ItemType File -Force; return }
$package=Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1
if(-not $package){throw 'Codex desktop is not installed'}
$exe=Join-Path $package.InstallLocation 'app/ChatGPT.exe'
$config=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../sidebar-config.json')).Path
$launcher=Join-Path $PSScriptRoot 'launch.py'
$python=Join-Path (Split-Path (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source) 'pythonw.exe'
$argsLine='"{0}" --managed --exe "{1}" --runtime "{2}" --legacy-config "{3}"' -f $launcher,$exe,$runtime,$config
$running=@(Get-CimInstance Win32_Process -Filter "Name='ChatGPT.exe'" | Where-Object {$_.ExecutablePath -ieq $exe -and $_.CommandLine -notmatch '--type=|--user-data-dir='})
if($MainPid){
    $main=$running | Where-Object ProcessId -eq $MainPid
    if(-not $main -or $main.CommandLine -match 'remote-debugging-pipe' -or (Get-Process -Id $MainPid).MainWindowHandle.ToInt64() -ne $MainWindow -or $GuardThread -notmatch '^[0-9a-f-]{36}$'){throw 'Main window or active chat changed'}
    $argsLine+=' --wait-pid {0} --handover-hwnd {1} --guard-thread {2}' -f $MainPid,$MainWindow,$GuardThread
} elseif($WaitForExit -and $running.Count -gt 0){
    if($running.Count -ne 1){throw 'More than one Codex main process was found'}
    if($running[0].CommandLine -match 'remote-debugging-pipe'){
        $statusPath=Join-Path $runtime 'status.json'
        $status=if(Test-Path -LiteralPath $statusPath){Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json}
        if($status -and $status.state -eq 'active' -and $status.pid -eq $running[0].ProcessId){'AlreadyInline';return}
        throw 'Close the existing Codex session normally and retry the inline launcher'
    }
    $argsLine+=' --wait-pid {0}' -f $running[0].ProcessId
} elseif($running){
    # If already open, activate it through normal Windows single-instance handling.
    Start-Process -FilePath $exe
    return
}
if(Test-Path -LiteralPath $stop){Remove-Item -LiteralPath $stop}
$before=Get-Date
$command='"{0}" {1}' -f $python,$argsLine
$p=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$command;CurrentDirectory=$PSScriptRoot}
if($p.ReturnValue){throw "Inline sidebar launcher failed: $($p.ReturnValue)"}
if($WaitForExit){
    $statusPath=Join-Path $runtime 'status.json'
    # Check the result written by the background process, which can have a
    # different view of protected package files from this foreground shell.
    for($i=0;$i -lt 40;$i++){
        $stateFile=Get-Item -LiteralPath $statusPath -ErrorAction SilentlyContinue
        if($stateFile -and $stateFile.LastWriteTime -ge $before){
            try{$state=(Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json).state}catch{$state=$null}
            if($state -eq 'waiting-for-normal-exit'){'WaitingForExit';return}
            if($state -in @('connecting','active')){'StartingInline';return}
            if($state -in @('blocked','error','cancelled','closed','disconnected','not-mounted')){throw "Inline launcher: $state"}
        }
        if(-not (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue)){throw 'Inline launcher exited before startup'}
        Start-Sleep -Milliseconds 100
    }
    if($running){throw 'Could not verify that the inline launcher is waiting for Codex'}
    'StartingInline';return
}
[pscustomobject]@{LauncherPid=$p.ProcessId;StatusFile=(Join-Path $runtime 'status.json')}
