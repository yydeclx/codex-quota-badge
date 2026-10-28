"""Mode transitions use temporary fixtures; never switch the user's gateway."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
import patch_mode
from sidebar_activity import ProviderActivity
from sidebar_plugin import SidebarPlugin


class PatchModeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);original=b'original';candidate=b'patched'
        self.original_sha=hashlib.sha256(original).hexdigest();self.patch_sha=hashlib.sha256(candidate).hexdigest()
        for name,body in (('original.exe',original),('candidate.exe',candidate),('target.exe',candidate)):(root/name).write_bytes(body)
        (root/'smoke-result.json').write_text(json.dumps({'passed':True,'binarySHA256':self.patch_sha}))
        self.root=root;self.running=True
        for name,value in {'ROOT':root,'ORIGINAL':root/'original.exe','CANDIDATE':root/'candidate.exe',
                           'TARGET':root/'target.exe','ORIGINAL_SHA':self.original_sha,'PATCH_SHA':self.patch_sha}.items():
            change=patch.object(patch_mode,name,value);change.start();self.addCleanup(change.stop)
        def deploy(enabled):
            patch_mode.TARGET.write_bytes((patch_mode.CANDIDATE if enabled else patch_mode.ORIGINAL).read_bytes())
            self.running=enabled
        self.deployer=patch.object(patch_mode,'run_deployment',side_effect=deploy).start();self.addCleanup(patch.stopall)
        patch.object(patch_mode,'runtime_mode',side_effect=lambda:(self.running,123)).start()

    def test_roundtrip_restores_exact_original_and_reenables_tested_patch(self):
        self.assertTrue(patch_mode.apply_mode(False)['changed'])
        self.assertEqual(patch_mode.digest(patch_mode.TARGET),self.original_sha)
        self.assertFalse(patch_mode.apply_mode(False)['changed'])
        self.assertTrue(patch_mode.apply_mode(True)['changed'])
        self.assertEqual(self.deployer.call_count,2)

    def test_unknown_updated_gateway_is_not_downgraded(self):
        patch_mode.TARGET.write_bytes(b'newer official release')
        with self.assertRaisesRegex(RuntimeError,'版本或程序已变化'):patch_mode.apply_mode(False)
        self.deployer.assert_not_called()
        self.assertEqual(patch_mode.TARGET.read_bytes(),b'newer official release')

    def test_turning_off_after_official_upgrade_keeps_new_program_untouched(self):
        patch_mode.TARGET.write_bytes(b'newer official release');self.running=False
        patch_mode.ORIGINAL.unlink()
        result=patch_mode.apply_mode(False)
        self.assertTrue(result['gatewayUntouched']);self.deployer.assert_not_called()
        self.assertEqual(patch_mode.TARGET.read_bytes(),b'newer official release')
        with self.assertRaisesRegex(RuntimeError,'版本或程序已变化'):patch_mode.apply_mode(True)

    def test_disabled_mode_does_not_require_candidate_file(self):
        patch_mode.CANDIDATE.unlink()
        self.assertFalse(patch_mode.apply_mode(False)['enabled'])

    def test_untested_candidate_and_corrupt_backup_are_rejected(self):
        patch_mode.apply_mode(False)
        patch_mode.CANDIDATE.write_bytes(b'untested')
        with self.assertRaisesRegex(RuntimeError,'测试校验'):patch_mode.apply_mode(True)
        patch_mode.ORIGINAL.write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError,'备份校验'):patch_mode.apply_mode(False)

    def test_disk_file_is_not_proof_of_running_mode(self):
        self.running=False
        with self.assertRaisesRegex(RuntimeError,'运行模式不一致'):patch_mode.apply_mode(True)
        self.deployer.assert_not_called()

    def test_runtime_must_match_after_switch(self):
        with patch.object(patch_mode,'runtime_mode',return_value=(True,123)):
            with self.assertRaisesRegex(RuntimeError,'尚未确认'):patch_mode.apply_mode(False)

    def test_verified_temporary_copy_is_removed_but_fixed_rollback_pair_remains(self):
        previous=self.root/'ocx.telemetry-2.92.0-before-fixture.exe'
        previous.write_bytes(patch_mode.CANDIDATE.read_bytes())
        (self.root/'deployment.json').write_text(json.dumps({'status':'verified','target':str(patch_mode.TARGET),
                                                          'previousExecutable':str(previous)}))
        patch_mode.discard_transaction_copy()
        self.assertFalse(previous.exists())
        self.assertTrue(patch_mode.ORIGINAL.exists());self.assertTrue(patch_mode.CANDIDATE.exists())

    def test_unverified_or_unrecognized_backup_is_preserved(self):
        previous=self.root/'ocx.telemetry-2.92.0-before-fixture.exe';previous.write_bytes(b'unknown')
        journal=self.root/'deployment.json'
        for status in ('preparing','verified'):
            journal.write_text(json.dumps({'status':status,'target':str(patch_mode.TARGET),'previousExecutable':str(previous)}))
            patch_mode.discard_transaction_copy();self.assertTrue(previous.exists())
        journal.write_text(json.dumps({'status':'verified','target':str(patch_mode.TARGET),
                                      'previousExecutable':str(patch_mode.ORIGINAL)}))
        patch_mode.discard_transaction_copy();self.assertTrue(patch_mode.ORIGINAL.exists())


class DisabledWorkTests(unittest.TestCase):
    def test_disabled_activity_has_no_history_reads_threads_or_fetches(self):
        fetch=MagicMock()
        with patch('sidebar_activity.Path.read_text',side_effect=AssertionError('history read')),patch('sidebar_activity.threading.Thread') as thread:
            activity=ProviderActivity(fetch=fetch,enabled=False,history_path='unused-history.json')
            for _ in range(100):activity.poll()
            activity._refresh()
        fetch.assert_not_called();thread.assert_not_called()
        self.assertEqual(activity.source,'legacy-log')
        self.assertFalse(activity.details('SKG')['supportsLive'])

    def test_disable_discards_inflight_result_and_does_not_write_history(self):
        activity=ProviderActivity(history_path=None)
        def fetch():
            activity.set_enabled(False)
            return {'schemaVersion':3,'scope':'responses','providers':{},'requests':[],'supportsLive':True}
        activity.fetch=fetch;activity.busy=True
        activity._refresh()
        self.assertIsNone(activity.snapshot);self.assertFalse(activity.busy)
        self.assertEqual(activity.source,'legacy-log')
        activity.set_enabled(True)
        self.assertTrue(activity.enabled);self.assertEqual(activity.next_poll,0)

    def test_legacy_mode_never_enters_live_metrics_repaint(self):
        p=SidebarPlugin.__new__(SidebarPlugin)
        p.config={'realtimeMetricsEnabled':False};p.update_tooltip=MagicMock()
        for now in range(100):p.refresh_visible_metrics(now)
        p.update_tooltip.assert_not_called()

    def test_legacy_card_keeps_completed_statistics_even_while_turn_is_running(self):
        from test_live_metrics import CardMetricsTests
        fixture=CardMetricsTests();history=fixture.history()
        original=SidebarPlugin.update_tooltip
        def force_legacy(p,**options):
            p.config['realtimeMetricsEnabled']=False
            with patch('live_metrics.request_metrics',side_effect=AssertionError('live calculation')):
                return original(p,**options)
        with patch.object(SidebarPlugin,'update_tooltip',force_legacy):
            call=fixture.render({'requests':[{'startedAt':1}]},history)
        self.assertIsNone(call.kwargs['activity_status'])
        self.assertIsNone(call.kwargs['live_first']);self.assertIsNone(call.kwargs['live_output'])
        self.assertIn('输出约 20 tok/s',call.kwargs['speed'])
        self.assertEqual(call.kwargs['previous_speed'],'约 10 tok/s')


if __name__=='__main__':unittest.main()
