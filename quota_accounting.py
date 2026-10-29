"""Two-device quota estimates with authoritative totals and closed daily buckets."""
import copy
import datetime as dt
import math

SOURCES=(('localUsed','localDays'),('otherUsed','otherDays'))
LEGACY_SOURCES=(*SOURCES,('unknownUsed','unknownDays'))

def number(value,default=0):
    try:return float(value) if type(value) in (int,float) and math.isfinite(value) and value>=0 else default
    except (OverflowError,ValueError):return default

def date_at(stamp):
    try:return dt.datetime.fromtimestamp(stamp).date().isoformat() if number(stamp,None) is not None else None
    except (ValueError,OSError,OverflowError):return None

def point_rows(value):
    rows={}
    for key,row in (value.items() if isinstance(value,dict) else ()):
        try:dt.date.fromisoformat(key)
        except (TypeError,ValueError):continue
        if isinstance(row,dict):rows[key]={**row,'points':min(100,number(row.get('points'))),'samples':int(number(row.get('samples')))}
    return rows

def add_points(rows,day,points,sample=False):
    row=rows.setdefault(day,{'points':0,'samples':0})
    row['points']=round(row.get('points',0)+points,4)
    if sample:row['samples']=row.get('samples',0)+1

def _legacy_cycle(value):
    """Read historical three-way records without discarding observed evidence."""
    cycle=copy.deepcopy(value or {});legacy_days=point_rows(cycle.get('days'))
    for aggregate,daily in LEGACY_SOURCES:
        cycle[daily]=point_rows(cycle.get(daily))
        cycle[aggregate]=max(min(100,number(cycle.get(aggregate))),sum(row['points'] for row in cycle[daily].values()))
    old_unallocated=number(cycle.get('unallocated'));cycle['unallocated']=0
    remaining=number(cycle.get('lastRemaining'),None)
    total=round(100-remaining,4) if remaining is not None and remaining<=100 else min(100,max(
        sum(cycle[key] for key,_ in LEGACY_SOURCES)+old_unallocated,sum(row['points'] for row in legacy_days.values())))
    # Corrections and legacy inconsistencies may exceed the official net total.
    known=cycle['localUsed']+cycle['otherUsed']
    factor=min(1,total/known) if known else 1
    for aggregate,daily in LEGACY_SOURCES[:2]:
        cycle[aggregate]=round(cycle[aggregate]*factor,4)
        for row in cycle[daily].values():row['points']=round(row['points']*factor,4)
    cycle['otherUsed']=min(cycle['otherUsed'],round(max(0,total-cycle['localUsed']),4))
    cycle['unknownUsed']=round(max(0,total-cycle['localUsed']-cycle['otherUsed']),4)
    # A source can be known while its date is not. Only a single possible
    # calendar day, or a fully dated one-day ledger, permits filling that date.
    reset=number(cycle.get('reset'),None);first=number(cycle.get('firstAt'),None)
    start=date_at(reset-7*86400) if reset is not None else None
    last=date_at(cycle.get('lastAt'));unique=last if start and start==last else None
    dated=[key for key,row in legacy_days.items() if row['points']>0]
    if unique is None and len(dated)==1 and abs(sum(row['points'] for row in legacy_days.values())-total)<.0001:unique=dated[0]
    for aggregate,daily in LEGACY_SOURCES[:2]:
        missing=max(0,cycle[aggregate]-sum(row['points'] for row in cycle[daily].values()))
        if unique and missing:add_points(cycle[daily],unique,missing)
    # Preserve dated but unclassified observations, within the official budget.
    budget=cycle['unknownUsed'];unknown=cycle['unknownDays']
    for day,row in legacy_days.items():
        gap=max(0,row['points']-sum(cycle[daily].get(day,{}).get('points',0) for _,daily in LEGACY_SOURCES))
        available=max(0,budget-sum(item['points'] for item in unknown.values()))
        if gap and available:add_points(unknown,day,min(gap,available))
    known_unknown=sum(row['points'] for row in unknown.values())
    if known_unknown>budget:
        for row in unknown.values():row['points']=round(row['points']*budget/known_unknown,4)
    missing=round(max(0,budget-sum(row['points'] for row in unknown.values())),4)
    initial=number(cycle.get('firstRemaining'),None)
    if start and start==date_at(first) and initial is not None and not cycle.get('initialDateReconciled'):
        # The first-sample gap has an exact date only if the reset and first
        # observation fall on that same date. Its source remains unconfirmed.
        initial_left=max(0,100-initial-unknown.get(start,{}).get('points',0))
        if missing and initial_left:add_points(unknown,start,min(missing,initial_left))
        cycle['initialDateReconciled']=True
    missing=round(max(0,budget-sum(row['points'] for row in unknown.values())),4)
    if missing and unique:add_points(unknown,unique,missing)
    keys=set(legacy_days)
    for _,daily in LEGACY_SOURCES:keys.update(cycle[daily])
    cycle['days']={key:{'points':round(sum(cycle[daily].get(key,{}).get('points',0) for _,daily in LEGACY_SOURCES),4),
                       'samples':legacy_days.get(key,{}).get('samples',0)} for key in sorted(keys)}
    cycle['attributionMethod']='local-activity-estimate-v4'
    return cycle


def _units(value):
    return int(math.floor(number(value)*10000+.50000001))


def _allocate(total,weights):
    values=[number(w) for w in weights];amount=sum(values)
    if not values:return []
    if not amount:values=[1]*len(values);amount=len(values)
    raw=[total*w/amount for w in values];parts=[int(math.floor(x+1e-9)) for x in raw]
    for i in sorted(range(len(values)),key=lambda j:raw[j]-parts[j],reverse=True)[:max(0,total-sum(parts))]:parts[i]+=1
    return parts


def estimate_share(cycle,gap=0,local_evidence=False,covered=False):
    """A neutral prior, updated by observed usage; no token-to-quota conversion."""
    local=number(cycle.get('localUsed'));other=number(cycle.get('otherUsed'))
    prior=(local+1)/(local+other+2)
    if local_evidence:
        coverage=min(1,900/max(1,gap))
        return coverage+(1-coverage)*prior
    return 0 if covered else prior


def _bounds(cycle,start=None,end=None):
    reset=number(cycle.get('reset'),None)
    end=number(end,number(cycle.get('lastAt'),number(cycle.get('firstAt'),None)))
    if end is None:
        keys=sorted(point_rows(cycle.get('days')))
        day=dt.date.fromisoformat(keys[-1]) if keys else dt.date.today()
        end=dt.datetime.combine(day,dt.time(12)).timestamp()
    if date_at(end) is None:end=dt.datetime.combine(dt.date.today(),dt.time(12)).timestamp()
    if reset is not None and date_at(reset) is None:reset=None
    if reset is not None:end=max(reset-7*86400,min(end,reset))
    start=number(start,reset-7*86400 if reset is not None else number(cycle.get('firstAt'),end))
    if date_at(start) is None:start=end
    if reset is not None:start=max(start,reset-7*86400)
    return min(start,end),end


def _date_weights(cycle,start=None,end=None):
    start,end=_bounds(cycle,start,end)
    if end<=start:return {date_at(end):1}
    result={};cursor=start
    # A weekly window spans at most eight local calendar dates (including DST).
    for _ in range(9):
        day=dt.datetime.fromtimestamp(cursor).date()
        boundary=dt.datetime.combine(day+dt.timedelta(days=1),dt.time()).timestamp()
        stop=min(end,boundary);result[day.isoformat()]=max(0,stop-cursor)
        if stop>=end:break
        cursor=stop
    return result


def _spread(cycle,rows,points,start=None,end=None):
    weights=_date_weights(cycle,start,end)
    for day,units in zip(weights,_allocate(_units(points),list(weights.values()))):
        if units:add_points(rows,day,units/10000)


def add_interval(cycle,start,end,points,share):
    """Attribute one delta, then split it across local midnights by elapsed time."""
    local,other=_allocate(_units(points),[max(0,min(1,share)),max(0,1-share)])
    for (aggregate,daily),units in zip(SOURCES,(local,other)):
        cycle.setdefault(daily,{})
        cycle[aggregate]=round(number(cycle.get(aggregate))+units/10000,4)
        _spread(cycle,cycle[daily],units/10000,start,end)


def reconcile_cycle(value):
    """Migrate old uncertainty once; return only two nonnegative closed sources."""
    cycle=_legacy_cycle(value)
    # Normalise retained dates to the current quota window before estimating gaps.
    low,high=_bounds(cycle);first_day,last_day=date_at(low),date_at(high)
    ratio=estimate_share(cycle)
    for aggregate,daily in SOURCES:
        rows={day:row for day,row in cycle[daily].items() if first_day<=day<=last_day}
        total=_units(cycle[aggregate]);dated=sum(_units(row['points']) for row in rows.values())
        if dated>total:
            for row,units in zip(rows.values(),_allocate(total,[r['points'] for r in rows.values()])):row['points']=units/10000
        cycle[daily]=rows
        missing=total-sum(_units(row['points']) for row in rows.values())
        if missing:_spread(cycle,rows,missing/10000)
    uncertain={day:row for day,row in cycle.get('unknownDays',{}).items() if first_day<=day<=last_day}
    budget=_units(cycle.get('unknownUsed'));dated=sum(_units(r['points']) for r in uncertain.values())
    if dated>budget:
        for row,units in zip(uncertain.values(),_allocate(budget,[r['points'] for r in uncertain.values()])):row['points']=units/10000
    missing=budget-sum(_units(r['points']) for r in uncertain.values())
    # The initial missing sample belongs between reset and the first observation.
    initial=number(cycle.get('firstRemaining'),None)
    if missing and initial is not None and not cycle.get('initialDateEstimated'):
        initial_units=min(missing,_units(max(0,100-initial)))
        _spread(cycle,uncertain,initial_units/10000,end=cycle.get('firstAt'))
        missing-=initial_units
    if missing:_spread(cycle,uncertain,missing/10000)
    cycle['initialDateEstimated']=True
    # Allocate the source budget once, then distribute each part across its dates.
    local_units,other_units=_allocate(budget,[ratio,1-ratio])
    dated_units=_allocate(budget,[r['points'] for r in uncertain.values()])
    # Keep each day's total exact while carrying rounding residuals forward.
    local_left=local_units;points_left=budget
    for (day,row),units in zip(uncertain.items(),dated_units):
        local=min(local_left,units) if units==points_left else _allocate(units,[local_left,max(0,points_left-local_left)])[0]
        local_left-=local;points_left-=units
        if local:add_points(cycle['localDays'],day,local/10000)
        if units-local:add_points(cycle['otherDays'],day,(units-local)/10000)
    cycle['localUsed']=round(cycle['localUsed']+local_units/10000,4)
    cycle['otherUsed']=round(cycle['otherUsed']+other_units/10000,4)
    keys=set(cycle['localDays'])|set(cycle['otherDays'])|{day for day in cycle.get('days',{}) if first_day<=day<=last_day}
    cycle['days']={day:{'points':round(sum(cycle[daily].get(day,{}).get('points',0) for _,daily in SOURCES),4),
                        'samples':cycle.get('days',{}).get(day,{}).get('samples',0)} for day in sorted(keys)}
    for key in ('unknownUsed','unknownDays','unallocated'):cycle.pop(key,None)
    cycle['attributionMethod']='two-source-interval-estimate-v1'
    return cycle

def apply_credit(cycle,points):
    """Reduce the latest dated buckets first; never count a rebound as usage."""
    remaining=points
    for day in sorted({day for _,daily in SOURCES for day in cycle.get(daily,{})},reverse=True):
        available=sum(cycle.get(daily,{}).get(day,{}).get('points',0) for _,daily in SOURCES)
        if not available:continue
        take=min(remaining,available);left=take
        positive=[(aggregate,daily) for aggregate,daily in SOURCES if cycle.get(daily,{}).get(day,{}).get('points',0)>0]
        for index,(aggregate,daily) in enumerate(positive):
            row=cycle[daily][day];cut=min(row['points'],left if index==len(positive)-1 else round(take*row['points']/available,4))
            row['points']=round(row['points']-cut,4);cycle[aggregate]=round(max(0,cycle[aggregate]-cut),4);left-=cut
        remaining=round(remaining-take,4)
        if remaining<=0:break
    # Undated credits are reconciled against the authoritative remaining value.
    cycle['days']={key:{**row,'points':round(sum(cycle.get(daily,{}).get(key,{}).get('points',0) for _,daily in SOURCES),4)}
                   for key,row in cycle.get('days',{}).items()}

def display_labels(total,local,other):
    """Largest-remainder rounding keeps the two integer percentages closed."""
    units=_allocate(int(math.floor(number(total)+.50000001)),[local,other])
    def text(value):return str(value)
    return {'total':text(sum(units)),'local':text(units[0]),'other':text(units[1])}



def _day_labels(days,labels):
    """Controlled rounding of an <=8 by 2 table, without creating a source.

    Each cell uses its floor or ceiling. Bounded dynamic programming chooses
    the smallest daily rounding error while keeping both column totals exact.
    """
    raw=[(row['local'],row['other']) for row in days]
    base=[tuple(int(math.floor(x+1e-8)) for x in row) for row in raw]
    target=[int(labels[key]) for key in ('local','other')]
    needs=tuple(target[j]-sum(row[j] for row in base) for j in (0,1))
    states={(0,0):(0,())}
    for values,floors in zip(raw,base):
        choices=[(0,1) if values[j]-floors[j]>1e-7 else (0,) for j in (0,1)]
        next_states={}
        for (done_l,done_o),(cost,path) in states.items():
            for l in choices[0]:
                for o in choices[1]:
                    key=(done_l+l,done_o+o)
                    if key[0]>needs[0] or key[1]>needs[1]:continue
                    cell=(floors[0]+l,floors[1]+o)
                    error=(sum(cell)-sum(values))**2+.01*sum((cell[j]-values[j])**2 for j in (0,1))
                    candidate=(cost+error,path+(cell,))
                    if key not in next_states or candidate[0]<next_states[key][0]-1e-10:next_states[key]=candidate
        states=next_states
    selected=states.get(needs)
    if selected is None:
        # Malformed legacy records may not cover the shown interval. Keep the
        # display safe and column totals closed, without a new fallback category.
        columns=[_allocate(target[j],[values[j] for values in raw]) for j in (0,1)]
        cells=list(zip(*columns))
    else:cells=selected[1]
    def text(units):return str(units)
    for row,(local,other) in zip(days,cells):
        row['labels']={'total':text(local+other),'local':text(local),'other':text(other)}

def quota_breakdown(cycle,remaining=None,reset=None,day_key=None,sample_at=None):
    today=dt.date.fromisoformat(day_key) if day_key else dt.date.today()
    data=copy.deepcopy(cycle or {})
    if number(reset,None) is not None:
        if number(data.get('reset'),None) is not None and abs(data['reset']-reset)>120:data={}
        if not data.get('reset'):data['reset']=reset
    if number(data.get('lastAt'),None) is None:
        end=number(sample_at,dt.datetime.combine(today,dt.time(12)).timestamp())
        data['lastAt']=min(end,number(data.get('reset'),end))
    data=reconcile_cycle(data)
    if number(remaining,None) is not None and remaining<=100:
        previous=number(data.get('lastRemaining'),None)
        if previous is not None and remaining>previous:apply_credit(data,remaining-previous)
        elif previous is not None and remaining<previous:
            fallback=dt.datetime.now().timestamp() if today==dt.date.today() else dt.datetime.combine(today,dt.time(23,59,59)).timestamp()
            start=data['lastAt'];end=max(start,number(sample_at,fallback))
            add_interval(data,start,end,previous-remaining,estimate_share(data))
            data['lastAt']=end
        data['lastRemaining']=remaining
    data=reconcile_cycle(data)
    start_day=date_at(data.get('reset',0)-7*86400) if data.get('reset') else None
    start=dt.date.fromisoformat(start_day) if start_day else today-dt.timedelta(days=6)
    # Without a reset, include all recorded dates rather than losing old buckets.
    if not start_day and data['days']:start=min(start,dt.date.fromisoformat(min(data['days'])))
    count=min(8,max(7,(today-start).days+1))
    days=[]
    for index in range(count):
        day=start+dt.timedelta(days=index);key=day.isoformat();record=data['days'].get(key)
        values={name:number(data[daily].get(key,{}).get('points')) for name,(_,daily) in zip(('local','other'),SOURCES)}
        row_total=record['points'] if record is not None else None
        days.append({'day':day,'total':row_total,**values,'labels':display_labels(row_total or 0,**values)})
    total=round(data['localUsed']+data['otherUsed'],4)
    labels=display_labels(total,data['localUsed'],data['otherUsed'])
    _day_labels(days,labels)
    return {'total':total,'local':data['localUsed'],'other':data['otherUsed'],
            'days':days,'ledger':data,'labels':labels}
