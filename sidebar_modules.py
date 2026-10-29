"""Local-only module adapters. No model calls and no writes to Codex databases."""
import datetime as dt
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
from urllib.parse import urlparse
from sidebar_data import ROOT
from quota_accounting import reconcile_cycle,apply_credit,add_points,date_at,number,add_interval,estimate_share

MODULES={'tasks_pending':('待处理','等待确认或补充信息'), 'tasks_running':('运行中','本机会话的当前回合'),
         'tasks_done':('待查看','已完成且 Codex 标记为未读'), 'services':('本地服务','检查配置的本机端口'),
         'shortcuts':('快捷入口','常用工具和项目目录')}

def provider_indicators(provider,snapshot,live_requests=(),active=False,now=None):
    """Completion and running are independent; only the Codex unread state clears blue."""
    now=time.time() if now is None else now
    if snapshot.get('taskError') or not 0<=now-snapshot.get('updatedAt',0)<=20:return bool(active),False
    def matches(task):
        for request in live_requests:
            if request.get('conversationId') and request['conversationId'] in (task.get('id'),task.get('innerId')):
                return request.get('provider')==provider
        model=task.get('model') or ''
        # Codex stores model_provider=openai even for SKG/model selections.
        route=model.split('/',1)[0] if '/' in model else task.get('provider')
        return route==provider
    tasks=snapshot.get('tasks',{})
    return bool(active or any(matches(t) for t in tasks.get('tasks_running',[]))),any(matches(t) for t in tasks.get('tasks_done',[]))

def atomic_json(path,data):
    """Write a snapshot without sharing one temporary name across processes."""
    import os
    token=f'{os.getpid()}.{threading.get_ident()}'
    tmp=path.with_name(path.name+'.'+token+'.tmp')
    try:
        tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        for attempt in range(4):
            try:
                tmp.replace(path);return
            except PermissionError:
                if attempt==3:raise
                time.sleep(.02*(attempt+1))
    finally:
        try:tmp.unlink(missing_ok=True)
        except OSError:pass

class QuotaHistory:
    def __init__(self,path=None):
        self.path=Path(path or ROOT/'quota-history.json')
        try:self.data=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.data={'version':3,'cycles':[]}
        self._migrate()
    def _migrate(self):
        version=self.data.get('version',1) if isinstance(self.data,dict) else 1
        backup=self.path.with_name(self.path.stem+('.schema1-backup' if version<4 else '.schema4-backup' if version<5 else '.schema5-backup')+self.path.suffix)
        if version<6 and self.path.exists() and not backup.exists():backup.write_bytes(self.path.read_bytes())
        updated={**self.data,'version':6,'cycles':[]}
        for source in self.data.get('cycles',[]):
            cycle=reconcile_cycle(source)
            cycle.setdefault('completeSamples',0);cycle.setdefault('lastBoundaryAt',None)
            cycle.setdefault('pendingLocalAt',None);cycle.setdefault('corrections',0)
            updated['cycles'].append(cycle)
        if updated!=self.data:self.data=updated;atomic_json(self.path,self.data)
    def observe(self,snapshot,local_usage=None,local_active=False,complete=False,force_local=False):
        if not snapshot:return
        remaining,reset,stamp=(snapshot.get(k) for k in ('remaining','resetsAt','updatedAt'))
        if not all(number(x,None) is not None for x in (remaining,reset,stamp)):return
        if not 0<=remaining<=100 or stamp>=reset:return
        cycles=self.data['cycles']
        if cycles and stamp<cycles[-1].get('lastAt',0):return
        # Reset headers for the same window vary by several seconds. Preserve
        # the cycle across small estimate changes instead of losing its baseline.
        if not cycles or abs(cycles[-1]['reset']-reset)>120:
            cycles.append(reconcile_cycle({'reset':reset,'firstAt':stamp,'firstRemaining':remaining,'lastAt':stamp,'lastRemaining':remaining,'days':{},'localDays':{},'otherDays':{},'localUsed':0,'otherUsed':0,'unallocated':0,'corrections':0,'completeSamples':1 if complete else 0,'lastBoundaryAt':stamp if complete else None,'pendingLocalAt':None,'lastLocalActive':bool(local_active)}))
            self._remember_local(cycles[-1],local_usage)
            self.data['cycles']=cycles[-8:];atomic_json(self.path,self.data);return
        cycle=cycles[-1]
        if stamp<=cycle['lastAt']:
            record={k:local_usage.get(k) for k in ('date','totalTokens','requests','updatedAt')} if isinstance(local_usage,dict) else None
            if self._usage_fresh(local_usage,stamp) and record!=cycle.get('lastLocalUsage'):
                if self._usage_fresh(cycle.get('lastLocalUsage'),cycle['lastAt']) and self._local_increased(cycle,local_usage):cycle['pendingLocalAt']=local_usage['updatedAt']
                self._remember_local(cycle,local_usage);atomic_json(self.path,self.data)
            return
        day=dt.datetime.fromtimestamp(stamp).date().isoformat();previous=dt.datetime.fromtimestamp(cycle['lastAt']).date().isoformat()
        local_increased=(self._usage_fresh(local_usage,stamp) and self._usage_fresh(cycle.get('lastLocalUsage'),cycle['lastAt'])
                         and self._local_increased(cycle,local_usage))
        if local_increased:cycle['pendingLocalAt']=stamp
        delta=cycle['lastRemaining']-remaining
        gap=stamp-cycle['lastAt']
        if delta<0:
            cycle['corrections']+=1;apply_credit(cycle,-delta);cycle['pendingLocalAt']=None
        elif delta>0:
            pending=cycle.get('pendingLocalAt')
            local_evidence=local_active or (force_local and complete) or local_increased or (number(pending,None) is not None and 0<=stamp-pending<=900)
            local_match=gap<=900 and local_evidence
            covered=(self._usage_fresh(local_usage,stamp) and self._usage_fresh(cycle.get('lastLocalUsage'),cycle['lastAt'])
                     and self._comparable_usage(cycle.get('lastLocalUsage'),local_usage)
                     and cycle['lastLocalUsage'].get('date')==local_usage.get('date')
                     and all(cycle['lastLocalUsage'].get(key)==local_usage.get(key) for key in ('totalTokens','requests')))
            share=estimate_share(cycle,gap,local_evidence,covered)
            add_interval(cycle,cycle['lastAt'],stamp,delta,share)
            if local_match:cycle['pendingLocalAt']=None
        elif day==previous:cycle['days'].setdefault(day,{'points':0,'samples':0})
        if complete:
            cycle['completeSamples']+=1;cycle['lastBoundaryAt']=stamp
        self._remember_local(cycle,local_usage)
        cycle.update(lastAt=stamp,lastRemaining=remaining,lastLocalActive=bool(local_active))
        self.data['cycles'][-1]=reconcile_cycle(cycle);atomic_json(self.path,self.data)
    @staticmethod
    def _usage_fresh(usage,stamp):
        if not isinstance(usage,dict):return False
        updated=number(usage.get('updatedAt'),None)
        return (updated is not None and -120<=stamp-updated<=180 and usage.get('date')==date_at(updated)
                and number(usage.get('totalTokens'),None) is not None and number(usage.get('requests'),None) is not None)
    @staticmethod
    def _comparable_usage(previous,current):
        if not isinstance(previous,dict) or not isinstance(current,dict):return False
        if number(current.get('updatedAt'))<number(previous.get('updatedAt')):return False
        if previous.get('date')==current.get('date'):
            return current.get('totalTokens',0)>=previous.get('totalTokens',0) and current.get('requests',0)>=previous.get('requests',0)
        try:return dt.date.fromisoformat(current['date'])-dt.date.fromisoformat(previous['date'])==dt.timedelta(days=1)
        except (KeyError,TypeError,ValueError):return False
    @staticmethod
    def _local_increased(cycle,usage):
        previous=cycle.get('lastLocalUsage')
        if not QuotaHistory._comparable_usage(previous,usage):return False
        if not 0<=number(usage.get('updatedAt'))-number(previous.get('updatedAt'))<=900:return False
        if previous.get('date')!=usage.get('date'):
            return QuotaHistory._comparable_usage(previous,usage) and (usage.get('totalTokens',0)>0 or usage.get('requests',0)>0)
        return (usage.get('totalTokens',0)>previous.get('totalTokens',0)
                or usage.get('requests',0)>previous.get('requests',0))
    @staticmethod
    def _remember_local(cycle,usage):
        previous=cycle.get('lastLocalUsage')
        if isinstance(usage,dict) and (not isinstance(previous,dict) or number(usage.get('updatedAt'))>=number(previous.get('updatedAt'))):
            cycle['lastLocalUsage']={k:usage.get(k) for k in ('date','totalTokens','requests','updatedAt')}
    def view(self):
        return self.data['cycles'][-1] if self.data['cycles'] else None

UUID=re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
def classify_task(turn,items,unread=False):
    status=turn['status']
    if status=='completed':return 'tasks_done' if unread else None
    if status!='inProgress':return None
    for item in items:
        if item.get('status') in ('waitingForApproval','waitingForInput','requiresAction','pendingApproval'):return 'tasks_pending'
        if item.get('type') in ('requestUserInput','approvalRequest') and item.get('status') not in ('completed','cancelled','failed'):return 'tasks_pending'
        if item.get('tool') in ('request_user_input','request_user_input_async') and item.get('status')=='inProgress':return 'tasks_pending'
    return 'tasks_running'

_task_cache={}
_task_cache_checked={}

def is_subagent_source(source):
    if not isinstance(source,str) or not source.lstrip().startswith('{'):return False
    try:value=json.loads(source)
    except ValueError:return False
    return isinstance(value,dict) and isinstance(value.get('subagent'),dict)

def read_tasks(home=None,live_ids=(),include_subagents=False,min_check_interval=0):
    import copy
    home=Path(home or Path.home()/'.codex')
    key=(str(home),tuple(sorted(live_ids)),bool(include_subagents))
    previous=_task_cache.get(key)
    # UI paints share a bounded snapshot instead of stat-ing every rollout.
    if previous and time.monotonic()-_task_cache_checked.get(key,float("-inf"))<min_check_interval and time.time()<previous[3]:
        return copy.deepcopy(previous[4])
    paths=[home/name for name in ('.codex-global-state.json','state_5.sqlite','state_5.sqlite-wal','thread_history_1.sqlite','thread_history_1.sqlite-wal')]
    def signature(paths):
        result=[]
        for path in paths:
            try:
                st=path.stat();result.append((st.st_ino,st.st_size,st.st_mtime_ns))
            except OSError:result.append(None)
        return tuple(result)
    stamp=signature(paths)
    if previous:
        old,watched,rollouts,expiry,data=previous
        if stamp==old and time.time()<expiry and signature(watched)==rollouts:
            _task_cache_checked[key]=time.monotonic()
            return copy.deepcopy(data)
    watched=[];data=_read_tasks(home,live_ids,watched,include_subagents)
    rollouts=signature(watched)
    # Invalidate on the 5-minute running freshness boundary even if no file changes.
    now=time.time();expiry=now+30
    for st in rollouts:
        if st and st[2]/1e9+300>now:expiry=min(expiry,st[2]/1e9+300)
    if signature(paths)==stamp:
        # Parent-only and subagent snapshots must not evict each other.
        if key not in _task_cache and len(_task_cache)>=8:
            evicted=next(iter(_task_cache));_task_cache.pop(evicted);_task_cache_checked.pop(evicted,None)
        _task_cache[key]=(stamp,watched,rollouts,expiry,copy.deepcopy(data))
        _task_cache_checked[key]=time.monotonic()
    return data

def _read_tasks(home=None,live_ids=(),watched=None,include_subagents=False):
    home=Path(home or Path.home()/'.codex');result={key:[] for key in MODULES if key.startswith('tasks_')};result['unverified']=[]
    global_state=json.loads((home/'.codex-global-state.json').read_text(encoding='utf-8'))
    unread=set()
    identities=global_state.get('electron-thread-read-state-v1',{}).get('unreadByIdentity',{})
    # Do not combine different signed-in accounts and falsely label a task unread.
    if len(identities)==1:
        for host,ids in next(iter(identities.values())).items():
            if host.startswith('local:'):unread.update(ids)
    with closing(sqlite3.connect((home/'state_5.sqlite').as_uri()+'?mode=ro',uri=True,timeout=.3)) as state, closing(sqlite3.connect((home/'thread_history_1.sqlite').as_uri()+'?mode=ro',uri=True,timeout=.3)) as history:
        state.row_factory=sqlite3.Row;history.row_factory=sqlite3.Row
        rows=state.execute('SELECT id,name,title,rollout_path,cwd,model_provider,model,updated_at,source,thread_source FROM threads WHERE archived=0 ORDER BY updated_at DESC LIMIT 300').fetchall()
        for row in rows:
            if not include_subagents and (row['thread_source']=='subagent' or is_subagent_source(row['source'])):continue
            if watched is not None:watched.append(Path(row['rollout_path']))
            ids=UUID.findall(Path(row['rollout_path']).name);inner=ids[-1] if ids else row['id']
            turn=history.execute('SELECT turn_id,status,started_at,completed_at FROM thread_turns WHERE thread_id=? ORDER BY started_at DESC,rollout_ordinal DESC LIMIT 1',(inner,)).fetchone()
            if not turn:continue
            items=[]
            if turn['status']=='inProgress':
                # The rollout file can stop changing while a long Codex turn is
                # still active. Prefer the thread database heartbeat, while
                # keeping an age bound so abandoned inProgress rows do not
                # light the indicator forever.
                try:fresh=time.time()-Path(row['rollout_path']).stat().st_mtime<=300
                except OSError:fresh=False
                for item in history.execute('SELECT item_json FROM thread_items WHERE thread_id=? AND turn_id=? ORDER BY rollout_ordinal DESC LIMIT 12',(inner,turn['turn_id'])):
                    try:items.append(json.loads(item[0]))
                    except ValueError:pass
                explicit_wait=classify_task(turn,items)=='tasks_pending'
                try:thread_fresh=time.time()-float(row['updated_at'])<=300
                except (TypeError,ValueError):thread_fresh=False
                try:turn_recent=time.time()-float(turn['started_at'])<=6*3600
                except (TypeError,ValueError):turn_recent=False
                if not (fresh or thread_fresh or explicit_wait or row['id'] in live_ids or inner in live_ids) or not turn_recent:
                    result['unverified'].append({'id':row['id'],'title':row['name'] or '未命名会话'})
                    continue
            kind=classify_task(turn,items,row['id'] in unread)
            if not kind:continue
            title=row['name'] or row['title'].splitlines()[0][:80] or '未命名会话'
            result[kind].append({'id':row['id'],'innerId':inner,'title':title,'startedAt':turn['started_at'],'completedAt':turn['completed_at'],'provider':row['model_provider'],'model':row['model'],'cwd':row['cwd'],'isSubagent':bool(include_subagents and (row['thread_source']=='subagent' or is_subagent_source(row['source'])) )})
    return result

def local_url(url):
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed.username or parsed.password:raise ValueError('仅支持不含凭据的本机 HTTP 地址')
    return parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80)

def service_status(service):
    host,port=local_url(service['url'])
    try:
        with socket.create_connection((host,port),timeout=.35):pass
        return {**service,'state':'online','detail':'端口可连接'}
    except OSError:return {**service,'state':'offline','detail':'端口不可连接'}

class Modules:
    def __init__(self):
        self.lock=threading.Lock();self.busy=False;self.next_poll=0
        self.service_key=None;self.service_cache=[];self.next_services=0
        self.snapshot={'tasks':{},'subagentTasks':{'tasks_running':[]},'services':[],'updatedAt':0,'taskError':'正在读取'}
    def poll(self,config,live_ids=()):
        import copy
        with self.lock:
            if self.busy or time.monotonic()<self.next_poll:return
            self.busy=True
        try:
            threading.Thread(target=self._refresh,args=(copy.deepcopy(config),tuple(live_ids)),daemon=True).start()
        except Exception:
            with self.lock:self.busy=False;self.next_poll=time.monotonic()+2
            raise
    def _refresh(self,config,live_ids=()):
        result={'tasks':{},'services':[],'updatedAt':time.time(),'taskError':None}
        try:
            try:
                all_tasks=read_tasks(live_ids=live_ids,include_subagents=True)
                result['tasks']={kind:[task for task in all_tasks.get(kind,[]) if not task.get('isSubagent')] for kind in ('tasks_pending','tasks_running','tasks_done')}
                result['subagentTasks']={'tasks_running':[task for task in all_tasks.get('tasks_running',[]) if task.get('isSubagent')]}
                result['tasks']['unverified']=all_tasks.get('unverified',[])
            except Exception:
                try:result['tasks']=read_tasks(live_ids=live_ids)
                except Exception:result['taskError']='本机会话状态暂不可用'
                result['subagentTasks']={'tasks_running':[]}
            services=config.get('services',[])
            key=json.dumps(services,sort_keys=True)
            if key!=self.service_key or time.monotonic()>=self.next_services:
                checked=[]
                for service in services:
                    try:checked.append(service_status(service))
                    except Exception:checked.append({**service,'state':'unknown','detail':'服务状态暂不可用'})
                self.service_cache=checked;self.service_key=key;self.next_services=time.monotonic()+10
            result['services']=self.service_cache
        except Exception:
            result['services']=self.service_cache
        finally:
            # Running turns need a short cadence for the green indicator. When
            # nothing is running, the same file scan only needs to refresh
            # occasionally; this avoids repeatedly walking rollout metadata
            # while the sidebar is idle.
            running=bool(result.get('tasks',{}).get('tasks_running') or
                         result.get('subagentTasks',{}).get('tasks_running'))
            cadence=2 if running else 5
            with self.lock:self.snapshot=result;self.next_poll=time.monotonic()+cadence;self.busy=False
    def view(self):
        with self.lock:return self.snapshot.copy()

def module_value(kind,snapshot,config):
    if kind.startswith('tasks_'):
        if snapshot.get('taskError') or time.time()-snapshot.get('updatedAt',0)>20:return '—'
        if kind=='tasks_pending' and not snapshot.get('tasks',{}).get(kind):return '—'
        return str(len(snapshot.get('tasks',{}).get(kind,[])))
    if kind=='services':return str(sum(s['state']!='online' for s in snapshot.get('services',[]))) if snapshot.get('updatedAt') else '—'
    return str(len(config.get('shortcuts',[])))
