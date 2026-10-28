[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][string]$Owner,
 [Parameter(Mandatory=$true)][string]$Repository,
 [Parameter(Mandatory=$true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
 [string]$Notes='', [string]$NotesFile='', [switch]$PrepareOnly, [switch]$IncludeGatewayBundle
)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
if($PSVersionTable.PSVersion.Major -lt 7){throw '发布要求 PowerShell 7'}
$root=[IO.Path]::GetFullPath($PSScriptRoot)
$tempRoot=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
$stage=Join-Path $tempRoot ('codex-quota-release-'+[guid]::NewGuid().ToString('N'))
$zip=Join-Path $root "codex-quota-badge-$Version.zip"
$files=@('version.py','quota_badge.py','quota_styles.py','quota_accounting.py','patch_mode.py','native_badge.py','sidebar_plugin.py','sidebar_activity.py','sidebar_context.py','font_fallback.py','sidebar_data.py','sidebar_details.py','sidebar_manager.py','sidebar_modules.py','sidebar_options.py','sidebar_startup.py','sidebar_styles.py','sidebar_telemetry.py','live_metrics.py','sidebar_transparency.py','sidebar_maintenance.py','sidebar_supervisor.py','reset_signals.py','update_client.py','update_prompt.py','mac_sidebar.py','start-mac.sh','install-mac.sh','update-mac.sh','tooltip_card.py','hover_panel.py','interaction_runtime.py','ui_presence.py','ui_render.py','manager_icons.py','manager_widgets.py','manage.py','start.ps1','manage.ps1','start-polling-backup.ps1','install.ps1','update.ps1','PORTABLE-INSTALL.md','README.md','codex-monochrome.ico','codex-original.ico','gateway-telemetry/deploy_gateway.py','gateway-telemetry/rollback.ps1','gateway-telemetry/local-telemetry-2.92.0.patch','gateway-telemetry/README.md','gateway-telemetry/LICENSE')
$assets=[Collections.Generic.List[string]]::new()
if($NotesFile){$Notes=Get-Content -LiteralPath $NotesFile -Raw -Encoding utf8}
function Copy-Program([string]$name,[string]$destinationRoot){
 $source=Join-Path $root $name
 if(!(Test-Path -LiteralPath $source -PathType Leaf)){throw "缺少程序文件：$name"}
 $destination=Join-Path $destinationRoot $name
 New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
 Copy-Item -LiteralPath $source -Destination $destination
}
try {
 New-Item -ItemType Directory -Path $stage | Out-Null
 foreach($f in $files){Copy-Program $f $stage}
 Copy-Program '.codex-plugin/plugin.json' $stage
 Copy-Program 'skills/sidebar-management/SKILL.md' $stage
 $skill=Join-Path $stage 'skills/sidebar-management/SKILL.md'
 $portableSkill=(Get-Content -LiteralPath $skill -Raw -Encoding utf8).Replace('真实运行目录：`D:/AI/Codex/ai-software/codex-quota-badge`','运行目录：用户解压本安装包的目录（包含 manage.py）')
 Set-Content -LiteralPath $skill -Value $portableSkill -Encoding utf8NoBOM
 # Fresh-install defaults; never publish this machine's settings.
 $config=@{version=1;refreshSeconds=60;realtimeMetricsEnabled=$false;activityEnabled=$true;items=@(@{id='chatgpt_today';type='opencodex_today';provider='openai';label='ChatGPT';enabled=$true;gapAfter=3},@{id='weekly_quota';type='weekly_quota';label='周额度';enabled=$true;gapAfter=0});services=@();shortcuts=@()}
 $config|ConvertTo-Json -Depth 10|Set-Content -LiteralPath (Join-Path $stage 'sidebar-config.json') -Encoding utf8NoBOM
 @{version=$Version;programFiles=$files;directories=@('.codex-plugin','skills');preservedFiles=@('sidebar-config.json','update-config.json','quota-history.json','quota-alert-state.json','connection-history.json','reset-signals.json','status.json','sidebar-status.json','update-status.json')}|ConvertTo-Json -Depth 10|Set-Content -LiteralPath (Join-Path $stage 'package-manifest.json') -Encoding utf8NoBOM
 foreach($module in @('test_update_client','test_reset_signals','test_quota_windows','test_quota_accounting','test_sidebar_activity','test_modules','test_provider_indicators','test_sidebar_data','test_power_mode','test_stability','test_maintenance','test_supervisor','test_diagnostics','test_patch_mode')){
  & python -m unittest $module -q
  if($LASTEXITCODE -ne 0){throw "专项测试失败：$module"}
 }
 Push-Location $stage
 try {
  & python -B -I -c 'from pathlib import Path; import sys,json; sys.path.insert(0,str(Path.cwd())); [compile(p.read_text(encoding="utf-8-sig"),str(p),"exec") for p in Path.cwd().rglob("*.py")]; import sidebar_plugin,sidebar_details,sidebar_manager,mac_sidebar,patch_mode,quota_accounting; from sidebar_data import load_config; assert load_config()["realtimeMetricsEnabled"] is False; from version import PLUGIN_VERSION; assert PLUGIN_VERSION==json.loads(Path("package-manifest.json").read_text(encoding="utf-8-sig"))["version"]==json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8-sig"))["version"]'
  if($LASTEXITCODE -ne 0){throw '干净安装包依赖或版本检查失败'}
 } finally {Pop-Location}
 if(Test-Path -LiteralPath $zip){throw "发布包已存在，请先核对：$zip"}
 # ZipFile retains dot directories that Compress-Archive silently omits.
 [IO.Compression.ZipFile]::CreateFromDirectory($stage,$zip,[IO.Compression.CompressionLevel]::Optimal,$false)
 $assets.Add($zip)
 if($IncludeGatewayBundle){
  $bundle=Join-Path $stage 'optional-gateway'
  foreach($name in @('gateway-telemetry/build/ocx.exe','gateway-telemetry/rollback-official-2.92.0.exe','gateway-telemetry/LICENSE')){Copy-Program $name $bundle}
  $smoke=Get-Content -LiteralPath (Join-Path $root 'gateway-telemetry/smoke-result.json') -Raw|ConvertFrom-Json
  $patchHash=(Get-FileHash -LiteralPath (Join-Path $bundle 'gateway-telemetry/build/ocx.exe') -Algorithm SHA256).Hash.ToLower()
  $officialHash=(Get-FileHash -LiteralPath (Join-Path $bundle 'gateway-telemetry/rollback-official-2.92.0.exe') -Algorithm SHA256).Hash.ToLower()
  if(!$smoke.passed -or $smoke.binarySHA256 -ne $patchHash -or $patchHash -ne '91401d409aeff213e760615cd36e40ec93a419e7b1a8b8a7367baea544de2372' -or $officialHash -ne 'c7c87829a2b69720612f08e40048f5b20c8097ad8e02f353f1ef8428ab42a4c9'){throw '网关附件哈希验证失败'}
  @{passed=$true;binarySHA256=$patchHash}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $bundle 'gateway-telemetry/smoke-result.json') -Encoding utf8NoBOM
  $bundleZip=Join-Path $root "opencodex-telemetry-2.92.0-$Version.zip"
  if(Test-Path -LiteralPath $bundleZip){throw "补丁包已存在：$bundleZip"}
  [IO.Compression.ZipFile]::CreateFromDirectory($bundle,$bundleZip,[IO.Compression.CompressionLevel]::Optimal,$false)
  $assets.Add($bundleZip)
 }
 $records=@($assets|ForEach-Object {@{asset=(Split-Path $_ -Leaf);sha256=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLower();size=(Get-Item -LiteralPath $_).Length}})
 $hashFile=Join-Path $root "SHA256SUMS-$Version.txt"
 @($records|ForEach-Object {$_.sha256+'  '+$_.asset})|Set-Content -LiteralPath $hashFile -Encoding utf8NoBOM
 @{version=$Version;tag="v$Version";asset=(Split-Path $zip -Leaf);sha256=$records[0].sha256;owner=$Owner;repository=$Repository;preparedAt=(Get-Date).ToUniversalTime().ToString('o');notes=$Notes;assets=$records}|ConvertTo-Json -Depth 10|Set-Content -LiteralPath (Join-Path $root 'release-manifest.json') -Encoding utf8NoBOM
 if(!$PrepareOnly){
  if(!(Get-Command gh -ErrorAction SilentlyContinue)){throw '未找到 gh，安装包已准备但未发布'}
  $notePath=Join-Path $stage 'release-notes.md';Set-Content -LiteralPath $notePath -Value $Notes -Encoding utf8NoBOM
  & gh release create "v$Version" @assets $hashFile --repo "$Owner/$Repository" --title "Codex Quota Badge v$Version" --notes-file $notePath --latest
  if($LASTEXITCODE -ne 0){throw 'GitHub 发布失败，请先核对远端实际状态再重试'}
 }
 $records|ConvertTo-Json
} finally {
 $resolved=[IO.Path]::GetFullPath($stage)
 if(!$resolved.StartsWith($tempRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase) -or !(Split-Path $resolved -Leaf).StartsWith('codex-quota-release-')){throw '临时目录范围校验失败，未清理'}
 if(Test-Path -LiteralPath $resolved){Remove-Item -LiteralPath $resolved -Recurse -Force}
}
