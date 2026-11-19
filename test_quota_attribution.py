import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quota_attribution import estimate, sample, METHOD, prepare, account_key
from quota_accounting import quota_breakdown, reconcile_cycle
from sidebar_modules import AccountQuotaHistory


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.start=dt.datetime(2026,11,1,12).timestamp();self.reset=self.start+604800

    def q(self,used=10,offset=600):
        return {'remaining':100-used,'updatedAt':self.start+offset,'resetsAt':self.reset,'quotaAccountKey':'main-key','quotaAccount':'main'}

    def s(self,used=0,offset=0,source='cache'):
        return sample(self.start+offset,100-used,self.reset,source)

    def requests(self,rows,complete=True):
        return {'requests':[{'at':self.start+at,'weight':weight,'account':account} for at,weight,account in rows],
                'complete':complete,'start':self.start,'end':self.start+10000}

    def closed(self,c):
        self.assertAlmostEqual(c['localUsed']+c['otherUsed'],100-c['lastRemaining'],places=4)
        for aggregate,daily in (('localUsed','localDays'),('otherUsed','otherDays')):
            self.assertAlmostEqual(sum(x['points'] for x in c[daily].values()),c[aggregate],places=4)
            self.assertGreaterEqual(c[aggregate],0)
        view=quota_breakdown(c,c['lastRemaining'],c['reset'],sample_at=c['lastAt'])
        self.assertEqual(int(view['labels']['local'])+int(view['labels']['other']),int(view['labels']['total']))
        self.assertNotIn('unknown',view)

    def test_later_log_gap_preserves_earlier_calibration(self):
        requests=self.requests([(300,100,None)],False)
        requests.update(start=self.start+1000,coverageComplete=True,
                        coverageRanges=[[self.start,self.start+600],[self.start+1000,self.start+1200]])
        c=estimate(self.q(10,1200),'__main__',{'__main__':[self.s(source='native-response'),self.s(10,600)]},requests)
        self.assertEqual(c['localUsed'],10);self.assertEqual(c['calibrationIntervals'],1);self.closed(c)
        requests['coverageRanges']=[[self.start,self.start+200],[self.start+400,self.start+1200]]
        c=estimate(self.q(10,1200),'__main__',{'__main__':[self.s(source='native-response'),self.s(10,600)]},requests)
        self.assertEqual(c['calibrationIntervals'],0)

    def test_live_main_observation_correlates_without_secondary(self):
        quota=self.q();quota['quotaSource']='opencodex live account quota'
        c=estimate(quota,'__main__',{'__main__':[self.s()]},self.requests([(300,100,None)]))
        self.assertEqual(c['localUsed'],10);self.closed(c)

    def test_missing_data_has_no_fifty_fifty_prior(self):
        c=estimate(self.q(40),'main',{'main':[]},{'requests':[],'complete':False})
        self.assertEqual((c['localUsed'],c['otherUsed']),(0,40))
        self.assertEqual(c['workloadRate'],0);self.assertIn('暂估',c['attributionNote']);self.closed(c)

    def test_account_bound_request_and_drop_calibrate_local(self):
        c=estimate(self.q(),'main',{'main':[self.s()]},self.requests([(300,100,'main')]))
        self.assertEqual((c['localUsed'],c['otherUsed']),(10,0));self.assertEqual(c['calibrationIntervals'],1);self.closed(c)

    def test_other_accounts_requests_cannot_claim_this_accounts_drop(self):
        c=estimate(self.q(),'main',{'main':[self.s()]},self.requests([(300,100,'secondary')]))
        self.assertEqual(c['localUsed'],0);self.closed(c)

    def test_native_main_correlation_is_independent_of_secondary(self):
        requests=self.requests([(300,100,None)])
        cases=[([],10),([self.s(20),self.s(21,600)],10),([self.s(20),self.s(20,600)],10)]
        for secondary,expected in cases:
            c=estimate(self.q(),'__main__',{'__main__':[self.s(source='native-response')],'secondary':secondary},requests)
            self.assertEqual(c['localUsed'],expected);self.closed(c)

    def test_unbound_request_without_native_account_evidence_is_not_confirmed_local(self):
        c=estimate(self.q(),'__main__',{'__main__':[self.s()]},self.requests([(300,100,None)]))
        self.assertEqual(c['localUsed'],0)

    def test_missing_log_coverage_cannot_calibrate_rate(self):
        c=estimate(self.q(),'main',{'main':[self.s()]},self.requests([(300,100,'main')],False))
        self.assertEqual(c['workloadRate'],0);self.assertFalse(c['requestLogCovered']);self.closed(c)

    def test_initial_gap_uses_measured_rate_and_real_request_day(self):
        # First real sample is 20 points used. One later 10-point drop per
        # 100 workload units supports 15 local points in the initial gap.
        q=self.q(30,90000)
        rows=[self.s(20,89400)]
        c=estimate(q,'main',{'main':rows},self.requests([(300,150,'main'),(89700,100,'main')]))
        self.assertEqual((c['localUsed'],c['otherUsed']),(25,5))
        self.assertEqual(c['localDays']['2026-11-01']['points'],15)
        self.assertEqual(c['localDays']['2026-11-02']['points'],10);self.closed(c)

    def test_idle_drop_stays_other_and_not_used_to_inflate_rate(self):
        c=estimate(self.q(30,1200),'main',{'main':[self.s(),self.s(10,600)]},self.requests([(300,100,'main')]))
        self.assertEqual((c['localUsed'],c['otherUsed']),(10,20));self.closed(c)

    def test_zero_change_workload_contributes_to_rate(self):
        c=estimate(self.q(10,1200),'main',{'main':[self.s(),self.s(0,600)]},self.requests([(300,100,'main'),(900,100,'main')]))
        self.assertEqual(c['workloadRate'],.05);self.assertEqual(c['localUsed'],10);self.closed(c)

    def test_delayed_drop_without_new_requests_uses_recent_pending_work(self):
        c=estimate(self.q(10,1200),'main',{'main':[self.s(),self.s(0,600)]},self.requests([(300,100,'main')]))
        self.assertEqual(c['workloadRate'],.1);self.assertEqual(c['localUsed'],10);self.closed(c)

    def test_recent_complete_coverage_calibrates_despite_missing_week_prefix(self):
        requests=self.requests([(300,100,'main')],False)
        requests['coverageComplete']=True
        c=estimate(self.q(),'main',{'main':[self.s()]},requests)
        self.assertEqual(c['localUsed'],10);self.assertFalse(c['requestLogCovered']);self.closed(c)

    def test_full_refund_cannot_restore_old_nonlocal_usage(self):
        q=self.q(10,90000)
        rows=[self.s(),self.s(20,300),self.s(0,600),self.s(0,89400)]
        c=estimate(q,'main',{'main':rows},self.requests([(89700,100,'main')]))
        self.assertEqual((c['localUsed'],c['otherUsed']),(10,0));self.closed(c)

    def test_rebound_nets_to_official_total(self):
        c=estimate(self.q(3,1200),'main',{'main':[self.s(),self.s(10,600)]},self.requests([(300,100,'main')]))
        self.assertEqual(c['corrections'],1);self.closed(c)

    def test_window_change_and_future_samples_ignored(self):
        old=sample(self.start-100,20,self.reset-86400,'cache')
        c=estimate(self.q(),'main',{'main':[old,self.s(0,1000)]},self.requests([(300,100,'main')]))
        self.assertEqual(c['calibrationIntervals'],0);self.closed(c)

    def test_reset_jitter_is_accepted_and_wrong_window_rejected(self):
        a=self.s();a['reset']+=1
        c=estimate(self.q(),'main',{'main':[a]},self.requests([(300,100,'main')]))
        self.assertEqual(c['localUsed'],10)
        a['reset']+=1000
        c=estimate(self.q(),'main',{'main':[a]},self.requests([(300,100,'main')]))
        self.assertEqual(c['localUsed'],0)

    def test_projection_is_pure_and_method_survives_reload(self):
        c=estimate(self.q(),'main',{'main':[self.s()]},self.requests([(300,100,'main')]))
        original=copy.deepcopy(c);result=reconcile_cycle(c)
        self.assertEqual(c,original);self.assertEqual(result['attributionMethod'],METHOD)
        self.assertEqual(result,reconcile_cycle(result))

    def test_old_equal_split_backed_up_once_and_heartbeat_does_not_change_new_result(self):
        with tempfile.TemporaryDirectory() as d:
            h=AccountQuotaHistory(Path(d)/'quota-history.json');h.select('main-key')
            h.observe(self.q(40));old=h._history.path.read_bytes()
            c=estimate(self.q(40),'main',{'main':[self.s()]},self.requests([(300,100,'main')]))
            h.observe({**self.q(40),'_attribution':c})
            self.assertEqual(h._history.path.with_suffix('.attribution-v1-backup.json').read_bytes(),old)
            with patch('sidebar_modules.atomic_json') as write:
                for _ in range(10):h.observe(self.q(40),{'totalTokens':999},True)
                h.observe({**self.q(40),'_attribution':c});write.assert_not_called()
            again=AccountQuotaHistory(Path(d)/'quota-history.json');again.select('main-key')
            self.assertEqual(again.view()['attributionMethod'],METHOD);self.assertEqual(again.view()['localUsed'],40)

    def test_prepare_never_bootstraps_unbound_or_rebuilt_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'config.json').write_text('{"codexAccounts":[]}')
            cache={'quotas':{'__main__':{'weeklyPercent':10,'weeklyResetAt':self.reset,'updatedAt':self.start+600}},
                   'mainPolicyQuota':{'identityKey':'b'*64}}
            (root/'codex-quota-cache.json').write_text(json.dumps(cache))
            h=AccountQuotaHistory(root/'quota-history.json');key=account_key('main','__main__');h.select(key)
            h.observe({**self.q(0,0),'quotaAccountKey':key});h.observe({**self.q(),'quotaAccountKey':key})
            original=h._history.path.read_bytes()
            with patch('quota_evidence.collect_requests',return_value=self.requests([(300,100,'__main__')])),patch('quota_native_evidence.collect_native_samples',return_value=[]):
                result=prepare({**self.q(),'quotaAccountKey':key},root,root)
            self.assertEqual(result['calibrationIntervals'],0);self.assertEqual(result['localUsed'],0)
            self.assertEqual(h._history.path.read_bytes(),original)

    def test_prepare_persists_accepted_snapshot_when_cache_already_advanced(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'config.json').write_text('{"codexAccounts":[]}')
            cache={'quotas':{'__main__':{'weeklyPercent':10,'weeklyResetAt':self.reset,'updatedAt':self.start+600}},
                   'mainPolicyQuota':{'identityKey':'a'*64}}
            (root/'codex-quota-cache.json').write_text(json.dumps(cache))
            key=account_key('main','__main__')
            with patch('quota_evidence.collect_requests',return_value=self.requests([(300,100,'__main__')])),patch('quota_native_evidence.collect_native_samples',return_value=[]):
                prepare({**self.q(0,0),'quotaAccountKey':key},root,root)
                state=json.loads((root/'quota-attribution-state.json').read_text())
                self.assertEqual(len(next(iter(state['accounts'].values()))['samples']),2)
                result=prepare({**self.q(),'quotaAccountKey':key},root,root)
            self.assertEqual(result['localUsed'],10)

    def test_prepare_rejects_identity_race_and_excludes_old_identity_samples(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'config.json').write_text('{"codexAccounts":[]}')
            cache={'quotas':{'__main__':{'weeklyPercent':10,'weeklyResetAt':self.reset,'updatedAt':self.start+600}},
                   'mainPolicyQuota':{'identityKey':'new'},
                   'history':{'accounts':{'__main__':{'identity':'old','samples':[
                       {'observedAt':self.start,'windows':[{'family':'account','window':'weekly','usedPercent':0,'resetAtMs':self.reset}]}]}}}}
            (root/'codex-quota-cache.json').write_text(json.dumps(cache))
            identity_key=hashlib.sha256(b'__main__').hexdigest()
            state={'version':2,'accounts':{identity_key:{'identity':hashlib.sha256(b'old').hexdigest(),'samples':[self.s()]}}}
            (root/'quota-attribution-state.json').write_text(json.dumps(state))
            key=account_key('main','__main__')
            with patch('quota_evidence.collect_requests',return_value=self.requests([(300,100,'__main__')])),patch('quota_native_evidence.collect_native_samples',return_value=[]):
                with self.assertRaises(ValueError):prepare({**self.q(),'quotaAccountKey':key,'quotaIdentity':hashlib.sha256(b'old').hexdigest()},root,root)
                result=prepare({**self.q(),'quotaAccountKey':key,'quotaIdentity':hashlib.sha256(b'new').hexdigest()},root,root)
            self.assertEqual(result['calibrationIntervals'],0);self.assertEqual(result['localUsed'],0)


if __name__=='__main__':unittest.main()
