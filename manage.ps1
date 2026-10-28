$ErrorActionPreference = 'Stop'
$sidebarPython = (Get-Command python.exe).Source
$sidebarPythonw = Join-Path (Split-Path $sidebarPython) 'pythonw.exe'
Start-Process -FilePath $sidebarPythonw -ArgumentList ('"' + (Join-Path $PSScriptRoot 'sidebar_manager.py') + '"') -WindowStyle Hidden
