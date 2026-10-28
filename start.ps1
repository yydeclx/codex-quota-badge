$ErrorActionPreference = 'Stop'
$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'pythonw.exe not found' }
$quotaCommand = '"' + $pythonw + '" "' + (Join-Path $PSScriptRoot 'sidebar_supervisor.py') + '"'
# WMI starts pythonw through Windows rather than as a Codex terminal child.
# The supervisor owns the single supervisor instance and restarts the existing
# plugin once after an abnormal exit.
$quotaLaunch = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $quotaCommand
    CurrentDirectory = $PSScriptRoot
}
if ($quotaLaunch.ReturnValue -ne 0) { throw "Quota helper launch failed: $($quotaLaunch.ReturnValue)" }
Write-Output "Quota helper started: $($quotaLaunch.ProcessId)"
