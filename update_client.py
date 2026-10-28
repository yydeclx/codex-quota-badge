"""GitHub Release checker; never contains credentials or auto-updates silently."""
import hashlib, json, subprocess, tempfile, urllib.request, zipfile
from pathlib import Path
import time, re
ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'update-config.json'; STATUS=ROOT/'update-status.json'
def load_config():
    try:
        value=json.loads(CONFIG.read_text(encoding='utf-8'));owner=value.get('owner');repo=value.get('repo')
        if not isinstance(owner,str) or not isinstance(repo,str) or not owner or not repo:return None
        return {'owner':owner,'repo':repo,'enabled':value.get('enabled',True) is True,'checkSeconds':max(900,int(value.get('checkSeconds',21600)))}
    except (OSError,ValueError,TypeError):return None
def current_version():
    try:
        from sidebar_plugin import PLUGIN_VERSION;return PLUGIN_VERSION
    except Exception:return '0.0.0'
def version_tuple(value):
    chunks=[]
    for part in str(value).lstrip('vV').split('.'):
        digits=''.join(ch for ch in part if ch in '0123456789')
        if digits:chunks.append(int(digits))
    return tuple((chunks+[0,0,0])[:3])
def fetch_latest(config,opener=urllib.request.urlopen):
    url=f"https://api.github.com/repos/{config['owner']}/{config['repo']}/releases/latest"
    request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'codex-quota-badge-updater/1.0'})
    with opener(request,timeout=8) as response:
        if response.status!=200:raise RuntimeError(f'HTTP_{response.status}')
        body=response.read(262145)
        if len(body)>262144:raise ValueError('Oversized release response')
        data=json.loads(body)
    tag=data.get('tag_name');assets=data.get('assets')
    if not isinstance(tag,str) or not isinstance(assets,list):raise ValueError('Invalid GitHub release')
    # Optional gateway bundles must never be selected as the sidebar update.
    asset=next((a for a in assets if isinstance(a,dict) and a.get('name')==f"codex-quota-badge-{tag.lstrip('vV')}.zip" and isinstance(a.get('browser_download_url'),str)),None)
    if not asset:raise ValueError('No portable ZIP asset')
    return {'version':tag.lstrip('vV'),'tag':tag,'url':asset['browser_download_url'],'name':asset['name'],'digest':asset.get('digest'),'releaseUrl':data.get('html_url'),'notes':data.get('body') or ''}
def check():
    config=load_config()
    if not config or not config['enabled']:return {'state':'disabled','current':current_version()}
    try:
        latest=fetch_latest(config);new=version_tuple(latest['version'])>version_tuple(current_version())
        result={'state':'update_available' if new else 'up_to_date','current':current_version(),'checkedAt':time.time(),**latest}
    except Exception as exc:result={'state':'error','current':current_version(),'error':type(exc).__name__}
    STATUS.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
def download_and_update(latest):
    folder=Path(tempfile.mkdtemp(prefix='codex-quota-release-'));archive=folder/latest['name'];request=urllib.request.Request(latest['url'],headers={'User-Agent':'codex-quota-badge-updater/1.0'})
    with urllib.request.urlopen(request,timeout=30) as response:archive.write_bytes(response.read())
    expected=latest.get('digest')
    if isinstance(expected,str) and expected.startswith('sha256:'):
        actual=hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual.lower()!=expected.split(':',1)[1].lower():raise RuntimeError('Release SHA256 mismatch')
    extract=folder/'package';extract.mkdir();zipfile.ZipFile(archive).extractall(extract);script=extract/'update.ps1'
    if not script.exists():raise RuntimeError('Release package lacks update.ps1')
    if __import__('sys').platform=='darwin':
        return subprocess.Popen(['sh',str(extract/'update-mac.sh'),str(ROOT)])
    return subprocess.Popen(['pwsh.exe','-NoProfile','-File',str(script),'-TargetPath',str(ROOT)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
