"""Read-only public Codex reset forecast with a persistent last-good cache."""
import datetime as dt
import json
import math
from pathlib import Path
import re
import time
import os
import threading
import urllib.request
from zoneinfo import ZoneInfo

FORECAST_URL='https://codex-reset.com/api/forecast'
FEED_URL='https://codex-reset.com/api/feed'
POLL_SECONDS=15*60
STALE_SECONDS=45*60
USER_AGENT='codex-quota-badge/1.1 (+local read-only companion)'

def _atomic_write(path,value):
    tmp=path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        for attempt in range(4):
            try:tmp.replace(path);return
            except PermissionError:
                if attempt==3:raise
                time.sleep(.02*(attempt+1))
    finally:
        try:tmp.unlink(missing_ok=True)
        except OSError:pass

def promised_reset_at(summary,signal_at):
    """Parse narrow Tibo time promises and return a UTC timestamp.

    Supported forms intentionally stay conservative: today/tomorrow plus a
    clock and PST/PDT/PT. Unknown prose is never guessed.
    """
    if not isinstance(summary,str) or not isinstance(signal_at,(int,float)):return None
    match=re.search(r'(today|tomorrow).*?(?:(?:before|by|around)[ ]+)?([0-9]{1,2})(?::([0-9]{2}))?[ ]*(am|pm)[ ]*(PST|PDT|PT)',summary,re.I)
    if not match:return None
    day_word,hour,minute,meridiem,zone=match.groups();hour=int(hour);minute=int(minute or 0)
    if not 1<=hour<=12 or not 0<=minute<=59:return None
    hour=hour%12+(12 if meridiem.lower()=='pm' else 0)
    if zone.upper()=='PST':tz=dt.timezone(dt.timedelta(hours=-8))
    elif zone.upper()=='PDT':tz=dt.timezone(dt.timedelta(hours=-7))
    else:tz=ZoneInfo('America/Los_Angeles')
    source=dt.datetime.fromtimestamp(signal_at,tz)
    date=source.date()+dt.timedelta(days=1 if day_word.lower()=='tomorrow' else 0)
    target=dt.datetime.combine(date,dt.time(hour,minute),tzinfo=tz)
    return target.timestamp() if target.timestamp()>=signal_at-300 else None

def _timestamp(value):
    if not isinstance(value,str):return None
    try:return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except ValueError:return None

def parse_forecast(value):
    if not isinstance(value,dict):raise ValueError('Invalid forecast')
    probabilities=value.get('probabilities') or {}
    p24,p48=probabilities.get('rounded_24h'),probabilities.get('rounded_48h')
    if not all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=100 for v in (p24,p48)):raise ValueError('Invalid probabilities')
    updated=_timestamp(value.get('updated_at'))
    if updated is None:raise ValueError('Invalid forecast time')
    raw_signal=value.get('official_signal') or value.get('commitment');signal=None
    if isinstance(raw_signal,str) and raw_signal.strip():signal={'summary':raw_signal.strip()[:240],'kind':'official'}
    elif isinstance(raw_signal,dict) and (raw_signal.get('active') is True or raw_signal.get('kind')=='signal'):
        summary=raw_signal.get('summary');at=_timestamp(raw_signal.get('at'));url=raw_signal.get('url')
        if isinstance(summary,str) and summary.strip() and (not url or isinstance(url,str) and url.startswith('https://x.com/')):
            signal={'summary':summary.strip()[:240],'at':at,'url':url,'kind':'official'}
            window=raw_signal.get('window') or {}
            target=_timestamp(window.get('target_at')) if isinstance(window,dict) else None
            if target:signal['targetAt']=target
    return {'updatedAt':updated,'probability24h':round(p24),'probability48h':round(p48),
            'confidence':value.get('confidence') if value.get('confidence') in ('low','medium','high') else None,
            'lastResetAt':_timestamp(value.get('last_reset_at')),'signal':signal,
            'source':'codex-reset.com'}

def parse_feed(value,now=None):
    if not isinstance(value,dict):raise ValueError('Invalid feed')
    fetched=_timestamp(value.get('fetched_at'))
    if fetched is None:raise ValueError('Invalid feed time')
    profile=value.get('profile') or {};signal=value.get('signal');result=None
    if profile.get('handle')=='thsottiaux' and isinstance(signal,dict) and signal.get('active') is True and signal.get('kind')=='signal':
        at=_timestamp(signal.get('at'));summary=signal.get('summary');url=signal.get('url')
        if at and isinstance(summary,str) and 1<=len(summary)<=240 and isinstance(url,str) and url.startswith('https://x.com/'):
            age=(time.time() if now is None else now)-at
            if -300<=age<=72*3600:
                result={'summary':summary,'at':at,'url':url,'kind':'explicit'}
                target=promised_reset_at(summary,at)
                if target:result['targetAt']=target
    return {'fetchedAt':fetched,'stale':value.get('stale') is True,'signal':result}

def _get(url,timeout=5):
    request=urllib.request.Request(url,headers={'User-Agent':USER_AGENT,'Accept':'application/json'})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        if response.status!=200:raise RuntimeError('HTTP_'+str(response.status))
        body=response.read(262145)
        if len(body)>262144:raise ValueError('Oversized response')
        return json.loads(body)

def fetch():
    forecast=parse_forecast(_get(FORECAST_URL));feed=parse_feed(_get(FEED_URL))
    signal=forecast.pop('signal',None) or feed['signal']
    if signal and forecast.get('lastResetAt') and signal.get('at') and signal['at']<=forecast['lastResetAt']:signal=None
    if signal and not signal.get('targetAt'):
        target=promised_reset_at(signal.get('summary'),signal.get('at'))
        if target:signal['targetAt']=target
    forecast['explicitSignal']=signal;forecast['feedStale']=feed['stale'];forecast['fetchedAt']=time.time()
    return forecast

class ResetSignals:
    def __init__(self,path,fetcher=None,clock=None):
        self.path=Path(path);self.fetcher=fetcher or fetch;self.clock=clock or time.time
        self.data=None;self.error=None;self.busy=False;self.nextPoll=0
        try:
            value=json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(value,dict):self.data=value
        except (OSError,ValueError):pass
    def due(self):return not self.busy and self.clock()>=self.nextPoll
    def refresh(self):
        self.busy=True;self.nextPoll=self.clock()+POLL_SECONDS
        try:
            value=self.fetcher();self.data=value;self.error=None
            _atomic_write(self.path,value)
        except Exception as exc:self.error=type(exc).__name__
        finally:self.busy=False
    def view(self):
        if not self.data:return {'state':'unavailable','freshness':'unavailable','error':self.error,'source':'codex-reset.com','lastSyncAt':None}
        result=dict(self.data);last_sync=result.get('fetchedAt',result.get('updatedAt',0));age=max(0,self.clock()-last_sync);result['stale']=age>STALE_SECONDS;result['error']=self.error
        result['lastSyncAt']=last_sync;result['syncAgeSeconds']=age
        result['freshness']='stale' if result['stale'] else 'cached' if self.error else 'fresh'
        confirmation=result.get('accountConfirmation')
        if isinstance(confirmation,dict) and self.clock()-confirmation.get('at',0)>72*3600:result.pop('accountConfirmation',None)
        result['state']='signal' if result.get('explicitSignal') and not result.get('feedStale') else 'forecast'
        return result
    def observe_account(self,previous,current,at=None):
        if not all(isinstance(v,(int,float)) for v in (previous,current)):return False
        public=self.view();signal=public.get('explicitSignal');last=public.get('lastResetAt')
        observed=self.clock() if at is None else at
        relevant=(isinstance(signal,dict) and observed-signal.get('at',0)<=72*3600) or (isinstance(last,(int,float)) and observed-last<=72*3600)
        if not relevant:return False
        candidate=self.data.get('accountCandidate')
        if isinstance(candidate,dict) and observed>candidate.get('sampleAt',0) and current>=candidate.get('to',101)-1:
            self.data['accountConfirmation']={'at':observed,'from':candidate['from'],'to':current,'increase':current-candidate['from']}
            self.data.pop('accountCandidate',None);confirmed=True
        elif current-previous>=5:
            self.data['accountCandidate']={'sampleAt':observed,'from':previous,'to':current};confirmed=False
        else:return False
        _atomic_write(self.path,self.data)
        return confirmed
    def dismiss_confirmation_after_use(self,current):
        """Hide the confirmation banner once post-reset usage begins."""
        confirmation=self.data.get('accountConfirmation') if isinstance(self.data,dict) else None
        if not isinstance(confirmation,dict) or not isinstance(current,(int,float)):return False
        confirmed_to=confirmation.get('to')
        if not isinstance(confirmed_to,(int,float)) or current >= confirmed_to:return False
        self.data.pop('accountConfirmation',None)
        _atomic_write(self.path,self.data)
        return True
