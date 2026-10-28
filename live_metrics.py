"""Format scalar request samples without mixing requests or clock domains."""
import hashlib
import math


def nonnegative(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0


def conversation_matches(stored,task):
    # OpenCodex 2.92 stores SHA-256(thread ID)[:32] in its usage log.
    return bool(stored and any(isinstance(identity,str) and identity and
        stored in (identity,hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32])
        for identity in (task.get('id'),task.get('innerId'))))


def request_metrics(details):
    if details.get('available') is False or details.get('supportsLive') is False:return None
    rows=[r for r in details.get('requests',()) if isinstance(r,dict) and nonnegative(r.get('startedAt'))]
    finished=False
    if not rows:
        rows=[r for r in details.get('finishedRequests',()) if isinstance(r,dict) and nonnegative(r.get('startedAt'))]
        finished=bool(rows)
    if not rows:return None
    request=max(rows,key=lambda r:(r['startedAt'],str(r.get('requestId',r.get('id','')))))
    started=request['startedAt'];sample=details.get('generatedAt')
    elapsed=request.get('durationMs')
    if not nonnegative(elapsed):elapsed=sample-started if nonnegative(sample) and sample>=started else None
    first=request.get('firstOutputMs');last=request.get('lastOutputMs')
    if not nonnegative(last) and nonnegative(request.get('lastOutputAt')) and request['lastOutputAt']>=started:
        last=request['lastOutputAt']-started
    tokens=request.get('outputTokens')
    has_output=nonnegative(first) or nonnegative(last) or (nonnegative(tokens) and tokens>0)
    age=details.get('ageMs',0);age=age if nonnegative(age) else 0
    # Prefer the gateway's relative clock; generatedAt can jump with wall time.
    latest=last if nonnegative(last) else first
    output_age=elapsed-latest+age if nonnegative(elapsed) and nonnegative(latest) and elapsed>=latest else None
    state='idle' if finished else 'output' if has_output and output_age is not None and output_age<=2000 else 'waiting'
    if nonnegative(first):first_text=f'{first/1000:.1f}s'
    elif has_output:first_text='未提供'
    elif finished:first_text='未提供'
    elif nonnegative(elapsed):first_text=f'等待 {(elapsed+age)/1000:.1f}s'
    else:first_text='等待首字…'
    output_text='—'
    if has_output:
        output_text='已结束' if finished else '输出中…' if state=='output' else '暂无增量'
        if not nonnegative(tokens):output_text='未提供'
        # The numerator and denominator belong to the same captured sample.
        # A frozen sample must not slow down just because the UI repaints.
        begin=request.get('genStartMs')
        if not nonnegative(begin):begin=first
        end=last if nonnegative(last) else elapsed
        if (request.get('usageStatus')!='unsupported' and nonnegative(tokens) and tokens>0
                and nonnegative(begin) and nonnegative(end) and end-begin>=1000
                and (not nonnegative(elapsed) or end<=elapsed)):
            output_text=f'约 {tokens*1000/(end-begin):.0f} tok/s'
    return {'first':first_text,'output':output_text,'state':state,'request':request,'finished':finished}


def running_sample(provider,telemetry,snapshot):
    """Find an ended request from a still-running local turn, never another turn."""
    if snapshot.get('taskError'):return None
    tasks=snapshot.get('tasks',{}).get('tasks_running',[])
    rows=telemetry.get('samples',telemetry.get('recent',[]))
    if not rows:return None
    # Build the current turn identities once per repaint. Hashing inside the
    # request/task loop repeated the same work for every retained sample.
    starts={};digests={}
    for task in tasks:
        model=task.get('model') or ''
        route=model.split('/',1)[0] if '/' in model else task.get('provider')
        start=task.get('startedAt')
        if route!=provider or not nonnegative(start):continue
        for identity in (task.get('id'),task.get('innerId')):
            if not isinstance(identity,str) or not identity:continue
            if identity not in digests:digests[identity]=hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]
            for stored in (identity,digests[identity]):
                starts[stored]=min(starts.get(stored,start),start)
    matches=[]
    for row in rows:
        identity=row.get('conversationId')
        if not isinstance(identity,str):continue
        start=starts.get(identity);stamp=row.get('timestamp')
        if start is not None and nonnegative(stamp) and stamp>=start*1000:matches.append(row)
    if not matches:return None
    from sidebar_telemetry import display_request_index
    return matches[display_request_index(matches)]
