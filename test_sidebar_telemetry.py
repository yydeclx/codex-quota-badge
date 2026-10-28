import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from sidebar_activity import ProviderActivity
from sidebar_telemetry import Telemetry, outcome, request_health, daily_trend, display_request_index

class TelemetryTests(unittest.TestCase):
    def row(self,status=200,**kw):
        value={'timestamp':100000,'status':status,'durationMs':2500,**kw}
        value['outcome']=outcome(value)
        return value
    def test_semantic_outcome_over_http(self):
        self.assertEqual(outcome(self.row(200,terminalStatus='failed')),'failed')
        self.assertEqual(outcome(self.row(200,terminalStatus='incomplete')),'incomplete')
        self.assertEqual(outcome(self.row(200,closeReason='client_cancel')),'aborted')
        self.assertEqual(outcome(self.row(499)),'aborted')
    def test_health_recovery_expiry_cancel(self):
        self.assertEqual(request_health([self.row(429)],101)[0],'amber')
        self.assertEqual(request_health([self.row(401)],101)[0],'red')
        self.assertEqual(request_health([self.row(500)]*3,101)[0],'red')
        self.assertIsNone(request_health([self.row(500),self.row()],101)[0])
        self.assertIsNone(request_health([self.row(499)],101)[0])
        self.assertIsNone(request_health([self.row(401)],401)[0])
    def test_no_output_cancel_does_not_hide_recent_completed_output(self):
        rows=[
            self.row(200,timestamp=100000,conversationId='same',firstOutputMs=1000,decodeTokPerSecond=75),
            self.row(499,timestamp=170000,conversationId='same',closeReason='client_cancel'),
        ]
        self.assertEqual(display_request_index(rows),0)
        rows[-1]['firstOutputMs']=500
        self.assertEqual(display_request_index(rows),1)
        rows[-1]['firstOutputMs']=None;rows[-1]['conversationId']='other'
        self.assertEqual(display_request_index(rows),1)
    def test_disconnect_debounce_and_reconnect(self):
        def disconnected():raise ConnectionRefusedError()
        a=ProviderActivity(disconnected);a._refresh();self.assertIsNone(a.health()[0])
        a._refresh();self.assertEqual(a.health()[0],'red')
        a.fetch=lambda:{'schemaVersion':2,'scope':'responses','providers':{}}
        a._refresh();self.assertIsNone(a.health()[0])
    def test_recent_tail_and_partial_line(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'usage.jsonl'
            path.write_bytes((json.dumps({'provider':'openai-pabcdef',**self.row()})+'\n{"provider":').encode())
            t=Telemetry(path);t._records()
            self.assertEqual(len(t.rows['openai']),1)
            self.assertEqual(set(t.rows['openai'][0]),{'timestamp','status','durationMs','outcome','retries'})
    def test_local_days_and_provider_merge(self):
        now=dt.datetime.now().astimezone();end=(int(now.timestamp())//3600+1)*3600
        data={'start':end-168*3600,'buckets':168,'bucketSeconds':3600,'metric':'total','aggregation':'sum','series':[{'provider':'openai','points':[1]*168},{'provider':'openai-pabcdef','points':[2]*168}]}
        trend=daily_trend(data,now)
        self.assertEqual(len(trend['labels']),7)
        self.assertEqual(trend['providers']['openai'][1],72)
        self.assertEqual(trend['providers']['openai'][-1],(now.hour+1)*3)
        # The service may mark measurements incomplete while still returning
        # all 168 buckets and full vectors. That response remains drawable;
        # malformed short vectors must still be rejected.
        data['truncated']=True
        self.assertEqual(daily_trend(data,now)['providers']['openai'][1],72)
        data['series'][0]['points']=data['series'][0]['points'][:-1]
        with self.assertRaises(ValueError):daily_trend(data,now)

if __name__=='__main__':unittest.main()
