import datetime as dt
import unittest
from sidebar_data import compact_tokens,parse_usage,item_usage


class UsageTests(unittest.TestCase):
    def test_realtime_mode_default_and_strict_boolean(self):
        from sidebar_data import validate_config
        base={'version':1,'items':[{'id':'weekly','type':'weekly_quota','label':'周额度'}]}
        import copy
        self.assertTrue(validate_config(copy.deepcopy(base))['realtimeMetricsEnabled'])
        for mode in (True,False):
            self.assertIs(validate_config(dict(copy.deepcopy(base),realtimeMetricsEnabled=mode))['realtimeMetricsEnabled'],mode)
        for invalid in (0,1,'true','false',None,[],{}):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                validate_config(dict(copy.deepcopy(base),realtimeMetricsEnabled=invalid))

    def test_realtime_mode_roundtrip_isolated_from_live_config(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from sidebar_data import load_config,save_config
        with tempfile.TemporaryDirectory() as directory,patch('sidebar_data.CONFIG',Path(directory)/'config.json'):
            for mode in (True,False):
                save_config({'version':1,'items':[{'id':'weekly','type':'weekly_quota','label':'周额度'}],
                             'realtimeMetricsEnabled':mode})
                self.assertIs(load_config()['realtimeMetricsEnabled'],mode)

    def test_integer_units_and_promotion(self):
        for raw,expected in [(None,'—'),(0,'0'),(999,'999'),(1000,'1K'),(123456789,'123M'),(999500,'1M'),(999500000,'1B')]:
            self.assertEqual(compact_tokens(raw),expected)

    def test_cache_not_double_counted(self):
        now=dt.datetime.now().astimezone()
        report={'range':'today','generatedAt':now.timestamp()*1000,'providers':[
            {'provider':'SKG','totalTokens':110,'inputTokens':100,'outputTokens':10,'cachedInputTokens':80,'requests':3,'reportedRequests':2}]}
        data=parse_usage(report)
        self.assertEqual(item_usage(data,'SKG',now)['totalTokens'],110)
        self.assertEqual(item_usage(data,'openai',now)['totalTokens'],0)
        self.assertIsNone(item_usage(data,'SKG',now+dt.timedelta(days=1)))
        self.assertIsNone(item_usage(data,'SKG',now+dt.timedelta(seconds=181)))
        data['partial']=True
        self.assertIsNone(item_usage(data,'openai',now))
        data['providers']['SKG']['reportedRequests']=0
        self.assertIsNone(item_usage(data,'SKG',now))

    def test_30day_range_and_missing_reports(self):
        now=dt.datetime.now().astimezone()
        report={'range':'30d','generatedAt':now.timestamp()*1000,'providers':[{'provider':'openai','totalTokens':321,'requests':5,'reportedRequests':4}]}
        with self.assertRaises(ValueError):parse_usage(report)
        data=parse_usage(report,'30d')
        self.assertEqual(data['range'],'30d')
        self.assertEqual(item_usage(data,'openai',now,max_age=900)['totalTokens'],321)
        self.assertEqual(item_usage(data,'openai',now,max_age=900)['reportedRequests'],4)
        self.assertIsNone(item_usage(data,'openai',now+dt.timedelta(seconds=901),max_age=900))

    def test_bad_response_rejected(self):
        with self.assertRaises(ValueError):parse_usage({'range':'all','providers':[]})
    def test_horizontal_offset_defaults_and_bounds(self):
        config={'version':1,'items':[{'id':'weekly','type':'weekly_quota','label':'周额度','enabled':True}], 'services':[],'shortcuts':[]}
        from sidebar_data import validate_config
        self.assertEqual(validate_config(config)['horizontalOffset'],0)
        config['horizontalOffset']=40;self.assertEqual(validate_config(config)['horizontalOffset'],40)
        config['horizontalOffset']=41
        with self.assertRaises(ValueError):validate_config(config)


if __name__=='__main__':unittest.main()
