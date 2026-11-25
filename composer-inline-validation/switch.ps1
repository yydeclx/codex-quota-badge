param(
    [int]$MainCodexPid,
    [switch]$Restore
)
$ErrorActionPreference = 'Stop'
$runtime = Join-Path $env:LOCALAPPDATA 'CodexComposerInlineValidation'
$launcher = Join-Path $PSScriptRoot 'launch.py'
$codexImagePattern = '[\\/]OpenAI\.Codex_[^\\/]+[\\/]app[\\/]ChatGPT\.exe$'
$installed = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../codex-quota-badge/sidebar-config.json')).Path
$python = (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source
$stop = Join-Path $runtime 'stop'
if ($Restore) {
    if (-not (Test-Path -LiteralPath $runtime)) { return }
    $null = New-Item -Path $stop -ItemType File -Force
    # The running launcher restores the old setting itself. If it is gone,
    # restore a left-over journal under the same exclusive launcher lock.
    & $python $launcher --runtime $runtime --legacy-config $installed --restore
    exit $LASTEXITCODE
}
if ($MainCodexPid -le 0) {
    $candidates = @(Get-CimInstance Win32_Process -Filter "Name='ChatGPT.exe'" |
        Where-Object { $_.ExecutablePath -match $codexImagePattern -and
            $_.CommandLine -notmatch '--type=|--remote-debugging-|--user-data-dir' } |
        Where-Object { (Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).MainWindowHandle })
    if ($candidates.Count -ne 1) { throw 'Could not identify exactly one normal Codex window.' }
    $MainCodexPid = $candidates[0].ProcessId
}
$main = Get-Process -Id $MainCodexPid -ErrorAction Stop
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$MainCodexPid"
if ($main.ProcessName -ne 'ChatGPT' -or -not $main.MainWindowHandle -or
    $main.Path -notmatch $codexImagePattern -or
    $process.CommandLine -match '--type=|--remote-debugging-|--user-data-dir') {
    throw 'Choose the normal installed main Codex window.'
}
$null = New-Item -Path $runtime -ItemType Directory -Force
if (Test-Path -LiteralPath $stop) { Remove-Item -LiteralPath $stop }
Write-Host '底栏内嵌验证已就绪。请保存草稿并正常退出主 Codex，等待自动打开。'
Write-Host '请在验证期间保留此窗口。要结束验证，请关闭验证中的 Codex。'
try {
    & $python -u $launcher --exe $main.Path --wait-pid $main.Id --runtime $runtime --legacy-config $installed
    $exitCode = $LASTEXITCODE
} finally {
    # This PowerShell is launched outside Codex and can recover the old switch
    # even if the Python bridge exits before executing its cleanup.
    & $python $launcher --runtime $runtime --legacy-config $installed --restore
}
exit $exitCode
