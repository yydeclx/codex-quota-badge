$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'deploy_gateway.py') --rollback --wait-seconds 90
if ($LASTEXITCODE -ne 0) { throw '回滚未完成，请查看 deployment.json；脚本不会强制中断在途请求。' }
