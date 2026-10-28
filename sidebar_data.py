"""Read-only OpenCodex adapter and configurable sidebar item registry."""
import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
import re
import copy

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'sidebar-config.json'
GAP_MIN,GAP_MAX=-10,30


def compact_tokens(value):
    if value is None:
        return '—'
    value = max(0, int(value))
    # Round half up; promote a rounded 1000K to 1M, etc.
    for unit, suffix in ((10**9,'B'),(10**6,'M'),(1000,'K')):
        if value >= unit or value >= unit - unit//2000:
            return f'{(value+unit//2)//unit}{suffix}'
    return str(value)


def load_config():
    config = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    return validate_config(config)


def validate_config(config):
    if config.get('version') != 1 or not isinstance(config.get('items'),list):
        raise ValueError('Invalid sidebar configuration')
    ids = set()
    for item in config['items']:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or item['id'] in ids:
            raise ValueError('Item ids must be unique strings')
        ids.add(item['id'])
        if item.get('type') not in ('opencodex_today','weekly_quota','tasks_pending','tasks_running','tasks_done','services','shortcuts'):
            raise ValueError('Unsupported item type')
        if not isinstance(item.get('label'),str) or not 1 <= len(item['label']) <= 14:
            raise ValueError('Item labels must have 1-14 characters')
        if item['type']=='opencodex_today' and not isinstance(item.get('provider'),str):
            raise ValueError('Daily token items require a provider')
        if not isinstance(item.get('enabled',True),bool):
            raise ValueError('enabled must be a boolean')
    if not 1 <= len(config['items']) <= 16:
        raise ValueError('Keep 1-16 registered items (all may be hidden)')
    config.setdefault('services',[]);config.setdefault('shortcuts',[])
    from urllib.parse import urlparse
    for service in config['services']:
        parsed=urlparse(service.get('url',''))
        if parsed.scheme not in ('http','https') or parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed.username or parsed.password:raise ValueError('Services must use local HTTP URLs without credentials')
        if not 1<=len(service.get('label',''))<=30:raise ValueError('Invalid service label')
    if len(config['services'])>8 or len(config['shortcuts'])>12:raise ValueError('Too many services or shortcuts')
    for shortcut in config['shortcuts']:
        target=shortcut.get('target','')
        if shortcut.get('kind') not in ('manager','opencodex','usage','folder'):raise ValueError('Unknown shortcut kind')
        if shortcut['kind']=='folder' and not Path(target).is_absolute():raise ValueError('Folder must be absolute')
        if not 1<=len(shortcut.get('label',''))<=30:raise ValueError('Invalid shortcut label')
    for key in ('compactMode','quotaAlertEnabled'):
        config.setdefault(key,False)
        if not isinstance(config[key],bool):raise ValueError('Invalid '+key)
    config.setdefault('quotaAlertThreshold',20)
    if type(config['quotaAlertThreshold']) is not int or not 1<=config['quotaAlertThreshold']<=100:
        raise ValueError('额度提醒阈值请输入 1–100 的整数')
    for key in ('activityEnabled','realtimeMetricsEnabled'):
        config.setdefault(key,True)
        if not isinstance(config[key],bool):raise ValueError('Invalid '+key)
    config.setdefault('quotaStyle','01')
    if config['quotaStyle'] not in [f'{i:02}' for i in range(1,18)]:raise ValueError('Unknown quota style')
    config.setdefault('style','10')
    if config['style'] not in [f'{i:02}' for i in range(1,11)]:
        raise ValueError('Unknown style')
    for key,default,low,high in [('gap',0,GAP_MIN,GAP_MAX),('bottomOffset',87,45,180),('horizontalOffset',0,-40,40)]:
        config[key]=int(config.get(key,default))
        if not low<=config[key]<=high: raise ValueError('Invalid '+key)
    for item in config['items']:
        item['gapAfter']=int(item.get('gapAfter',config['gap']))
        if not GAP_MIN<=item['gapAfter']<=GAP_MAX: raise ValueError('模块间距请输入 -10–30 的整数')
    config['refreshSeconds'] = max(30,min(3600,int(config.get('refreshSeconds',60))))
    return config


def save_config(config):
    validate_config(config)
    import tempfile,os,time
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=CONFIG.parent,prefix=CONFIG.name+'.',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name)
            stream.write(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
            stream.flush();os.fsync(stream.fileno())
        # Windows readers/parallel writers may hold a brief sharing lock.
        for attempt in range(5):
            try:
                temporary.replace(CONFIG)
                break
            except PermissionError:
                if attempt==4:raise
                time.sleep(.02*(attempt+1))
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def parse_usage(report,period="today"):
    if period not in ('today','7d','30d') or report.get('range') != period or not isinstance(report.get('providers'),list):
        raise ValueError('Unexpected OpenCodex usage response')
    generated = float(report['generatedAt'])/1000
    today = dt.datetime.fromtimestamp(generated).date().isoformat()
    partial = any(report.get(k,False) for k in ('historyTruncated','entriesTruncated'))
    providers={}
    for p in report['providers']:
        total = p.get('totalTokens')
        if not isinstance(total,(int,float)) or total < 0:
            raise ValueError('Invalid token total')
        providers[p['provider']] = {
            'totalTokens':int(total),'requests':int(p.get('requests',0)),
            'reportedRequests':int(p.get('reportedRequests',p.get('measuredRequests',0))),
            'inputTokens':int(p.get('inputTokens',0)), 'outputTokens':int(p.get('outputTokens',0)),
            'cachedInputTokens':int(p.get('cachedInputTokens',0)),
        }
    return {'range':period,'date':today,'updatedAt':generated,'providers':providers,'partial':partial,
            'source':f'OpenCodex usage --range {period} --surface all',
            'scope':'本机 OpenCodex 记录；按 provider 汇总，非账号全设备或公司账单'}


def fetch_usage(period="today"):
    if period not in ("today","7d","30d"):raise ValueError("Unsupported usage period")
    candidates=[shutil.which('ocx'),shutil.which('opencodex'),
                r'C:\Program Files\OpenCodex\ocx.exe',
                str(Path.home()/'.opencodex/bin/ocx'),
                '/usr/local/bin/ocx','/opt/homebrew/bin/ocx']
    executable=next((item for item in candidates if item and Path(item).exists()),None)
    if not executable:raise FileNotFoundError('未找到 OpenCodex 用量命令 ocx，请确认 OpenCodex 已安装并加入 PATH')
    proc=subprocess.run([executable,'usage','--range',period,'--surface','all','--json'],
        capture_output=True,text=True,encoding='utf-8',timeout=25,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
    usage=parse_usage(json.loads(proc.stdout),period)
    # Usage contains only providers that produced requests in the selected
    # period. Read the non-secret configured-provider list separately so an
    # idle provider remains visible as zero instead of disappearing.
    try:
        listed=subprocess.run([executable,'provider','list','--json'],capture_output=True,text=True,encoding='utf-8',timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
        document=json.loads(listed.stdout);configured=document.get('configured',[])
        names=[row.get('name') for row in configured if isinstance(row,dict) and isinstance(row.get('name'),str) and row.get('name')]
        if names:usage['configuredProviders']=names
    except (OSError,ValueError,TypeError,subprocess.SubprocessError):pass
    return usage

def provider_item_id(provider):
    safe=re.sub(r'[^A-Za-z0-9_-]+','_',str(provider)).strip('_') or 'provider'
    return 'provider_'+safe.lower()

def provider_label(provider):
    return 'GPT' if provider=='openai' else str(provider)

def sync_provider_items(config,report):
    """Sync provider names while preserving the order chosen in management UI."""
    if not isinstance(report,dict):return config
    providers=report.get('providers',{})
    if not isinstance(providers,dict):return config
    # Return a distinct value so callers can reliably detect and persist
    # provider additions, removals, label updates, and order changes.
    config=copy.deepcopy(config)
    existing_items=[i for i in config.get('items',[]) if i.get('type')=='opencodex_today']
    existing={i.get('provider'):i for i in existing_items}
    # OpenCodex output order may change between refreshes. Keep providers already
    # known to the config in their management-page order, then append genuinely
    # new providers in the order first reported by OpenCodex.
    configured=report.get('configuredProviders')
    authoritative=[provider for provider in configured if isinstance(provider,str)] if isinstance(configured,list) else None
    available=authoritative or list(providers)
    provider_order=[i.get('provider') for i in existing_items if authoritative is None or i.get('provider') in available]
    provider_order.extend(provider for provider in available if provider not in existing)
    dynamic=[]
    for provider in provider_order:
        item=existing.get(provider)
        if item is None:
            item={'id':provider_item_id(provider),'type':'opencodex_today','provider':provider,'enabled':True,'gapAfter':config.get('gap',3),'autoProvider':True,'label':provider_label(provider)[:14]}
        elif item.get('autoProvider'):
            item['label']=provider_label(provider)[:14]
        dynamic.append(item)
    old_auto=existing_items
    kept=[i for i in config.get('items',[]) if i not in old_auto]
    quota=[i for i in kept if i.get('type')=='weekly_quota']
    before=kept[:kept.index(quota[0])] if quota else kept
    after=kept[kept.index(quota[0]):] if quota else []
    config['items']=before+dynamic+after
    return validate_config(config)


def item_usage(snapshot, provider, now=None, max_age=180):
    now = now or dt.datetime.now().astimezone()
    if not snapshot or snapshot['date'] != now.date().isoformat() or not 0 <= now.timestamp()-snapshot['updatedAt'] <= max_age:
        return None
    row=snapshot['providers'].get(provider)
    if row is None:
        if snapshot['partial']:
            return None
        return {'totalTokens':0,'requests':0,'reportedRequests':0,'inputTokens':0,'outputTokens':0,'cachedInputTokens':0}
    if row['requests'] and not row['reportedRequests']:
        return None
    return row
