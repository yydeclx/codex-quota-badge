import copy
import json
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from sidebar_modules import Modules
import sidebar_data

class StabilityTests(unittest.TestCase):
    def test_atomic_snapshot_retries_transient_replace_failure(self):
        from sidebar_modules import atomic_json
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'snapshot.json'
            original=Path.replace;calls={'count':0}
            def flaky(path,destination):
                calls['count']+=1
                if calls['count']==1:raise PermissionError('busy')
                return original(path,destination)
            with patch.object(Path,'replace',flaky):atomic_json(target,{'remaining':20})
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'remaining':20})
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])
    def test_service_failure_does_not_block_tasks_or_retry(self):
        m=Modules();m.busy=True
        config={'services':[{'label':'test','url':'http://localhost:1'}]}
        with patch('sidebar_modules.read_tasks',return_value={'tasks_running':[{'id':'one'}]}),patch('sidebar_modules.service_status',side_effect=RuntimeError('failure')):
            m._refresh(config)
        self.assertFalse(m.busy)
        self.assertEqual(m.view()['tasks']['tasks_running'][0]['id'],'one')
        self.assertEqual(m.view()['services'][0]['state'],'unknown')
        m.next_services=0
        with patch('sidebar_modules.read_tasks',return_value={}),patch('sidebar_modules.service_status',return_value={'state':'online'}):m._refresh(config)
        self.assertEqual(m.view()['services'][0]['state'],'online')

    def test_services_cached_but_configuration_invalidates(self):
        m=Modules();config={'services':[{'url':'http://localhost:1'}]}
        with patch('sidebar_modules.read_tasks',return_value={}),patch('sidebar_modules.service_status',return_value={'state':'online'}) as status:
            m._refresh(config);m._refresh(config);self.assertEqual(status.call_count,1)
            config['services'][0]['url']='http://localhost:2'
            m._refresh(config);self.assertEqual(status.call_count,2)

    def test_idle_task_refresh_uses_low_resource_cadence(self):
        import time
        m=Modules();m.busy=True;started=time.monotonic()
        with patch('sidebar_modules.read_tasks',return_value={'tasks_running':[]}):m._refresh({})
        self.assertGreaterEqual(m.next_poll-started,4.8)

    def test_running_subagent_keeps_fast_indicator_cadence(self):
        import time
        m=Modules();m.busy=True;started=time.monotonic()
        running={'tasks_running':[{'id':'sub','isSubagent':True,'model':'OCX/glm-5.3'}]}
        with patch('sidebar_modules.read_tasks',return_value=running):m._refresh({})
        self.assertGreaterEqual(m.next_poll-started,1.8)
        self.assertLess(m.next_poll-started,3)

    def test_thread_start_failure_releases_busy(self):
        m=Modules()
        with patch('sidebar_modules.threading.Thread.start',side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):m.poll({})
        self.assertFalse(m.busy)

    def test_parallel_saves_produce_complete_json(self):
        config=sidebar_data.load_config()
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'config.json'
            with patch.object(sidebar_data,'CONFIG',target):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(sidebar_data.save_config,[copy.deepcopy(config) for _ in range(16)]))
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),config)
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    def test_failed_replace_keeps_existing_configuration(self):
        config=sidebar_data.load_config()
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'config.json';target.write_text('original',encoding='utf-8')
            with patch.object(sidebar_data,'CONFIG',target),patch.object(Path,'replace',side_effect=PermissionError('locked')):
                with self.assertRaises(PermissionError):sidebar_data.save_config(config)
            self.assertEqual(target.read_text(encoding='utf-8'),'original')
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    def test_optional_workers_recover_from_thread_start_failure(self):
        from sidebar_activity import ProviderActivity
        from sidebar_telemetry import Telemetry
        for worker in (ProviderActivity(),Telemetry()):
            with patch('threading.Thread.start',side_effect=RuntimeError('unavailable')):worker.poll()
            self.assertFalse(worker.busy)

    def test_quota_reset_jitter_does_not_split_history(self):
        from sidebar_modules import QuotaHistory
        with tempfile.TemporaryDirectory() as folder:
            h=QuotaHistory(Path(folder)/'history.json')
            h.observe({'remaining':80,'resetsAt':2000000,'updatedAt':1000000})
            h.observe({'remaining':79,'resetsAt':2000001,'updatedAt':1000010})
            h.observe({'remaining':78,'resetsAt':2000000,'updatedAt':1000020})
            self.assertEqual(len(h.data['cycles']),1)
            self.assertEqual(sum(d['points'] for d in h.view()['days'].values()),22)  # Includes the estimated initial 20-point gap.
            h.observe({'remaining':100,'resetsAt':2604800,'updatedAt':2000001})
            self.assertEqual(len(h.data['cycles']),2)

if __name__=='__main__':unittest.main()
