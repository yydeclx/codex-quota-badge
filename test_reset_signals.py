import tempfile,time,unittest
from pathlib import Path
from reset_signals import parse_forecast,parse_feed,promised_reset_at,ResetSignals

class ResetSignalTests(unittest.TestCase):
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

if __name__=='__main__':unittest.main()
