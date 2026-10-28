"""Collector regressions; all files and network responses are isolated fixtures."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from live_metrics import running_sample
from sidebar_activity import ProviderActivity, read_local
from sidebar_telemetry import Telemetry, TIMELINE


def record(stamp=100000, **values):
    return (json.dumps({'provider':'SKG', 'timestamp':stamp, 'status':200,
                       'durationMs':2500, **values})+'\n').encode()


class CollectorRecoveryTests(unittest.TestCase):
    def test_append_reads_only_new_bytes_and_keeps_split_record(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'usage.jsonl'
            path.write_bytes(record()*10000)
            collector=Telemetry(path);collector._records()
            new=record(200000);split=len(new)//2
            total=[];original=Path.open
            class Reader:
                def __enter__(self):return self
                def __exit__(self,*args):self.source.close()
                def __getattr__(self,name):return getattr(self.source,name)
                def read(self,size=-1):
                    self.assert_bounded(size)
                    data=self.source.read(size);total.append(len(data));return data
                def assert_bounded(self,size):
                    if size<0:raise AssertionError('unbounded read')
                def readline(self,*args):raise AssertionError('unbounded line scan')
            def tracked(target,*args,**kwargs):
                wrapper=Reader();wrapper.source=original(target,*args,**kwargs);return wrapper
            with path.open('ab') as source:source.write(new[:split])
            with patch.object(Path,'open',tracked):collector._records()
            self.assertEqual(collector.rows['SKG'][-1]['timestamp'],100000)
            with path.open('ab') as source:source.write(new[split:])
            with patch.object(Path,'open',tracked):collector._records()
            self.assertEqual(collector.rows['SKG'][-1]['timestamp'],200000)
            # Each append also verifies a 64-byte anchor against in-place rewrites.
            self.assertEqual(sum(total),len(new)+128)

    def test_large_unterminated_line_has_bounded_reads_and_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'usage.jsonl';path.write_bytes(b'x'*(3*1024*1024))
            collector=Telemetry(path);original=Path.open;read_sizes=[]
            class Reader:
                def __enter__(self):return self
                def __exit__(self,*args):self.source.close()
                def __getattr__(self,name):return getattr(self.source,name)
                def read(self,size=-1):
                    if size<0:raise AssertionError('unbounded read')
                    data=self.source.read(size);read_sizes.append(len(data));return data
                def readline(self,*args):raise AssertionError('unbounded line scan')
            def tracked(target,*args,**kwargs):
                wrapper=Reader();wrapper.source=original(target,*args,**kwargs);return wrapper
            with patch.object(Path,'open',tracked):collector._records()
            self.assertLessEqual(sum(read_sizes),2*1024*1024)
            with path.open('ab') as source:source.write(b'\n'+record(200000))
            collector._records()
            self.assertEqual(collector.rows['SKG'][-1]['timestamp'],200000)

    def test_malformed_rows_do_not_abort_later_records_or_timeline(self):
        bad=[b'[]\n',b'null\n',record(usage=[]),record(usage='bad'),
             record(attempts=1),record(provider=[]),record(status=float('nan')),
             record(attempts=[{'sendCount':float('inf')}])]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'usage.jsonl';path.write_bytes(b''.join(bad)+record(200000))
            collector=Telemetry(path)
            with patch('sidebar_telemetry.read_local',side_effect=ValueError('offline')) as timeline:
                collector._refresh()
            timeline.assert_called_once_with(TIMELINE)
            self.assertEqual(collector.rows['SKG'][-1]['timestamp'],200000)
            self.assertFalse(collector.busy)

    def test_file_failure_backoff_is_capped_and_recovery_resets_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'missing.jsonl';collector=Telemetry(path)
            with patch('sidebar_telemetry.time.monotonic',return_value=100),patch('sidebar_telemetry.read_local',side_effect=OSError()):
                for delay in (5,10,20,40,60,60):
                    collector._refresh();self.assertEqual(collector.next_poll,100+delay)
                path.write_bytes(record());collector._refresh()
                self.assertEqual(collector.next_poll,102)

    def test_timeline_failure_backoff_and_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'usage.jsonl';path.write_bytes(record())
            collector=Telemetry(path);now=[100.0]
            with patch('sidebar_telemetry.time.monotonic',side_effect=lambda:now[0]),patch('sidebar_telemetry.read_local',side_effect=OSError()):
                for delay in (30,60,120,240,300,300):
                    collector._refresh();self.assertEqual(collector.next_trend,now[0]+delay)
                    now[0]=collector.next_trend
            with patch('sidebar_telemetry.time.monotonic',side_effect=lambda:now[0]),patch('sidebar_telemetry.read_local',return_value={}),patch('sidebar_telemetry.daily_trend',return_value={'labels':[],'providers':{}}):
                collector._refresh();self.assertEqual(collector.next_trend,now[0]+300)
            now[0]=collector.next_trend
            with patch('sidebar_telemetry.time.monotonic',side_effect=lambda:now[0]),patch('sidebar_telemetry.read_local',side_effect=OSError()):
                collector._refresh();self.assertEqual(collector.next_trend,now[0]+30)

    def test_incremental_caches_are_bounded_and_rotation_drops_old_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'usage.jsonl';path.write_bytes(b'')
            collector=Telemetry(path)
            for index in range(140):
                with path.open('ab') as source:source.write(record(index,provider=f'p{index}'))
                collector._records()
            self.assertLessEqual(len(collector.rows),128)
            for index in range(40):
                with path.open('ab') as source:source.write(record(index,provider='latest'))
                collector._records()
            self.assertEqual(len(collector.rows['latest']),32)
            replacement=Path(folder)/'replacement';replacement.write_bytes(record(500000,provider='rotated'))
            replacement.replace(path);collector._records()
            self.assertEqual(set(collector.rows),{'rotated'})
            path.write_bytes(record(1));collector._records()
            self.assertEqual(set(collector.rows),{'SKG'})

    def test_larger_in_place_rewrite_resets_incremental_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'usage.jsonl';path.write_bytes(record(1,provider='old'))
            collector=Telemetry(path);collector._records()
            path.write_bytes(record(2,provider='new')+record(3,provider='new'))
            collector._records()
            self.assertEqual(set(collector.rows),{'new'})
            self.assertEqual([r['timestamp'] for r in collector.rows['new']],[2,3])

    def test_disabled_generation_cannot_start_a_queued_fetch(self):
        fetch=Mock(return_value={'schemaVersion':3,'scope':'responses','providers':{}})
        collector=ProviderActivity(fetch=fetch)
        with patch('sidebar_activity.threading.Thread') as thread:
            collector.poll();kwargs=thread.call_args.kwargs
            collector.set_enabled(False);collector.set_enabled(True)
            kwargs['target'](*kwargs.get('args',()))
        fetch.assert_not_called()
        self.assertIsNone(collector.snapshot);self.assertFalse(collector.busy)

    def test_history_malformed_entries_are_ignored_and_rows_are_allowlisted(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history.json'
            path.write_text(json.dumps([None,1,{'state':'red','at':float('nan')},
                {'state':'amber','at':1,'unrelated':'private'}]),encoding='utf-8')
            collector=ProviderActivity(history_path=path)
            self.assertEqual(collector.events,[{'state':'amber','at':1}])

    def test_disabled_collector_does_not_read_or_fetch_and_drops_inflight_result(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history.json';fetch=Mock()
            with patch.object(Path,'open',side_effect=AssertionError('disabled read')):
                collector=ProviderActivity(fetch=fetch,history_path=path,enabled=False)
                collector.poll();collector._refresh()
            fetch.assert_not_called()
            collector.set_enabled(True)
            def disable_during_fetch():
                collector.set_enabled(False)
                return {'schemaVersion':3,'scope':'responses','providers':{'SKG':{'active':1}}}
            collector.fetch=disable_during_fetch;collector._refresh()
            self.assertIsNone(collector.snapshot);self.assertEqual(collector.events,[])
            self.assertFalse(collector.busy);self.assertFalse(path.exists())

    def test_history_size_limit_and_write_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history.json'
            path.write_bytes(b' '*65536+b'[]')
            collector=ProviderActivity(history_path=path)
            self.assertEqual(collector.events,[])
            collector.fetch=lambda:(_ for _ in ()).throw(ConnectionRefusedError())
            collector._refresh()
            with patch.object(Path,'replace',side_effect=PermissionError('busy')):
                collector._refresh()
            self.assertFalse(collector.busy)
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_telemetry_poll_deadline_busy_and_thread_start_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            collector=Telemetry(Path(folder)/'usage.jsonl')
            collector.next_poll=101
            with patch('sidebar_telemetry.time.monotonic',return_value=100),patch('sidebar_telemetry.threading.Thread') as thread:
                collector.poll();thread.assert_not_called()
                collector.next_poll=0;thread.return_value.start.side_effect=RuntimeError('unavailable')
                collector.poll();self.assertFalse(collector.busy);self.assertEqual(collector.next_poll,105)
                collector.next_poll=0;collector.busy=True;collector.poll()
                self.assertEqual(thread.call_count,1)

    def test_worker_remains_busy_until_history_write_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history.json'
            collector=ProviderActivity(fetch=lambda:(_ for _ in ()).throw(ConnectionRefusedError()),history_path=path)
            collector._refresh();collector.busy=True
            entered=threading.Event();release=threading.Event();original=Path.replace
            def slow_replace(source,target):
                entered.set()
                if not release.wait(3):raise AssertionError('test release timed out')
                return original(source,target)
            with patch.object(Path,'replace',slow_replace):
                worker=threading.Thread(target=collector._refresh);worker.start()
                try:
                    self.assertTrue(entered.wait(3))
                    # An independent thread checks busy while replacement is in progress.
                    self.assertTrue(collector.busy)
                finally:release.set();worker.join(3)
            self.assertFalse(worker.is_alive());self.assertFalse(collector.busy)
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_timeline_runtime_recheck_never_uses_unbounded_read_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'runtime-port.json').write_text(json.dumps({'pid':42,'port':10100,'attestationSecret':'a'*43}),encoding='utf-8')
            with patch('sidebar_activity.http.client.HTTPConnection') as connection,patch.object(Path,'read_bytes',side_effect=AssertionError('unbounded read')):
                response=connection.return_value.getresponse.return_value
                response.status=200;response.read.return_value=b'{}'
                self.assertEqual(read_local(TIMELINE,folder),{})
                connection.return_value.close.assert_called_once()

    def test_running_sample_hashes_each_task_identity_once(self):
        task={'id':'thread','innerId':'inner','provider':'SKG','startedAt':100}
        rows=[{'conversationId':'thread','timestamp':101000+i,'outcome':'completed'} for i in range(32)]
        with patch('live_metrics.hashlib.sha256',wraps=hashlib.sha256) as digest:
            self.assertIs(running_sample('SKG',{'samples':rows},{'tasks':{'tasks_running':[task]}}),rows[-1])
            self.assertLessEqual(digest.call_count,2)

    def test_running_sample_rejects_non_string_conversation_identity(self):
        task={'id':'thread','provider':'SKG','startedAt':100}
        for identity in ([],{},None):
            self.assertIsNone(running_sample('SKG',{'samples':[{'conversationId':identity,'timestamp':101000}]},
                {'tasks':{'tasks_running':[task]}}))


if __name__=='__main__':unittest.main()
