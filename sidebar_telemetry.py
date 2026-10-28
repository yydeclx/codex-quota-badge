"""Read-only bounded recent outcomes and cached local-calendar usage bars."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import threading
import time
from sidebar_activity import read_local
from live_metrics import nonnegative

TIMELINE='/api/usage/timeline?hours=168&bucketMinutes=60&metric=total&aggregation=sum&grouping=model'
MAX_TAIL_BYTES=2*1024*1024
MAX_PROVIDERS=128

def provider_name(name):
    name=re.sub(r'-p[0-9a-f]{6}$','',name)
    if name in ('openai-main','chatgpt-main','openai-multi-main'):name=name[:-5]
    return 'openai' if name in ('chatgpt','openai-multi') else name

def outcome(row):
    status=row.get('status',0);terminal=row.get('terminalStatus');close=row.get('closeReason')
    if close=='client_cancel' or status==499:return 'aborted'
    if terminal=='failed':return 'failed'
    if terminal=='incomplete' or close in ('body_stall','body_overflow'):return 'incomplete'
    if terminal=='completed':return 'completed'
    if terminal is None and (status==101 or 200<=status<400):return 'completed'
    return 'failed'

def request_health(rows,now=None):
    now=time.time() if now is None else now
    # A later success clears the warning; a user cancellation is not a failure.
    recent=[r for r in rows if 0<=now-r['timestamp']/1000<=300 and r['outcome']!='aborted']
    if not recent or recent[-1]['outcome']=='completed':return (None,None)
    last=recent[-1]
    if last['status'] in (401,403):return ('red','最近请求鉴权失败')
    if last['status']==429:return ('amber','最近请求被限流')
    streak=0
    for row in reversed(recent):
        if row['outcome'] not in ('failed','incomplete'):break
        streak+=1
    if streak>=3:return ('red',f'连续 {streak} 次请求异常')
    return ('amber','最近请求未完成' if last['outcome']=='incomplete' else '最近请求失败')

def display_request_index(rows):
    """Ignore a short-lived no-output cancel after a substantive response."""
    if not rows:return None
    index=len(rows)-1;last=rows[index]
    if last.get('outcome')!='aborted' or last.get('firstOutputMs') is not None:return index
    conversation=last.get('conversationId')
    for candidate in range(index-1,-1,-1):
        row=rows[candidate]
        if last.get('timestamp',0)-row.get('timestamp',0)>180000:break
        if conversation and row.get('conversationId')!=conversation:continue
        if row.get('outcome')=='completed' and (row.get('firstOutputMs') is not None or row.get('decodeTokPerSecond',0)>0):
            return candidate
    return index

def daily_trend(data,now=None):
    today=(now or dt.datetime.now().astimezone()).date()
    days=[today-dt.timedelta(days=6-i) for i in range(7)]
    # OpenCodex may mark a timeline as truncated when its measurement set is
    # incomplete, even though it still returns every requested bucket and a
    # complete point vector for each model. In that case the chart is useful:
    # missing measurements are represented by zero buckets, while malformed
    # or short vectors remain rejected below.
    if data.get('truncated') and not (
        data.get('buckets')==168 and isinstance(data.get('series'),list) and
        all(isinstance(row,dict) and isinstance(row.get('points'),list) and len(row['points'])==168
            for row in data['series'])
    ):raise ValueError('Incomplete timeline')
    if data.get('metric')!='total' or data.get('aggregation')!='sum' or data.get('bucketSeconds')!=3600:raise ValueError('Unexpected timeline')
    result={};start=data['start'];count=data['buckets']
    if count!=168:raise ValueError('Unexpected bucket count')
    for row in data['series']:
        values=row['points']
        if len(values)!=count:raise ValueError('Unexpected points')
        target=result.setdefault(provider_name(row['provider']),[0]*7)
        for index,value in enumerate(values):
            day=dt.datetime.fromtimestamp(start+index*3600).date()
            if day in days and isinstance(value,(int,float)) and value>=0:target[days.index(day)]+=int(value)
    return {'labels':[d.strftime('%m/%d') for d in days],'providers':result}

class Telemetry:
    def __init__(self,path=None):
        self.path=Path(path or Path(os.environ.get('OPENCODEX_HOME',Path.home()/'.opencodex'))/'usage.jsonl')
        self.lock=threading.Lock();self.busy=False;self.next_poll=0;self.next_trend=0
        self.rows={};self.trend=None;self.trend_at=0;self.records_at=0;self.signature=None
        self.offset=0;self.pending=b'';self.skip_partial=False;self.anchor=b''
        self.record_failures=0;self.trend_failures=0
    def poll(self):
        with self.lock:
            if self.busy or time.monotonic()<self.next_poll:return
            self.busy=True
        try:threading.Thread(target=self._refresh,daemon=True).start()
        except Exception:
            with self.lock:self.busy=False;self.next_poll=time.monotonic()+5

    def _refresh(self):
        try:
            try:
                if self._records():self.record_failures=0
                else:self.record_failures+=1
            except Exception:
                self.record_failures+=1
                with self.lock:self.records_at=0
            if time.monotonic()>=self.next_trend:
                try:
                    trend=daily_trend(read_local(TIMELINE))
                    with self.lock:self.trend=trend;self.trend_at=time.monotonic()
                    self.trend_failures=0;self.next_trend=time.monotonic()+300
                except Exception:
                    self.trend_failures+=1
                    self.next_trend=time.monotonic()+min(300,30*2**min(self.trend_failures-1,4))
        finally:
            delay=2 if not self.record_failures else min(60,5*2**min(self.record_failures-1,4))
            with self.lock:self.busy=False;self.next_poll=time.monotonic()+delay
    def _records(self):
        try:
            stat=self.path.stat();signature=(stat.st_ino,stat.st_size,stat.st_mtime_ns)
            if signature==self.signature:
                with self.lock:self.records_at=time.monotonic()
                return True
            # Append-only reads retain an incomplete last record, never rescan the
            # whole tail. Rotation, truncation and a backlog over 2 MiB reset it.
            with self.path.open('rb') as source:
                stat=os.fstat(source.fileno());signature=(stat.st_ino,stat.st_size,stat.st_mtime_ns)
                append=(self.signature is not None and signature[0]==self.signature[0]
                        and stat.st_size>self.offset and stat.st_size-self.offset<=MAX_TAIL_BYTES)
                if append and self.anchor:
                    source.seek(self.offset-len(self.anchor))
                    append=source.read(len(self.anchor))==self.anchor
                offset=self.offset if append else max(0,stat.st_size-MAX_TAIL_BYTES)
                pending=self.pending if append else b''
                skip_partial=self.skip_partial if append else offset>0
                source.seek(offset);raw=source.read(min(MAX_TAIL_BYTES,stat.st_size-offset))
            anchor=raw[-64:] if len(raw)>=64 or not append else (self.anchor+raw)[-64:]
            next_offset=offset+len(raw)
            rows={k:list(v) for k,v in self.rows.items()} if append else {}
            if skip_partial:
                boundary=raw.find(b'\n')
                skip_partial=boundary<0
                raw=b'' if skip_partial else raw[boundary+1:]
            raw=pending+raw
            lines=raw.splitlines(keepends=True);pending=b''
            if lines and not lines[-1].endswith(b'\n'):
                pending=lines.pop()
                if len(pending)>MAX_TAIL_BYTES:pending=b'';skip_partial=True
            for line in lines:
                if not line.endswith(b'\n'):continue
                if len(line)>MAX_TAIL_BYTES:continue
                try:
                    entry=json.loads(line)
                    if not isinstance(entry,dict) or not isinstance(entry.get('provider'),str):continue
                    if not 1<=len(entry['provider'])<=100:continue
                    provider=provider_name(entry['provider'])
                    row={k:entry.get(k) for k in ('timestamp','status','durationMs')}
                    if not all(nonnegative(row[k]) for k in row):continue
                    row['outcome']=outcome(entry)
                    conversation=entry.get('conversationId')
                    if isinstance(conversation,str) and re.fullmatch(r'[\w-]{1,100}',conversation):
                        row['conversationId']=conversation
                    request_id=entry.get('requestId')
                    if isinstance(request_id,str) and re.fullmatch(r'[\w-]{1,100}',request_id):row['requestId']=request_id
                    for key in ('firstOutputMs','decodeTokPerSecond','genStartMs','lastOutputMs'):
                        value=entry.get(key)
                        if nonnegative(value) and value<1e9:row[key]=value
                    # Strict allowlist: never retain prompts, headers, errors or protocol traces.
                    for key in ('servedModel','resolvedModel','requestedModel','requestedEffort'):
                        value=entry.get(key)
                        if isinstance(value,str) and re.fullmatch(r'[\w./:+ -]{1,100}',value):row[key]=value
                    attempts=entry.get('attempts') or []
                    if not isinstance(attempts,list):continue
                    row['retries']=max(0,sum(max(0,int(a.get('sendCount',1))) for a in attempts if isinstance(a,dict))-1)
                    usage=entry.get('usage') or {}
                    if not isinstance(usage,dict):continue
                    first=row.get('firstOutputMs');output=usage.get('outputTokens',0)
                    begin=row.get('genStartMs',first);end=row.get('lastOutputMs',row['durationMs'])
                    if begin is not None and end-begin>=1000 and nonnegative(output) and output>0 and entry.get('usageStatus')!='unsupported':
                        row['decodeTokPerSecond']=output*1000/(end-begin)
                    values=rows.pop(provider,[]);values.append(row)
                    rows[provider]=sorted(values,key=lambda r:r['timestamp'])[-32:]
                    if len(rows)>MAX_PROVIDERS:rows.pop(next(iter(rows)))
                except (ValueError,KeyError,TypeError,OverflowError):continue
            with self.lock:
                self.rows=rows;self.records_at=time.monotonic();self.signature=signature
                self.offset=next_offset;self.pending=pending;self.skip_partial=skip_partial;self.anchor=anchor
            return True
        except OSError:
            with self.lock:self.records_at=0
            return False
    def view(self,provider):
        with self.lock:
            rows=list(self.rows.get(provider,[])) if self.records_at and time.monotonic()-self.records_at<15 else []
            display_index=display_request_index(rows)
            latest=rows[display_index] if display_index is not None else None
            previous=next((row for row in reversed(rows[:display_index] if display_index is not None else [])
                           if row.get('outcome')=='completed' and row.get('decodeTokPerSecond',0)>0),None)
            trend=None
            # daily_trend uses the local timezone; use the same timezone when
            # deciding whether the cached seven-day series is still current.
            local_today=dt.datetime.now().astimezone().strftime('%m/%d')
            if self.trend and time.monotonic()-self.trend_at<900 and self.trend['labels'][-1]==local_today:
                trend={'labels':self.trend['labels'],'values':self.trend['providers'].get(provider,[0]*7)}
        return {'latest':latest,'previous':previous,'health':request_health(rows),'trend':trend,'recent':rows[-6:],'samples':rows}

def speed_description(row):
    if not row:return ''
    parts=[]
    if row.get('firstOutputMs') is not None:parts.append(f"首字 {row['firstOutputMs']/1000:.1f}s")
    if row.get('decodeTokPerSecond',0)>0:parts.append(f"输出约 {row['decodeTokPerSecond']:.0f} tok/s")
    return ' · '.join(parts)

def previous_speed_description(row):
    if not row or row.get('decodeTokPerSecond',0)<=0:return '—'
    return f"约 {row['decodeTokPerSecond']:.0f} tok/s"

def recent_description(row):
    if not row:return '最近请求 · 暂无记录'
    label={'completed':'成功','aborted':'已取消','incomplete':'未完成','failed':'失败'}[row['outcome']]
    stamp=dt.datetime.fromtimestamp(row['timestamp']/1000).strftime('%m/%d %H:%M')
    seconds=max(0,row['durationMs'])/1000
    duration=f'{seconds:.1f}秒' if seconds<60 else f'{seconds/60:.1f}分'
    return f'最近 {stamp} · {label} · {duration}'
