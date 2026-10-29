"""Off-screen Win32 integration: only windows created by this test are touched."""
import ctypes as C
from ctypes import wintypes as W
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch


@unittest.skipUnless(os.name=='nt','Windows owned popup integration')
class NativePopupTests(unittest.TestCase):
    def test_owned_surface_layout_input_and_host_lifecycle(self):
        from native_badge import Badge,Win,WNDCLASS,WNDPROC,WINEVENTPROC
        from sidebar_compat import CompatibleSidebar
        from sidebar_transparency import present
        w=Win()
        # No network, timers, account readers, foreground changes or live files.
        b=CompatibleSidebar.__new__(CompatibleSidebar);b.win=w;b.test_host=True
        b.target=None;b.hwnd=None;b.tooltip=None;b.geometry=None;b.location_hook=None
        b.layout_events=0;b.closing=False;b.config={'style':'01','quotaStyle':'01',
            'bottomOffset':100,'horizontalOffset':13,'activityEnabled':False,'items':[
                {'id':'token','type':'opencodex_today','provider':'test','label':'SKG','gapAfter':0},
                {'id':'quota','type':'weekly_quota','label':'周额度','gapAfter':0}]}
        b.compact_rows=SimpleNamespace(hidden=set());b.bg=0xf0f2f3
        b.data=None;b.error=False;b.usage_error=None;b.usage={'providers':{'test':{'totalTokens':81000000}}}
        b.reset_signals=SimpleNamespace(view=lambda:{})
        b.quota_history=SimpleNamespace(view=lambda:{})
        b.hover_item='quota'
        b.indicators=lambda provider:(False,False);b.chain_health=lambda provider:(None,None)
        b.hover_tile=None;b.hover_since=None;b.tooltip_active=False
        b.request_repaint=MagicMock()
        errors=[];b.log_error=lambda:errors.append('native callback failed')
        hook_events=[]
        def on_event(*args):
            hook_events.append((args[1],args[2]))
            b.window_event(*args)
        b.proc=WNDPROC(b.window_proc);b.event_proc=WINEVENTPROC(on_event)
        w.u.PeekMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT]
        w.u.PeekMessageW.restype=W.BOOL
        def pump_until(predicate):
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                message=W.MSG()
                while w.u.PeekMessageW(C.byref(message),None,0,0,1):
                    w.u.TranslateMessage(C.byref(message));w.u.DispatchMessageW(C.byref(message))
                if predicate():return
                time.sleep(.01)
            self.fail('Native WinEvent state did not settle: '+str(hook_events[-12:]))
        wc=WNDCLASS(8,b.proc,0,0,w.instance,None,None,None,None,'CodexQuotaNativeV2')
        self.assertTrue(w.u.RegisterClassW(C.byref(wc)))
        temp=tempfile.TemporaryDirectory(prefix='quota-native-popup-')
        ready=Path(temp.name)/'host.txt'
        # The production WinEvent hook skips its own process. A separate inert
        # host is needed to verify the real event chain, rather than call layout.
        host_code="""
import ctypes as C,sys,time
from ctypes import wintypes as W
from pathlib import Path
from native_badge import Win
w=Win();h=w.u.CreateWindowExW(0x08000080,'STATIC','Quota isolated test host',
    0x90000000,-20000,-20000,900,1600,None,None,w.instance,None)
if not h:raise C.WinError(C.get_last_error())
Path(sys.argv[1]).write_text(str(h))
w.u.PeekMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT]
deadline=time.monotonic()+55
try:
    while time.monotonic()<deadline and w.u.IsWindow(h):
        msg=W.MSG()
        while w.u.PeekMessageW(C.byref(msg),None,0,0,1):
            w.u.TranslateMessage(C.byref(msg));w.u.DispatchMessageW(C.byref(msg))
        time.sleep(.01)
finally:
    if w.u.IsWindow(h):w.u.DestroyWindow(h)
"""
        process=subprocess.Popen([sys.executable,'-c',host_code,str(ready)],
            cwd=str(Path(__file__).resolve().parent),stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        host=None
        try:
            deadline=time.monotonic()+8
            while not ready.is_file() and time.monotonic()<deadline:
                self.assertIsNone(process.poll());time.sleep(.01)
            self.assertTrue(ready.is_file());host=int(ready.read_text())
            b.attach(host);self.assertTrue(b.hwnd)
            self.assertEqual(w.u.GetParent(b.hwnd),host)
            self.assertFalse(w.u.GetWindowLongPtrW(b.hwnd,-16)&0x40000000)
            self.assertTrue(w.u.GetWindowLongPtrW(b.hwnd,-16)&0x80000000)
            self.assertFalse(w.u.GetWindowLongPtrW(b.hwnd,-20)&8)
            rect=W.RECT();self.assertTrue(w.u.GetClientRect(b.hwnd,C.byref(rect)))
            self.assertEqual(rect.right,round(40*b.scale))
            present(b)
            self.assertEqual(b.alpha_surface.size,(rect.right,rect.bottom))
            # The real style renderer has visible ink inside both added gutters.
            ink=b.alpha_surface.getchannel('a').point(lambda p:255 if p>32 else 0).getbbox()
            self.assertIsNotNone(ink);self.assertGreater(ink[0],0)
            self.assertLess(ink[2],rect.right)
            self.assertEqual(w.u.SendMessageW(b.hwnd,0x21,0,0),3)
            # Keep the physical pointer/focus untouched; feed only this isolated
            # object's pointer/foreground inputs. The window root check, hover
            # threshold, production card renderer/upload/show and clicks are real.
            def cursor(out):
                out._obj.x=b.geometry[0]+round(10*b.scale)
                out._obj.y=b.geometry[1]+round(45*b.scale)
                return True
            with patch.object(w.u,'GetCursorPos',side_effect=cursor), \
                 patch.object(w.u,'GetForegroundWindow',return_value=host), \
                 patch.object(w.u,'WindowFromPoint',return_value=b.hwnd):
                Badge.poll_hover(b);b.hover_since=time.monotonic()-.5;Badge.poll_hover(b)
                self.assertTrue(b.tooltip_active);self.assertTrue(w.u.IsWindowVisible(b.tooltip))
                self.assertGreater(b.tooltip_surface.width,rect.right)
                b.track_hover(False)
                with patch('interaction_runtime.trace'):
                    w.u.SendMessageW(b.hwnd,0x202,0,0)
                self.assertTrue(b.tooltip_active);self.assertTrue(w.u.IsWindowVisible(b.tooltip))
                b.hover_actions=[((0,0,20,20),('thread','isolated-test-thread'))]
                with patch('sidebar_details.open_thread') as opened:
                    coordinate=round(10*b.scale)
                    w.u.SendMessageW(b.tooltip,0x202,0,coordinate|(coordinate<<16))
                    opened.assert_called_once_with('isolated-test-thread')
                self.assertFalse(b.tooltip_active)
            before=b.geometry;hook_events.clear()
            self.assertTrue(w.u.SetWindowPos(host,None,-19800,-19900,900,1600,0x14))
            pump_until(lambda:b.geometry[:2]==(before[0]+200,before[1]+100))
            self.assertIn((0x800B,host),hook_events)
            events=b.layout_events;b.layout();self.assertEqual(b.layout_events,events)
            hook_events.clear();w.u.ShowWindow(host,0)
            pump_until(lambda:(0x8003,host) in hook_events and not w.u.IsWindowVisible(b.hwnd))
            hook_events.clear();w.u.ShowWindow(host,4)
            pump_until(lambda:(0x8002,host) in hook_events and w.u.IsWindowVisible(b.hwnd))
            w.u.ShowWindow(host,7)
            pump_until(lambda:w.u.IsIconic(host) and not w.u.IsWindowVisible(b.hwnd))
            w.u.ShowWindow(host,4)
            pump_until(lambda:not w.u.IsIconic(host) and w.u.IsWindowVisible(b.hwnd))
            b.compact_rows.hidden={'token','quota'};b.layout()
            self.assertFalse(w.u.IsWindowVisible(b.hwnd))
            b.compact_rows.hidden.clear();b.layout();self.assertTrue(w.u.IsWindowVisible(b.hwnd))
            self.assertEqual(errors,[])
        finally:
            b.closing=True
            if b.location_hook:w.u.UnhookWinEvent(b.location_hook)
            if b.tooltip:w.u.DestroyWindow(b.tooltip)
            if b.hwnd:w.u.DestroyWindow(b.hwnd)
            if host:w.u.PostMessageW(host,0x10,0,0)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)
            temp.cleanup()
            w.u.UnregisterClassW.argtypes=[W.LPCWSTR,W.HINSTANCE]
            w.u.UnregisterClassW(wc.lpszClassName,w.instance)


if __name__=='__main__':unittest.main()
