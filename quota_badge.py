"""Read-only Codex weekly quota badge; no client resource or credential edits."""
import ctypes as C
from ctypes import wintypes as W
import datetime as dt
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import shutil
import sys
import threading
import time
import tkinter as tk
from sidebar_supervisor import owned_process, ProcessCleanupError, PROCESS_STOP_SECONDS

HERE = Path(__file__).resolve().parent
STATUS = HERE / 'status.json'
REFRESH_SECONDS = 300
QUOTA_COMMAND_TIMEOUT = 15
QUOTA_RPC_TIMEOUT = 25
MAX_QUOTA_OUTPUT = 1024 * 1024


def _close_reader(proc, reader):
    if reader is not None and reader.ident is not None:
        reader.join(timeout=PROCESS_STOP_SECONDS)
        if reader.is_alive():
            raise ProcessCleanupError('Quota output reader did not stop')
    if proc is not None:
        error = None
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError as exc:
                    error = exc
        if error is not None:
            raise error


def _provider_quota_output():
    """Read a bounded response, with a deadline covering both output and exit."""
    proc = reader = None
    output = queue.Queue(maxsize=1)
    try:
        with owned_process([str(find_opencodex()), 'provider', 'quota', '--json'],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as proc:
            def read():
                try:
                    output.put_nowait(proc.stdout.read(MAX_QUOTA_OUTPUT + 1))
                except Exception as exc:
                    output.put_nowait(exc)
            reader = threading.Thread(target=read, daemon=True)
            deadline = time.monotonic() + QUOTA_COMMAND_TIMEOUT
            reader.start()
            try:
                data = output.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise subprocess.TimeoutExpired(proc.args, QUOTA_COMMAND_TIMEOUT) from None
            if isinstance(data, Exception):
                raise data
            if len(data) > MAX_QUOTA_OUTPUT:
                raise ValueError('Quota command output exceeds limit')
            code = proc.wait(timeout=max(0, deadline - time.monotonic()))
            if code:
                raise subprocess.CalledProcessError(code, proc.args)
            return data.decode('utf-8')
    finally:
        _close_reader(proc, reader)

def _bounded_percent(value):
    if not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=100:
        raise ValueError('Invalid weekly quota percent')
    return max(0,min(100,100-float(value)))

def weekly_quota_from_opencodex_report(report):
    """Read old/new report layouts without mixing account and pool windows."""
    if not isinstance(report,dict) or not isinstance(report.get('reports'),list):
        raise ValueError('OpenCodex quota report unavailable')
    row=next((item for item in report['reports'] if isinstance(item,dict) and item.get('provider')=='openai'),None)
    quota=(row or {}).get('quota') or {}
    if not isinstance(quota, dict):
        raise ValueError('Invalid OpenCodex quota data')
    # Current CLI reports put metadata beside quota; older reports nested it.
    aggregation=(row or {}).get('aggregation')
    if aggregation is None:aggregation=quota.get('aggregation')
    if aggregation is None:aggregation={}
    if not isinstance(aggregation, dict):
        raise ValueError('Invalid OpenCodex aggregation data')
    current=aggregation.get('currentAccount') or {}
    weekly=aggregation.get('weekly') or {}
    if not isinstance(current, dict) or not isinstance(weekly, dict):
        raise ValueError('Invalid OpenCodex account data')
    current_quota=current.get('quota') or {}
    if not isinstance(current_quota, dict):
        raise ValueError('Invalid OpenCodex account quota')
    def stamp(value):
        if type(value) not in (int,float) or not math.isfinite(value) or value<=0:return None
        return value/1000 if value>10**12 else value
    def window(source,percent_key,reset_key):
        used=source.get(percent_key);reset=stamp(source.get(reset_key))
        if type(used) not in (int,float) or not math.isfinite(used) or not 0<=used<=100 or reset is None:return None
        return {'remaining':_bounded_percent(used),'resetsAt':reset}

    # A pool percentage and one account's reset describe different populations.
    # Prefer one complete current-account observation, otherwise a complete pool.
    selected=current_quota
    result=window(selected,'weeklyPercent','weeklyResetAt')
    scope='current-account';plan=current.get('plan')
    if result is None:
        selected=quota;result=window(selected,'weeklyPercent','weeklyResetAt')
        scope='provider';plan=None
    if result is None:
        selected=weekly;result=window(selected,'usedPercent','nextRecoveryAt')
        scope='account-pool';plan=None
    if result is None:raise ValueError('Complete weekly quota window unavailable')
    if scope=='account-pool':
        five=aggregation.get('fiveHour')
        five_hour=window(five,'usedPercent','nextRecoveryAt') if isinstance(five,dict) else None
    else:five_hour=window(selected,'fiveHourPercent','fiveHourResetAt')
    updated=stamp(selected.get('updatedAt')) or stamp((row or {}).get('updatedAt')) or stamp(quota.get('updatedAt')) or time.time()
    return {**result,'fiveHour':five_hour,'planType':plan,'updatedAt':updated,
            'quotaSource':'opencodex provider quota','quotaScope':scope}


def weekly_quota(result):
    if not isinstance(result, dict):
        raise ValueError('Invalid Codex quota data')
    buckets = result.get('rateLimitsByLimitId')
    if buckets is not None and not isinstance(buckets, dict):
        raise ValueError('Invalid Codex quota buckets')
    bucket = buckets.get('codex') if buckets is not None else result.get('rateLimits')
    if not isinstance(bucket, dict) or not bucket:
        raise ValueError('Codex quota unavailable')
    windows = {}
    for name, duration in (('fiveHour', 300), ('weekly', 10080)):
        window = next((candidate for key in ('primary', 'secondary')
                       if isinstance((candidate := bucket.get(key)), dict)
                       and candidate.get('windowDurationMins') == duration), None)
        if not window:
            continue
        used, reset = window.get('usedPercent'), window.get('resetsAt')
        if not isinstance(used, (int, float)) or not math.isfinite(used):
            continue
        if not isinstance(reset, (int, float)) or not math.isfinite(reset):
            continue
        windows[name] = {'remaining': max(0, min(100, 100 - used)), 'resetsAt': reset}
    if 'weekly' not in windows:
        raise ValueError('Weekly window unavailable')
    return {**windows['weekly'], 'fiveHour': windows.get('fiveHour'),
            'planType': bucket.get('planType'), 'updatedAt': time.time()}


def find_codex():
    command=shutil.which('codex')
    if command:return Path(command)
    root = Path(os.environ.get('LOCALAPPDATA','')) / 'OpenAI/Codex/bin'
    found = sorted(root.glob('*/codex.exe'), key=lambda p: p.stat().st_mtime, reverse=True) if root.exists() else []
    if not found:
        raise FileNotFoundError('Codex executable unavailable')
    return found[0]

def find_opencodex():
    command=shutil.which('ocx')
    if command:return Path(command)
    for path in (Path(r'C:/Program Files/OpenCodex/ocx.exe'),Path(os.environ.get('LOCALAPPDATA',''))/'OpenCodex/ocx.exe'):
        if path.exists():return path
    raise FileNotFoundError('OpenCodex ocx executable unavailable')


def fetch_quota():
    # Prefer the read-only provider quota observation used by OpenCodex 2.92+
    # and fall back to the older Codex app-server response for older clients.
    try:
        return weekly_quota_from_opencodex_report(json.loads(_provider_quota_output()))
    except (OSError,ValueError,subprocess.SubprocessError,RecursionError):
        pass
    # Only initialize and read quota; never starts a model turn or consumes a reset.
    proc = reader = None
    # Two requested responses and one terminal message; notifications are discarded.
    messages = queue.Queue(maxsize=3)
    try:
        with owned_process([str(find_codex()), 'app-server', '--stdio'],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as proc:
            def read():
                seen = set()
                try:
                    while True:
                        line = proc.stdout.readline(MAX_QUOTA_OUTPUT + 1)
                        if not line:
                            break
                        if len(line) > MAX_QUOTA_OUTPUT:
                            break
                        try:
                            msg = json.loads(line)
                        except (ValueError, RecursionError):
                            continue
                        if not isinstance(msg, dict):
                            continue
                        request_id = msg.get('id')
                        if type(request_id) is int and request_id in (1, 2) and request_id not in seen:
                            seen.add(request_id)
                            messages.put_nowait(msg)
                except (OSError, ValueError):
                    pass
                finally:
                    messages.put_nowait({'error': 'closed'})
            reader = threading.Thread(target=read, daemon=True)
            reader.start()

            def send(data):
                proc.stdin.write((json.dumps(data) + '\n').encode('utf-8'))
                proc.stdin.flush()

            def receive(request_id):
                deadline = time.monotonic() + QUOTA_RPC_TIMEOUT
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError('Quota service timed out')
                    try:
                        msg = messages.get(timeout=remaining)
                    except queue.Empty:
                        raise RuntimeError('Quota service timed out') from None
                    if msg.get('id') == request_id:
                        if 'error' in msg or 'result' not in msg:
                            raise RuntimeError('Quota request rejected')
                        return msg['result']
                    if msg.get('error') == 'closed':
                        raise RuntimeError('Quota service unavailable')

            send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {
                'name': 'codex_quota_badge', 'title': 'Codex weekly quota badge', 'version': '1.0.0'}}})
            receive(1)
            send({'method': 'initialized'})
            send({'id': 2, 'method': 'account/rateLimits/read', 'params': {}})
            return weekly_quota(receive(2))
    finally:
        _close_reader(proc, reader)


class Native:
    def __init__(self):
        self.u = C.WinDLL('user32', use_last_error=True)
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.u.SetProcessDpiAwarenessContext.argtypes = [W.HANDLE]
        self.u.SetProcessDpiAwarenessContext(W.HANDLE(-4))
        self.u.GetForegroundWindow.restype = W.HWND
        self.u.GetParent.argtypes = [W.HWND]
        self.u.GetParent.restype = W.HWND
        self.u.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
        self.u.ClientToScreen.argtypes = [W.HWND, C.POINTER(W.POINT)]
        self.u.GetDpiForWindow.argtypes = [W.HWND]
        self.u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        self.u.IsIconic.argtypes = [W.HWND]
        self.u.IsWindowVisible.argtypes = [W.HWND]
        self.u.GetWindowLongPtrW.argtypes = [W.HWND, C.c_int]
        self.u.GetWindowLongPtrW.restype = C.c_ssize_t
        self.u.SetWindowLongPtrW.argtypes = [W.HWND, C.c_int, C.c_ssize_t]
        self.u.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.CreateMutexW.argtypes = [W.LPVOID, W.BOOL, W.LPCWSTR]
        self.k.CreateMutexW.restype = W.HANDLE
        self.mutex = self.k.CreateMutexW(None, False, 'Local\\CodexWeeklyQuotaBadge-260162')
        if C.get_last_error() == 183:
            sys.exit(0)

    def is_codex(self, hwnd):
        pid = W.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return False
        try:
            size = W.DWORD(32768)
            path = C.create_unicode_buffer(size.value)
            if not self.k.QueryFullProcessImageNameW(handle, 0, path, C.byref(size)):
                return False
            name = path.value.lower()
            return ('openai.codex_' in name or '\\openai\\codex\\' in name) and name.endswith(('\\chatgpt.exe', '\\codex.exe'))
        finally:
            self.k.CloseHandle(handle)


class Badge:
    def __init__(self):
        self.native = Native()
        initial_foreground = self.native.u.GetForegroundWindow()
        self.root = tk.Tk()
        self.root.title('Codex Weekly Quota Badge')
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.configure(bg='#f4f3f2')
        self.root.update_idletasks()
        self.hwnd = self.native.u.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        style = self.native.u.GetWindowLongPtrW(self.hwnd, -20)
        self.native.u.SetWindowLongPtrW(self.hwnd, -20, style | 0x08000000 | 0x80)
        self.canvas = tk.Canvas(self.root, bg='#f4f3f2', highlightthickness=0, bd=0, cursor='hand2')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Enter>', self.show_tip)
        self.canvas.bind('<Leave>', self.hide_tip)
        self.canvas.bind('<Button-1>', lambda _: self.refresh())
        self.canvas.bind('<Button-3>', self.show_menu)
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label='刷新周额度', command=self.refresh)
        self.menu.add_separator()
        self.menu.add_command(label='退出额度显示', command=self.close)
        self.tip = None
        self.data = None
        self.error = False
        self.busy = False
        self.results = queue.Queue()
        self.target = initial_foreground if self.native.is_codex(initial_foreground) else None
        self.visible = False
        self.scale = 1
        self.last_geometry = None
        self.last_status = None
        self.next_refresh = 0
        self.refresh()
        self.tick()

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.next_refresh = time.time() + REFRESH_SECONDS
        def worker():
            try:
                self.results.put((fetch_quota(), False))
            except Exception:
                self.results.put((None, True))
        threading.Thread(target=worker, daemon=True).start()

    def draw(self):
        s = self.scale
        self.canvas.delete('all')
        valid = self.data and time.time() < self.data['resetsAt'] and time.time() - self.data['updatedAt'] < 660
        text = f"{self.data['remaining']:g}%" if valid and not self.error else '—'
        color = '#b45309' if valid and self.data['remaining'] <= 10 else '#555555'
        self.canvas.create_text(17*s, 9*s, text=text, fill=color,
                                font=('Segoe UI', -max(11, round(13*s)), 'bold'))
        self.canvas.create_text(17*s, 24*s, text='周剩余', fill='#777777',
                                font=('Microsoft YaHei UI', -max(8, round(9*s))))

    def show_tip(self, _=None):
        self.hide_tip()
        self.tip = tk.Toplevel(self.root)
        self.tip.overrideredirect(True)
        self.tip.attributes('-topmost', True)
        text = 'Codex 周额度\n暂时无法读取，点击重试' if self.error else 'Codex 周额度\n正在读取…'
        if self.data and not self.error:
            reset = dt.datetime.fromtimestamp(self.data['resetsAt']).strftime('%m月%d日 %H:%M')
            updated = dt.datetime.fromtimestamp(self.data['updatedAt']).strftime('%H:%M:%S')
            text = f"Codex 周额度剩余 {self.data['remaining']:g}%\n重置：{reset}\n更新：{updated} · 每5分钟刷新\n点击刷新 · 右键退出"
        tk.Label(self.tip, text=text, justify='left', padx=12, pady=8, bg='#fffdf9', fg='#333333',
                 font=('Microsoft YaHei UI', 10), relief='solid', bd=1).pack()
        self.tip.geometry(f'+{self.root.winfo_x()+self.root.winfo_width()+8}+{self.root.winfo_y()-45}')

    def hide_tip(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None

    def show_menu(self, event):
        self.hide_tip()
        self.menu.tk_popup(event.x_root, event.y_root)

    def close(self):
        self.root.destroy()

    def tick(self):
        changed = False
        try:
            data, error = self.results.get_nowait()
            if data:
                self.data = data
            self.error, self.busy, changed = error, False, True
        except queue.Empty:
            pass
        foreground = self.native.u.GetForegroundWindow()
        if foreground and self.native.is_codex(foreground):
            self.target = foreground
        should_show = bool(self.target and foreground in (self.target, self.hwnd)
                           and not self.native.u.IsIconic(self.target)
                           and self.native.u.IsWindowVisible(self.target))
        if should_show:
            rect, point = W.RECT(), W.POINT(0, 0)
            self.native.u.GetClientRect(self.target, C.byref(rect))
            self.native.u.ClientToScreen(self.target, C.byref(point))
            self.scale = (self.native.u.GetDpiForWindow(self.target) or 96) / 96
            s = self.scale
            x, y = point.x+round(6*s), point.y+rect.bottom-round(108*s)
            w, h = round(34*s), round(30*s)
            geometry = (x, y, w, h)
            if self.last_geometry != geometry or not self.visible:
                self.root.geometry(f'{w}x{h}+{x}+{y}')
                self.root.deiconify()
                self.native.u.SetWindowPos(self.hwnd, W.HWND(-1), x, y, w, h, 0x10 | 0x40)
                self.last_geometry = geometry
                changed = True
            self.visible = True
        elif self.visible:
            self.hide_tip()
            self.root.withdraw()
            self.visible = False
        if changed:
            self.draw()
        if self.visible and not self.busy and time.time() >= self.next_refresh:
            self.refresh()
        status = {'pid': os.getpid(), 'hwnd': self.hwnd, 'target': self.target,
                  'visible': self.visible, 'geometry': self.last_geometry,
                  'quota': self.data, 'readError': self.error, 'refreshSeconds': REFRESH_SECONDS}
        if status != self.last_status:
            import threading
            temp=STATUS.with_name(f'{STATUS.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            try:
                temp.write_text(json.dumps(status, ensure_ascii=False), encoding='utf-8')
                for attempt in range(4):
                    try:temp.replace(STATUS);break
                    except PermissionError:
                        if attempt==3:raise
                        time.sleep(.02*(attempt+1))
            finally:
                try:temp.unlink(missing_ok=True)
                except OSError:pass
            self.last_status = status
        self.root.after(350, self.tick)


if __name__ == '__main__':
    if '--check' in sys.argv:
        print(json.dumps(fetch_quota()))
    else:
        try:
            Badge().root.mainloop()
        except Exception:
            import traceback
            (HERE / 'error.log').write_text(traceback.format_exc(), encoding='utf-8')
            raise
