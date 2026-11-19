"""Account-window/request correlation estimates; no fixed source split.

Only quota samples are authoritative. Workload calibration and historical
extrapolation remain estimates: neither a local request nor a matching reset
proves that other devices were idle. The unclaimed remainder stays in the
other-device estimate, never in a third displayed category.
"""
import bisect
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import threading

from quota_accounting import number, date_at, _allocate, _units, _date_weights, add_points, apply_credit

METHOD = 'account-request-calibration-v2'
_LOCK = threading.Lock()


def stamp(value):
    value = number(value, None)
    return value / 1000 if value is not None and value > 10**12 else value


def read_json(path, limit=2*1024*1024):
    try:
        with Path(path).open('rb') as f: raw = f.read(limit+1)
        return json.loads(raw) if len(raw) <= limit else {}
    except (OSError, ValueError, RecursionError): return {}


def account_key(role, ident):
    identity = 'role-only' if role == 'main' else ident
    return hashlib.sha256(('quota-account-v1\0'+role+'\0'+identity).encode()).hexdigest()


def sample(at, remaining, reset, source):
    at, reset = stamp(at), stamp(reset)
    remaining = number(remaining, None)
    if at is None or reset is None or remaining is None or remaining > 100: return None
    if not reset-604800-120 <= at < reset: return None
    return {'at':at, 'remaining':remaining, 'reset':reset, 'source':source}


def normalized(rows, reset, end):
    by_time = {}
    for row in rows:
        if not isinstance(row, dict): continue
        r = sample(row.get('at'),row.get('remaining'),row.get('reset'),row.get('source','cache'))
        if r and abs(r['reset']-reset)<=120 and reset-604800<=r['at']<=end:
            # Later sources (current quota cache) take precedence at equal times.
            by_time[r['at']] = r
    return sorted(by_time.values(),key=lambda r:r['at'])


def stable_between(rows, start, end):
    """Other accounts must actually bracket the interval, not merely look fresh."""
    before = [r for r in rows if r['at']<=start]
    after = [r for r in rows if r['at']>=end]
    if not before or not after:return False
    a,b=before[-1],after[0]
    if start-a['at']>900 or b['at']-end>900 or abs(a['reset']-b['reset'])>120:return False
    window=[a]+[r for r in rows if a['at']<r['at']<=b['at']]
    return all(y['remaining']>=x['remaining'] for x,y in zip(window,window[1:]))


class RequestIndex:
    def __init__(self,rows):
        self.rows=sorted((r for r in rows if isinstance(r,dict) and number(r.get('at'),None) is not None),key=lambda r:r['at'])
        self.times=[r['at'] for r in self.rows]

    def between(self,start,end):
        # Evidence rows are five-minute buckets, so only the immediately
        # preceding bucket can overlap an interval's left boundary.
        return self.rows[bisect.bisect_left(self.times,start-300):bisect.bisect_right(self.times,end)]


def request_weights(requests, start, end, selected, allow_unscoped):
    days={}; total=0
    for r in requests.between(start,end) if isinstance(requests,RequestIndex) else requests:
        if r.get('account') != selected and not (r.get('account') is None and allow_unscoped):continue
        at=number(r.get('at'),None); weight=number(r.get('weight'))
        if at is None or not weight:continue
        lo=number(r.get('start'),at); hi=number(r.get('end'),at)
        if hi>lo:
            share=max(0,min(end,hi)-max(start,lo))/(hi-lo)
        else:share=1 if start<at<=end else 0
        if share:
            value=weight*share;day=date_at(max(start,min(at,end)))
            if day:days[day]=days.get(day,0)+value;total+=value
    return total,days


def observation_source(quota):
    return {'native identity-matched quota response':'native-response',
            'opencodex live account quota':'live-account'}.get(quota.get('quotaSource'),'cache')


def estimate(quota, selected, accounts, request_data):
    """Rebuild only from raw samples and requests; old source percentages ignored."""
    reset,end=quota['resetsAt'],quota['updatedAt'];begin=reset-604800
    current=sample(end,quota['remaining'],reset,observation_source(quota))
    rows=normalized(accounts.get(selected,[])+([current] if current else []),reset,end)
    if not rows:raise ValueError('No account observation')
    requests=RequestIndex(request_data.get('requests',[]))
    intervals=[]; calibration_drop=calibration_work=0.; matched_intervals=0
    calibration_pending=[];unscoped_calibrated=False
    for a,b in zip(rows,rows[1:]):
        start,stop=a['at'],b['at'];delta=max(0,a['remaining']-b['remaining'])
        # Native responses pass identity/window checks; the live endpoint selects
        # the explicit account. Both support temporal estimates, not device proof.
        native_match=selected=='__main__' and (a['source'] in ('native-response','live-account') or b['source'] in ('native-response','live-account'))
        exclusive=native_match
        work,days=request_weights(requests,start,stop,selected,exclusive)
        covered=(request_data.get('coverageComplete',request_data.get('complete')) is True
                 and number(request_data.get('start'),end)<=start)
        if 'coverageRanges' in request_data:
            covered=any(lo<=start and stop<=hi for lo,hi in request_data['coverageRanges'])
        clean=(covered and 0<stop-start<=3600 and a['source']!='legacy-window'
               and b['source']!='legacy-window' and b['remaining']<=a['remaining'])
        calibration_pending=[p for p in calibration_pending if stop-p[0]<=900]
        if not clean:calibration_pending=[]
        if clean:
            calibration_work+=work
            if work:calibration_pending.append((stop,work))
            if delta and calibration_pending:
                calibration_drop+=delta;matched_intervals+=1
                unscoped_calibrated=unscoped_calibrated or exclusive
                calibration_pending=[]
        intervals.append({'start':start,'end':stop,'delta':a['remaining']-b['remaining'],'work':work,'days':days,'exclusive':exclusive,
                          'nativeMatch':native_match and covered and 0<stop-start<=900})
    rate=calibration_drop/calibration_work if calibration_work and calibration_drop else 0
    # Historical unlabelled requests can only be extrapolated after an actual
    # uniquely correlated interval, not from the selected role/currentAccount.
    unscoped_calibrated=bool(rate and unscoped_calibrated)
    first=rows[0];initial=max(0,100-first['remaining'])
    work,days=request_weights(requests,begin,first['at'],selected,unscoped_calibrated)
    intervals.insert(0,{'start':begin,'end':first['at'],'delta':initial,'work':work,'days':days,'initial':True})
    # Long gaps use the learned account-specific rate too. Explicit account
    # requests never get borrowed by the other account; ambiguous requests only
    # inherit a low-confidence calibration, never a fixed prior.
    if rate:
        for interval in intervals[1:]:
            if interval['end']-interval['start']>3600 and unscoped_calibrated:
                interval['work'],interval['days']=request_weights(requests,interval['start'],interval['end'],selected,True)
    local_days={};other_days={};local_total=other_total=0;pending=[]
    for interval in intervals:
        pending=[p for p in pending if 0<=interval['end']-p['end']<=900]
        if interval['delta']<0:
            credit={'localDays':local_days,'otherDays':other_days,
                    'localUsed':local_total/10000,'otherUsed':other_total/10000}
            apply_credit(credit,-interval['delta'])
            local_total=_units(credit['localUsed']);other_total=_units(credit['otherUsed'])
            pending=[]
            continue
        if interval['delta']==0:
            if interval['work']:pending.append(interval)
            continue
        work=interval['work'];days=dict(interval['days'])
        for previous in pending:
            work+=previous['work']
            for day,weight in previous['days'].items():days[day]=days.get(day,0)+weight
        pending=[]
        budget=_units(interval['delta'])
        local=min(budget,_units(rate*work)) if rate else 0
        if interval.get('nativeMatch') and work:
            # Direct temporal correlation is stronger than extrapolating a
            # token coefficient; still an estimate, not a device billing proof.
            local=budget
        other=budget-local
        if local and days:
            for day,units in zip(days,_allocate(local,list(days.values()))):
                if units:add_points(local_days,day,units/10000)
        # Other-device dates without samples remain interval estimates, while
        # local dates follow request timestamps, not elapsed calendar days.
        weights=_date_weights({'reset':reset,'lastAt':end},interval['start'],interval['end'])
        for day,units in zip(weights,_allocate(other,list(weights.values()))):
            if units:add_points(other_days,day,units/10000)
        local_total+=local;other_total+=other
    # Quota rebounds are credits, not new usage. Net to the official total.
    target=_units(100-quota['remaining']);gross=local_total+other_total
    if gross!=target:
        local_target,other_target=_allocate(target,[local_total,other_total]) if gross else [0,target]
        for rows_out,units in ((local_days,local_target),(other_days,other_target)):
            if rows_out:
                for row,part in zip(rows_out.values(),_allocate(units,[x['points'] for x in rows_out.values()])):row['points']=part/10000
            elif units:add_points(rows_out,date_at(end),units/10000)
        local_total,other_total=local_target,other_target
    dates=sorted(set(local_days)|set(other_days))
    confidence='低' if (initial or not request_data.get('complete') or unscoped_calibrated or matched_intervals<3) else '中'
    return {'reset':reset,'firstAt':first['at'],'firstRemaining':first['remaining'],
            'lastAt':end,'lastRemaining':quota['remaining'],'localUsed':local_total/10000,'otherUsed':other_total/10000,
            'localDays':local_days,'otherDays':other_days,
            'days':{day:{'points':round(local_days.get(day,{}).get('points',0)+other_days.get(day,{}).get('points',0),4),'samples':0} for day in dates},
            'attributionMethod':METHOD,'initialDateEstimated':True,'corrections':sum(b['remaining']>a['remaining'] for a,b in zip(rows,rows[1:])),
            'completeSamples':len(rows),'lastBoundaryAt':end,'pendingLocalAt':None,
            'attributionConfidence':confidence,'calibrationIntervals':matched_intervals,
            'requestLogCovered':request_data.get('complete') is True,'requestEvidenceCount':len(requests.rows),
            'estimatedInitialPoints':initial,'workloadRate':rate,
            'attributionNote':('按账号额度变化与请求工作量估算；缺口回推，非本机含未获本机证据的余量'
                               if rate else '尚无足够账号关联样本；未获本机证据的余量暂估为非本机')}


def prepare(quota, home, storage):
    """Run on the existing quota worker. No network, polling thread or timer."""
    from quota_evidence import collect_requests
    from quota_native_evidence import collect_native_samples
    home,storage=Path(home),Path(storage)
    with _LOCK:
        cache=read_json(home/'codex-quota-cache.json');config=read_json(home/'config.json')
        if not isinstance(cache,dict) or not isinstance(config,dict) or not isinstance(config.get('codexAccounts'),list):
            raise ValueError('Invalid evidence source')
        ids=['__main__']+[r['id'] for r in config.get('codexAccounts',[]) if isinstance(r,dict) and r.get('isMain') is False and isinstance(r.get('id'),str)]
        ids=list(dict.fromkeys(ids))
        if len(ids)>16:raise ValueError('Too many account sources')
        selected='__main__' if quota['quotaAccount']=='main' else next((i for i in ids if account_key('secondary',i)==quota['quotaAccountKey']),None)
        if selected is None:raise ValueError('Account evidence mismatch')
        path=storage/'quota-attribution-state.json';state=read_json(path)
        if not isinstance(state,dict) or state.get('version')!=2:state={'version':2,'accounts':{}}
        accounts={};end=quota['updatedAt'];reset=quota['resetsAt'];identity=(cache.get('mainPolicyQuota') or {}).get('identityKey')
        for ident in ids:
            key=hashlib.sha256(ident.encode()).hexdigest();saved=state.get('accounts',{}).get(key,{})
            current=(cache.get('quotas') or {}).get(ident,{})
            history=((cache.get('history') or {}).get('accounts') or {}).get(ident,{})
            account_identity=identity if ident=='__main__' else history.get('identity')
            account_identity=(hashlib.sha256(account_identity.encode()).hexdigest()
                              if isinstance(account_identity,str) and 0<len(account_identity)<=1024 else None)
            if ident==selected and quota.get('quotaIdentity') is not None and quota['quotaIdentity']!=account_identity:
                raise ValueError('Account identity changed during observation')
            identity_changed=bool(saved and account_identity and saved.get('identity')!=account_identity)
            if identity_changed:saved={}
            records=[r for r in saved.get('samples',[]) if isinstance(r,dict) and r.get('source') in ('cache','account-cache','native-response','live-account')]
            current_reset=reset if ident==selected else stamp(current.get('weeklyResetAt'))
            if current_reset is None:accounts[ident]=[];continue
            config_row=next((r for r in config['codexAccounts'] if isinstance(r,dict) and r.get('id')==ident),{})
            generation=config_row.get('planCredentialGeneration')
            history_matches=(ident!='__main__' or isinstance(identity,str) and history.get('identity')==identity)
            for raw in history.get('samples',[]) if history_matches else ():
                if isinstance(generation,int) and not isinstance(generation,bool) and raw.get('credentialGeneration')!=generation:continue
                for window in raw.get('windows',[]):
                    used=number(window.get('usedPercent'),None)
                    if window.get('family')=='account' and window.get('window')=='weekly' and used is not None:
                        value=sample(raw.get('observedAt'),100-used,window.get('resetAtMs'),'account-cache')
                        if value:records.append(value)
            used=number(current.get('weeklyPercent'),None)
            value=sample(current.get('updatedAt'),100-used if used is not None else None,current_reset,'cache')
            if value:records.append(value)
            # Never seed from a rendered ledger (even matching reset/role). It
            # has neither an independently verified identity nor raw provenance.
            if ident==selected:
                value=sample(end,quota['remaining'],reset,observation_source(quota))
                if value:records.append(value)
            if ident=='__main__' and isinstance(identity,str):
                records.extend(collect_native_samples(Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex'),identity,current_reset,current_reset-604800,end))
            # Preserve genuine observation source when endpoints coincide.
            priority={'legacy-window':0,'native-response':1,'account-cache':2,'cache':3,'live-account':4}
            records.sort(key=lambda r:priority.get(r.get('source'),0))
            records=normalized(records,current_reset,max(end,stamp(current.get('updatedAt')) or end))
            if len(records)>256:records=records[:1]+records[-255:]
            accounts[ident]=records
            state.setdefault('accounts',{})[key]={'identity':account_identity,'samples':records,'bootstrapped':True}
        state['accounts']={hashlib.sha256(i.encode()).hexdigest():state['accounts'].get(hashlib.sha256(i.encode()).hexdigest(),{}) for i in ids}
        if state!=read_json(path):
            from sidebar_modules import atomic_json
            atomic_json(path,state)
        requests=collect_requests(home,reset-604800,end,ids)
        return estimate(quota,selected,accounts,requests)
