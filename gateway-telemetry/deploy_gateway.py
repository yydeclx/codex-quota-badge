"""Version-pinned local gateway switch with idle checks, journal and reversible file replacement."""
from pathlib import Path
import argparse,base64,hashlib,hmac,http.client,json,os,secrets,shutil,sys,time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
from sidebar_activity import read_local
from patch_mode import TARGET,check_bundle,digest,gateway_bundle

def write(journal,record):
    tmp=journal.with_suffix('.tmp');tmp.write_text(json.dumps(record,indent=2),encoding='utf-8');tmp.replace(journal)
def summary(state):return {k:state.get(k) for k in ('pid','activeTurnCount','isDraining','bunVersion')}


def can_switch(state,graceful):
    count=state.get('activeTurnCount')
    return type(count) is int and 0<=count<=(1 if graceful else 0) and state.get('isDraining') is False

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
        quiet=can_switch(state,graceful)
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
    parser.add_argument('--expect-version')
    parser.add_argument('--wait-seconds',type=int,default=90);args=parser.parse_args()
    current_sha=digest(TARGET)
    bundle=gateway_bundle(current_sha)
    if bundle is None or (args.expect_version and args.expect_version!=bundle.version):
        raise RuntimeError('installed executable changed; refusing to overwrite unknown work')
    if args.graceful and bundle.version!='2.92.0':
        raise RuntimeError('switch requires zero active requests for this version')
    check_bundle(bundle,not args.rollback)
    journal=bundle.root/'deployment.json'
    expected_sha=bundle.patch_sha if args.rollback else bundle.original_sha
    desired=bundle.original if args.rollback else bundle.candidate
    desired_sha=bundle.original_sha if args.rollback else bundle.patch_sha
    state=read_local('/api/system/memory')
    if args.check_only:
        print(json.dumps({'mode':'check-only','version':bundle.version,'rollbackVerified':True,'candidateSHA256':bundle.patch_sha,'runtime':summary(state)}));return
    if current_sha==desired_sha:
        print(json.dumps({'alreadyInstalled':True,'rollback':args.rollback,'sha256':current_sha}));return
    suffix=time.strftime('%Y%m%d-%H%M%S')
    previous=TARGET.with_name(f'ocx.{"official" if expected_sha==bundle.original_sha else "telemetry"}-{bundle.version}-before-{suffix}.exe')
    stage=TARGET.with_name(f'ocx.telemetry-stage-{suffix}.exe')
    record={'status':'preparing','rollback':args.rollback,'gracefulDrain':args.graceful,'oldPid':state['pid'],'target':str(TARGET),
        'previousExecutable':str(previous),'officialBackup':str(bundle.original),'previousSHA256':expected_sha,'targetSHA256':desired_sha,
        'candidateSHA256':bundle.patch_sha,'version':bundle.version,'startedAt':time.time()}
    write(journal,record)
    shutil.copy2(desired,stage)
    if digest(stage)!=desired_sha:raise RuntimeError('staged executable hash mismatch')
    # Three quiet observations; no forced stop and no new model requests.
    try:state=idle(args.wait_seconds,args.graceful)
    except Exception:
        # A rejected settings toggle must not leave another full executable
        # behind on every click. The persistent original/candidate stay intact.
        if stage.exists() and digest(stage)==desired_sha:stage.unlink()
        record['status']='deferred_before_switch';write(journal,record);raise
    record['oldPid']=state['pid'];record['idleBeforeSwitch']=summary(state);write(journal,record)
    if digest(TARGET)!=expected_sha:raise RuntimeError('installed executable changed while waiting')
    if previous.exists():raise RuntimeError('backup collision')
    TARGET.rename(previous)
    try:stage.rename(TARGET)
    except Exception:
        previous.rename(TARGET);raise
    record['status']='installed_before_restart';write(journal,record)
    try:
        current=read_local('/api/system/memory')
        if current['pid']!=state['pid'] or not can_switch(current,args.graceful):
            raise RuntimeError('gateway became busy or changed; reverting executable without restart')
    except Exception:
        TARGET.rename(stage);previous.rename(TARGET)
        record['status']='reverted_before_restart';write(journal,record);raise
    # Do not replay this non-idempotent operation after an ambiguous transport result.
    try:record['restart']=restart(state)
    except Exception as exc:record['restartObservation']=type(exc).__name__
    record['status']='restart_observation_pending';write(journal,record)
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
            record.update(status='verified',newPid=live['pid'],verifiedAt=time.time(),runtime=summary(live));write(journal,record)
            print(json.dumps({'status':'verified','rollback':args.rollback,'oldPid':state['pid'],'newPid':live['pid'],'sha256':desired_sha}),flush=True)
            return
        except (OSError,RuntimeError,ValueError,KeyError):pass
    # Restore the known-good launch file. Leave any observed runtime alone; its requests may be active.
    failed=TARGET.with_name(f'ocx.unverified-{suffix}.exe')
    if digest(TARGET)==desired_sha:
        TARGET.rename(failed);previous.rename(TARGET)
    record.update(status='launch_file_restored_runtime_unverified',observedAt=time.time());write(journal,record)
    raise RuntimeError('restart verification timed out; previous launch executable restored, inspect runtime before any retry')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'status':'stopped','reason':str(error)}),flush=True);sys.exit(1)
