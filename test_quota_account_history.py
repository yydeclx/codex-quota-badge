"""Account isolation regressions; all history writes stay in temporary folders."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import weakref

from sidebar_modules import AccountQuotaHistory,QuotaHistory


class AccountQuotaHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.path=self.root/'quota-history.json'
        self.history=AccountQuotaHistory(self.path)
        self.start=dt.datetime(2026,11,2,12).timestamp()
        self.reset=self.start+604800

    def sample(self,key,remaining=100,offset=0):
        return {'quotaAccountKey':key,'remaining':remaining,
                'resetsAt':self.reset,'updatedAt':self.start+offset}

    def account_path(self,key):
        digest=hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]
        return self.root/f'quota-history-account-{digest}.json'

    def test_same_reset_and_sample_timestamps_are_isolated_and_restored(self):
        views={}
        for key,remaining,local in (('anonymous-a',97,True),('anonymous-b',89,False)):
            self.assertIsNone(self.history.select(key))
            self.history.observe(self.sample(key),complete=True)
            self.history.observe(self.sample(key,remaining,60),local_usage={'quotaAccountKey':key},force_local=local,complete=True)
            views[key]=copy.deepcopy(self.history.view())
            saved=json.loads(self.account_path(key).read_text(encoding='utf-8'))
            self.assertEqual(saved['version'],6)
            self.assertEqual(len(saved['cycles']),1)
        self.assertEqual(views['anonymous-a']['localUsed'],3)
        self.assertEqual(views['anonymous-b']['lastRemaining'],89)
        for key in ('anonymous-a','anonymous-b','anonymous-a'):
            self.assertEqual(self.history.select(key),views[key])
        restarted=AccountQuotaHistory(self.path)
        self.assertIsNone(restarted.view())
        self.assertEqual(restarted.select('anonymous-b'),views['anonymous-b'])
        self.assertFalse(self.path.exists())

    def test_unselected_and_missing_keys_never_create_history(self):
        with patch('sidebar_modules.QuotaHistory') as ledger:
            for key in (None,'','  ',False,123,[],{}):
                self.assertIsNone(self.history.select(key))
                self.history.observe(self.sample(key))
                self.assertIsNone(self.history.view())
            self.history.observe(self.sample('anonymous-a'))
            ledger.assert_not_called()
        self.assertEqual(list(self.root.iterdir()),[])

    def test_missing_or_mismatched_sample_does_not_write_or_switch(self):
        self.history.select('anonymous-a')
        self.history.observe(self.sample('anonymous-a'))
        before=self.account_path('anonymous-a').read_bytes()
        view=copy.deepcopy(self.history.view())
        missing=self.sample('anonymous-a',50,60)
        del missing['quotaAccountKey']
        with patch('sidebar_modules.atomic_json') as write:
            for sample in (missing,None,[],self.sample(None),self.sample(''),
                           self.sample('anonymous-b',50,60)):
                self.history.observe(sample,{'totalTokens':999},True,True,True)
            write.assert_not_called()
        self.assertEqual(self.history.account_key,'anonymous-a')
        self.assertEqual(self.history.view(),view)
        self.assertEqual(self.account_path('anonymous-a').read_bytes(),before)
        self.assertFalse(self.account_path('anonymous-b').exists())
        self.assertIsNone(self.history.select(None))
        self.history.observe(self.sample('anonymous-a',50,60))
        self.assertIsNone(self.history.view())
        self.assertEqual(self.account_path('anonymous-a').read_bytes(),before)
        self.assertEqual(self.history.select('anonymous-a'),view)

    def test_legacy_file_is_never_read_migrated_or_attributed(self):
        # Both a migratable legacy document and malformed bytes must stay intact.
        for original in (b'{"version":1,"cycles":[{"mixedAccountHistory":true}]}',
                         b'\xffmixed legacy bytes'):
            with self.subTest(original=original):
                self.path.write_bytes(original)
                history=AccountQuotaHistory(self.path)
                for key in ('anonymous-a','anonymous-b'):
                    history.select(key)
                    history.observe(self.sample(key))
                    self.assertNotIn('mixedAccountHistory',history.view())
                history.select(None)
                self.assertEqual(self.path.read_bytes(),original)
                self.assertFalse(list(self.root.glob('*backup*')))

    def test_default_and_custom_base_paths_use_only_hashed_siblings(self):
        key='../../outside\\private:account\x00/匿名'
        with patch('sidebar_modules.ROOT',self.root):
            default=AccountQuotaHistory()
            default.select(key)
            default.observe(self.sample(key))
        self.assertTrue(self.account_path(key).is_file())
        self.assertEqual(default._history.path.parent,self.root)
        custom=AccountQuotaHistory(self.root/'custom.json')
        custom.select(key)
        self.assertEqual(custom._history.path.name,
                         self.account_path(key).name.replace('quota-history','custom'))
        self.assertNotIn(key,custom._history.path.read_text(encoding='utf-8'))
        self.assertFalse(self.path.exists())
        self.assertFalse((self.root/'custom.json').exists())

    def test_only_current_ledger_is_retained_and_same_selection_is_reused(self):
        self.history.select('anonymous-a')
        previous=weakref.ref(self.history._history)
        with patch('sidebar_modules.QuotaHistory') as ledger:
            self.history.select('anonymous-a')
            ledger.assert_not_called()
        self.assertIs(self.history._history,previous())
        self.history.select('anonymous-b')
        self.assertIsNone(previous())
        previous=weakref.ref(self.history._history)
        self.history.select(None)
        self.assertIsNone(previous())
        self.assertIsNone(self.history._history)

    def test_observation_options_preserve_existing_schema6_behavior(self):
        reference=QuotaHistory(self.root/'reference.json')
        self.history.select('anonymous-a')
        for offset,remaining,active,complete,force in (
                (0,100,False,True,False),(60,98,True,False,False),
                (120,95,False,True,True),(180,96,False,True,False)):
            quota=self.sample('anonymous-a',remaining,offset)
            usage={'quotaAccountKey':'anonymous-a','date':'2026-11-02','totalTokens':offset*100,
                   'requests':offset//60,'updatedAt':self.start+offset}
            original=copy.deepcopy((quota,usage))
            self.history.observe(quota,local_usage=usage,local_active=active,
                                 complete=complete,force_local=force)
            reference.observe(quota,usage,active,complete,force)
            self.assertEqual(self.history.view(),reference.view())
            self.assertEqual((quota,usage),original)

    def test_unscoped_or_other_account_activity_cannot_claim_local_drop(self):
        for usage in ({'totalTokens':1000},{'quotaAccountKey':'other','totalTokens':1000}):
            with self.subTest(usage=usage):
                key=str(len(list(self.root.iterdir())))
                self.history.select(key);self.history.observe(self.sample(key))
                self.history.observe(self.sample(key,90,60),local_usage=usage,local_active=True,force_local=True)
                self.assertEqual(self.history.view()['localUsed'],5)
                self.assertEqual(self.history.view()['otherUsed'],5)


if __name__=='__main__':
    unittest.main()
