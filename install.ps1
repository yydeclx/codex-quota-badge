$ErrorActionPreference = 'Stop'
$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { throw '未找到 Python 3。请先安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。' }
$version = & $python.Source --version 2>&1
Write-Output "Python: $version"
$pythonw = Join-Path (Split-Path $python.Source) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw '未找到 pythonw.exe。请确认 Python 安装完整。' }
$supervisor = Join-Path $PSScriptRoot 'sidebar_supervisor.py'
if (-not (Test-Path -LiteralPath $supervisor)) { throw '缺少 sidebar_supervisor.py。请重新解压完整插件包。' }
& $python.Source -m py_compile (Join-Path $PSScriptRoot 'sidebar_supervisor.py')
if ($LASTEXITCODE -ne 0) { throw '故障自恢复模块检查失败。' }
try { & $python.Source -c 'import PIL' 2>$null; $hasPillow = ($LASTEXITCODE -eq 0) } catch { $hasPillow = $false }
if (-not $hasPillow) {
    Write-Output '正在安装 Pillow 依赖...'
    & $python.Source -m pip install --user Pillow
    if ($LASTEXITCODE -ne 0) { throw 'Pillow 安装失败。请手动运行: python -m pip install --user Pillow' }
}
Write-Output '依赖检查通过。'
Write-Output '启动侧栏: .\start.ps1'
Write-Output '打开管理页: .\manage.ps1'
Write-Output '查看状态: python .\manage.py status'
Write-Output '停止侧栏: python .\manage.py stop'
Write-Output '故障自恢复: 异常退出自动重启一次，短期连续异常会自动停止'
Write-Output '后续安全更新: pwsh.exe -File .\update.ps1 -TargetPath <原安装目录>'
Write-Output '自动更新检查: 设置 update-config.json 后按配置检查 GitHub Release，发现新版本自动静默更新，失败时提示并保留旧版本'
