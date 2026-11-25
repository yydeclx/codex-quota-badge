"""Mode transitions use temporary fixtures; never switch the user's gateway."""
import hashlib,importlib.util,json,sys,tempfile,unittest
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
        def deploy(enabled,bundle):
            patch_mode.TARGET.write_bytes((bundle.candidate if enabled else bundle.original).read_bytes())
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


class GatewayDeploymentTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.root=Path(directory.name)
        self.target=self.root/'target.exe';self.original=self.root/'original.exe';self.candidate=self.root/'candidate.exe'
        for path,data in ((self.target,b'original'),(self.original,b'original'),(self.candidate,b'candidate')):
            path.write_bytes(data)
        self.bundle=patch_mode.GatewayBundle('2.100.8',self.root,self.original,self.candidate,
                                             patch_mode.digest(self.original),patch_mode.digest(self.candidate))
        (self.root/'smoke-result.json').write_text(json.dumps({'passed':True,'binarySHA256':self.bundle.patch_sha}))
        self.journal=self.root/'deployment.json'
        spec=importlib.util.spec_from_file_location('test_deploy_gateway',Path(patch_mode.__file__).parent/'gateway-telemetry/deploy_gateway.py')
        self.deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.deploy)
        self.now={'pid':101,'activeTurnCount':0,'isDraining':False}
        self.restart_count=0

        def lookup(sha):
            return self.bundle if sha in (self.bundle.original_sha,self.bundle.patch_sha) else None

        def read(path):
            if path.endswith('provider-activity'):
                return {'pid':self.now['pid'],'supportsLive':self.target.read_bytes()==b'candidate'}
            return dict(self.now)

        def restart(state):
            self.restart_count+=1
            self.now['pid']+=1
            return {'activeTurnCount':0}

        for change in (patch.object(self.deploy,'TARGET',self.target),patch.object(self.deploy,'gateway_bundle',side_effect=lookup),
                       patch.object(self.deploy,'read_local',side_effect=read),patch.object(self.deploy,'restart',side_effect=restart),
                       patch.object(self.deploy.time,'sleep')):
            change.start();self.addCleanup(change.stop)

    def run_deploy(self,*args):
        with patch.object(sys,'argv',['deploy_gateway.py',*args]),patch('builtins.print'):
            self.deploy.main()

    def test_check_only_preserves_executable_and_does_not_restart(self):
        self.now['activeTurnCount']=2
        self.run_deploy('--check-only','--expect-version','2.100.8')
        self.assertEqual(self.target.read_bytes(),b'original')
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.restart_count,0)

    def test_version_pin_and_strict_idle_gate(self):
        with self.assertRaisesRegex(RuntimeError,'unknown work'):self.run_deploy('--expect-version','2.92.0')
        with self.assertRaisesRegex(RuntimeError,'zero active requests'):self.run_deploy('--graceful')
        self.now['activeTurnCount']=1
        with patch.object(self.deploy.time,'monotonic',side_effect=range(20)):
            with self.assertRaisesRegex(RuntimeError,'remained busy'):self.run_deploy('--wait-seconds','3')
        self.assertEqual(self.target.read_bytes(),b'original')
        self.assertEqual(json.loads(self.journal.read_text())['status'],'deferred_before_switch')
        self.assertEqual(self.restart_count,0)

    def test_switch_and_restore_the_same_version(self):
        self.run_deploy('--expect-version','2.100.8')
        self.assertEqual(self.target.read_bytes(),b'candidate')
        self.assertEqual(json.loads(self.journal.read_text())['status'],'verified')
        self.candidate.unlink()
        self.run_deploy('--rollback')
        self.assertEqual(self.target.read_bytes(),b'original')
        self.assertEqual(json.loads(self.journal.read_text())['status'],'verified')
        self.assertEqual(self.restart_count,2)


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
