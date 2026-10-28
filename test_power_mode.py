import unittest
import datetime as dt
import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock,patch
from sidebar_plugin import SidebarPlugin
from native_badge import Badge

class PowerTests(unittest.TestCase):
    def make(self):
        p=SidebarPlugin.__new__(SidebarPlugin)
        p.win=MagicMock();p.test_host=None;p.target=11;p.tooltip=22;p.controller=33
        p.win.u.IsWindow.return_value=True;p.win.u.IsIconic.return_value=False
        p.modules=MagicMock();p.telemetry=MagicMock();p.activity=MagicMock()
        p.track_hover=MagicMock()
        return p
    def test_background_pauses_and_foreground_wakes(self):
        p=self.make();p.win.u.GetForegroundWindow.return_value=99
        self.assertFalse(p.sync_power_mode())
        p.win.u.KillTimer.assert_called_with(33,2)
        p.win.u.SetTimer.assert_called_with(33,1,10000,None)
        p.win.u.GetForegroundWindow.return_value=11
        self.assertTrue(p.sync_power_mode())
        p.win.u.SetTimer.assert_any_call(33,2,150,None)
        p.win.u.PostMessageW.assert_called_with(33,0x113,1,0)
        self.assertEqual(p.modules.next_poll,0)
        p.win.u.PostMessageW.reset_mock();p.sync_power_mode()
        p.win.u.PostMessageW.assert_not_called()
    def test_background_heartbeat_has_no_polling(self):
        p=self.make();p.win.u.GetForegroundWindow.return_value=99
        p.heartbeat()
        p.modules.poll.assert_not_called();p.telemetry.poll.assert_not_called();p.activity.poll.assert_not_called()
    def test_minimized_pauses_even_if_foreground(self):
        p=self.make();p.win.u.GetForegroundWindow.return_value=11;p.win.u.IsIconic.return_value=True
        self.assertFalse(p.sync_power_mode())
    def test_tooltip_keeps_interaction_active(self):
        p=self.make();p.win.u.GetForegroundWindow.return_value=22
        self.assertTrue(p.sync_power_mode())

    def test_companion_window_keeps_data_polling_active(self):
        p=self.make();p.win.u.GetForegroundWindow.return_value=99
        with patch('ui_presence.is_companion',return_value=True):self.assertTrue(p.sync_power_mode())

    def test_stale_successful_usage_remains_visible_during_wake_refresh(self):
        p=SidebarPlugin.__new__(SidebarPlugin);p.usage_error=None
        p.usage={'date':'2026-10-22','updatedAt':1,'providers':{'OCX':{'totalTokens':12000000}}}
        p.config={'refreshSeconds':60}
        self.assertEqual(p.row_usage({'provider':'OCX'})['totalTokens'],12000000)
        self.assertEqual(p.row_usage({'provider':'openai'})['totalTokens'],0)
        p.usage_error='TimeoutExpired'
        self.assertIsNone(p.row_usage({'provider':'OCX'}))

    def test_stale_successful_quota_remains_displayable_and_restores(self):
        now=dt.datetime.now().timestamp()
        badge=Badge.__new__(Badge);badge.data={'remaining':97,'resetsAt':now+86400,'updatedAt':1};badge.error=False
        self.assertTrue(badge.valid())
        with tempfile.TemporaryDirectory() as folder:
            badge.status_file=Path(folder)/'status.json'
            badge.status_file.write_text(json.dumps({'quota':badge.data,'readError':False}),encoding='utf-8')
            restored,error=badge.restore_last_quota()
            self.assertEqual(restored['remaining'],97);self.assertFalse(error)
            badge.status_file.write_text(json.dumps({'quota':badge.data,'readError':True}),encoding='utf-8')
            _,error=badge.restore_last_quota();self.assertTrue(error)

    def test_single_refresh_failure_keeps_last_valid_quota(self):
        badge=Badge.__new__(Badge)
        badge.data={'remaining':20,'resetsAt':dt.datetime.now().timestamp()+86400,'updatedAt':1}
        badge.error=False;badge.busy=True;badge.refresh_failures=0
        badge.results=__import__('queue').Queue();badge.results.put((None,True))
        badge.update_visual=MagicMock();badge.write_status=MagicMock()
        badge.win=MagicMock();badge.win.u.DefWindowProcW.return_value=0
        Badge.window_proc(badge,1,0x8001,0,0)
        self.assertFalse(badge.error);self.assertEqual(badge.data['remaining'],20)
        self.assertFalse(badge.busy);self.assertGreater(badge.next_refresh,dt.datetime.now().timestamp())

    def test_success_clears_previous_refresh_error(self):
        badge=Badge.__new__(Badge);badge.data=None;badge.error=True;badge.busy=True;badge.refresh_failures=2
        badge.results=__import__('queue').Queue();badge.results.put(({'remaining':20,'resetsAt':dt.datetime.now().timestamp()+86400,'updatedAt':1},False))
        badge.update_visual=MagicMock();badge.write_status=MagicMock()
        badge.win=MagicMock();badge.win.u.DefWindowProcW.return_value=0
        Badge.window_proc(badge,1,0x8001,0,0)
        self.assertFalse(badge.error);self.assertEqual(badge.refresh_failures,0)

if __name__=='__main__':unittest.main()
