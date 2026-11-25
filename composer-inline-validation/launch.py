"""Opt-in validation in the normal Windows Codex profile."""
import argparse
from contextlib import closing
import ctypes as C
from ctypes import wintypes as W
import json
import msvcrt
import os
from pathlib import Path
from queue import Empty
import sqlite3
import subprocess
import tempfile
import traceback
from time import monotonic, sleep, time
import _winapi

from devtools_pipe import DevTools
from usage_bridge import UsageBridge

HERE = Path(__file__).resolve().parent


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile('w', dir=path.parent, encoding='utf-8', delete=False) as f:
        tmp = Path(f.name)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
    try:
        for attempt in range(8):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                sleep(.03)
    finally:
        tmp.unlink(missing_ok=True)


class LegacySetting:
    """Switch only the installed floating composer module for this trial."""
    def __init__(self, config, journal):
        self.config, self.journal = config, journal

    def get_item(self):
        cfg = read_json(self.config)
        items = [i for i in cfg['items'] if i.get('type') == 'session_usage']
        if len(items) != 1:
            raise ValueError('Expected one legacy composer module')
        return cfg, items[0]

    def suppress(self):
        cfg, item = self.get_item()
        if item.get('enabled', True) is False:
            return
        previous = item.copy()
        item['enabled'] = False
        atomic_json(self.journal, {'before': previous, 'after': item})
        atomic_json(self.config, cfg)

    def restore(self):
        if not self.journal.exists():
            return
        prior = read_json(self.journal)
        cfg, item = self.get_item()
        if item == prior['after']:
            item.pop('enabled', None)
            if 'enabled' in prior['before']:
                item['enabled'] = prior['before']['enabled']
            atomic_json(self.config, cfg)
        self.journal.unlink()


class RendererAssetUnavailable(RuntimeError):
    def __init__(self, name, reason):
        self.asset = name
        self.reason = reason
        super().__init__(f'{name}: {reason}')


def read_renderer_asset(path):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RendererAssetUnavailable(path.name, 'unreadable') from exc
    if raw.startswith(b'%TSD-Header'):
        raise RendererAssetUnavailable(path.name, 'protected-file-header')
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise RendererAssetUnavailable(path.name, 'invalid-utf8') from exc


def source():
    return '\n'.join(('(() => {', *(read_renderer_asset(HERE / name) for name in
                                    ('legacy-widget.js', 'codex-host.js')), '})();'))


def send(dev, sid, method, obj):
    dev.evaluate(sid, 'window.__codexInlineUsageTest?.' + method + '(' + json.dumps(obj) + ')')


def desktop_idle(home, guard):
    path=(home/'thread_history_1.sqlite').resolve().as_uri()+'?mode=ro'
    with closing(sqlite3.connect(path,uri=True,timeout=.4)) as db:
        if guard:
            row=db.execute('SELECT status FROM thread_turns WHERE thread_id=? ORDER BY started_at DESC, rollout_ordinal DESC LIMIT 1',(guard,)).fetchone()
            if not row or row[0]=='inProgress':return False
        return not db.execute("SELECT 1 FROM thread_turns WHERE status='inProgress' AND started_at>=? LIMIT 1",(time()-1800,)).fetchone()


def close_host(pid, hwnd):
    user=C.WinDLL('user32',use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
    user.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
    owner=W.DWORD()
    if not user.GetWindowThreadProcessId(hwnd,C.byref(owner)) or owner.value!=pid:
        return False
    return bool(user.PostMessageW(hwnd,0x10,0,0))


def wait_for_exit(pid, report, cancel, hwnd=None, guard=None):
    try:
        handle = _winapi.OpenProcess(0x100000, False, pid)
    except OSError as exc:
        if exc.winerror == 87:
            return True
        raise
    try:
        report({'state': 'waiting-for-idle' if hwnd else 'waiting-for-normal-exit', 'previousPid': pid})
        deadline = monotonic() + 600
        quiet_since=None
        while not cancel.exists() and monotonic() < deadline:
            if _winapi.WaitForSingleObject(handle, 400) == 0:
                return True
            if hwnd:
                try:idle=desktop_idle(Path.home()/'.codex',guard)
                except (OSError,sqlite3.Error):idle=False
                quiet_since=(quiet_since or monotonic()) if idle else None
                if quiet_since and monotonic()-quiet_since>=3:
                    if not close_host(pid,hwnd):return False
                    report({'state':'closing-normally','previousPid':pid})
                    deadline=monotonic()+20
                    hwnd=None
        return False
    finally:
        _winapi.CloseHandle(handle)


def attach(proc, dev, js, root, bridge, legacy, report, *, managed=False):
    sessions = {}
    attached = exact = False
    launch_deadline = monotonic() + 35
    next_lease=0
    try:
        while proc.poll() is None and not (root / 'stop').exists():
            targets = dev.call('Target.getTargets', {'filter': [{}]})['targetInfos']
            known = {t['targetId'] for t in targets if t['type'] == 'page'
                     and t['url'].split('#', 1)[0].split('?', 1)[0] == 'app://-/index.html'}
            for stale in sessions.keys() - known:
                del sessions[stale]
            rows = []
            appearance = bridge.appearance()
            enabled = not managed or bridge.enabled()
            for target in sorted(known):
                try:
                    if target not in sessions:
                        sid = dev.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
                        sessions[target] = {'sid': sid}
                    record = sessions[target]
                    sid = record['sid']
                    status = dev.evaluate(sid, 'window.__codexInlineUsageTest?.status() ?? (document.body ? 1 : 0)')
                    if status == 1:
                        dev.evaluate(sid, js)
                        record.clear()
                        record['sid'] = sid
                        status = dev.evaluate(sid, 'window.__codexInlineUsageTest?.status()')
                    if not isinstance(status, dict):
                        continue
                    if record.get('appearance') != appearance:
                        send(dev, sid, 'setAppearance', appearance)
                        record['appearance'] = appearance
                    if record.get('enabled') is not enabled:
                        send(dev, sid, 'setEnabled', enabled)
                        record['enabled'] = enabled
                        status = dev.evaluate(sid, 'window.__codexInlineUsageTest?.status()')
                        if not isinstance(status, dict):
                            continue
                    identity = status.pop('id', None)
                    request = status.pop('card', None)
                    generation = status.pop('generation')
                    if status['mounted']:
                        if identity:
                            bridge.tick()
                        view = bridge.view(identity) if identity else None
                        summary = bridge.summary(view)
                        if summary and (generation, summary) != record.get('summary'):
                            send(dev, sid, 'setView', summary)
                        record['summary'] = generation, summary
                        if request:
                            key = generation, identity, request, view, status.get('draft'), status.get('running')
                            if key != record.get('card'):
                                card = bridge.card(view, request, draft=status.get('draft'))
                                if card:
                                    send(dev, sid, 'setCard', {'session': identity, 'card': card, 'generation': generation})
                                record['card'] = key
                        else:
                            record.pop('card', None)
                    else:
                        record.pop('card', None)
                        record.pop('summary', None)
                    rows.append({**status, 'hasExactSession': bool(identity), 'cardOpen': bool(request)})
                except RuntimeError:
                    # Navigation can discard a renderer between two requests.
                    continue
            mounted = any(row['mounted'] for row in rows)
            if (mounted or (not enabled and rows)) and not attached:
                if not managed:legacy.suppress()
                attached = True
            if managed and enabled and mounted and monotonic() >= next_lease:
                atomic_json(root/'lease.json',{'pid':proc.pid,'expires':time()+6})
                next_lease = monotonic() + 2
            elif managed and next_lease and (not enabled or not mounted):
                (root/'lease.json').unlink(missing_ok=True)
                next_lease = 0
            exact = exact or any(row['hasExactSession'] for row in rows)
            report(dict(state='active' if attached else 'connecting', pid=proc.pid, transport='pipe', enabled=enabled,
                        hasSeenExactSession=exact, renderers=rows))
            if not attached and monotonic() >= launch_deadline:
                return 'not-mounted'
            sleep(.08 if any(row['cardOpen'] for row in rows) else .17)
        return 'stopped' if (root / 'stop').exists() else 'closed'
    except (BrokenPipeError, EOFError, Empty):
        return 'disconnected'
    finally:
        if managed:(root/'lease.json').unlink(missing_ok=True)
        for record in sessions.values():
            if proc.poll() is not None:
                break
            try:
                dev.evaluate(record['sid'], 'window.__codexInlineUsageTest?.dispose()')
            except (OSError, EOFError, Empty, RuntimeError):
                continue


def connect(exe, root, bridge, legacy, report, *, managed=False):
    js = source()
    child_in, host_out = _winapi.CreatePipe(None, 0)
    host_in, child_out = _winapi.CreatePipe(None, 0)
    for handle in (child_in, child_out):
        os.set_handle_inheritable(handle, True)
    startup = subprocess.STARTUPINFO(lpAttributeList={'handle_list': [child_in, child_out]})
    args = [str(exe), '--remote-debugging-pipe', f'--remote-debugging-io-pipes={child_in},{child_out}']
    # Inherit the normal app profile without agent-specific overrides.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('CODEX_', 'OPENAI_', 'OPENCODEX_'))
           and k.upper() not in ('ELECTRON_RUN_AS_NODE', 'NODE_OPTIONS')}
    try:
        proc = subprocess.Popen(args, cwd=Path.home(), startupinfo=startup, close_fds=True, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        _winapi.CloseHandle(host_in)
        _winapi.CloseHandle(host_out)
        raise
    finally:
        _winapi.CloseHandle(child_in)
        _winapi.CloseHandle(child_out)
    with (open(msvcrt.open_osfhandle(host_in, os.O_RDONLY | os.O_BINARY), 'rb', buffering=0) as reader,
          open(msvcrt.open_osfhandle(host_out, os.O_WRONLY | os.O_BINARY), 'wb', buffering=0) as writer):
        bridge.appearance()
        if (not managed or bridge.enabled()) and proc.poll() is None:
            bridge.prime_recent()
        return attach(proc, DevTools(reader, writer), js, root, bridge, legacy, report, managed=managed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--wait-pid', type=int)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--legacy-config', type=Path, required=True)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--managed', action='store_true')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--handover-hwnd', type=int)
    parser.add_argument('--guard-thread')
    args = parser.parse_args()
    root = args.runtime.resolve()
    root.mkdir(parents=True, exist_ok=True)
    legacy = LegacySetting(args.legacy_config.resolve(), root / 'legacy-setting.json')
    with (root / 'launch.lock').open('a+b') as lock:
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            parser.exit(1, 'Inline validation is already running.\n')
        legacy.restore()
        if args.restore:
            return
        if not args.exe or not args.exe.is_file() or args.exe.name.lower() != 'chatgpt.exe' or (not args.managed and not args.wait_pid):
            parser.error('Specify the installed Codex executable and verified main Codex PID')
        if args.handover_hwnd and not (args.managed and args.wait_pid and args.guard_thread):
            parser.error('Automatic handover requires the verified desktop window and current thread')
        if (root / 'stop').exists():
            parser.error('Remove the stop marker before running validation')
        previous = None
        stage = 'checking-assets'

        def report(value):
            nonlocal previous
            if value != previous:
                atomic_json(root / 'status.json', value)
                if not args.managed and (previous is None or value['state'] != previous['state']):
                    print('Composer validation: ' + value['state'], flush=True)
                previous = value

        try:
            source()  # Check from this launcher before telling the user to exit.
            if args.preflight:
                report({'state': 'ready', 'stage': stage})
                return
            stage = 'waiting-for-exit'
            if args.wait_pid and not wait_for_exit(args.wait_pid, report, root / 'stop', args.handover_hwnd, args.guard_thread):
                report({'state': 'cancelled'})
                return
            stage = 'preparing-bridge'
            bridge = UsageBridge(Path.home() / '.codex', legacy.config)
            stage = 'starting-client'
            report({'state': connect(args.exe.resolve(), root, bridge, legacy, report, managed=args.managed)})
        except RendererAssetUnavailable as exc:
            report({'state': 'blocked', 'stage': stage, 'asset': exc.asset, 'reason': exc.reason})
            raise SystemExit(2) from None
        except Exception as exc:
            frames = [{'file': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
                      for frame in traceback.extract_tb(exc.__traceback__)[-6:]]
            report({'state': 'error', 'stage': stage, 'type': type(exc).__name__, 'frames': frames})
            raise
        finally:
            legacy.restore()


if __name__ == '__main__':
    main()
