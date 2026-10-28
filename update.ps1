[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$TargetPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw '此更新脚本要求 PowerShell 7。请使用 pwsh.exe 运行。'
}

$sourceRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$targetRoot = [System.IO.Path]::GetFullPath($TargetPath)
if ($sourceRoot.TrimEnd('\') -ieq $targetRoot.TrimEnd('\')) {
    throw '更新包目录和目标安装目录不能相同。请先把新版压缩包解压到另一个目录。'
}
if (-not (Test-Path -LiteralPath $targetRoot -PathType Container)) {
    throw "目标安装目录不存在：$targetRoot"
}
foreach ($required in @('manage.py', 'start.ps1', 'sidebar_plugin.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $targetRoot $required) -PathType Leaf)) {
        throw "目标目录不是有效的插件安装目录，缺少：$required"
    }
}

$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw '未找到 Python 3。请先安装 Python 3.10 或更高版本，并加入 PATH。'
}
$python = $pythonCommand.Source

# 只部署程序和随包文档。运行数据、配置、状态、日志以及任何账号资料均不在清单中。
$programFiles = @(
    'version.py', 'quota_badge.py', 'quota_styles.py', 'native_badge.py',
    'sidebar_plugin.py', 'sidebar_activity.py', 'sidebar_context.py',
    'font_fallback.py', 'quota_accounting.py', 'patch_mode.py',
    'sidebar_data.py', 'sidebar_details.py', 'sidebar_manager.py',
    'sidebar_modules.py', 'sidebar_options.py', 'sidebar_startup.py',
    'sidebar_styles.py', 'sidebar_telemetry.py','live_metrics.py', 'sidebar_transparency.py',
    'tooltip_card.py', 'hover_panel.py', 'interaction_runtime.py',
    'sidebar_maintenance.py',
    'sidebar_supervisor.py',
    'reset_signals.py',
    'update_client.py', 'update_prompt.py', 'mac_sidebar.py',
    'start-mac.sh', 'install-mac.sh', 'update-mac.sh',
    'ui_presence.py', 'ui_render.py', 'manager_icons.py', 'manager_widgets.py',
    'manage.py', 'start.ps1', 'manage.ps1', 'start-polling-backup.ps1',
    'install.ps1', 'update.ps1', 'PORTABLE-INSTALL.md', 'README.md',
    'codex-monochrome.ico', 'codex-original.ico', 'package-manifest.json',
    'gateway-telemetry/deploy_gateway.py', 'gateway-telemetry/rollback.ps1',
    'gateway-telemetry/local-telemetry-2.92.0.patch', 'gateway-telemetry/README.md',
    'gateway-telemetry/LICENSE'
)
$programDirectories = @('.codex-plugin', 'skills')

foreach ($relativePath in $programFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $relativePath) -PathType Leaf)) {
        throw "更新包不完整，缺少程序文件：$relativePath"
    }
}
foreach ($relativePath in $programDirectories) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $relativePath) -PathType Container)) {
        throw "更新包不完整，缺少程序目录：$relativePath"
    }
}

$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("codex-quota-update-" + [guid]::NewGuid().ToString('N'))
$stageRoot = Join-Path $workRoot 'stage'
$backupRoot = Join-Path $targetRoot ('.update-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmssfff'))
$deployedFiles = [System.Collections.Generic.List[string]]::new()
$wasRunning = $false
$filesChanged = $false
$oldPid = $null

function Copy-RelativeFile {
    param([string]$FromRoot, [string]$ToRoot, [string]$RelativePath)
    $source = Join-Path $FromRoot $RelativePath
    $destination = Join-Path $ToRoot $RelativePath
    $parent = Split-Path -Parent $destination
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Copy-Item -LiteralPath $source -Destination $destination -Force
}

function Start-Sidebar {
    & (Join-Path $targetRoot 'start.ps1')
    if ($LASTEXITCODE -ne 0) { throw '侧栏启动脚本返回失败。' }
}

try {
    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
    foreach ($relativePath in $programFiles) {
        Copy-RelativeFile -FromRoot $sourceRoot -ToRoot $stageRoot -RelativePath $relativePath
    }
    foreach ($relativeDirectory in $programDirectories) {
        Copy-Item -LiteralPath (Join-Path $sourceRoot $relativeDirectory) -Destination $stageRoot -Recurse -Force
    }

    $stagedPythonFiles = @(Get-ChildItem -LiteralPath $stageRoot -Filter '*.py' -File -Recurse | Select-Object -ExpandProperty FullName)
    if ($stagedPythonFiles.Count -eq 0) { throw '更新包内没有可校验的 Python 程序。' }
    & $python -m py_compile @stagedPythonFiles
    if ($LASTEXITCODE -ne 0) { throw '新版程序未通过 py_compile，目标安装未修改。' }
    Get-ChildItem -LiteralPath $stageRoot -Directory -Filter '__pycache__' -Recurse | Remove-Item -Recurse -Force

    $statusPath = Join-Path $targetRoot 'status.json'
    if (Test-Path -LiteralPath $statusPath -PathType Leaf) {
        $statusOutput = & $python (Join-Path $targetRoot 'manage.py') status 2>$null
        if ($LASTEXITCODE -eq 0) {
            try { $wasRunning = [bool](($statusOutput -join "`n" | ConvertFrom-Json).running) } catch { $wasRunning = $false }
        }
    }

    if ($wasRunning) {
        $oldPid = (Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json).pid
        & $python (Join-Path $targetRoot 'manage.py') stop | Out-Host
        if ($LASTEXITCODE -ne 0) { throw '无法请求侧栏停止，更新已取消。' }
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Process -Id $oldPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 250
        }
        if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
            throw '侧栏在 15 秒内未停止，更新已取消。'
        }
    }

    $stageFiles = @(Get-ChildItem -LiteralPath $stageRoot -File -Recurse)
    foreach ($file in $stageFiles) {
        $relativePath = [System.IO.Path]::GetRelativePath($stageRoot, $file.FullName)
        $destination = Join-Path $targetRoot $relativePath
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            Copy-RelativeFile -FromRoot $targetRoot -ToRoot $backupRoot -RelativePath $relativePath
        }
        $deployedFiles.Add($relativePath)
    }

    # 从首次替换前开始启用回滚；这样部署中途失败也会恢复已处理文件。
    $filesChanged = $true
    foreach ($relativePath in $deployedFiles) {
        Copy-RelativeFile -FromRoot $stageRoot -ToRoot $targetRoot -RelativePath $relativePath
    }

    $targetPythonFiles = @($deployedFiles | Where-Object { $_ -like '*.py' } | ForEach-Object { Join-Path $targetRoot $_ })
    & $python -m py_compile @targetPythonFiles
    if ($LASTEXITCODE -ne 0) { throw '更新后的程序未通过 py_compile。' }

    Start-Sidebar
    Write-Output "更新完成并已重启侧栏。备份目录：$backupRoot"
    Write-Output '已保留 sidebar-config.json、额度历史、提醒状态、连接历史以及现有状态和日志。'
}
catch {
    $originalError = $_
    if ($filesChanged) {
        Write-Warning '更新失败，正在恢复更新前的程序文件……'
        foreach ($relativePath in $deployedFiles) {
            $destination = Join-Path $targetRoot $relativePath
            if (Test-Path -LiteralPath $destination -PathType Leaf) {
                Remove-Item -LiteralPath $destination -Force
            }
            $backupFile = Join-Path $backupRoot $relativePath
            if (Test-Path -LiteralPath $backupFile -PathType Leaf) {
                Copy-RelativeFile -FromRoot $backupRoot -ToRoot $targetRoot -RelativePath $relativePath
            }
        }
        if ($wasRunning) {
            try { Start-Sidebar } catch { Write-Warning "旧版程序已恢复，但侧栏重启失败：$($_.Exception.Message)" }
        }
    } elseif ($wasRunning -and $null -ne $oldPid -and -not (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        try { Start-Sidebar } catch { Write-Warning "更新未写入，但侧栏恢复启动失败：$($_.Exception.Message)" }
    }
    throw $originalError
}
finally {
    if (Test-Path -LiteralPath $workRoot) { Remove-Item -LiteralPath $workRoot -Recurse -Force }
}
