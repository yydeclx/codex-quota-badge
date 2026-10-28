"""Bounded click diagnostics and PID-scoped native window activation."""
import ctypes as C
from ctypes import wintypes as W
import json,time
from pathlib import Path
LOG=Path(__file__).with_name('interaction.log')
def trace(event,**fields):
    try:
        if LOG.exists() and LOG.stat().st_size>131072:LOG.replace(LOG.with_suffix('.previous.log'))
        with LOG.open('a',encoding='utf-8') as f:f.write(json.dumps({'at':time.time(),'event':event,**fields})+'\n')
    except OSError:pass

def wake(win,pid):
    from native_badge import ENUMPROC
    found=[]
    @ENUMPROC
    def visit(hwnd,_):
        owner=W.DWORD();win.u.GetWindowThreadProcessId(hwnd,C.byref(owner))
        if owner.value==pid:
            name=C.create_unicode_buffer(128)
            win.u.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
            win.u.GetClassNameW(hwnd,name,128)
            if name.value=='TkTopLevel':found.append(hwnd)
        return True
    win.u.EnumWindows(visit,0)
    if not found:return False
    hwnd=found[0];win.u.ShowWindow(hwnd,9)
    win.u.SetForegroundWindow.argtypes=[W.HWND];win.u.SetForegroundWindow(hwnd)
    trace('details_wake',pid=pid,hwnd=hwnd)
    return True
