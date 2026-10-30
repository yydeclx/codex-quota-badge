import json,queue,tempfile,time,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from reset_signals import parse_forecast,parse_feed,promised_reset_at,ResetSignals,fetch
from sidebar_plugin import SidebarPlugin

class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.network=self.enterContext(patch('reset_signals.urllib.request.urlopen',side_effect=AssertionError('Network forbidden')))

class ResetSignalTests(OfflineTests):
    def test_strict_parsing_and_explicit_signal(self):
        now=time.time();iso=lambda t:time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(t))
        f=parse_forecast({'updated_at':iso(now),'probabilities':{'rounded_24h':32,'rounded_48h':53},'confidence':'low','last_reset_at':iso(now-100)})
        feed=parse_feed({'fetched_at':iso(now),'stale':False,'profile':{'handle':'thsottiaux'},'signal':{'active':True,'kind':'signal','at':iso(now-60),'summary':'Lands tomorrow.','url':'https://x.com/thsottiaux/status/1'}},now)
        self.assertEqual((f['probability24h'],f['probability48h']),(32,53));self.assertEqual(feed['signal']['kind'],'explicit')
    def test_stale_signal_is_not_shown(self):
        now=time.time();iso=lambda t:time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(t))
        self.assertIsNone(parse_feed({'fetched_at':iso(now),'profile':{'handle':'thsottiaux'},'signal':{'active':True,'kind':'signal','at':iso(now-73*3600),'summary':'old','url':'https://x.com/a'}},now)['signal'])
    def test_last_good_cache_survives_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            clock=[1000];good={'updatedAt':1000,'fetchedAt':1000,'probability24h':10,'probability48h':20,'source':'codex-reset.com'}
            calls=[good,RuntimeError()]
            def fetcher():
                value=calls.pop(0)
                if isinstance(value,Exception):raise value
                return value
            state=ResetSignals(Path(folder)/'s.json',fetcher,lambda:clock[0]);state.refresh();clock[0]+=901;state.refresh()
            self.assertEqual(state.view()['probability24h'],10);self.assertEqual(state.error,'RuntimeError');self.assertEqual(state.view()['freshness'],'cached')
    def test_local_increase_confirms_only_near_public_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            now=10000;state=ResetSignals(Path(folder)/'s.json',clock=lambda:now);state.data={'fetchedAt':now,'updatedAt':now,'probability24h':1,'probability48h':2,'lastResetAt':now-10,'source':'codex-reset.com'}
            self.assertFalse(state.observe_account(40,100,now))
            self.assertTrue(state.observe_account(100,100,now+60));self.assertEqual(state.view()['accountConfirmation']['increase'],60)
    def test_freshness_states(self):
        with tempfile.TemporaryDirectory() as folder:
            clock=[1000];state=ResetSignals(Path(folder)/'s.json',clock=lambda:clock[0])
            self.assertEqual(state.view()['freshness'],'unavailable')
            state.data={'updatedAt':1000,'fetchedAt':1000,'probability24h':1,'probability48h':2,'source':'codex-reset.com'}
            self.assertEqual(state.view()['freshness'],'fresh')
            state.error='TimeoutError';self.assertEqual(state.view()['freshness'],'cached')
            clock[0]+=2701;self.assertEqual(state.view()['freshness'],'stale')
    def test_confirmation_is_dismissed_after_post_reset_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            state=ResetSignals(Path(folder)/'s.json',clock=lambda:1000)
            state.data={'updatedAt':1000,'fetchedAt':1000,'probability24h':1,'probability48h':2,'source':'codex-reset.com',
                'accountConfirmation':{'at':900,'from':1,'to':100,'increase':99}}
            self.assertTrue(state.dismiss_confirmation_after_use(97))
            self.assertNotIn('accountConfirmation',state.view())
            self.assertFalse(state.dismiss_confirmation_after_use(97))
    def test_pacific_promise_converts_to_beijing_timestamp(self):
        import datetime as dt
        signal=dt.datetime(2026,10,22,6,55,54,tzinfo=dt.timezone.utc).timestamp()
        target=promised_reset_at('Lands tomorrow before 10am PST.',signal)
        beijing=dt.datetime.fromtimestamp(target,dt.timezone(dt.timedelta(hours=8)))
        self.assertEqual((beijing.month,beijing.day,beijing.hour,beijing.minute),(10,23,2,0))
        self.assertIsNone(promised_reset_at('Reset soon maybe.',signal))
    def test_official_signal_new_schema_uses_server_deadline(self):
        value=parse_forecast({'updated_at':'2026-10-23T05:39:48Z','last_reset_at':'2026-10-22T17:10:01Z','confidence':'high',
            'probabilities':{'rounded_24h':93,'rounded_48h':97},'official_signal':{
                'kind':'signal','summary':'Landing tomorrow before 10am PST.','at':'2026-10-23T04:47:34Z',
                'url':'https://x.com/thsottiaux/status/1','window':{'target_at':'2026-10-23T17:00:00Z'}}})
        self.assertEqual(value['signal']['kind'],'official')
        self.assertEqual(value['signal']['targetAt'],1792774800)


class ResetRefreshTests(OfflineTests):
    def setUp(self):
        super().setUp()
        self.folder=self.enterContext(tempfile.TemporaryDirectory())
        self.clock=[1000]
        self.good={'updatedAt':990,'fetchedAt':1000,'probability24h':10,'probability48h':20,'source':'codex-reset.com'}
        self.fetcher=MagicMock(return_value=self.good)
        self.state=ResetSignals(Path(self.folder)/'s.json',self.fetcher,lambda:self.clock[0])

    def sidebar(self):
        sidebar=SidebarPlugin.__new__(SidebarPlugin)
        sidebar.reset_signals=self.state;sidebar.reset_signal_results=queue.Queue()
        sidebar.win=MagicMock();sidebar.controller=3
        for name in ('request_repaint','update_tooltip','publish_details','log_error'):
            setattr(sidebar,name,MagicMock())
        return sidebar

    def test_failures_back_off_at_exact_due_time_and_remain_capped(self):
        self.fetcher.side_effect=TimeoutError('private transport detail')
        for count,delay in enumerate((60,120,240,480,900,900,900),1):
            started=self.clock[0];self.state.refresh()
            self.assertEqual(self.state.nextPoll,started+delay)
            self.assertEqual(self.state.failures,count)
            self.assertEqual(self.state.error,'TimeoutError')
            self.assertFalse(self.state.busy)
            self.assertIsNone(self.state.view()['lastSyncAt'])
            self.assertFalse(self.state.path.exists())
            self.clock[0]=self.state.nextPoll-.001
            self.assertFalse(self.state.due());self.state.refresh()
            self.assertEqual(self.fetcher.call_count,count)
            self.clock[0]=self.state.nextPoll;self.assertTrue(self.state.due())

    def test_success_clears_failure_state_and_restarts_backoff(self):
        self.fetcher.side_effect=TimeoutError()
        self.state.refresh();self.clock[0]=self.state.nextPoll;self.state.refresh()
        self.clock[0]=self.state.nextPoll;self.fetcher.side_effect=None
        self.state.refresh()
        self.assertIsNone(self.state.error);self.assertEqual(self.state.failures,0)
        self.assertEqual(self.state.nextPoll,self.clock[0]+900)
        self.assertEqual(self.state.view()['freshness'],'fresh')
        self.clock[0]=self.state.nextPoll;self.fetcher.side_effect=RuntimeError()
        self.state.refresh();self.assertEqual(self.state.nextPoll,self.clock[0]+60)

    def test_failures_preserve_last_good_bytes_timestamps_and_stale_age(self):
        self.state.refresh();saved=self.state.path.read_bytes()
        self.clock[0]=self.state.nextPoll;self.fetcher.side_effect=TimeoutError()
        self.state.refresh()
        self.assertEqual(self.state.view()['lastSyncAt'],1000)
        self.assertEqual(self.state.path.read_bytes(),saved)
        self.clock[0]=3701;self.state.refresh()
        self.assertEqual(self.state.view()['syncAgeSeconds'],2701)
        self.assertEqual(self.state.view()['freshness'],'stale')
        self.assertEqual(self.state.path.read_bytes(),saved)
        restored=ResetSignals(self.state.path,clock=lambda:self.clock[0])
        self.assertEqual(restored.data,self.good)
        self.assertEqual(restored.view()['lastSyncAt'],1000)

    def test_schedule_starts_at_completion_instead_of_request_start(self):
        def fetcher():self.clock[0]+=10;return self.good
        self.fetcher.side_effect=fetcher;self.state.refresh()
        self.assertEqual(self.state.nextPoll,1910)
        def failure():self.clock[0]+=10;raise TimeoutError()
        self.clock[0]=1910;self.fetcher.side_effect=failure;self.state.refresh()
        self.assertEqual(self.state.nextPoll,1980)

    def test_write_failure_keeps_last_good_and_uses_failure_schedule(self):
        self.state.refresh();saved=self.state.path.read_bytes()
        self.clock[0]=1900;self.fetcher.return_value=dict(self.good,fetchedAt=1900,probability24h=99)
        with patch('reset_signals._atomic_write',side_effect=PermissionError()):self.state.refresh()
        self.assertEqual(self.state.error,'PermissionError');self.assertEqual(self.state.nextPoll,1960)
        self.assertEqual(self.state.data,self.good);self.assertEqual(self.state.path.read_bytes(),saved)
        self.clock[0]=1960;self.state.refresh()
        self.assertIsNone(self.state.error);self.assertEqual(self.state.failures,0)
        self.assertEqual(self.state.view()['lastSyncAt'],1900)

    def test_partial_public_api_failure_does_not_commit_or_stamp_a_success(self):
        self.state.refresh();saved=self.state.path.read_bytes();self.clock[0]=1900
        self.state.fetcher=fetch
        forecast={'updated_at':'2026-10-23T05:39:48Z','probabilities':{'rounded_24h':90,'rounded_48h':99}}
        with patch('reset_signals._get',side_effect=[forecast,TimeoutError()]) as get:
            self.state.refresh();self.assertEqual(get.call_count,2)
        self.assertEqual(self.state.error,'TimeoutError');self.assertEqual(self.state.nextPoll,1960)
        self.assertEqual(self.state.path.read_bytes(),saved)
        self.assertEqual(self.state.view()['lastSyncAt'],1000)

    def test_reentrant_refresh_cannot_start_a_second_fetch(self):
        def fetcher():
            self.assertTrue(self.state.busy);self.assertFalse(self.state.due())
            self.assertFalse(self.state.begin_refresh());self.state.refresh()
            return self.good
        self.fetcher.side_effect=fetcher;self.state.refresh()
        self.fetcher.assert_called_once();self.assertFalse(self.state.busy)

    def test_confirmation_is_preserved_by_both_result_paths(self):
        confirmation={'at':900,'from':10,'to':100}
        self.state.data=dict(self.good,accountConfirmation=confirmation)
        self.state.refresh();self.assertEqual(self.state.data['accountConfirmation'],confirmation)
        self.assertNotIn('accountConfirmation',self.good)
        self.assertEqual(json.loads(self.state.path.read_text())['accountConfirmation'],confirmation)

    def test_invalid_or_empty_success_is_failure(self):
        for value in (None,{},[],False):
            self.fetcher.return_value=value;self.state.refresh()
            self.assertEqual(self.state.error,'ValueError');self.assertIsNone(self.state.data)
            self.clock[0]=self.state.nextPoll
        self.assertFalse(self.state.path.exists())

    def test_sidebar_and_sync_share_full_backoff_and_recovery(self):
        sidebar=self.sidebar();self.fetcher.side_effect=TimeoutError()
        with patch('sidebar_plugin.threading.Thread') as thread:
            for index,delay in enumerate((60,120,240,480,900,900)):
                if index%2:self.state.refresh()
                else:
                    sidebar.refresh_reset_signals();thread.call_args.kwargs['target']()
                    sidebar.window_proc(3,0x800A,0,0)
                self.assertEqual(self.state.nextPoll,self.clock[0]+delay)
                self.assertEqual(self.state.failures,index+1)
                self.clock[0]=self.state.nextPoll
            self.fetcher.side_effect=None
            sidebar.refresh_reset_signals();thread.call_args.kwargs['target']()
            sidebar.window_proc(3,0x800A,0,0)
        self.assertIsNone(self.state.error);self.assertEqual(self.state.failures,0)
        self.assertEqual(self.state.nextPoll,self.clock[0]+900)
        self.assertEqual(self.state.data,self.good);sidebar.log_error.assert_not_called()
        sidebar.win.u.SetTimer.assert_not_called()

    def test_sidebar_keeps_single_flight_until_result_is_consumed(self):
        sidebar=self.sidebar()
        with patch('sidebar_plugin.threading.Thread') as thread:
            sidebar.refresh_reset_signals();self.clock[0]+=5000
            sidebar.refresh_reset_signals();self.state.refresh()
            self.assertEqual(thread.call_count,1);self.fetcher.assert_not_called()
            # An empty/duplicate native message must not release an active worker.
            sidebar.window_proc(3,0x800A,0,0);self.assertTrue(self.state.busy)
            thread.call_args.kwargs['target']();self.assertTrue(self.state.busy)
            self.state.refresh();self.fetcher.assert_called_once()
            sidebar.window_proc(3,0x800A,0,0)
            deadline=self.state.nextPoll
            sidebar.window_proc(3,0x800A,0,0)
            self.assertEqual(self.state.nextPoll,deadline);self.assertFalse(self.state.busy)
            self.assertEqual(thread.call_count,1)

    def test_sidebar_does_not_launch_while_sync_fetch_is_active(self):
        sidebar=self.sidebar()
        def fetcher():sidebar.refresh_reset_signals();return self.good
        self.fetcher.side_effect=fetcher
        with patch('sidebar_plugin.threading.Thread') as thread:
            self.state.refresh();thread.assert_not_called()
        self.fetcher.assert_called_once()

    def test_thread_construction_and_start_failure_share_backoff(self):
        sidebar=self.sidebar()
        with patch('sidebar_plugin.threading.Thread',side_effect=RuntimeError()):sidebar.refresh_reset_signals()
        self.assertFalse(self.state.busy);self.assertEqual(self.state.nextPoll,1060)
        self.assertEqual(self.state.error,'RuntimeError');self.assertEqual(self.state.failures,1)
        self.clock[0]=1060
        with patch('sidebar_plugin.threading.Thread') as thread:
            thread.return_value.start.side_effect=RuntimeError()
            sidebar.refresh_reset_signals();self.assertEqual(self.state.nextPoll,1180)
            sidebar.refresh_reset_signals();thread.assert_called_once()
        self.assertFalse(self.state.busy);self.assertEqual(self.state.failures,2)
        self.fetcher.assert_not_called();self.assertFalse(self.state.path.exists())

    def test_heartbeat_consumes_result_when_native_notification_is_lost(self):
        for notification in (False,RuntimeError()):
            with self.subTest(notification=notification):
                sidebar=self.sidebar();self.state.nextPoll=self.clock[0]
                sidebar.win.u.PostMessageW.side_effect=notification if isinstance(notification,Exception) else None
                sidebar.win.u.PostMessageW.return_value=notification
                with patch('sidebar_plugin.threading.Thread') as thread:
                    sidebar.refresh_reset_signals();thread.call_args.kwargs['target']()
                    self.assertTrue(self.state.busy)
                    sidebar.refresh_reset_signals()  # Same entry point as existing heartbeat.
                    self.assertFalse(self.state.busy);thread.assert_called_once()
                self.assertEqual(self.state.data,self.good);self.assertIsNone(self.state.error)
                sidebar.publish_details.assert_called_once()

    def test_sidebar_write_failure_and_ui_failure_do_not_fake_fetch_success(self):
        self.state.refresh();saved=self.state.path.read_bytes();self.clock[0]=1900
        sidebar=self.sidebar();self.fetcher.return_value=dict(self.good,fetchedAt=1900)
        with patch('sidebar_plugin.threading.Thread') as thread:
            sidebar.refresh_reset_signals();thread.call_args.kwargs['target']()
            with patch('reset_signals._atomic_write',side_effect=PermissionError()):
                sidebar.window_proc(3,0x800A,0,0)
        self.assertEqual(self.state.error,'PermissionError');self.assertEqual(self.state.nextPoll,1960)
        self.assertEqual(self.state.path.read_bytes(),saved);self.assertEqual(self.state.data,self.good)
        self.clock[0]=1960;sidebar.request_repaint.side_effect=RuntimeError()
        with patch('sidebar_plugin.threading.Thread') as thread:
            sidebar.refresh_reset_signals();thread.call_args.kwargs['target']()
            sidebar.window_proc(3,0x800A,0,0)
        self.assertFalse(self.state.busy);self.assertIsNone(self.state.error)
        self.assertEqual(self.state.failures,0);self.assertEqual(self.state.nextPoll,2860)
        sidebar.log_error.assert_called_once()

if __name__=='__main__':unittest.main()
