"""Opt-in owned popup for Windows hosts that cannot display the alpha child.

Uses the same content, DPI layout and message loop as the default sidebar.
No global topmost window, extra timer or polling worker is introduced.
"""
import ctypes as C
from ctypes import wintypes as W

from sidebar_plugin import SidebarPlugin


class CompatibleSidebar(SidebarPlugin):
    owned_popup = True

    def layout(self):
        if not self.hwnd or not self.win.u.IsWindow(self.hwnd):
            return
        if (not self.target or not self.win.u.IsWindow(self.target)
                or self.win.u.IsIconic(self.target)
                or not self.win.u.IsWindowVisible(self.target) or not self.items):
            self.win.u.ShowWindow(self.hwnd,0)
            self.track_hover(False)
            return
        rect=W.RECT()
        if not self.win.u.GetClientRect(self.target,C.byref(rect)):
            return
        self.scale=(self.win.u.GetDpiForWindow(self.target) or 96)/96
        x,y,width,height=self.client_geometry(rect,self.scale)
        origin=W.POINT(x,y)
        if not self.win.u.ClientToScreen(self.target,C.byref(origin)):
            return
        geometry=(origin.x,origin.y,width,height)
        if geometry!=self.geometry or not self.win.u.IsWindowVisible(self.hwnd):
            # SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOZORDER. The owner keeps
            # the popup above itself; never raise it above unrelated apps.
            if not self.win.u.SetWindowPos(self.hwnd,None,*geometry,0x10|0x40|0x4):
                return
            self.geometry=geometry
            self.layout_events+=1
            self.request_repaint()

    def ensure_child_front(self):
        # GetParent(popup) returns its owner, but it is not a sibling child.
        # The base repair would otherwise reorder unrelated top-level windows.
        return

    def window_event(self,hook,event,hwnd,obj,child,thread,stamp):
        super().window_event(hook,event,hwnd,obj,child,thread,stamp)
        if (not self.closing and hwnd==self.target and obj==0 and child==0
                and event in (0x8002,0x8003)):  # EVENT_OBJECT_SHOW / HIDE
            self.layout()


if __name__=='__main__':
    try:
        CompatibleSidebar().run()
    except Exception:
        import traceback
        from sidebar_data import CONFIG
        CONFIG.with_name('sidebar-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        raise
