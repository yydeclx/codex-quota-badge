"""Bounded, nonblocking reads of the local proxy's Responses request lifecycle."""
import base64, hashlib, hmac, http.client, json, os, re, secrets, tempfile, threading, time
from pathlib import Path
from live_metrics import nonnegative
ACTIVITY_PATH='/api/system/provider-activity'
LOGS_PATH='/api/logs?limit=50'
MAX_SAMPLE_AGE=3

def _read_json(path,limit=8192):
    with path.open('rb') as source:raw=source.read(limit+1)
    if len(raw)>limit:raise ValueError('Oversized local state')
    return json.loads(raw)

def activity_from_logs(data,now_ms=None):
    if not isinstance(data,dict) or not isinstance(data.get('logs'),list):raise ValueError('Invalid logs response')
    now=int(time.time()*1000) if now_ms is None else now_ms
    # Stock 2.86+ logs are written after a request ends. Missing terminalStatus
    # is common in older records and is NOT evidence that a request is active.
    return {'schemaVersion':3,'scope':'responses','generatedAt':now,
            'providers':{},'requests':[],'source':'api/logs','supportsLive':False}

def read_local(path=ACTIVITY_PATH, home=None):
    root=Path(home or os.environ.get('OPENCODEX_HOME') or Path.home()/'.opencodex')
    state=_read_json(root/'runtime-port.json')
    pid,port,secret=state['pid'],state['port'],state['attestationSecret']
    if type(pid) is not int or pid<=0 or type(port) is not int or not 1<=port<=65535 or not isinstance(secret,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',secret):
        raise ValueError('Invalid local runtime identity')
    nonce=secrets.token_urlsafe(32); expires=int(time.time()*1000)+5000
    payload=f'opencodex-local-management-read-v1\n{nonce}\nGET\n{path}\n{pid}\n{port}\n{expires}'
    signature=base64.urlsafe_b64encode(hmac.new(secret.encode(),payload.encode(),hashlib.sha256).digest()).decode().rstrip('=')
    headers={'x-opencodex-local-expected-pid':str(pid),'x-opencodex-local-nonce':nonce,'x-opencodex-local-expires-at':str(expires),'x-opencodex-local-capability':signature}
    # Direct loopback; no redirects, proxy, administrator or model credential.
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=.8)
    try:
        conn.request('GET',path,headers=headers)
        response=conn.getresponse()
        if response.status!=200:raise RuntimeError(f'HTTP_{response.status}')
        timeline=path.startswith('/api/usage/timeline?')
        # A 168-hour timeline can contain one series per model. Keep the
        # response bounded, but allow the normal multi-model response to
        # complete instead of marking it truncated and hiding the chart.
        limit=4*1024*1024 if timeline else 256*1024 if path==ACTIVITY_PATH else 65536
        body=response.read(limit+1)
        if len(body)>limit:raise ValueError('Oversized response')
        data=json.loads(body)
        if not timeline and data.get('pid')!=pid:raise ValueError('Runtime identity changed')
        if timeline:
            # The server verifies the PID-bound capability; recheck discovery after reading.
            current=_read_json(root/'runtime-port.json')
            if (current.get('pid'),current.get('port'))!=(pid,port):raise ValueError('Runtime identity changed')
        return data
    finally:conn.close()

def _read_logs(home=None):
    """Read the 2.86+ request log after provider-activity was removed."""
    root=Path(home or os.environ.get('OPENCODEX_HOME') or Path.home()/'.opencodex')
    token=(root/'admin-api-token').read_text(encoding='utf-8').strip()
    if not token:raise ValueError('Admin API token unavailable')
    with (root/'runtime-port.json').open('rb') as source:state=json.loads(source.read(8192))
    pid,port=state['pid'],state['port']
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=.8)
    try:
        conn.request('GET',LOGS_PATH,headers={'Authorization':'Bearer '+token})
        response=conn.getresponse()
        if response.status!=200:raise RuntimeError(f'HTTP_{response.status}')
        body=response.read(1048577)
        if len(body)>1048576:raise ValueError('Oversized response')
        return activity_from_logs(json.loads(body))
    finally:conn.close()

def read_activity(home=None):
    try:return read_local(ACTIVITY_PATH,home)
    except RuntimeError as exc:
        if str(exc) not in ('HTTP_401','HTTP_404'):raise
        return _read_logs(home)

class ProviderActivity:
    def __init__(self,fetch=None,clock=None,history_path=None,enabled=True):
        self.fetch=fetch or read_activity;self.clock=clock or time.monotonic
        self.snapshot=None;self.received=0;self.error='尚未连接';self.busy=False;self.next_poll=0
        self.lock=threading.Lock()
        self.enabled=bool(enabled);self.generation=0
        self.failures=0;self.offline_failures=0
        self.events=[];self.last_health=None
        self.source=None if self.enabled else 'legacy-log'
        self.history_path=Path(history_path) if history_path else None
        if self.enabled and self.history_path:
            try:
                rows=_read_json(self.history_path,65536)
                if not isinstance(rows,list):raise ValueError('Invalid history')
                self.events=[{'state':r['state'],'at':r['at']} for r in rows if isinstance(r,dict) and r.get('state') in ('red','amber','recovered') and nonnegative(r.get('at'))][-20:]
                if self.events:self.last_health=self.events[-1]['state'] if self.events[-1]['state']!='recovered' else None
            except (OSError,ValueError,TypeError):pass
    def set_enabled(self,enabled):
        with self.lock:
            if self.enabled==bool(enabled):return
            self.enabled=bool(enabled);self.generation+=1
            self.snapshot=None;self.received=0;self.error=None;self.next_poll=0
            self.failures=0;self.offline_failures=0;self.events=[];self.last_health=None
            self.source=None if self.enabled else 'legacy-log'
    def poll(self):
        with self.lock:
            if not self.enabled or self.busy or self.clock()<self.next_poll:return
            self.busy=True;generation=self.generation
        try:threading.Thread(target=self._refresh,args=(generation,),daemon=True).start()
        except Exception:
            with self.lock:self.busy=False;self.next_poll=self.clock()+5

    def _refresh(self,generation=None):
        with self.lock:
            if generation is None:generation=self.generation
            if not self.enabled or generation!=self.generation:self.busy=False;return
        try:
            data=self.fetch()
            if not isinstance(data,dict) or type(data.get('schemaVersion')) is not int or data['schemaVersion'] not in (1,2,3) or data.get('scope')!='responses' or not isinstance(data.get('providers'),dict):raise ValueError('Unsupported schema')
            for row in data['providers'].values():
                if not isinstance(row,dict) or type(row.get('active')) is not int or row['active']<0:raise ValueError('Invalid count')
            if 'supportsLive' in data and type(data['supportsLive']) is not bool:raise ValueError('Invalid live capability')
            # Copy a bounded sample before publishing it to the UI thread.
            data={**data,'providers':{key:dict(row) for key,row in data['providers'].items()}}
            for key,limit in (('requests',128),('finishedRequests',32)):
                rows=data.get(key,[])
                if not isinstance(rows,list):raise ValueError('Invalid requests')
                data[key]=[dict(row) for row in rows[:limit] if isinstance(row,dict)]
            source=data.get('source') or 'provider-activity'
            data['supportsLive']=data.get('supportsLive',source!='api/logs') and source!='api/logs'
            with self.lock:
                if not self.enabled or generation!=self.generation:return
                self.snapshot=data;self.source=source;self.received=self.clock();self.error=None;self.failures=0;self.offline_failures=0
        except Exception as exc:
            with self.lock:
                if not self.enabled or generation!=self.generation:return
                # Keep the last captured sample only until its original 3s TTL.
                self.error=str(exc) if isinstance(exc,RuntimeError) and str(exc) in ('HTTP_401','HTTP_404') else type(exc).__name__;self.failures+=1
                self.offline_failures=self.offline_failures+1 if isinstance(exc,(ConnectionRefusedError,FileNotFoundError)) else 0
        finally:
            try:
                with self.lock:
                    if not self.enabled or generation!=self.generation:return
                    delay=(1 if self.snapshot.get('supportsLive') else 5) if not self.error else 60 if self.error in ('HTTP_401','HTTP_404') else min(30,5*2**min(self.failures-1,3))
                    self.next_poll=self.clock()+delay
                health=self.health()[0]
                changed=False
                with self.lock:
                    if not self.enabled or generation!=self.generation:return
                    if health!=self.last_health:
                        if health or self.last_health:self.events=(self.events+[{'at':time.time(),'state':health or 'recovered'}])[-20:];changed=True
                        self.last_health=health
                    events=list(self.events)
                if changed and self.history_path:
                    temp=None
                    try:
                        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=self.history_path.parent,
                                prefix=self.history_path.name+'.',suffix='.tmp',delete=False) as output:
                            temp=Path(output.name);output.write(json.dumps(events))
                        with self.lock:
                            if self.enabled and generation==self.generation:temp.replace(self.history_path)
                    except OSError:pass
                    finally:
                        if temp is not None:
                            try:temp.unlink(missing_ok=True)
                            except OSError:pass
            finally:
                # Keep one worker until persistence and cleanup are complete.
                with self.lock:self.busy=False
    def count(self,provider):
        with self.lock:
            if self.snapshot is None or not self.snapshot['supportsLive'] or self.clock()-self.received>MAX_SAMPLE_AGE:return None
            return self.snapshot['providers'].get(provider,{}).get('active')
    def active(self,provider):return (self.count(provider) or 0)>0
    def health(self):
        with self.lock:
            if self.offline_failures>=2:return ('red','OpenCodex 未连接')
            if self.failures>=2 and self.error in ('HTTP_401','HTTP_404'):return ('amber','活动接口未授权或版本不兼容')
            if self.failures>=2:return ('amber','链路状态暂不可用')
            return (None,None)
    def state(self,provider):
        with self.lock:
            elapsed=max(0,self.clock()-self.received)
            if self.snapshot is None or not self.snapshot['supportsLive'] or elapsed>MAX_SAMPLE_AGE:return 'unknown'
            row=self.snapshot['providers'].get(provider)
            if row is None:return 'unknown'
            if not row['active']:return 'idle'
            stamp=row.get('lastOutputAt')
            generated=self.snapshot.get('generatedAt')
            if self.snapshot.get('schemaVersion') in (2,3) and nonnegative(stamp) and nonnegative(generated):
                age=(generated-stamp)/1000+elapsed
                if 0<=age<=2:return 'output'
            return 'waiting'
    def details(self,provider):
        with self.lock:
            age=max(0,self.clock()-self.received)
            fresh=self.snapshot is not None and age<=MAX_SAMPLE_AGE
            live=fresh and self.snapshot['supportsLive']
            rows=[dict(r) for r in self.snapshot['requests'] if r.get('provider')==provider] if live else []
            finished=[dict(r) for r in self.snapshot['finishedRequests'] if r.get('provider')==provider] if live else []
            return {'requests':rows,'finishedRequests':finished,'events':[dict(r) for r in self.events],
                    'available':fresh,'supportsLive':bool(live),'stale':bool(fresh and self.error),
                    'detailed':live and self.snapshot.get('schemaVersion')==3,
                    'generatedAt':self.snapshot.get('generatedAt') if fresh else None,'ageMs':age*1000,'source':self.source}
    def description(self,provider):
        state=self.state(provider)
        return {'unknown':'链路状态暂不可用','idle':'当前无在途模型请求',
                'waiting':'浅绿常亮：请求等待中，暂无内容增量',
                'output':'绿点呼吸：最近收到模型内容增量（含推理或工具参数）'}[state]
