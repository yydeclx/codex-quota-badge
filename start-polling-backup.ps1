$ErrorActionPreference = 'Stop'
$python = (Get-Command python.exe).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'pythonw.exe not found' }
Start-Process -FilePath $pythonw -ArgumentList @('"' + (Join-Path $PSScriptRoot 'quota_badge.py') + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
