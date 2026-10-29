"""Native child-window quota indicator. Windows handles host movement and clipping."""
import ctypes as C
from ctypes import wintypes as W
import datetime as dt
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
import time

from quota_badge import fetch_quota, REFRESH_SECONDS

ROOT = Path(__file__).resolve().parent
LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
WINEVENTPROC = C.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND, W.LONG, W.LONG, W.DWORD, W.DWORD)
ENUMPROC = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)


class WNDCLASS(C.Structure):
    _fields_ = [('style', W.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', C.c_int),
                ('cbWndExtra', C.c_int), ('hInstance', W.HINSTANCE), ('hIcon', W.HICON),
                ('hCursor', W.HANDLE), ('hbrBackground', W.HBRUSH),
                ('lpszMenuName', W.LPCWSTR), ('lpszClassName', W.LPCWSTR)]


class PAINTSTRUCT(C.Structure):
    _fields_ = [('hdc', W.HDC), ('fErase', W.BOOL), ('rcPaint', W.RECT),
                ('fRestore', W.BOOL), ('fIncUpdate', W.BOOL), ('rgbReserved', W.BYTE * 32)]


class TOOLINFO(C.Structure):
    _fields_ = [('cbSize', W.UINT), ('uFlags', W.UINT), ('hwnd', W.HWND),
                ('uId', W.WPARAM), ('rect', W.RECT), ('hinst', W.HINSTANCE),
                ('lpszText', W.LPWSTR), ('lParam', W.LPARAM), ('lpReserved', C.c_void_p)]


class NMHDR(C.Structure):
    _fields_ = [('hwndFrom',W.HWND),('idFrom',W.WPARAM),('code',W.UINT)]


def rgb(r, g, b):
    return r | g << 8 | b << 16

def write_runtime_json(path,data):
    """Best-effort UI snapshots: one atomic attempt, no sleep on the message loop."""
    temp=path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        temp.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
        temp.replace(path)
        return True
    except OSError:
        return False
    finally:
        try:temp.unlink(missing_ok=True)
        except OSError:pass


class Win:
    def __init__(self):
        self.u = C.WinDLL('user32', use_last_error=True)
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.g = C.WinDLL('gdi32', use_last_error=True)
        self.cc = C.WinDLL('comctl32', use_last_error=True)
        self.theme = C.WinDLL('uxtheme', use_last_error=True)
        def bind(dll, name, result, *args):
            f = getattr(dll, name)
            f.restype, f.argtypes = result, list(args)
        bind(self.k, 'GetModuleHandleW', W.HMODULE, W.LPCWSTR)
        bind(self.k, 'OpenProcess', W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
        bind(self.k, 'CloseHandle', W.BOOL, W.HANDLE)
        bind(self.k, 'QueryFullProcessImageNameW', W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
        bind(self.k, 'CreateMutexW', W.HANDLE, C.c_void_p, W.BOOL, W.LPCWSTR)
        bind(self.u, 'SetProcessDpiAwarenessContext', W.BOOL, W.HANDLE)
        bind(self.u, 'GetWindowDpiAwarenessContext', W.HANDLE, W.HWND)
        bind(self.u, 'SetThreadDpiAwarenessContext', W.HANDLE, W.HANDLE)
        bind(self.u, 'GetDpiForWindow', W.UINT, W.HWND)
        bind(self.u, 'RegisterClassW', W.ATOM, C.POINTER(WNDCLASS))
        bind(self.u, 'CreateWindowExW', W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
             C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p)
        bind(self.u, 'DefWindowProcW', LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        bind(self.u, 'GetForegroundWindow', W.HWND)
        bind(self.u, 'GetCursorPos', W.BOOL, C.POINTER(W.POINT))
        bind(self.u, 'WindowFromPoint', W.HWND, W.POINT)
        bind(self.u, 'GetWindowThreadProcessId', W.DWORD, W.HWND, C.POINTER(W.DWORD))
        bind(self.u, 'GetParent', W.HWND, W.HWND)
        bind(self.u, 'GetAncestor', W.HWND, W.HWND, W.UINT)
        bind(self.u, 'GetWindowLongPtrW', C.c_ssize_t, W.HWND, C.c_int)
        bind(self.u, 'SetWindowLongPtrW', C.c_ssize_t, W.HWND, C.c_int, C.c_ssize_t)
        bind(self.u, 'SetWindowRgn', C.c_int, W.HWND, W.HANDLE, W.BOOL)
        bind(self.g, 'CreateRoundRectRgn', W.HANDLE, C.c_int,C.c_int,C.c_int,C.c_int,C.c_int,C.c_int)
        bind(self.theme, 'SetWindowTheme', W.LONG, W.HWND, W.LPCWSTR, W.LPCWSTR)
        bind(self.u, 'GetClientRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
        bind(self.u, 'GetWindowRect', W.BOOL, W.HWND, C.POINTER(W.RECT))
        bind(self.u, 'ClientToScreen', W.BOOL, W.HWND, C.POINTER(W.POINT))
        bind(self.u, 'EnumWindows', W.BOOL, ENUMPROC, W.LPARAM)
        bind(self.u, 'IsWindow', W.BOOL, W.HWND)
        bind(self.u, 'IsWindowVisible', W.BOOL, W.HWND)
        bind(self.u, 'IsIconic', W.BOOL, W.HWND)
        bind(self.u, 'ShowWindow', W.BOOL, W.HWND, C.c_int)
        bind(self.u, 'SetWindowPos', W.BOOL, W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT)
        bind(self.u, 'SetLayeredWindowAttributes', W.BOOL, W.HWND, W.DWORD, W.BYTE, W.DWORD)
        bind(self.u, 'DestroyWindow', W.BOOL, W.HWND)
        bind(self.u, 'PostMessageW', W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        bind(self.u, 'SendMessageW', LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        bind(self.u, 'SetTimer', W.WPARAM, W.HWND, W.WPARAM, W.UINT, C.c_void_p)
        bind(self.u, 'KillTimer', W.BOOL, W.HWND, W.WPARAM)
        bind(self.u, 'GetMessageW', W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
        bind(self.u, 'TranslateMessage', W.BOOL, C.POINTER(W.MSG))
        bind(self.u, 'DispatchMessageW', LRESULT, C.POINTER(W.MSG))
        bind(self.u, 'PostQuitMessage', None, C.c_int)
        bind(self.u, 'SetWinEventHook', W.HANDLE, W.DWORD, W.DWORD, W.HMODULE,
             WINEVENTPROC, W.DWORD, W.DWORD, W.DWORD)
        bind(self.u, 'UnhookWinEvent', W.BOOL, W.HANDLE)
        bind(self.u, 'BeginPaint', W.HDC, W.HWND, C.POINTER(PAINTSTRUCT))
        bind(self.u, 'EndPaint', W.BOOL, W.HWND, C.POINTER(PAINTSTRUCT))
        bind(self.u, 'InvalidateRect', W.BOOL, W.HWND, C.POINTER(W.RECT), W.BOOL)
        bind(self.u, 'FillRect', C.c_int, W.HDC, C.POINTER(W.RECT), W.HBRUSH)
        bind(self.u, 'DrawTextW', C.c_int, W.HDC, W.LPCWSTR, C.c_int, C.POINTER(W.RECT), W.UINT)
        bind(self.u, 'GetDC', W.HDC, W.HWND)
        bind(self.u, 'ReleaseDC', C.c_int, W.HWND, W.HDC)
        bind(self.u, 'CreatePopupMenu', W.HMENU)
        bind(self.u, 'AppendMenuW', W.BOOL, W.HMENU, W.UINT, W.WPARAM, W.LPCWSTR)
        bind(self.u, 'TrackPopupMenuEx', W.UINT, W.HMENU, W.UINT, C.c_int, C.c_int, W.HWND, C.c_void_p)
        bind(self.u, 'DestroyMenu', W.BOOL, W.HMENU)
        bind(self.u, 'LoadCursorW', W.HANDLE, W.HINSTANCE, C.c_void_p)
        bind(self.g, 'CreateSolidBrush', W.HBRUSH, W.DWORD)
        bind(self.g, 'CreatePen', W.HANDLE, C.c_int, C.c_int, W.DWORD)
        bind(self.g, 'GetStockObject', W.HANDLE, C.c_int)
        bind(self.g, 'Ellipse', W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int)
        bind(self.g, 'RoundRect', W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int)
        bind(self.g, 'MoveToEx', W.BOOL, W.HDC, C.c_int, C.c_int, C.c_void_p)
        bind(self.g, 'LineTo', W.BOOL, W.HDC, C.c_int, C.c_int)
        bind(self.g, 'CreateCompatibleDC', W.HDC, W.HDC)
        bind(self.g, 'CreateCompatibleBitmap', W.HBITMAP, W.HDC, C.c_int, C.c_int)
        bind(self.g, 'DeleteDC', W.BOOL, W.HDC)
        bind(self.g, 'SetStretchBltMode', C.c_int, W.HDC, C.c_int)
        bind(self.g, 'SetBrushOrgEx', W.BOOL, W.HDC, C.c_int, C.c_int, C.POINTER(W.POINT))
        bind(self.g, 'StretchBlt', W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int,
             W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.DWORD)
        bind(self.g, 'SelectObject', W.HANDLE, W.HDC, W.HANDLE)
        bind(self.g, 'DeleteObject', W.BOOL, W.HANDLE)
        bind(self.g, 'SetBkMode', C.c_int, W.HDC, C.c_int)
        bind(self.g, 'SetTextColor', W.DWORD, W.HDC, W.DWORD)
        bind(self.g, 'GetPixel', W.DWORD, W.HDC, C.c_int, C.c_int)
        bind(self.g, 'CreateFontW', W.HFONT, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int,
             W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR)
        self.u.SetProcessDpiAwarenessContext(W.HANDLE(-4))
        self.instance = self.k.GetModuleHandleW(None)

    def process_path(self, hwnd):
        pid = W.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ''
        try:
            path, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            return path.value if self.k.QueryFullProcessImageNameW(handle, 0, path, C.byref(size)) else ''
        finally:
            self.k.CloseHandle(handle)

    def is_codex(self, hwnd):
        if not hwnd or not self.u.IsWindowVisible(hwnd):
            return False
        self.u.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
        self.u.GetWindow.argtypes=[W.HWND,W.UINT];self.u.GetWindow.restype=W.HWND
        name=C.create_unicode_buffer(256)
        if not self.u.GetClassNameW(hwnd,name,len(name)) or name.value!='Chrome_WidgetWin_1':return False
        # Only the unowned Electron main window can host the sidebar.
        if self.u.GetWindow(hwnd,4):return False  # GW_OWNER: about/settings/dialogs
        if self.u.GetWindowLongPtrW(hwnd,-16)&0x40000000:return False  # WS_CHILD
        if self.u.GetWindowLongPtrW(hwnd,-20)&0x80:return False  # WS_EX_TOOLWINDOW
        rect = W.RECT()
        self.u.GetClientRect(hwnd, C.byref(rect))
        if rect.right < 400 or rect.bottom < 200:
            return False
        path = self.process_path(hwnd).lower()
        return ('openai.codex_' in path or '\\openai\\codex\\' in path) and path.endswith(('\\chatgpt.exe', '\\codex.exe'))


class Badge:
    owned_popup = False
    def __init__(self, test_host=None):
        self.win = Win()
        self.test_host = test_host
        self.status_file = ROOT / ('test-status.json' if test_host else 'status.json')
        self.mutex = self.win.k.CreateMutexW(None, False, 'Local\\CodexQuotaNative-' + ('test' if test_host else '260162'))
        if C.get_last_error() == 183:
            raise SystemExit(0)
        self.target = self.hwnd = self.tooltip = None
        self.tooltip_font = None
        self.hover_since = None
        self.tooltip_active = False
        self.location_hook = None
        if test_host:
            restored, restored_error = None, False
        else:
            restored, restored_error = self.restore_last_quota()
        self.data, self.error, self.busy = restored, restored_error, False
        self.refresh_failures = 0
        self.results = queue.Queue()
        self.next_refresh = 0
        self.scale, self.geometry = 1, None
        self.bg = rgb(243, 242, 240)  # Reference screenshot sidebar: #F3F2F0
        self.last_visual = None
        self.last_status = None
        self.layout_events = 0
        self.closing = False
        self.proc = WNDPROC(self.window_proc)
        self.event_proc = WINEVENTPROC(self.window_event)
        # CS_DBLCLKS asks Windows to emit WM_LBUTTONDBLCLK for the native
        # sidebar child instead of reporting every activation as a single click.
        wc = WNDCLASS(0x0008, self.proc, 0, 0, self.win.instance, None,
                      self.win.u.LoadCursorW(None, C.c_void_p(32649)), None, None, 'CodexQuotaNativeV2')
        if not self.win.u.RegisterClassW(C.byref(wc)):
            raise C.WinError(C.get_last_error())
        self.controller = self.win.u.CreateWindowExW(0, wc.lpszClassName, 'Quota controller', 0,
                                                   0, 0, 0, 0, W.HWND(-3), None, self.win.instance, None)
        if not self.controller:
            raise C.WinError(C.get_last_error())
        self.foreground_hook = self.win.u.SetWinEventHook(3, 3, None, self.event_proc, 0, 0, 2)
        if not self.foreground_hook:
            raise C.WinError(C.get_last_error())
        self.win.u.SetTimer(self.controller, 1, 1000, None)
        # The activity dot remains smooth at 75 ms while avoiding an
        # unnecessary 20 Hz full GDI surface rebuild when the sidebar is idle.
        self.win.u.SetTimer(self.controller, 2, 75, None)
        self.find_target()
        self.refresh()

    def find_target(self):
        if self.test_host:
            if self.win.u.IsWindow(self.test_host):
                self.attach(self.test_host)
            return
        foreground = self.win.u.GetForegroundWindow()
        if self.win.is_codex(foreground):
            self.attach(foreground)
            return
        if self.target and self.win.is_codex(self.target):
            self.attach(self.target)
            return
        found = []
        @ENUMPROC
        def enum(hwnd, _):
            if self.win.is_codex(hwnd):
                found.append(hwnd)
            return True
        self.win.u.EnumWindows(enum, 0)
        if found:
            self.attach(found[0])

    def attach(self, target):
        if not self.test_host and not self.win.is_codex(target):return
        if self.target == target and self.hwnd and self.win.u.IsWindow(self.hwnd):
            return
        if self.location_hook:
            self.win.u.UnhookWinEvent(self.location_hook)
            self.location_hook = None
        if self.tooltip:
            self.win.u.DestroyWindow(self.tooltip)
            self.tooltip = None
        if self.hwnd and self.win.u.IsWindow(self.hwnd):
            self.win.u.DestroyWindow(self.hwnd)
        self.target, self.hwnd, self.geometry = target, None, None
        # Match the host's DPI context before creating a *child* directly.
        # No SetParent DPI reset; no code injected into or style changed on Codex.
        context = self.win.u.GetWindowDpiAwarenessContext(target)
        old_context = self.win.u.SetThreadDpiAwarenessContext(context)
        try:
            extended = 0x08000000 | 0x80000 | (0x80 if self.owned_popup else 4)
            style = (0x80000000 if self.owned_popup else 0x40000000) | 0x04000000
            self.hwnd = self.win.u.CreateWindowExW(extended, 'CodexQuotaNativeV2',
                'Codex Weekly Quota Badge', style,
                0, 0, 1, 1, target, None, self.win.instance, None)
        finally:
            self.win.u.SetThreadDpiAwarenessContext(old_context)
        if not self.hwnd:
            raise C.WinError(C.get_last_error())
        # UpdateLayeredWindow and SetLayeredWindowAttributes cannot be mixed.
        if not getattr(self,'per_pixel_alpha',False) and not self.win.u.SetLayeredWindowAttributes(self.hwnd, 0, 255, 2):
            raise C.WinError(C.get_last_error())
        pid = W.DWORD()
        self.win.u.GetWindowThreadProcessId(target, C.byref(pid))
        self.location_hook = self.win.u.SetWinEventHook(0x8000, 0x800B, None,
            self.event_proc, pid.value, 0, 2)
        if not self.location_hook:
            raise C.WinError(C.get_last_error())
        self.layout()
        self.create_tooltip()

    def ensure_child_front(self):
        """Electron can recreate/reorder its renderer above our visible child."""
        if not self.hwnd or not self.target or not self.win.u.IsWindow(self.hwnd):return
        if self.win.u.GetParent(self.hwnd)!=self.target:return
        self.win.u.GetWindow.argtypes=[W.HWND,W.UINT];self.win.u.GetWindow.restype=W.HWND
        if self.win.u.GetWindow(self.hwnd,3):  # GW_HWNDPREV: a sibling is above us
            self.win.u.SetWindowPos(self.hwnd,0,0,0,0,0,0x13)  # top sibling, no activate/move/size

    def layout(self):
        if not self.hwnd or not self.win.u.IsWindow(self.hwnd) or self.win.u.IsIconic(self.target):
            return
        rect = W.RECT()
        if not self.win.u.GetClientRect(self.target, C.byref(rect)):
            return
        self.scale = (self.win.u.GetDpiForWindow(self.target) or 96) / 96
        s = self.scale
        geometry = (round(6*s), rect.bottom-round(117*s), round(34*s), round(30*s))
        if geometry != self.geometry:
            if not self.win.u.SetWindowPos(self.hwnd, W.HWND(0), *geometry, 0x10 | 0x40):
                raise C.WinError(C.get_last_error())
            self.geometry = geometry
            self.layout_events += 1
            self.request_repaint()

    def window_event(self, hook, event, hwnd, obj, child, thread, stamp):
        try:
            if self.closing:
                return
            if event == 3 and not self.test_host:
                if self.win.is_codex(hwnd):
                    self.attach(hwnd)
            elif event in (0x8000,0x8002,0x8004) and hwnd!=self.hwnd and obj==0 and child==0 and (hwnd==self.target or self.win.u.GetParent(hwnd)==self.target):
                self.ensure_child_front()
            elif event == 0x800B and hwnd == self.target and obj == 0 and child == 0:
                self.layout()
                self.track_hover(False)
        except Exception:
            self.log_error()

    def valid(self):
        # A successful quota value remains suitable for display throughout its
        # server-declared window.  Staleness controls background refresh timing,
        # not whether the UI briefly falls back to a dash after sleep.
        return bool(self.data and not self.error and time.time() < self.data['resetsAt'])

    def restore_last_quota(self):
        """Restore only a bounded, previously displayed quota snapshot."""
        try:
            state=json.loads(self.status_file.read_text(encoding='utf-8'))
            quota=state.get('quota');error=state.get('readError') is True
            if not isinstance(quota,dict):return None,error
            remaining,reset,updated=(quota.get(k) for k in ('remaining','resetsAt','updatedAt'))
            if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (remaining,reset,updated)):return None,error
            if not 0<=remaining<=100 or reset<=time.time():return None,error
            five=quota.get('fiveHour')
            if five is not None and not isinstance(five,dict):five=None
            return {**quota,'fiveHour':five},error
        except (OSError,ValueError,TypeError):return None,False

    def refresh(self):
        if self.busy:
            return
        self.next_refresh = time.time() + REFRESH_SECONDS
        if self.test_host:
            self.data = {'remaining': 3, 'resetsAt': time.time()+86400, 'updatedAt': time.time()}
            self.update_visual()
            return
        self.busy = True
        def worker():
            try:
                self.results.put((fetch_quota(), False))
            except Exception:
                self.results.put((None, True))
            self.win.u.PostMessageW(self.controller, 0x8001, 0, 0)
        try:threading.Thread(target=worker, daemon=True).start()
        except Exception:
            self.busy=False;self.next_refresh=time.time()+5
            self.refresh_failures=getattr(self,'refresh_failures',0)+1
            self.error=self.refresh_failures>=2 or not self.data
            self.log_error()

    def update_visual(self):
        state = (self.valid(), self.data.get('remaining') if self.data else None, self.bg, self.scale)
        if state != self.last_visual and self.hwnd:
            self.request_repaint()
            self.last_visual = state
        self.update_tooltip()

    def request_repaint(self,rect=None):
        if self.hwnd:self.win.u.InvalidateRect(self.hwnd,C.byref(rect) if rect is not None else None,False)

    def paint(self, hwnd, dc):
        rect = W.RECT()
        self.win.u.GetClientRect(hwnd, C.byref(rect))
        # Render only on repaint, then downsample for a smooth small icon.
        factor = 3
        clip=W.RECT()
        self.win.g.GetClipBox.argtypes=[W.HDC,C.POINTER(W.RECT)]
        self.win.g.GetClipBox(dc,C.byref(clip))
        if clip.right<=clip.left or clip.bottom<=clip.top:return
        width,height=(clip.right-clip.left)*factor,(clip.bottom-clip.top)*factor
        surface = self.win.g.CreateCompatibleDC(dc)
        bitmap = self.win.g.CreateCompatibleBitmap(dc,width,height)
        if not surface or not bitmap:
            if bitmap:
                self.win.g.DeleteObject(bitmap)
            if surface:
                self.win.g.DeleteDC(surface)
            self.paint_icon(dc,rect,self.scale)
            return
        previous = self.win.g.SelectObject(surface,bitmap)
        try:
            self.win.g.SetViewportOrgEx.argtypes=[W.HDC,C.c_int,C.c_int,C.c_void_p]
            self.win.g.SetViewportOrgEx(surface,-clip.left*factor,-clip.top*factor,None)
            self.paint_clip=(clip.top*factor,clip.bottom*factor)
            self.paint_icon(surface,W.RECT(0,0,rect.right*factor,rect.bottom*factor),self.scale*factor)
            self.win.g.SetViewportOrgEx(surface,0,0,None)
            old_mode = self.win.g.SetStretchBltMode(dc,4)
            old_origin = W.POINT()
            self.win.g.SetBrushOrgEx(dc,0,0,C.byref(old_origin))
            self.win.g.StretchBlt(dc,clip.left,clip.top,clip.right-clip.left,clip.bottom-clip.top,surface,0,0,width,height,0x00CC0020)
            self.win.g.SetStretchBltMode(dc,old_mode)
            self.win.g.SetBrushOrgEx(dc,old_origin.x,old_origin.y,None)
        finally:
            self.paint_clip=None
            self.win.g.SelectObject(surface,previous)
            self.win.g.DeleteObject(bitmap)
            self.win.g.DeleteDC(surface)

    def paint_icon(self, dc, rect, s):
        brush = self.win.g.CreateSolidBrush(self.bg)
        self.win.u.FillRect(dc, C.byref(rect), brush)
        self.win.g.DeleteObject(brush)
        text = f"{self.data['remaining']:.0f}" if self.valid() else '—'
        dark = (self.bg & 255) + (self.bg >> 8 & 255) + (self.bg >> 16 & 255) < 384
        # Compact closed ring matching the selected outline reference.
        color = rgb(190,189,187) if dark else rgb(126,125,123)
        width = round(21*s)
        left, top = (rect.right-width)//2+round(.5*s), (rect.bottom-width)//2
        stroke = max(1,round(1.7*s))
        pen = self.win.g.CreatePen(0,stroke,color)
        old_pen = self.win.g.SelectObject(dc,pen)
        old_brush = self.win.g.SelectObject(dc,self.win.g.GetStockObject(5))
        inset = stroke//2
        self.win.g.Ellipse(dc,left+inset,top+inset,left+width-inset,top+width-inset)
        self.win.g.SelectObject(dc,old_brush)
        self.win.g.SelectObject(dc,old_pen)
        self.win.g.DeleteObject(pen)
        self.win.g.SetBkMode(dc, 1)
        font = self.win.g.CreateFontW(-round((8 if len(text) > 2 else 10.5)*s),0,0,0,700,0,0,0,1,0,0,4,0,'Calibri')
        old = self.win.g.SelectObject(dc,font)
        self.win.g.SetTextColor(dc,color)
        r = W.RECT(left,top,left+width,top+width)
        self.win.u.DrawTextW(dc,text,-1,C.byref(r),1 | 4 | 32 | 0x800)
        self.win.g.SelectObject(dc,old)
        self.win.g.DeleteObject(font)

    def create_tooltip(self):
        self.last_tooltip_text = None
        self.tooltip_active = False
        self.hover_since = None
        self.tooltip = self.win.u.CreateWindowExW(0x80 | 0x08000000 | 0x80000,
            'CodexQuotaNativeV2', 'Codex Quota Hover Card', 0x80000000,
            0,0,1,1,self.hwnd,None,self.win.instance,None)
        if not self.tooltip:
            raise C.WinError(C.get_last_error())
        self.update_tooltip()

    def track_hover(self, inside, now=None):
        now = time.monotonic() if now is None else now
        if inside:
            if self.hover_since is None:
                self.hover_since = now
            elif now-self.hover_since >= .25 and not self.tooltip_active and self.tooltip:
                self.update_tooltip(force=True)
                self.position_tooltip()
                self.win.u.ShowWindow(self.tooltip,4)
                self.tooltip_active = True
        else:
            self.hover_since = None
            if self.tooltip_active and self.tooltip:
                self.win.u.ShowWindow(self.tooltip,0)
            self.tooltip_active = False

    def poll_hover(self):
        inside = False
        if (self.hwnd and self.tooltip and self.win.u.IsWindow(self.hwnd)
                and self.win.u.GetForegroundWindow() == self.target
                and not self.win.u.IsIconic(self.target)):
            cursor, bounds = W.POINT(), W.RECT()
            if self.win.u.GetCursorPos(C.byref(cursor)) and self.win.u.GetWindowRect(self.hwnd,C.byref(bounds)):
                inside = bounds.left <= cursor.x < bounds.right and bounds.top <= cursor.y < bounds.bottom
                if inside:
                    inside = self.win.u.GetAncestor(self.win.u.WindowFromPoint(cursor),2) in (self.target,self.hwnd)
        self.track_hover(inside)

    def position_tooltip(self):
        tip, badge, host = W.RECT(), W.RECT(), W.RECT()
        self.win.u.GetWindowRect(self.tooltip,C.byref(tip))
        self.win.u.GetWindowRect(self.hwnd,C.byref(badge))
        self.win.u.GetWindowRect(self.target,C.byref(host))
        width,height = tip.right-tip.left,tip.bottom-tip.top
        gap = round(8*self.scale)
        x = min(badge.right+gap,host.right-width-gap)
        y = max(host.top+gap,min((badge.top+badge.bottom-height)//2,host.bottom-height-gap))
        self.win.u.SetWindowPos(self.tooltip,W.HWND(-1),x,y,0,0,0x1 | 0x10)

    def update_tooltip(self,force=False):
        if not self.tooltip:
            return
        valid = self.valid()
        reset = dt.datetime.fromtimestamp(self.data['resetsAt']).strftime('%m月%d日 %H:%M') if valid else ''
        remaining = self.data['remaining'] if valid else None
        key = (remaining, reset, self.scale)
        if key == self.last_tooltip_text:
            return
        from tooltip_card import render_card, upload
        self.tooltip_surface = render_card(self.scale, remaining, reset)
        upload(self.win,self.tooltip,self.tooltip_surface)
        self.last_tooltip_text = key
        if self.tooltip_active:
            self.position_tooltip()

    def context_menu(self, hwnd, lparam):
        self.track_hover(False)
        x = C.c_short(lparam & 0xffff).value
        y = C.c_short((lparam >> 16) & 0xffff).value
        menu = self.win.u.CreatePopupMenu()
        try:
            self.win.u.AppendMenuW(menu, 0, 1, '刷新周额度')
            self.win.u.AppendMenuW(menu, 0x800, 0, None)
            self.win.u.AppendMenuW(menu, 0, 2, '退出额度显示')
            choice = self.win.u.TrackPopupMenuEx(menu, 0x100 | 2, x,y,hwnd,None)
            if choice == 1:
                self.refresh()
            elif choice == 2:
                self.close()
        finally:
            self.win.u.DestroyMenu(menu)

    def heartbeat(self):
        if (not self.hwnd or not self.win.u.IsWindow(self.hwnd)
                or not self.target or not self.win.u.IsWindow(self.target)
                or self.win.u.GetParent(self.hwnd) != self.target
                or (not self.test_host and not self.win.is_codex(self.target))):
            self.find_target()
        if self.hwnd and self.win.u.IsWindow(self.hwnd):
            # Health/DPI reconciliation only; position following is not timer-driven.
            self.layout()
            if time.time() >= self.next_refresh and not self.busy and not self.win.u.IsIconic(self.target):
                self.refresh()
        self.update_visual()
        self.write_status()

    def write_status(self):
        valid_window = bool(self.hwnd and self.win.u.IsWindow(self.hwnd))
        r = W.RECT()
        if valid_window:
            self.win.u.GetWindowRect(self.hwnd,C.byref(r))
        status = {'mode':'owned-popup' if self.owned_popup else 'native-child','pid':os.getpid(),'hwnd':self.hwnd,'target':self.target,
                  'actualParent':self.win.u.GetParent(self.hwnd) if valid_window else None,
                  'childStyle':bool(self.win.u.GetWindowLongPtrW(self.hwnd,-16)&0x40000000) if valid_window else False,
                  'topmost':bool(self.win.u.GetWindowLongPtrW(self.hwnd,-20)&8) if valid_window else False,
                  'visible':bool(valid_window and self.win.u.IsWindowVisible(self.hwnd) and not self.win.u.IsIconic(self.target)),
                  'geometry':self.geometry,'screenRect':[r.left,r.top,r.right,r.bottom],
                  'quota':self.data,'readError':self.error,'layoutEvents':self.layout_events,
                  'background':'transparent' if getattr(self,'per_pixel_alpha',False) else f'#{self.bg&255:02x}{self.bg>>8&255:02x}{self.bg>>16&255:02x}',
                  'refreshSeconds':REFRESH_SECONDS,'tooltip':self.tooltip,'hoverTracking':'explicit',
                  'tooltipVisible':bool(self.tooltip and self.win.u.IsWindowVisible(self.tooltip)),
                  'tooltipStyle':{'renderer':'custom-layered-card','cornerRadiusDip':10,'opacity':249/255,'background':'#fcfbfa'}}
        if status != self.last_status:
            if write_runtime_json(self.status_file,status):self.last_status=status

    def window_proc(self, hwnd, msg, wp, lp):
        try:
            if msg == 0xF:
                ps = PAINTSTRUCT()
                dc = self.win.u.BeginPaint(hwnd,C.byref(ps))
                try:
                    if hwnd == self.hwnd:
                        self.paint(hwnd,dc)
                finally:
                    self.win.u.EndPaint(hwnd,C.byref(ps))
                return 0
            if msg == 0x14:
                return 1
            if msg == 0x21:  # MA_NOACTIVATE: never steals keyboard focus from Codex.
                return 3
            if msg == 0x202:
                self.refresh()
                return 0
            if msg == 0x7B:
                self.context_menu(hwnd,lp)
                return 0
            if msg == 0x113:
                if wp == 2:
                    self.poll_hover()
                else:
                    self.heartbeat()
                return 0
            if msg == 0x8001:
                data,error = self.results.get_nowait()
                if data:
                    self.data = data
                    self.error = False
                    self.refresh_failures = 0
                else:
                    self.refresh_failures += 1
                    # Keep the last valid value through one transient CLI or
                    # process-start failure. A quick retry distinguishes a
                    # momentary wake-up race from a real unavailable source.
                    self.error = self.refresh_failures >= 2 or not self.data
                    if not self.error:self.next_refresh=time.time()+5
                self.busy = False
                self.update_visual()
                self.write_status()
                return 0
            if msg == 0x10:
                self.close()
                return 0
            if msg == 0x318:  # WM_PRINTCLIENT supports renderer verification.
                self.paint(hwnd,wp)
                return 0
        except Exception:
            self.log_error()
        return self.win.u.DefWindowProcW(hwnd,msg,wp,lp)

    def log_error(self):
        now=time.monotonic()
        if now<getattr(self,'next_error_log',0):return
        self.next_error_log=now+30
        import traceback
        try:(ROOT/'native-error.log').write_text(traceback.format_exc()[-32768:],encoding='utf-8')
        except OSError:pass

    def close(self):
        if self.closing:return
        self.closing = True
        for hook in (self.location_hook,self.foreground_hook):
            if hook:
                self.win.u.UnhookWinEvent(hook)
        if self.tooltip:
            self.win.u.DestroyWindow(self.tooltip)
        if self.tooltip_font:
            self.win.g.DeleteObject(self.tooltip_font)
        if self.hwnd and self.win.u.IsWindow(self.hwnd):
            self.win.u.DestroyWindow(self.hwnd)
        self.win.u.KillTimer(self.controller,1)
        self.win.u.KillTimer(self.controller,2)
        self.win.u.DestroyWindow(self.controller)
        self.win.u.PostQuitMessage(0)

    def run(self):
        msg = W.MSG()
        while self.win.u.GetMessageW(C.byref(msg),None,0,0) > 0:
            self.win.u.TranslateMessage(C.byref(msg))
            self.win.u.DispatchMessageW(C.byref(msg))


if __name__ == '__main__':
    try:
        host = int(sys.argv[2]) if len(sys.argv)>2 and sys.argv[1]=='--test-host' else None
        Badge(host).run()
    except Exception:
        import traceback
        (ROOT/'native-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        raise
