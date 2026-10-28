import base64
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sidebar_activity import ACTIVITY_PATH,ProviderActivity,activity_from_logs,read_activity,read_local
from sidebar_telemetry import Telemetry
class ActivityTests(unittest.TestCase):
    def test_trend_uses_local_timezone_for_freshness(self):
        import datetime as dt
        today=dt.datetime.now().astimezone().date();labels=[(today-dt.timedelta(days=6-i)).strftime('%m/%d') for i in range(7)]
        t=Telemetry();t.trend={'labels':labels,'providers':{'openai':[1,2,3,4,5,6,7]}};t.trend_at=__import__('time').monotonic();t.records_at=t.trend_at
        view=t.view('openai')
        self.assertEqual(view['trend']['values'],[1,2,3,4,5,6,7])
    def test_silent_active_and_stale(self):
        now=[100]
        data={'schemaVersion':1,'scope':'responses','providers':{'SKG':{'active':2},'openai':{'active':1}}}
        a=ProviderActivity(lambda:data,lambda:now[0]);a._refresh()
        self.assertTrue(a.active('SKG'));self.assertTrue(a.active('openai'))
        for i in range(40):
            now[0]+=1;a._refresh();self.assertTrue(a.active('SKG'))
        now[0]+=4;self.assertIsNone(a.count('SKG'))
        data['providers']['SKG']['active']=0;a._refresh()
        self.assertFalse(a.active('SKG'));self.assertTrue(a.active('openai'))
    def test_error_and_invalid_are_unknown(self):
        a=ProviderActivity(lambda:{'schemaVersion':1,'scope':'responses','providers':{'SKG':{'active':-1}}})
        a._refresh();self.assertIsNone(a.count('SKG'))
        a.fetch=lambda:(_ for _ in ()).throw(OSError())
        a._refresh();self.assertIsNone(a.count('SKG'));self.assertFalse(a.active('SKG'))
    def test_output_waiting_and_idle(self):
        now=[100.0]
        d={'schemaVersion':2,'scope':'responses','generatedAt':100000,'providers':{'openai':{'active':1,'lastOutputAt':None}}}
        a=ProviderActivity(lambda:d,lambda:now[0]);a._refresh()
        self.assertEqual(a.state('openai'),'waiting')
        d['providers']['openai']['lastOutputAt']=100000;a._refresh()
        self.assertEqual(a.state('openai'),'output')
        now[0]+=2.1;self.assertEqual(a.state('openai'),'waiting')
        d['providers']['openai']['active']=0;a._refresh()
        self.assertEqual(a.state('openai'),'idle')
        now[0]+=4;self.assertEqual(a.state('openai'),'unknown')
    def test_no_overlapping_poll(self):
        a=ProviderActivity();a.busy=True;a.poll();self.assertIsNone(a.snapshot)
    def test_finished_log_adapter_never_infers_inflight_requests(self):
        now=1000000
        rows=[
            {'requestId':'active','provider':'SKG','timestamp':now-10000,'durationMs':9500,'terminalStatus':None,'lastOutputMs':9000},
            {'requestId':'done','provider':'OCX','timestamp':now-10000,'durationMs':9500,'terminalStatus':'completed'},
            {'requestId':'old-null','provider':'openai','timestamp':now-200000,'durationMs':10000,'terminalStatus':None},
            {'requestId':'waiting','provider':'OCX','timestamp':now-30000,'durationMs':None,'terminalStatus':None},
        ]
        result=activity_from_logs({'logs':rows},now)
        self.assertEqual(result['providers'],{})
        self.assertEqual(result['requests'],[])
        self.assertFalse(result['supportsLive'])
        self.assertEqual(result['source'],'api/logs')
    def test_provider_activity_records_compatibility_source(self):
        data={'schemaVersion':3,'scope':'responses','providers':{},'requests':[],'source':'api/logs'}
        a=ProviderActivity(lambda:data);a._refresh()
        self.assertEqual(a.source,'api/logs')
    def test_401_and_404_use_log_compatibility_path(self):
        expected={'schemaVersion':3,'scope':'responses','providers':{},'requests':[],'source':'api/logs'}
        for code in ('HTTP_401','HTTP_404'):
            with self.subTest(code=code),patch('sidebar_activity.read_local',side_effect=RuntimeError(code)),patch('sidebar_activity._read_logs',return_value=expected) as fallback:
                self.assertIs(read_activity(),expected);fallback.assert_called_once()
    def test_other_failures_do_not_use_log_fallback(self):
        with patch('sidebar_activity.read_local',side_effect=RuntimeError('HTTP_500')),patch('sidebar_activity._read_logs') as fallback:
            with self.assertRaisesRegex(RuntimeError,'HTTP_500'):read_activity()
            fallback.assert_not_called()
    def test_log_terminal_and_five_second_boundaries(self):
        now=1000000
        rows=[
            {'provider':'SKG','timestamp':now-10000,'durationMs':5001,'terminalStatus':None},
            {'provider':'OCX','timestamp':now-10000,'durationMs':4999,'terminalStatus':None},
            {'provider':'openai','timestamp':now-1000,'durationMs':2000,'terminalStatus':'completed'},
        ]
        result=activity_from_logs({'logs':rows},now)
        self.assertEqual(result['providers'],{});self.assertEqual(result['requests'],[])

class GatewayActivityTests(unittest.TestCase):
    def sample(self,**values):
        return {'schemaVersion':3,'scope':'responses','source':'provider-activity',
                'supportsLive':True,'generatedAt':100000,'providers':{'SKG':{'active':1}},
                'requests':[{'provider':'SKG','requestId':'r','startedAt':98000}],**values}

    def test_success_poll_is_one_second_and_failures_back_off_and_reset(self):
        now=[100.0];a=ProviderActivity(lambda:self.sample(),lambda:now[0])
        a._refresh();self.assertEqual(a.next_poll,101)
        a.fetch=lambda:(_ for _ in ()).throw(TimeoutError())
        for delay in (5,10,20,30,30):
            a._refresh();self.assertEqual(a.next_poll-now[0],delay)
            now[0]=a.next_poll
        a.fetch=lambda:self.sample();a._refresh()
        self.assertEqual(a.failures,0);self.assertEqual(a.next_poll-now[0],1)
        a.fetch=lambda:(_ for _ in ()).throw(RuntimeError('HTTP_404'))
        a._refresh();self.assertEqual(a.next_poll-now[0],60)

    def test_failed_refresh_keeps_original_sample_for_only_three_seconds(self):
        now=[100.0];a=ProviderActivity(lambda:self.sample(),lambda:now[0]);a._refresh()
        now[0]=101;a.fetch=lambda:(_ for _ in ()).throw(TimeoutError());a._refresh()
        self.assertEqual(a.count('SKG'),1)
        self.assertTrue(a.details('SKG')['stale'])
        self.assertEqual(a.details('SKG')['ageMs'],1000)
        now[0]=103;self.assertEqual(a.count('SKG'),1)
        now[0]=103.001
        self.assertIsNone(a.count('SKG'));self.assertEqual(a.state('SKG'),'unknown')
        details=a.details('SKG')
        self.assertFalse(details['available']);self.assertEqual(details['requests'],[])
        self.assertEqual(details['finishedRequests'],[])

    def test_poll_deadline_and_busy_gate_do_not_spawn_threads(self):
        now=[100.0];a=ProviderActivity(lambda:self.sample(),lambda:now[0]);a._refresh()
        with patch('sidebar_activity.threading.Thread') as thread:
            for _ in range(20):a.poll()
            thread.assert_not_called()
            now[0]=101;a.poll();thread.assert_called_once()
            self.assertTrue(a.busy)
            a.poll();thread.assert_called_once()

    def test_missing_provider_is_unknown_and_explicit_zero_is_idle(self):
        a=ProviderActivity(lambda:self.sample(providers={'OCX':{'active':0}}));a._refresh()
        self.assertIsNone(a.count('SKG'));self.assertEqual(a.state('SKG'),'unknown')
        self.assertEqual(a.count('OCX'),0);self.assertEqual(a.state('OCX'),'idle')

    def test_legacy_schemas_and_log_fallback_preserve_capability(self):
        for version in (1,2,3):
            with self.subTest(version=version):
                data={'schemaVersion':version,'scope':'responses','providers':{'SKG':{'active':1}}}
                a=ProviderActivity(lambda:data);a._refresh()
                self.assertEqual(a.count('SKG'),1)
                self.assertEqual(a.details('SKG')['detailed'],version==3)
        # Even unexpected extra records in the logs adapter cannot become live.
        data=self.sample(source='api/logs',supportsLive=True)
        a=ProviderActivity(lambda:data,lambda:100);a._refresh()
        details=a.details('SKG')
        self.assertTrue(details['available']);self.assertFalse(details['supportsLive'])
        self.assertFalse(details['detailed']);self.assertEqual(details['requests'],[])
        self.assertIsNone(a.count('SKG'));self.assertEqual(a.state('SKG'),'unknown')
        self.assertEqual(a.next_poll,105)

    def test_samples_are_bounded_copied_and_provider_scoped(self):
        rows=[{'provider':'SKG','requestId':str(i)} for i in range(200)]
        finished=[{'provider':'OCX','requestId':str(i)} for i in range(40)]
        data=self.sample(requests=rows,finishedRequests=finished)
        a=ProviderActivity(lambda:data);a._refresh()
        data['providers']['SKG']['active']=0;rows[0]['requestId']='changed'
        self.assertEqual(a.count('SKG'),1)
        self.assertEqual(len(a.details('SKG')['requests']),128)
        details=a.details('SKG');self.assertEqual(details['requests'][0]['requestId'],'0')
        details['requests'][0]['requestId']='ui-change'
        self.assertEqual(a.details('SKG')['requests'][0]['requestId'],'0')
        self.assertEqual(a.details('SKG')['finishedRequests'],[])
        self.assertEqual(len(a.details('OCX')['finishedRequests']),32)

    def test_malformed_schema_or_collections_are_unavailable(self):
        for data in (None,self.sample(schemaVersion=True),self.sample(requests=None),
                     self.sample(finishedRequests={}),self.sample(supportsLive=1),
                     self.sample(providers={'SKG':{'active':None}})):
            with self.subTest(data=data):
                a=ProviderActivity(lambda:data);a._refresh()
                self.assertFalse(a.details('SKG')['available']);self.assertIsNone(a.count('SKG'))
        a=ProviderActivity(lambda:self.sample(requests=[None,7,{'provider':'SKG'}]));a._refresh()
        self.assertEqual(a.details('SKG')['requests'],[{'provider':'SKG'}])

    def test_invalid_and_future_provider_timestamps_never_claim_output(self):
        for stamp in (True,float('nan'),float('inf'),-1,101000):
            with self.subTest(stamp=stamp):
                a=ProviderActivity(lambda:self.sample(providers={'SKG':{'active':1,'lastOutputAt':stamp}}))
                a._refresh();self.assertEqual(a.state('SKG'),'waiting')

    def test_hmac_read_remains_short_lived_pid_bound_and_bounded(self):
        secret='a'*43
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'runtime-port.json').write_text(json.dumps({'pid':42,'port':10100,'attestationSecret':secret}),encoding='utf-8')
            # 160 scalar summaries can exceed the former 64 KiB response limit.
            data=self.sample(pid=42,padding='x'*70000)
            with patch('sidebar_activity.http.client.HTTPConnection') as connection,patch('sidebar_activity.time.time',return_value=100),patch('sidebar_activity.secrets.token_urlsafe',return_value='nonce'):
                conn=connection.return_value;response=conn.getresponse.return_value
                response.status=200;response.read.return_value=json.dumps(data).encode()
                self.assertEqual(read_local(home=folder)['pid'],42)
                connection.assert_called_once_with('127.0.0.1',10100,timeout=.8)
                method,path=conn.request.call_args.args
                self.assertEqual((method,path),('GET',ACTIVITY_PATH))
                headers=conn.request.call_args.kwargs['headers']
                self.assertEqual(headers['x-opencodex-local-expires-at'],'105000')
                payload=f'opencodex-local-management-read-v1\nnonce\nGET\n{ACTIVITY_PATH}\n42\n10100\n105000'
                signature=base64.urlsafe_b64encode(hmac.new(secret.encode(),payload.encode(),hashlib.sha256).digest()).decode().rstrip('=')
                self.assertEqual(headers['x-opencodex-local-capability'],signature)
                response.read.assert_called_once_with(256*1024+1);conn.close.assert_called_once()

    def test_hmac_read_rejects_wrong_pid_and_oversized_body(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'runtime-port.json').write_text(json.dumps({'pid':42,'port':10100,'attestationSecret':'a'*43}),encoding='utf-8')
            for body,message in ((json.dumps(self.sample(pid=43)).encode(),'Runtime identity changed'),
                                 (b'x'*(256*1024+1),'Oversized response')):
                with self.subTest(message=message),patch('sidebar_activity.http.client.HTTPConnection') as connection:
                    conn=connection.return_value;response=conn.getresponse.return_value
                    response.status=200;response.read.return_value=body
                    with self.assertRaisesRegex(ValueError,message):read_local(home=folder)
                    conn.close.assert_called_once()

if __name__=='__main__':unittest.main()
