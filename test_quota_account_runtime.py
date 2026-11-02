import queue
import unittest
from unittest.mock import MagicMock
from native_badge import Badge
from sidebar_plugin import SidebarPlugin


class AccountRuntimeTests(unittest.TestCase):
    def badge(self):
        b=SidebarPlugin.__new__(SidebarPlugin)
        b.config={'quotaAccount':'main'};b.quota_generation=2
        b.data=None;b.error=False;b.busy=True;b.boundary_busy=True
        b.results=queue.Queue();b.boundary_results=queue.Queue()
        b.quota_history=MagicMock();b.reset_signals=MagicMock();b.reset_signals.data={}
        b.update_visual=MagicMock();b.write_status=MagicMock();b.log_error=MagicMock()
        b.pending_settlement=None;b.last_quota_remaining=None
        return b

    def test_late_success_and_failure_never_override_new_selection(self):
        for data,error in (({'remaining':50},False),(None,'TimeoutError')):
            b=self.badge();b.results.put((data,error,1))
            Badge.window_proc(b,1,0x8001,0,0)
            self.assertIsNone(b.data);self.assertFalse(b.busy)
            self.assertEqual(b.next_refresh,0);self.assertFalse(b.error)
            b.quota_history.select.assert_not_called();b.log_error.assert_not_called()

    def test_late_boundary_result_cannot_enter_new_account_history(self):
        b=self.badge();b.boundary_results.put(({'remaining':50},{},True,False,None,1))
        b.window_proc(1,0x8009,0,0)
        b.quota_history.observe.assert_not_called();self.assertIsNone(b.data)
        self.assertFalse(b.boundary_busy);self.assertEqual(b.next_refresh,0)

    def test_mismatched_or_unscoped_observation_is_rejected(self):
        for q in ({'quotaAccount':'secondary','quotaAccountKey':'other'}, {'remaining':50}):
            b=self.badge();self.assertFalse(b.accept_quota(q))
            self.assertIsNone(b.data);b.quota_history.select.assert_not_called()

    def test_matching_account_resets_cross_account_confirmation(self):
        b=self.badge();b.data={'quotaAccountKey':'other'}
        b.reset_signals.data={'quotaAccountKey':'other','accountConfirmation':{'to':100},'accountCandidate':{'to':100},'probability24h':48}
        q={'quotaAccount':'main','quotaAccountKey':'main-key','remaining':63,'resetsAt':2000000000,'updatedAt':1800000000}
        self.assertTrue(b.accept_quota(q));self.assertEqual(b.data,q)
        b.quota_history.select.assert_called_once_with('main-key')
        self.assertNotIn('accountConfirmation',b.reset_signals.data)
        self.assertNotIn('accountCandidate',b.reset_signals.data)
        self.assertEqual(b.reset_signals.data['probability24h'],48)

    def test_startup_does_not_restore_legacy_or_other_account_balance(self):
        b=self.badge();self.assertEqual(b.restore_last_quota(),(None,False))

    def test_alert_same_reset_remains_independent_between_accounts(self):
        import tempfile
        from pathlib import Path
        from sidebar_options import QuotaAlert
        with tempfile.TemporaryDirectory() as d:
            a=QuotaAlert(Path(d)/'alert.json');config={'quotaAlertEnabled':True,'quotaAlertThreshold':20}
            q={'remaining':10,'resetsAt':2000000000,'quotaAccountKey':'main'}
            a.mark(config,q);self.assertFalse(a.due(config,q))
            self.assertTrue(a.due(config,{**q,'quotaAccountKey':'secondary'}))
            a.mark(config,{**q,'quotaAccountKey':'secondary'})
            self.assertFalse(a.due(config,q))
            restored=QuotaAlert(Path(d)/'alert.json')
            self.assertFalse(restored.due(config,q))

if __name__=='__main__':unittest.main()
