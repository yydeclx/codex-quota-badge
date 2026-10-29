"""Layout and input regressions; no desktop window or account access."""
import ctypes as C
from ctypes import wintypes as W
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from native_badge import Badge
from sidebar_plugin import SidebarPlugin
from sidebar_compat import CompatibleSidebar


class PopupLayoutTests(unittest.TestCase):
    def make(self,dpi=192,offset=13,kind=CompatibleSidebar):
        b=kind.__new__(kind)
        b.win=SimpleNamespace(u=MagicMock());u=b.win.u
        b.target=11;b.hwnd=22;b.tooltip=33;b.geometry=None;b.layout_events=0
        b.config={'horizontalOffset':offset,'bottomOffset':100,'quotaStyle':'01',
                  'items':[{'id':str(i),'type':'opencodex_today','gapAfter':0} for i in range(3)]}
        b.compact_rows=SimpleNamespace(hidden=set())
        b.request_repaint=MagicMock();b.track_hover=MagicMock()
        b.origin=(284,216);b.visible=True;b.closing=False;b.test_host=True
        u.IsWindow.return_value=True;u.IsIconic.return_value=False
        u.IsWindowVisible.side_effect=lambda h:True if h==b.target else b.visible
        u.GetDpiForWindow.return_value=dpi
        def client(h,out):
            r=C.cast(out,C.POINTER(W.RECT)).contents
            r.right=2560;r.bottom=1590;return True
        def screen(h,out):
            p=C.cast(out,C.POINTER(W.POINT)).contents
            p.x+=b.origin[0];p.y+=b.origin[1];return True
        def position(*args):b.visible=True;return True
        def show(h,mode):
            if h==b.hwnd:b.visible=bool(mode)
            return True
        u.GetClientRect.side_effect=client;u.ClientToScreen.side_effect=screen
        u.SetWindowPos.side_effect=position;u.ShowWindow.side_effect=show
        return b

    def test_dpi_offset_matrix_and_matching_child_surface(self):
        for dpi in (96,144,192):
            for offset in (0,13,-17):
                with self.subTest(dpi=dpi,offset=offset):
                    b=self.make(dpi,offset);b.layout();s=dpi/96
                    expected=(284+round((6+offset)*s)-round(3*s),
                              216+1590-round(100*s)-round(102*s),round(40*s),round(102*s))
                    self.assertEqual(b.geometry,expected)
                    self.assertEqual(b.layout_events,1);b.request_repaint.assert_called_once()
                    self.assertEqual(b.win.u.SetWindowPos.call_args.args[-1],0x54)
                    child=self.make(dpi,offset,SidebarPlugin);child.layout()
                    self.assertEqual(child.geometry,(expected[0]-284,expected[1]-216,*expected[2:]))
                    child.win.u.ClientToScreen.assert_not_called()
        b=self.make();b.layout();self.assertEqual(b.geometry,(316,1402,80,204))
        b.config['horizontalOffset']=14;b.layout();self.assertEqual(b.geometry[0],318)

    def test_geometry_cached_only_after_success_and_retry_has_no_extra_timer(self):
        for failure in ('GetClientRect','ClientToScreen','SetWindowPos'):
            with self.subTest(failure=failure):
                b=self.make();u=b.win.u;operation=getattr(u,failure)
                original=operation.side_effect;operation.side_effect=lambda *a:False
                b.layout();self.assertIsNone(b.geometry);self.assertEqual(b.layout_events,0)
                b.request_repaint.assert_not_called()
                if failure!='SetWindowPos':u.SetWindowPos.assert_not_called()
                operation.side_effect=original;b.layout()
                self.assertEqual(b.geometry,(316,1402,80,204));self.assertEqual(b.layout_events,1)
                u.SetWindowPos.reset_mock();b.request_repaint.reset_mock()
                b.layout();u.SetWindowPos.assert_not_called();b.request_repaint.assert_not_called()
                u.SetTimer.assert_not_called()

    def test_failed_move_retains_last_success_then_retries(self):
        b=self.make();b.layout();before=b.geometry
        b.config['horizontalOffset']=-5;b.request_repaint.reset_mock()
        u=b.win.u;original=u.SetWindowPos.side_effect;u.SetWindowPos.side_effect=lambda *a:False
        b.layout();self.assertEqual(b.geometry,before);self.assertEqual(b.layout_events,1)
        b.request_repaint.assert_not_called()
        u.SetWindowPos.side_effect=original;b.layout()
        self.assertNotEqual(b.geometry,before);self.assertEqual(b.layout_events,2)

    def test_child_position_failure_also_retries(self):
        b=self.make(kind=SidebarPlugin);u=b.win.u
        original=u.SetWindowPos.side_effect;u.SetWindowPos.side_effect=lambda *a:False
        b.layout();self.assertIsNone(b.geometry);self.assertEqual(b.layout_events,0)
        b.request_repaint.assert_not_called();u.SetWindowPos.side_effect=original
        b.layout();self.assertEqual(b.layout_events,1)

    def test_host_movement_uses_screen_origin_including_negative_monitor(self):
        b=self.make();b.layout();b.origin=(-1600,-300)
        b.window_event(None,0x800B,b.target,0,0,0,0)
        self.assertEqual(b.geometry,(-1568,886,80,204))
        self.assertEqual(b.layout_events,2)

    def test_minimize_hidden_and_empty_restore_without_geometry_change(self):
        for condition in ('minimized','hidden','empty'):
            with self.subTest(condition=condition):
                b=self.make();b.layout();before=b.geometry;u=b.win.u
                if condition=='minimized':u.IsIconic.return_value=True
                if condition=='hidden':u.IsWindowVisible.side_effect=lambda h:False
                if condition=='empty':b.compact_rows.hidden={'0','1','2'}
                u.SetWindowPos.reset_mock();b.request_repaint.reset_mock()
                b.layout();self.assertFalse(b.visible);self.assertEqual(b.geometry,before)
                self.assertEqual(b.layout_events,1);u.SetWindowPos.assert_not_called()
                b.request_repaint.assert_not_called();b.track_hover.assert_called_with(False)
                u.IsIconic.return_value=False
                u.IsWindowVisible.side_effect=lambda h:True if h==b.target else b.visible
                b.compact_rows.hidden.clear();b.layout()
                self.assertTrue(b.visible);self.assertEqual(b.geometry,before)
                self.assertEqual(b.layout_events,2)

    def test_dynamic_rows_quota_style_and_negative_spacing_preserved(self):
        b=self.make();b.config['items'][1]['type']='weekly_quota'
        for style in ('01','02','17'):
            b.config['quotaStyle']=style;b.config['items'][0]['gapAfter']=-2
            b.layout();expected=round((b.row_top(2)+b.item_height(b.items[-1]))*2)
            self.assertEqual(b.geometry[3],expected)
            self.assertEqual(b.geometry[2],80)

    def test_popup_never_runs_child_z_order_repair(self):
        b=self.make();b.ensure_child_front();b.win.u.SetWindowPos.assert_not_called()


class PopupInputTests(unittest.TestCase):
    def test_attached_style_is_opt_in_and_owner_is_preserved(self):
        for kind,popup in ((SidebarPlugin,False),(CompatibleSidebar,True)):
            b=kind.__new__(kind);b.win=MagicMock();b.test_host=True
            b.target=None;b.hwnd=None;b.tooltip=None;b.location_hook=None
            b.event_proc=MagicMock()
            b.layout=MagicMock();b.create_tooltip=MagicMock()
            b.win.u.CreateWindowExW.return_value=22
            b.win.u.GetWindowDpiAwarenessContext.return_value=-4
            b.win.u.SetThreadDpiAwarenessContext.return_value=-4
            Badge.attach(b,11)
            args=b.win.u.CreateWindowExW.call_args.args
            self.assertEqual(args[3]&0x80000000!=0,popup)
            self.assertEqual(args[3]&0x40000000!=0,not popup)
            self.assertEqual(args[8],11)
            self.assertFalse(args[0]&8,'No global topmost mode')
            self.assertTrue(args[0]&0x08000000,'Popup must not activate')
            b.win.u.SetThreadDpiAwarenessContext.assert_called_with(-4)

    def test_hover_accepts_owner_or_badge_root_but_not_another_window(self):
        for root,expected in ((11,True),(22,True),(99,False)):
            b=CompatibleSidebar.__new__(CompatibleSidebar);b.win=MagicMock()
            b.target=11;b.hwnd=22;b.tooltip=33;b.track_hover=MagicMock()
            b.win.u.IsIconic.return_value=False;b.win.u.GetForegroundWindow.return_value=11
            def cursor(out):out._obj.x=320;out._obj.y=1420;return True
            def bounds(h,out):
                r=out._obj;r.left=316;r.top=1402;r.right=396;r.bottom=1606;return True
            b.win.u.GetCursorPos.side_effect=cursor;b.win.u.GetWindowRect.side_effect=bounds
            b.win.u.GetAncestor.return_value=root
            Badge.poll_hover(b);b.track_hover.assert_called_once_with(expected)

    def test_click_keeps_existing_card_action(self):
        b=CompatibleSidebar.__new__(CompatibleSidebar);b.hwnd=22;b.tooltip=33
        b.show_item_card=MagicMock();b.log_error=MagicMock()
        with patch('interaction_runtime.trace'):
            self.assertEqual(b.window_proc(22,0x202,0,0),0)
        b.show_item_card.assert_called_once();b.log_error.assert_not_called()


if __name__=='__main__':unittest.main()
