import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sidebar_modules import Modules,QuotaHistory,classify_task,is_subagent_source,local_url,read_tasks,module_value
from sidebar_data import validate_config,load_config
from sidebar_details import diagnostic
from sidebar_telemetry import Telemetry

class ModuleTests(unittest.TestCase):
    def test_quota_duplicate_reset_correction_and_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            h=QuotaHistory(Path(folder)/'history.json');stamp=dt.datetime(2026,9,29,10).timestamp();reset=stamp+5*86400
            def put(t,n,r=reset):h.observe({'updatedAt':t,'remaining':n,'resetsAt':r})
            put(stamp,80);initial_today=h.view()['days']['2026-09-29']['points']
            put(stamp+300,78);before_duplicate=copy.deepcopy(h.view());put(stamp+300,78)
            self.assertEqual(h.view(),before_duplicate)
            self.assertAlmostEqual(h.view()['days']['2026-09-29']['points'],initial_today+2,places=4)
            put(stamp+600,79);self.assertEqual(h.view()['corrections'],1)
            put(stamp+3000,75);self.assertEqual((h.view()['localUsed'],h.view()['otherUsed']),(12.5,12.5))
            self.assertAlmostEqual(h.view()['days']['2026-09-29']['points'],initial_today+5,places=4)
            put(stamp+3300,99,reset+86400);self.assertEqual((h.view()['localUsed'],h.view()['otherUsed']),(.5,.5))
            self.assertEqual(len(h.data['cycles']),2)
    def test_same_day_long_gap_uses_neutral_prior_without_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            h=QuotaHistory(Path(folder)/'h.json')
            t=dt.datetime(2026,10,27,9).timestamp();reset=t+6*86400
            h.observe({'updatedAt':t,'remaining':100,'resetsAt':reset})
            h.observe({'updatedAt':t+6*3600,'remaining':90,'resetsAt':reset})
            cycle=h.view()
            self.assertEqual((cycle['localUsed'],cycle['otherUsed']),(5,5))
            self.assertEqual(cycle['localDays']['2026-10-27']['points'],5)
            self.assertEqual(cycle['otherDays']['2026-10-27']['points'],5)
            self.assertEqual(cycle['days']['2026-10-27']['points'],10)
    def test_cross_midnight_drop_is_not_forced_into_today(self):
        with tempfile.TemporaryDirectory() as folder:
            h=QuotaHistory(Path(folder)/'h.json')
            today=dt.date(2026,10,28)
            before_midnight=dt.datetime.combine(today-dt.timedelta(days=1),dt.time(23,55)).timestamp()
            after_midnight=dt.datetime.combine(today,dt.time(0,5)).timestamp()
            reset=after_midnight+6*86400
            h.observe({'updatedAt':before_midnight,'remaining':100,'resetsAt':reset})
            h.observe({'updatedAt':after_midnight,'remaining':90,'resetsAt':reset})
            cycle=h.view();key=today.isoformat()
            self.assertEqual((cycle['localUsed'],cycle['otherUsed']),(5,5))
            self.assertEqual(cycle['days'][key]['points'],5)
            self.assertEqual(cycle['days'][(today-dt.timedelta(days=1)).isoformat()]['points'],5)
    def test_migration_distributes_missing_other_dates_across_the_cycle(self):
        with tempfile.TemporaryDirectory() as folder:
            today=dt.date(2026,10,28)
            last_at=dt.datetime.combine(today,dt.time(10,0)).timestamp()
            path=Path(folder)/'h.json'
            path.write_text(json.dumps({'version':4,'cycles':[{
                'reset':last_at+86400,'firstAt':last_at-86400,'lastAt':last_at,
                'firstRemaining':100,'lastRemaining':46,
                'days':{today.isoformat():{'points':40,'samples':4}},
                'localDays':{today.isoformat():{'points':40,'samples':4}},
                'otherDays':{},'localUsed':40,'otherUsed':14,'unallocated':0,
            }]}),encoding='utf-8')
            h=QuotaHistory(path);cycle=h.view();key=today.isoformat()
            self.assertGreaterEqual(cycle['days'][key]['points'],40)
            self.assertGreater(sum(row['points'] for day,row in cycle['otherDays'].items() if day!=key),0)
            self.assertEqual(cycle['otherUsed'],14)
            self.assertAlmostEqual(sum(row['points'] for row in cycle['days'].values()),54,places=4)
            h2=QuotaHistory(path);cycle2=h2.view()
            self.assertEqual(cycle2,cycle)
    def test_midnight_never_assigns_all_to_next_day(self):
        with tempfile.TemporaryDirectory() as folder:
            h=QuotaHistory(Path(folder)/'h.json');t=dt.datetime(2026,9,29,23,59).timestamp()
            h.observe({'updatedAt':t,'remaining':100,'resetsAt':t+86400})
            h.observe({'updatedAt':t+120,'remaining':98,'resetsAt':t+86400})
            cycle=h.view();self.assertEqual((cycle['localUsed'],cycle['otherUsed']),(1,1))
            self.assertEqual(cycle['days']['2026-09-29']['points'],1)
            self.assertEqual(cycle['days']['2026-09-30']['points'],1)
    def test_complete_samples_split_local_and_other_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            h=QuotaHistory(Path(folder)/'h.json');t=dt.datetime(2026,10,23,10).timestamp();reset=t+6*86400
            usage=lambda tokens,requests:{'date':'2026-10-23','totalTokens':tokens,'requests':requests,'updatedAt':t}
            h.observe({'updatedAt':t,'remaining':100,'resetsAt':reset},usage(100,1),True,True)
            h.observe({'updatedAt':t+60,'remaining':98,'resetsAt':reset},usage(200,2),True,True)
            h.observe({'updatedAt':t+120,'remaining':97,'resetsAt':reset},usage(200,2),False,True)
            cycle=h.view()
            self.assertEqual(cycle['localUsed'],2)
            self.assertEqual(cycle['otherUsed'],1)
            self.assertEqual(cycle['completeSamples'],3)
    def test_fresh_counter_growth_can_attribute_local_without_complete_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            h=QuotaHistory(Path(folder)/'h.json');t=dt.datetime(2026,10,23,10).timestamp();reset=t+6*86400
            usage={'date':'2026-10-23','totalTokens':100,'requests':1,'updatedAt':t}
            h.observe({'updatedAt':t,'remaining':100,'resetsAt':reset},usage,False,False)
            h.observe({'updatedAt':t+60,'remaining':99,'resetsAt':reset},{**usage,'totalTokens':200},False,False)
            self.assertEqual(h.view()['localUsed'],1)
            self.assertEqual(h.view()['otherUsed'],0)
    def test_history_schema_migration_preserves_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history.json';path.write_text(json.dumps({'version':1,'cycles':[{'days':{}}]}),encoding='utf-8')
            h=QuotaHistory(path)
            self.assertEqual(h.data['version'],6)
            self.assertTrue(any(p.exists() for p in (path.with_name('history.schema5-backup.json'),path.with_name('history.schema1-backup.json'))))
            self.assertEqual(h.data['cycles'][0]['localDays'],{})
    def test_legacy_unallocated_history_uses_smoothed_source_prior(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history.json'
            path.write_text(json.dumps({'version':3,'cycles':[{'days':{},'localDays':{},'otherDays':{},'localUsed':4,'otherUsed':2,'unallocated':8}]}),encoding='utf-8')
            h=QuotaHistory(path);cycle=h.view()
            self.assertEqual(h.data['version'],6)
            self.assertEqual((cycle['localUsed'],cycle['otherUsed']),(9,5))
            for key in ('unknownUsed','unknownDays','unallocated'):self.assertNotIn(key,cycle)
            self.assertEqual(sum(row['points'] for row in cycle['days'].values()),14)
    def test_delayed_settlement_inherits_local_attribution_once(self):
        with tempfile.TemporaryDirectory() as folder:
            h=QuotaHistory(Path(folder)/'h.json');t=dt.datetime(2026,10,23,10).timestamp();reset=t+6*86400
            usage={'date':'2026-10-23','totalTokens':200,'requests':2,'updatedAt':t}
            h.observe({'updatedAt':t,'remaining':100,'resetsAt':reset},usage,True,True)
            h.observe({'updatedAt':t+60,'remaining':99,'resetsAt':reset},usage,True,True,True)
            self.assertEqual(h.view()['localUsed'],1)
    def test_delayed_quota_drop_uses_recent_local_interval_without_green_dot(self):
        with tempfile.TemporaryDirectory() as folder:
            h=QuotaHistory(Path(folder)/'h.json');t=dt.datetime(2026,10,23,10).timestamp();reset=t+6*86400
            usage=lambda tokens,requests,stamp:{'date':'2026-10-23','totalTokens':tokens,'requests':requests,'updatedAt':stamp}
            h.observe({'updatedAt':t,'remaining':100,'resetsAt':reset},usage(100,1,t),False,False)
            h.observe({'updatedAt':t+60,'remaining':100,'resetsAt':reset},usage(500,2,t+60),False,False)
            h.observe({'updatedAt':t+180,'remaining':98,'resetsAt':reset},usage(500,2,t+180),False,False)
            self.assertEqual(h.view()['localUsed'],2)
            self.assertEqual(h.view()['otherUsed'],0)
    def test_task_classification(self):
        self.assertEqual(classify_task({'status':'inProgress'},[{'status':'waitingForApproval'}]),'tasks_pending')
        self.assertEqual(classify_task({'status':'inProgress'},[{'status':'completed','tool':'request_user_input'}]),'tasks_running')
        self.assertEqual(classify_task({'status':'completed'},[],True),'tasks_done')
        self.assertIsNone(classify_task({'status':'completed'},[],False))
        self.assertIsNone(classify_task({'status':'interrupted'},[],True))
    def test_subagent_source_is_filtered_by_structured_marker(self):
        source=json.dumps({'subagent':{'thread_spawn':{'parent_thread_id':'parent','depth':1}}})
        self.assertTrue(is_subagent_source(source))
        self.assertFalse(is_subagent_source('vscode'))
        self.assertFalse(is_subagent_source('{invalid'))
    def test_background_snapshot_keeps_parent_tasks_and_subagent_tasks_separate(self):
        m=Modules()
        all_tasks={'tasks_pending':[],'tasks_running':[
            {'id':'parent','isSubagent':False,'model':'SKG/gpt-5.6-sol'},
            {'id':'worker','isSubagent':True,'model':'OCX/glm-5.3'}],
            'tasks_done':[],'unverified':[]}
        with patch('sidebar_modules.read_tasks',return_value=all_tasks),patch('sidebar_modules.service_status',return_value={}):
            m._refresh({'services':[]})
        snapshot=m.view()
        self.assertEqual([x['id'] for x in snapshot['tasks']['tasks_running']],['parent'])
        self.assertEqual([x['id'] for x in snapshot['subagentTasks']['tasks_running']],['worker'])
    def test_unobservable_pending_is_not_zero(self):
        import time
        snapshot={'updatedAt':time.time(),'tasks':{'tasks_pending':[],'tasks_running':[]}}
        self.assertEqual(module_value('tasks_pending',snapshot,{}),'—')
        self.assertEqual(module_value('tasks_running',snapshot,{}),'0')
    def test_services_loopback_only(self):
        self.assertEqual(local_url('http://127.0.0.1:1234/health'),('127.0.0.1',1234))
        for url in ('http://example.com','http://u:p@localhost','file:///etc','http://127.0.0.1.evil.com'):
            with self.assertRaises(ValueError):local_url(url)
    def test_diagnostics_are_allowlisted(self):
        s={'chains':{'SKG':{'latest':{'status':429,'secret':'do-not-copy','prompt':'do-not-copy'},'requestBody':'do-not-copy'}}}
        value=diagnostic(s,'SKG');self.assertNotIn('do-not-copy',value);self.assertIn('429',value)
    def test_new_config_and_legacy(self):
        config=load_config();before=copy.deepcopy(config);validate_config(config)
        self.assertEqual(config['items'],before['items'])
        for kind in ('tasks_running','tasks_pending','tasks_done','services','shortcuts'):
            if not any(i['type']==kind for i in config['items']):config['items'].append({'id':kind,'type':kind,'label':'模块','enabled':False})
        validate_config(config)
    def test_speed_and_retry_privacy(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'usage.jsonl';row={'provider':'SKG','timestamp':100000,'status':200,'durationMs':3000,'firstOutputMs':1000,'usage':{'outputTokens':120},'servedModel':'gpt-test','attempts':[{'sendCount':2}],'body':'PRIVATE'}
            p.write_text(json.dumps(row)+'\n',encoding='utf-8');t=Telemetry(p);t._records();r=t.rows['SKG'][0]
            self.assertEqual(r['decodeTokPerSecond'],60);self.assertEqual(r['retries'],1);self.assertNotIn('PRIVATE',json.dumps(r))
            row['durationMs']=1500;p.write_text(json.dumps(row)+'\n',encoding='utf-8');t._records();self.assertNotIn('decodeTokPerSecond',t.rows['SKG'][0])

if __name__=='__main__':unittest.main()
