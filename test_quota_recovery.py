"""Quota read outages recover on bounded retries without additional timers."""
import queue
import unittest
from unittest.mock import MagicMock,patch
from native_badge import Badge


class QuotaRecoveryTests(unittest.TestCase):
    def badge(self, cached=True):
        badge=Badge.__new__(Badge)
        badge.data={'remaining':44,'resetsAt':10000,'updatedAt':90} if cached else None
        badge.error=False;badge.refresh_failures=0;badge.busy=True
        badge.results=queue.Queue();badge.update_visual=MagicMock();badge.write_status=MagicMock()
        badge.win=MagicMock();badge.log_error=MagicMock()
        return badge

    def test_repeated_failures_retry_quickly_then_back_off(self):
        badge=self.badge()
        for index,delay in enumerate((5,10,20,40,80,160,300,300),1):
            badge.results.put((None,'TimeoutError'))
            with patch('native_badge.time.time',return_value=100):
                badge.window_proc(1,0x8001,0,0)
            self.assertEqual(badge.next_refresh,100+delay)
            self.assertEqual(badge.refresh_failures,index)
            self.assertEqual(badge.error,index>=2)
            self.assertEqual(badge.last_quota_error,'TimeoutError')
            self.assertFalse(badge.busy)
            self.assertEqual(badge.data['remaining'],44)
        badge.win.u.SetTimer.assert_not_called()

    def test_success_clears_failure_state_and_resumes_normal_cadence(self):
        badge=self.badge()
        with patch('native_badge.time.time',return_value=100):
            badge.quota_refresh_failed('TimeoutError');badge.quota_refresh_failed('TimeoutError')
            badge.results.put(({'remaining':43,'resetsAt':10000,'updatedAt':100},False))
            badge.window_proc(1,0x8001,0,0)
        self.assertFalse(badge.error);self.assertEqual(badge.refresh_failures,0)
        self.assertIsNone(badge.last_quota_error);self.assertIsNone(badge.last_quota_failure_at)
        self.assertEqual(badge.next_refresh,400);self.assertEqual(badge.data['remaining'],43)
        badge.update_visual.assert_called_once()

    def test_first_read_failure_stays_unknown_and_never_saves_error_text(self):
        badge=self.badge(False)
        with patch('native_badge.time.time',return_value=100):badge.quota_refresh_failed('private CLI output / token')
        self.assertTrue(badge.error);self.assertEqual(badge.last_quota_error,'QuotaReadFailed')
        self.assertEqual(badge.next_refresh,105)

if __name__=='__main__':unittest.main()
