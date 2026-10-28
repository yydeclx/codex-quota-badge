import unittest
from sidebar_data import sync_provider_items
class ProviderItemTests(unittest.TestCase):
    def test_usage_only_fallback_preserves_existing_and_appends_new(self):
        config={'version':1,'items':[{'id':'provider_ocx','type':'opencodex_today','provider':'OCX','label':'OCX','enabled':True},{'id':'weekly_quota','type':'weekly_quota','label':'周额度','enabled':True}],'services':[],'shortcuts':[]}
        result=sync_provider_items(config,{'providers':{'Anthropic':{'totalTokens':1},'openai':{'totalTokens':2}}})
        self.assertEqual([item['provider'] for item in result['items'] if item['type']=='opencodex_today'],['OCX','Anthropic','openai'])
    def test_configured_idle_provider_is_kept_and_ordered(self):
        config={'version':1,'items':[
            {'id':'ocx','type':'opencodex_today','provider':'OCX','label':'OCX','enabled':True},
            {'id':'skg','type':'opencodex_today','provider':'SKG','label':'SKG','enabled':True},
            {'id':'gpt','type':'opencodex_today','provider':'openai','label':'ChatGPT','enabled':True},
            {'id':'weekly_quota','type':'weekly_quota','label':'周额度','enabled':True}],
            'services':[],'shortcuts':[]}
        report={'configuredProviders':['openai','SKG','OCX'],'providers':{'SKG':{},'openai':{}}}
        result=sync_provider_items(config,report)
        providers=[item['provider'] for item in result['items'] if item['type']=='opencodex_today']
        self.assertEqual(providers,['OCX','SKG','openai'])
    def test_existing_management_order_wins_over_usage_order(self):
        config={'version':1,'items':[
            {'id':'ocx','type':'opencodex_today','provider':'OCX','label':'OCX','enabled':True},
            {'id':'skg','type':'opencodex_today','provider':'SKG','label':'SKG','enabled':True},
            {'id':'gpt','type':'opencodex_today','provider':'openai','label':'ChatGPT','enabled':True},
            {'id':'weekly_quota','type':'weekly_quota','label':'周额度','enabled':True}],
            'services':[],'shortcuts':[]}
        result=sync_provider_items(config,{'providers':{'SKG':{},'openai':{},'OCX':{}}})
        providers=[item['provider'] for item in result['items'] if item['type']=='opencodex_today']
        self.assertEqual(providers,['OCX','SKG','openai'])
    def test_new_provider_appends_after_existing_providers(self):
        config={'version':1,'items':[
            {'id':'ocx','type':'opencodex_today','provider':'OCX','label':'OCX','enabled':True},
            {'id':'weekly_quota','type':'weekly_quota','label':'周额度','enabled':True}],
            'services':[],'shortcuts':[]}
        result=sync_provider_items(config,{'providers':{'Anthropic':{},'OCX':{}}})
        providers=[item['provider'] for item in result['items'] if item['type']=='opencodex_today']
        self.assertEqual(providers,['OCX','Anthropic'])
    def test_sync_returns_distinct_config_and_does_not_mutate_input(self):
        config={'version':1,'items':[
            {'id':'gpt','type':'opencodex_today','provider':'openai','label':'ChatGPT','enabled':True},
            {'id':'weekly_quota','type':'weekly_quota','label':'周额度','enabled':True}],
            'services':[],'shortcuts':[]}
        result=sync_provider_items(config,{'providers':{'openai':{}}})
        self.assertIsNot(result,config)
        self.assertEqual(config['items'][0]['label'],'ChatGPT')
        self.assertEqual(result['items'][0]['label'],'ChatGPT')
if __name__=='__main__':unittest.main()
