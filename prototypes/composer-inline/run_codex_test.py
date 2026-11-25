"""Run the opt-in prototype in an isolated Windows Codex profile."""
import argparse
import json
import msvcrt
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import _winapi

from read_view import read_view
from session_usage import SessionUsageAdapter


class DevTools:
    def __init__(self, reader, writer):
        self.writer, self.responses, self.sequence = writer, queue.Queue(), 0
        threading.Thread(target=self.read, args=(reader,), daemon=True).start()

    def read(self, reader):
        saved = b''
        try:
            while chunk := reader.read(32768):
                parts = (saved + chunk).split(bytes(1))
                saved = parts.pop()
                for part in parts:
                    response = json.loads(part)
                    if 'id' in response:
                        self.responses.put(response)
        finally:
            self.responses.put(None)

    def call(self, method, params=None, session=None):
        self.sequence += 1
        request = {'id': self.sequence, 'method': method, 'params': params or {}}
        if session:
            request['sessionId'] = session
        self.writer.write(json.dumps(request).encode() + bytes(1))
        deadline = time.monotonic() + 5
        while True:
            reply = self.responses.get(timeout=max(0, deadline - time.monotonic()))
            if reply is None:
                raise EOFError('Test client closed')
            if reply.get('id') == self.sequence:
                if 'error' in reply:
                    raise RuntimeError(method)
                return reply['result']

    def evaluate(self, session, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True}, session)
        if 'exceptionDetails' in result:
            raise RuntimeError('Renderer script failed')
        return result['result'].get('value')


def source():
    folder = Path(__file__).resolve().parent
    css = (folder / 'preview.css').read_text(encoding='utf-8').splitlines()
    css = chr(10).join(line for line in css if line.startswith(('.inline-', '@keyframes breathe', '@media (prefers-reduced-motion')))
    return chr(10).join(('(() => {const INLINE_TEST_CSS=' + json.dumps(css) + ';',
                         (folder / 'inline-usage.js').read_text(encoding='utf-8'),
                         (folder / 'codex-host.js').read_text(encoding='utf-8'), '})();'))


def run(exe, root, probe, resume=False):
    profile, home = root / 'profile', root / 'codex'
    if resume:
        previous = json.loads((root / 'status.json').read_text(encoding='utf-8'))
        if (previous.get('transport') != 'pipe' or not profile.is_dir() or not home.is_dir()
                or profile.resolve() != profile or home.resolve() != home):
            raise ValueError('Select an existing isolated test directory')
        try:
            handle = _winapi.OpenProcess(0x100000, False, previous['pid'])
        except OSError as error:
            if error.winerror != 87:  # PID no longer exists
                raise
        else:
            try:
                if _winapi.WaitForSingleObject(handle, 0) != 0:
                    raise ValueError('Close the previous test client before resuming')
            finally:
                _winapi.CloseHandle(handle)
        if (root / 'stop').exists():
            raise ValueError('Remove the stop marker before resuming')
    else:
        root.mkdir(parents=True, exist_ok=False)
        profile.mkdir()
        home.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('CODEX_', 'OPENAI_', 'OPENCODEX_'))
           and key.upper() not in ('NODE_OPTIONS', 'ELECTRON_RUN_AS_NODE')}
    env.update(CODEX_HOME=str(home), CODEX_ELECTRON_USER_DATA_PATH=str(profile))
    child_in, host_out = _winapi.CreatePipe(None, 0)
    host_in, child_out = _winapi.CreatePipe(None, 0)
    for handle in (child_in, child_out):
        os.set_handle_inheritable(handle, True)
    startup = subprocess.STARTUPINFO(lpAttributeList={'handle_list': [child_in, child_out]})
    args = [str(exe), f'--user-data-dir={profile}', '--remote-debugging-pipe',
            f'--remote-debugging-io-pipes={child_in},{child_out}']
    launch_time = time.monotonic()
    js = source()
    adapter = SessionUsageAdapter(home)
    sessions = {}
    with (root / 'client.log').open('wb') as log:
        try:
            proc = subprocess.Popen(args, cwd=root, env=env, startupinfo=startup, close_fds=True,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        finally:
            for handle in (child_in, child_out):
                _winapi.CloseHandle(handle)
        reader = open(msvcrt.open_osfhandle(host_in, os.O_RDONLY | os.O_BINARY), 'rb', buffering=0)
        writer = open(msvcrt.open_osfhandle(host_out, os.O_WRONLY | os.O_BINARY), 'wb', buffering=0)
        with reader, writer:
            dev = DevTools(reader, writer)
            enabled_before, last_report = None, None
            next_metrics = 0
            try:
                while proc.poll() is None and not (root / 'stop').exists():
                    reload_marker = root / 'reload'
                    reload_source = reload_marker.exists()
                    if reload_source:
                        js = source()
                        reload_marker.unlink()
                    targets = dev.call('Target.getTargets', {'filter': [{}]})['targetInfos']
                    known = {t['targetId'] for t in targets if t['type'] == 'page'
                             and t['url'].split('#', 1)[0].split('?', 1)[0] == 'app://-/index.html'}
                    for stale in sessions.keys() - known:
                        del sessions[stale]
                    reports = []
                    enabled = not (root / 'disable').exists()
                    update = time.monotonic() >= next_metrics
                    for target in sorted(known):
                        try:
                            if target not in sessions:
                                sessions[target] = dev.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
                            session = sessions[target]
                            install = dev.evaluate(session, '!!document.body && !window.__codexInlineUsageTest')
                            if install or (reload_source and dev.evaluate(session, '!!document.body')):
                                dev.evaluate(session, js)
                            if install or reload_source or enabled != enabled_before:
                                dev.evaluate(session, 'window.__codexInlineUsageTest?.setEnabled(' + json.dumps(enabled) + ')')
                            view = dev.evaluate(session, 'window.__codexInlineUsageTest?.status()')
                            if view is None:
                                continue
                            identity = view.pop('id', None)
                            if enabled and update and identity and view['mounted']:
                                result = read_view(identity, adapter=adapter, include_telemetry=False)
                                if result:
                                    dev.evaluate(session, 'window.__codexInlineUsageTest?.setView(' + json.dumps(result) + ')')
                            reports.append({**view, 'hasExactSession': bool(identity)})
                        except RuntimeError:
                            sessions.pop(target, None)
                    if update:
                        next_metrics = time.monotonic() + 2
                    enabled_before = enabled
                    report = {'pid': proc.pid, 'transport': 'pipe', 'enabled': enabled, 'renderers': reports}
                    if report != last_report:
                        tmp = root / 'status.tmp'
                        tmp.write_text(json.dumps(report), encoding='utf-8')
                        tmp.replace(root / 'status.json')
                        print(json.dumps(report), flush=True)
                        last_report = report
                    if probe and (reports or time.monotonic() - launch_time > 20):
                        return bool(reports)
                    time.sleep(.45)
            except (EOFError, BrokenPipeError):
                if probe:
                    return False
            finally:
                if proc.poll() is None:
                    try:
                        dev.call('Browser.close')
                        proc.wait(timeout=6)
                    except (EOFError, OSError, RuntimeError, queue.Empty, subprocess.TimeoutExpired):
                        if proc.poll() is None:
                            proc.terminate()
                            proc.wait(timeout=6)
    return not probe


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--test-dir', type=Path, required=True)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Reuse a closed isolated test profile')
    args = parser.parse_args()
    if args.exe.name.lower() != 'chatgpt.exe' or not args.exe.is_file():
        parser.error('Select the installed Codex desktop ChatGPT.exe')
    raise SystemExit(0 if run(args.exe.resolve(), args.test_dir.resolve(), args.probe, args.resume) else 1)
