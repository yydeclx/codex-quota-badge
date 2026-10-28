"""Fault injection for bounded UI work; no live configuration or service changes."""
import json
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from native_badge import Badge,write_runtime_json
from sidebar_modules import Modules,module_value
from sidebar_plugin import SidebarPlugin
from sidebar_details import Details
from PIL import Image


class ResourceGuardTests(unittest.TestCase):
    def test_hidden_hover_card_does_no_raster_work(self):
        sidebar=self.make_sidebar();sidebar.tooltip_active=False
        sidebar.selected_item=MagicMock(side_effect=AssertionError('Hidden card was read'))
        sidebar.update_tooltip();sidebar.selected_item.assert_not_called()

    def test_hover_entry_forces_first_render_before_showing(self):
        badge=Badge.__new__(Badge);badge.hover_since=0;badge.tooltip_active=False
        badge.tooltip=2;badge.win=MagicMock();badge.update_tooltip=MagicMock();badge.position_tooltip=MagicMock()
        badge.track_hover(True,.3)
        badge.update_tooltip.assert_called_once_with(force=True)
        self.assertTrue(badge.tooltip_active)

    def test_visible_quota_sync_age_only_repaints_at_display_resolution(self):
        sidebar=self.make_sidebar();sidebar.tooltip_active=True;sidebar.scale=1
        sidebar.config={'items':[{'id':'quota','type':'weekly_quota'}]}
        sidebar.compact_rows=SimpleNamespace(hidden=set());sidebar.hover_item='quota'
        sidebar.quota_history=MagicMock();sidebar.quota_history.view.return_value={}
        sidebar.valid=MagicMock(return_value=False);sidebar.data=None
        sidebar.reset_signals=MagicMock();sidebar.last_tooltip_text=None;sidebar.position_tooltip=MagicMock()
        sidebar.reset_signals.view.side_effect=[{'lastSyncAt':100,'syncAgeSeconds':age} for age in (1,2,60)]
        with patch('hover_panel.render_quota_chart',return_value=(Image.new('RGBA',(10,10)),[])) as draw,patch('sidebar_plugin.upload'):
            sidebar.update_tooltip();sidebar.update_tooltip();self.assertEqual(draw.call_count,1)
            sidebar.update_tooltip();self.assertEqual(draw.call_count,2)

    def test_minimized_details_does_not_read_or_rebuild(self):
        app=Details.__new__(Details);app.closed=False;app.refreshing=False;app.timer='old'
        app.root=MagicMock();app.root.state.return_value='iconic';app._refresh=MagicMock()
        app.refresh();app._refresh.assert_not_called()
        app.root.after_cancel.assert_called_once_with('old')
        app.root.after.assert_called_once_with(10000,app.refresh)
        app.root.state.return_value='normal';app.refresh();app._refresh.assert_called_once()

    def test_runtime_write_lock_keeps_previous_and_never_sleeps(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json';path.write_text('{"old":true}',encoding='utf-8')
            with patch.object(Path,'replace',side_effect=PermissionError('locked')) as replace,patch('time.sleep') as sleep:
                self.assertFalse(write_runtime_json(path,{'new':True}))
                replace.assert_called_once();sleep.assert_not_called()
            self.assertEqual(json.loads(path.read_text()),{'old':True})
            self.assertEqual(list(Path(directory).glob('*.tmp')),[])
            self.assertTrue(write_runtime_json(path,{'new':True}))
            self.assertEqual(json.loads(path.read_text()),{'new':True})

    def test_error_logging_is_rate_limited_and_cannot_raise(self):
        badge=Badge.__new__(Badge)
        with patch('native_badge.time.monotonic',return_value=100),patch.object(Path,'write_text',side_effect=PermissionError()) as write:
            for _ in range(100):badge.log_error()
            write.assert_called_once()

    def test_quota_worker_start_failure_releases_busy_for_retry(self):
        badge=Badge.__new__(Badge);badge.busy=False;badge.test_host=None
        badge.data={'remaining':50};badge.error=False;badge.results=queue.Queue()
        badge.log_error=MagicMock();badge.win=MagicMock()
        with patch('threading.Thread.start',side_effect=RuntimeError('unavailable')),patch('native_badge.time.time',return_value=100):badge.refresh()
        self.assertFalse(badge.busy);self.assertFalse(badge.error)
        self.assertEqual(badge.next_refresh,105)

    def test_unreadable_task_database_remains_unknown_and_services_continue(self):
        modules=Modules();modules.busy=True
        with patch('sidebar_modules.read_tasks',side_effect=OSError('unavailable')),patch('sidebar_modules.service_status',return_value={'state':'online'}):
            modules._refresh({'services':[{'url':'http://localhost:1'}]})
        self.assertIsNotNone(modules.view()['taskError'])
        self.assertEqual(module_value('tasks_running',modules.view(),{}),'—')
        self.assertEqual(modules.view()['services'][0]['state'],'online')
        self.assertFalse(modules.busy)

    def test_primary_task_reader_failure_can_recover_via_fallback(self):
        modules=Modules()
        with patch('sidebar_modules.read_tasks',side_effect=[OSError(),{'tasks_running':[{'id':'one'}]}]):modules._refresh({})
        self.assertIsNone(modules.view()['taskError'])
        self.assertEqual(modules.view()['tasks']['tasks_running'][0]['id'],'one')

    def make_sidebar(self):
        sidebar=SidebarPlugin.__new__(SidebarPlugin);sidebar.win=MagicMock()
        sidebar.controller=3;sidebar.hwnd=4;sidebar.target=1;sidebar.tooltip=2
        sidebar.test_host=None;sidebar.track_hover=MagicMock();sidebar.set_hover_tile=MagicMock()
        sidebar.modules=MagicMock();sidebar.telemetry=MagicMock();sidebar.closing=False
        return sidebar

    def test_hover_timer_only_changes_when_cadence_changes(self):
        sidebar=self.make_sidebar()
        for rate in (150,150,75,75):sidebar.set_hover_cadence(rate)
        self.assertEqual(sidebar.win.u.SetTimer.call_count,2)

    def test_companion_focus_disables_animation_without_stopping_data(self):
        sidebar=self.make_sidebar();sidebar.win.u.GetForegroundWindow.return_value=1
        sidebar.win.u.IsWindow.return_value=True;sidebar.win.u.IsIconic.return_value=False
        with patch('ui_presence.is_companion',return_value=False):self.assertTrue(sidebar.sync_power_mode())
        sidebar.win.u.GetForegroundWindow.return_value=99
        with patch('ui_presence.is_companion',return_value=True):self.assertTrue(sidebar.sync_power_mode())
        self.assertTrue(sidebar.foreground_mode);self.assertFalse(sidebar.interaction_mode)
        sidebar.win.u.KillTimer.assert_called_with(3,2)

    def test_closed_or_minimized_details_reduces_snapshot_writes(self):
        sidebar=self.make_sidebar();sidebar.win.u.FindWindowW.return_value=None
        self.assertEqual(sidebar.detail_snapshot_interval(),10)
        sidebar.win.u.FindWindowW.return_value=9;sidebar.win.u.IsIconic.return_value=True
        self.assertEqual(sidebar.detail_snapshot_interval(),10)
        sidebar.win.u.IsIconic.return_value=False
        with patch('ui_presence.is_companion',return_value=True):self.assertEqual(sidebar.detail_snapshot_interval(),2)

    def test_opening_details_expires_the_idle_snapshot_deadline(self):
        sidebar=self.make_sidebar();sidebar.next_snapshot=999;sidebar.scale=1
        sidebar.config={'items':[{'id':'test','type':'opencodex_today','provider':'SKG'}]}
        sidebar.compact_rows=SimpleNamespace(hidden=set());sidebar.index_at=MagicMock(return_value=0)
        sidebar.publish_details=MagicMock();sidebar.details_process=MagicMock()
        sidebar.details_process.poll.return_value=None
        with patch('sidebar_modules.atomic_json'),patch('interaction_runtime.wake'),patch('sidebar_plugin.time.time',return_value=100):sidebar.open_details()
        self.assertEqual(sidebar.next_snapshot,102)

    def test_repeated_render_failure_backs_off_and_blocks_animation_retries(self):
        sidebar=self.make_sidebar();sidebar.log_error=MagicMock()
        with patch('sidebar_transparency.present',side_effect=RuntimeError('GDI unavailable')) as draw,patch('sidebar_plugin.time.monotonic',return_value=100):
            sidebar.window_proc(3,0x8007,0,0)
            for _ in range(100):sidebar.request_repaint()
            self.assertEqual(draw.call_count,1)
            sidebar.win.u.PostMessageW.assert_not_called()
            self.assertEqual(sidebar.alpha_retry_due,100.5)
        with patch('sidebar_transparency.present',side_effect=RuntimeError()),patch('sidebar_plugin.time.monotonic',return_value=101):
            sidebar.window_proc(3,0x8007,0,0)
            self.assertEqual(sidebar.alpha_retry_due,102)
        with patch('sidebar_transparency.present'),patch('sidebar_plugin.time.monotonic',return_value=103):
            sidebar.window_proc(3,0x8007,0,0)
            self.assertEqual(sidebar.alpha_failures,0)


if __name__=='__main__':unittest.main()
