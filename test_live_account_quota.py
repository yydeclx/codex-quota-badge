import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import quota_badge as q

class LiveQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'runtime-port.json').write_text('{"pid":123,"port":10100}')
        (self.root/'admin-api-token').write_text('fixture-only')
        q._LIVE_STATE.clear()
        self.rows=[{'id':'__main__','isMain':True,'quota':{'updatedAt':time.time()*1000,'weeklyPercent':48,'weeklyResetAt':time.time()+86400}},
                   {'id':'secondary','isMain':False,'quota':{'updatedAt':time.time()*1000,'weeklyPercent':55,'weeklyResetAt':time.time()+99999}}]
    def connection(self,status=200):
        c=MagicMock();r=c.getresponse.return_value;r.status=status;r.read.return_value=json.dumps({'accounts':self.rows}).encode();return c
    def test_roles_and_shared_throttle(self):
        c=self.connection()
        with patch('quota_badge.http.client.HTTPConnection',return_value=c) as factory:
            self.assertEqual(q._live_account_quota(self.root,'__main__')['weeklyPercent'],48)
            self.assertEqual(q._live_account_quota(self.root,'secondary')['weeklyPercent'],55)
            factory.assert_called_once_with('127.0.0.1',10100,timeout=8)
            self.assertEqual(c.request.call_args.args[:2],('GET','/api/codex-auth/accounts?refresh=1'))
            c.close.assert_called_once()
    def test_failure_backoff_and_no_wrong_account_fallback(self):
        with patch('quota_badge.http.client.HTTPConnection',return_value=self.connection(503)) as factory:
            for _ in range(2):
                with self.assertRaises(ValueError):q._live_account_quota(self.root,'__main__')
            factory.assert_called_once()
    def test_stale_timestamp_not_renewed_by_success(self):
        self.rows[0]['quota']['updatedAt']=(time.time()-2000)*1000
        with patch('quota_badge.http.client.HTTPConnection',return_value=self.connection()):
            with self.assertRaises(ValueError):q._live_account_quota(self.root,'__main__')
    def test_main_flag_conflict_is_rejected(self):
        self.rows[0]['isMain']=False
        with patch('quota_badge.http.client.HTTPConnection',return_value=self.connection()):
            with self.assertRaises(ValueError):q._live_account_quota(self.root,'__main__')

if __name__=='__main__':unittest.main()
