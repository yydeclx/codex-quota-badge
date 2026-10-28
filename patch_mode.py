"""Explicit settings-time gateway switch; no timers, hooks or background service."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parent/'gateway-telemetry'
TARGET=Path('C:/Program Files/OpenCodex/ocx.exe')
ORIGINAL=ROOT/'rollback-official-2.92.0.exe'
CANDIDATE=ROOT/'build/ocx.exe'
ORIGINAL_SHA='c7c87829a2b69720612f08e40048f5b20c8097ad8e02f353f1ef8428ab42a4c9'
PATCH_SHA='91401d409aeff213e760615cd36e40ec93a419e7b1a8b8a7367baea544de2372'


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def runtime_mode():
    from sidebar_activity import read_local
    before=read_local('/api/system/memory')
    try:
        sample=read_local('/api/system/provider-activity')
        if sample.get('pid')!=before['pid']:raise RuntimeError('网关进程已变化，请重新应用')
        enabled=sample.get('supportsLive') is True
    except RuntimeError as exc:
        if str(exc) not in ('HTTP_401','HTTP_404'):raise
        enabled=False
    after=read_local('/api/system/memory')
    if before['pid']!=after['pid']:raise RuntimeError('网关进程正在切换，请稍后重试')
    return enabled,after['pid']


def run_deployment(enabled):
    command=[sys.executable,str(ROOT/'deploy_gateway.py'),'--graceful','--wait-seconds','30']
    if not enabled:command.append('--rollback')
    result=subprocess.run(command,cwd=str(ROOT.parent),capture_output=True,text=True,encoding='utf-8',
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=180)
    if result.returncode:
        reason=''
        for line in result.stdout.splitlines():
            try:reason=json.loads(line).get('reason',reason)
            except (ValueError,AttributeError):pass
        if reason=='gateway remained busy; running executable was not replaced':
            reason='网关仍有多个请求运行，请稍后重试；程序未被替换。'
        raise RuntimeError('网关切换未完成，原设置未保存。'+(reason or '请查看 gateway-telemetry/deployment.json'))


def discard_transaction_copy():
    """Keep the fixed original/patch rollback pair, not one 135 MB copy per toggle."""
    try:
        record=json.loads((ROOT/'deployment.json').read_text(encoding='utf-8'))
        previous=Path(record.get('previousExecutable',''))
        if (record.get('status')!='verified' or Path(record.get('target','')).resolve()!=TARGET.resolve()
                or previous.resolve().parent!=TARGET.resolve().parent
                or not previous.name.startswith(('ocx.official-2.92.0-before-','ocx.telemetry-2.92.0-before-'))
                or previous.suffix!='.exe' or previous.resolve() in (TARGET.resolve(),ORIGINAL.resolve(),CANDIDATE.resolve())):return
        if digest(previous) not in (ORIGINAL_SHA,PATCH_SHA):return
        previous.unlink()
    except (OSError,ValueError,TypeError):pass


def apply_mode(enabled):
    """Switch known, tested 2.92 binaries and verify the *running* mode before save."""
    if type(enabled) is not bool:raise ValueError('Invalid realtime mode')
    installed=digest(TARGET)
    if installed not in (ORIGINAL_SHA,PATCH_SHA):
        # After an official upgrade, turning the sidebar feature off must not
        # downgrade the new installation. Only accept it if the live endpoint
        # is already absent/disabled; never assume a disk file proves that.
        if not enabled:
            running,pid=runtime_mode()
            if not running:return {'enabled':False,'pid':pid,'changed':False,'gatewayUntouched':True}
        raise RuntimeError('OpenCodex 版本或程序已变化；补丁只适配 2.92.0，未覆盖当前程序')
    if digest(ORIGINAL)!=ORIGINAL_SHA:raise RuntimeError('原版网关备份校验失败，未切换')
    desired=PATCH_SHA if enabled else ORIGINAL_SHA
    if enabled:
        smoke=json.loads((ROOT/'smoke-result.json').read_text(encoding='utf-8'))
        if digest(CANDIDATE)!=PATCH_SHA or smoke.get('passed') is not True or smoke.get('binarySHA256')!=PATCH_SHA:
            raise RuntimeError('补丁程序未通过对应版本的测试校验，未切换')
    running,pid=runtime_mode()
    if installed==desired:
        if running!=enabled:
            raise RuntimeError('磁盘程序与运行模式不一致；请先完成已有网关重启后重试')
        return {'enabled':enabled,'pid':pid,'changed':False}
    run_deployment(enabled)
    running,pid=runtime_mode()
    if digest(TARGET)!=desired or running!=enabled:
        raise RuntimeError('网关实际运行模式尚未确认；未保存新设置，请查看部署记录')
    discard_transaction_copy()
    return {'enabled':enabled,'pid':pid,'changed':True}
