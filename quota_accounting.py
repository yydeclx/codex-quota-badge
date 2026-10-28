"""Conservative quota ledger: dates and device attribution are separate facts."""
import copy
import datetime as dt
import math

SOURCES=(('localUsed','localDays'),('otherUsed','otherDays'),('unknownUsed','unknownDays'))

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

def reconcile_cycle(value):
    """Return a closed ledger; missing consumption is never invented as local."""
    cycle=copy.deepcopy(value or {});legacy_days=point_rows(cycle.get('days'))
    for aggregate,daily in SOURCES:
        cycle[daily]=point_rows(cycle.get(daily))
        cycle[aggregate]=max(min(100,number(cycle.get(aggregate))),sum(row['points'] for row in cycle[daily].values()))
    old_unallocated=number(cycle.get('unallocated'));cycle['unallocated']=0
    remaining=number(cycle.get('lastRemaining'),None)
    total=round(100-remaining,4) if remaining is not None and remaining<=100 else min(100,max(
        sum(cycle[key] for key,_ in SOURCES)+old_unallocated,sum(row['points'] for row in legacy_days.values())))
    # Corrections and legacy inconsistencies may exceed the official net total.
    known=cycle['localUsed']+cycle['otherUsed']
    factor=min(1,total/known) if known else 1
    for aggregate,daily in SOURCES[:2]:
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
    for aggregate,daily in SOURCES[:2]:
        missing=max(0,cycle[aggregate]-sum(row['points'] for row in cycle[daily].values()))
        if unique and missing:add_points(cycle[daily],unique,missing)
    # Preserve dated but unclassified observations, within the official budget.
    budget=cycle['unknownUsed'];unknown=cycle['unknownDays']
    for day,row in legacy_days.items():
        gap=max(0,row['points']-sum(cycle[daily].get(day,{}).get('points',0) for _,daily in SOURCES))
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
    for _,daily in SOURCES:keys.update(cycle[daily])
    cycle['days']={key:{'points':round(sum(cycle[daily].get(key,{}).get('points',0) for _,daily in SOURCES),4),
                       'samples':legacy_days.get(key,{}).get('samples',0)} for key in sorted(keys)}
    cycle['attributionMethod']='local-activity-estimate-v4'
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

def display_labels(total,local,other,unknown):
    """Round to tenths without making the displayed source sum disagree."""
    def format_unit(units):return str(units//10) if units%10==0 else f'{units/10:.1f}'
    target=int(math.floor(total*10+.50000001));values=[local*10,other*10,unknown*10]
    units=[int(math.floor(value+1e-7)) for value in values]
    order=sorted(range(3),key=lambda index:values[index]-units[index],reverse=True)
    for index in order[:max(0,target-sum(units))]:units[index]+=1
    return {'total':format_unit(target),**{key:format_unit(value) for key,value in zip(('local','other','unknown'),units)}}

def quota_breakdown(cycle,remaining=None,reset=None,day_key=None):
    data=copy.deepcopy(cycle or {})
    if number(reset,None) is not None:
        if number(data.get('reset'),None) is not None and abs(data['reset']-reset)>120:data={}
        if not data.get('reset'):data['reset']=reset
    data=reconcile_cycle(data)
    if number(remaining,None) is not None and remaining<=100:
        previous=number(data.get('lastRemaining'),None)
        if previous is not None and remaining>previous:apply_credit(data,remaining-previous)
        data['lastRemaining']=remaining
    data=reconcile_cycle(data)
    today=dt.date.fromisoformat(day_key) if day_key else dt.date.today()
    start_day=date_at(data.get('reset',0)-7*86400) if data.get('reset') else None
    start=dt.date.fromisoformat(start_day) if start_day else today-dt.timedelta(days=6)
    count=8 if today==start+dt.timedelta(days=7) else 7
    days=[]
    for index in range(count):
        day=start+dt.timedelta(days=index);key=day.isoformat();record=data['days'].get(key)
        values={name:number(data[daily].get(key,{}).get('points')) for name,(_,daily) in zip(('local','other','unknown'),SOURCES)}
        row_total=record['points'] if record is not None else None
        days.append({'day':day,'total':row_total,**values,'labels':display_labels(row_total or 0,**values)})
    total=round(sum(data[aggregate] for aggregate,_ in SOURCES),4)
    undated=round(max(0,total-sum(row['points'] for row in data['days'].values())),4)
    return {'total':total,'local':data['localUsed'],'other':data['otherUsed'],'unknown':data['unknownUsed'],
            'undated':undated,'days':days,'ledger':data,'labels':display_labels(total,data['localUsed'],data['otherUsed'],data['unknownUsed'])}
