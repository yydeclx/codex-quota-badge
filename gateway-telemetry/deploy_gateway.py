"""Version-pinned local gateway switch with idle checks, journal and reversible file replacement."""
from pathlib import Path
import argparse,base64,hashlib,hmac,http.client,json,os,secrets,shutil,subprocess,sys,time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
from sidebar_activity import read_local
TARGET=Path('C:/Program Files/OpenCodex/ocx.exe')
OFFICIAL=ROOT/'rollback-official-2.92.0.exe'
CANDIDATE=ROOT/'build/ocx.exe'
JOURNAL=ROOT/'deployment.json'
OFFICIAL_SHA='c7c87829a2b69720612f08e40048f5b20c8097ad8e02f353f1ef8428ab42a4c9'

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write(record):
    tmp=JOURNAL.with_suffix('.tmp');tmp.write_text(json.dumps(record,indent=2),encoding='utf-8');tmp.replace(JOURNAL)
def summary(state):return {k:state.get(k) for k in ('pid','activeTurnCount','isDraining','bunVersion')}

def restart(expected):
    home=Path(os.environ.get('OPENCODEX_HOME') or Path.home()/'.opencodex')
    state=json.loads((home/'runtime-port.json').read_text(encoding='utf-8'))
    if state['pid']!=expected['pid']:raise RuntimeError('runtime identity changed before restart')
    path='/api/system/restart';nonce=secrets.token_urlsafe(32)
    payload=f"opencodex-system-restart-v1\n{nonce}\nPOST\n{path}\n{state['pid']}\n{state['port']}"
    proof=base64.urlsafe_b64encode(hmac.new(state['attestationSecret'].encode(),payload.encode(),hashlib.sha256).digest()).decode().rstrip('=')
    headers={'Content-Type':'application/json','x-opencodex-restart-expected-pid':str(state['pid']),
        'x-opencodex-restart-nonce':nonce,'x-opencodex-restart-capability':proof}
    c=http.client.HTTPConnection('127.0.0.1',state['port'],timeout=4)
    try:
        c.request('POST',path,b'{}',headers);response=c.getresponse();data=json.loads(response.read(8192))
        if response.status!=202:raise RuntimeError(f'restart rejected: HTTP {response.status}')
        return {k:data.get(k) for k in ('activeTurnCount','drainTimeoutMs','alreadyDraining')}
    finally:c.close()

def idle(wait,graceful=False):
    deadline=time.monotonic()+wait;consecutive=0;previous_pid=None;last_report=0
    while time.monotonic()<deadline:
        state=read_local('/api/system/memory')
        quiet=isinstance(state.get('activeTurnCount'),int) and state['activeTurnCount']<=(1 if graceful else 0) and state.get('isDraining') is False
        consecutive=consecutive+1 if quiet and state['pid']==previous_pid else 1 if quiet else 0
        previous_pid=state['pid']
        if consecutive>=3:return state
        if time.monotonic()-last_report>=10:
            print(json.dumps({'waitingForIdle':summary(state)}),flush=True);last_report=time.monotonic()
        time.sleep(1)
    raise RuntimeError('gateway remained busy; running executable was not replaced')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--rollback',action='store_true');parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--graceful',action='store_true',help='Use the existing 60s gateway drain when at most one turn remains')
    parser.add_argument('--wait-seconds',type=int,default=90);args=parser.parse_args()
    if digest(OFFICIAL)!=OFFICIAL_SHA:raise RuntimeError('official rollback hash does not match')
    previous_record=json.loads(JOURNAL.read_text(encoding='utf-8')) if JOURNAL.exists() else {}
    candidate_sha=previous_record.get('candidateSHA256') if args.rollback else digest(CANDIDATE)
    if not candidate_sha:raise RuntimeError('no recorded installed candidate hash for rollback')
    expected_sha=candidate_sha if args.rollback else OFFICIAL_SHA
    desired=OFFICIAL if args.rollback else CANDIDATE
    desired_sha=OFFICIAL_SHA if args.rollback else candidate_sha
    current_sha=digest(TARGET)
    if current_sha==desired_sha:
        print(json.dumps({'alreadyInstalled':True,'rollback':args.rollback,'sha256':current_sha}));return
    if not args.rollback and previous_record.get('status')=='verified' and current_sha==previous_record.get('targetSHA256'):
        expected_sha=current_sha
    if current_sha!=expected_sha:raise RuntimeError('installed executable changed; refusing to overwrite unknown work')
    if not args.rollback:
        smoke=json.loads((ROOT/'smoke-result.json').read_text(encoding='utf-8'))
        if not smoke.get('passed') or smoke.get('binarySHA256')!=candidate_sha:raise RuntimeError('this exact binary has not passed the isolated smoke test')
    state=read_local('/api/system/memory')
    if args.check_only:
        print(json.dumps({'mode':'check-only','rollbackVerified':True,'candidateSHA256':candidate_sha,'runtime':summary(state)}));return
    suffix=time.strftime('%Y%m%d-%H%M%S')
    previous=TARGET.with_name(f'ocx.{"official" if expected_sha==OFFICIAL_SHA else "telemetry"}-2.92.0-before-{suffix}.exe')
    stage=TARGET.with_name(f'ocx.telemetry-stage-{suffix}.exe')
    record={'status':'preparing','rollback':args.rollback,'gracefulDrain':args.graceful,'oldPid':state['pid'],'target':str(TARGET),
        'previousExecutable':str(previous),'officialBackup':str(OFFICIAL),'previousSHA256':expected_sha,'targetSHA256':desired_sha,
        'candidateSHA256':candidate_sha,'startedAt':time.time()}
    write(record)
    shutil.copy2(desired,stage)
    if digest(stage)!=desired_sha:raise RuntimeError('staged executable hash mismatch')
    # Three quiet observations; no forced stop and no new model requests.
    try:state=idle(args.wait_seconds,args.graceful)
    except Exception:
        # A rejected settings toggle must not leave another full executable
        # behind on every click. The persistent original/candidate stay intact.
        if stage.exists() and digest(stage)==desired_sha:stage.unlink()
        record['status']='deferred_before_switch';write(record);raise
    record['oldPid']=state['pid'];record['idleBeforeSwitch']=summary(state);write(record)
    if digest(TARGET)!=expected_sha:raise RuntimeError('installed executable changed while waiting')
    if previous.exists():raise RuntimeError('backup collision')
    TARGET.rename(previous)
    try:stage.rename(TARGET)
    except Exception:
        previous.rename(TARGET);raise
    record['status']='installed_before_restart';write(record)
    try:
        current=read_local('/api/system/memory')
        if current['pid']!=state['pid'] or current.get('activeTurnCount',999)>(1 if args.graceful else 0) or current.get('isDraining'):
            raise RuntimeError('gateway became busy or changed; reverting executable without restart')
    except Exception:
        TARGET.rename(stage);previous.rename(TARGET)
        record['status']='reverted_before_restart';write(record);raise
    # Do not replay this non-idempotent operation after an ambiguous transport result.
    try:record['restart']=restart(state)
    except Exception as exc:record['restartObservation']=type(exc).__name__
    record['status']='restart_observation_pending';write(record)
    deadline=time.monotonic()+75
    while time.monotonic()<deadline:
        time.sleep(1)
        try:
            live=read_local('/api/system/memory')
            if live['pid']==state['pid']:continue
            if not args.rollback:
                telemetry=read_local('/api/system/provider-activity')
                if telemetry.get('pid')!=live['pid'] or telemetry.get('supportsLive') is not True:continue
            if digest(TARGET)!=desired_sha:raise RuntimeError('executable hash changed after restart')
            record.update(status='verified',newPid=live['pid'],verifiedAt=time.time(),runtime=summary(live));write(record)
            print(json.dumps({'status':'verified','rollback':args.rollback,'oldPid':state['pid'],'newPid':live['pid'],'sha256':desired_sha}),flush=True)
            return
        except (OSError,RuntimeError,ValueError,KeyError):pass
    # Restore the known-good launch file. Leave any observed runtime alone; its requests may be active.
    failed=TARGET.with_name(f'ocx.unverified-{suffix}.exe')
    if digest(TARGET)==desired_sha:
        TARGET.rename(failed);previous.rename(TARGET)
    record.update(status='launch_file_restored_runtime_unverified',observedAt=time.time());write(record)
    raise RuntimeError('restart verification timed out; previous launch executable restored, inspect runtime before any retry')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'status':'stopped','reason':str(error)}),flush=True);sys.exit(1)
