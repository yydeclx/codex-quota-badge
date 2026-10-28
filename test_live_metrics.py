import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from live_metrics import request_metrics,running_sample
from sidebar_activity import ProviderActivity,activity_from_logs
from sidebar_plugin import SidebarPlugin
from sidebar_telemetry import Telemetry


class LiveMetricsTests(unittest.TestCase):
    def details(self,**values):
        request={'requestId':'a','startedAt':1000000,**values}
        return {'generatedAt':1008000,'ageMs':0,'requests':[request]}

    def test_waiting_advances_from_sample_and_monotonic_age(self):
        details=self.details()
        self.assertEqual(request_metrics(details)['first'],'等待 8.0s')
        details['ageMs']=500
        self.assertEqual(request_metrics(details)['first'],'等待 8.5s')

    def test_first_is_fixed_and_not_replaced_by_last_output(self):
        details=self.details(firstOutputMs=2000,lastOutputMs=6000,outputTokens=80)
        self.assertEqual(request_metrics(details)['first'],'2.0s')
        details['requests'][0]['lastOutputMs']=8000
        self.assertEqual(request_metrics(details)['first'],'2.0s')

    def test_missing_first_is_unknown_after_output(self):
        details=self.details(lastOutputMs=7000,outputTokens=80)
        metrics=request_metrics(details)
        self.assertEqual(metrics['first'],'未提供')
        self.assertNotIn('tok/s',metrics['output'])

    def test_frozen_counter_does_not_slow_down_on_repaint(self):
        details=self.details(firstOutputMs=2000,lastOutputMs=6000,outputTokens=80)
        self.assertEqual(request_metrics(details)['output'],'约 20 tok/s')
        details['ageMs']=2500
        self.assertEqual(request_metrics(details)['output'],'约 20 tok/s')
        self.assertEqual(request_metrics(details)['state'],'waiting')

    def test_generation_window_and_waiting_after_output(self):
        details=self.details(firstOutputMs=4000,genStartMs=2000,lastOutputMs=6000,outputTokens=80)
        self.assertEqual(request_metrics(details)['output'],'约 20 tok/s')
        details['requests'][0]['outputTokens']=None
        self.assertEqual(request_metrics(details)['output'],'未提供')

    def test_newest_request_drives_both_state_and_metrics(self):
        details=self.details(firstOutputMs=2000,lastOutputMs=8000,outputTokens=80)
        details['requests'].append({'requestId':'b','startedAt':1007000})
        metrics=request_metrics(details)
        self.assertEqual(metrics['request']['requestId'],'b')
        self.assertEqual(metrics['state'],'waiting')
        self.assertEqual(metrics['first'],'等待 1.0s')
        self.assertEqual(metrics['output'],'—')

    def test_invalid_values_do_not_become_valid_measurements(self):
        for value in (True,-1,float('nan'),float('inf')):
            with self.subTest(value=value):
                details=self.details(firstOutputMs=value,lastOutputMs=7000,outputTokens=value)
                metrics=request_metrics(details)
                self.assertEqual(metrics['first'],'未提供')
                self.assertNotIn('tok/s',metrics['output'])

    def test_details_capture_sample_age_in_one_snapshot(self):
        now=[1.0]
        activity=ProviderActivity(lambda:{'schemaVersion':3,'scope':'responses','generatedAt':1008000,
            'providers':{'SKG':{'active':1}},'requests':[{'provider':'SKG','startedAt':1000000}]},lambda:now[0])
        activity._refresh();now[0]=1.5
        details=activity.details('SKG')
        self.assertEqual(details['generatedAt'],1008000)
        self.assertEqual(details['ageMs'],500)
        self.assertEqual(request_metrics(details)['first'],'等待 8.5s')
        now[0]=5
        self.assertIsNone(request_metrics(activity.details('SKG')))

    def test_finished_logs_never_masquerade_as_inflight_samples(self):
        rows=[{'provider':'SKG','timestamp':1000000,'durationMs':None,'terminalStatus':'incomplete'},
              {'provider':'SKG','timestamp':1000000,'durationMs':8000,'lastOutputMs':7000},
              {'provider':'SKG','timestamp':1000000,'durationMs':8000,'lastOutputMs':1000}]
        data=activity_from_logs({'logs':rows},1008000)
        self.assertEqual(data['providers'],{})
        self.assertEqual(data['requests'],[])
        self.assertFalse(data['supportsLive'])

    def test_completed_requests_are_visible_within_the_same_running_turn(self):
        snapshot={'tasks':{'tasks_running':[{'id':'t','innerId':'inner','provider':'openai','model':'SKG/m','startedAt':1000}]}}
        row={'outcome':'completed','timestamp':1001000,'conversationId':'inner','decodeTokPerSecond':20}
        self.assertIs(running_sample('SKG',{'recent':[row]},snapshot),row)
        self.assertIsNone(running_sample('openai',{'recent':[row]},snapshot))
        row['conversationId']=hashlib.sha256(b'inner').hexdigest()[:32]
        self.assertIs(running_sample('SKG',{'recent':[row]},snapshot),row)
        row['conversationId']='other'
        self.assertIsNone(running_sample('SKG',{'recent':[row]},snapshot))
        row['conversationId']='inner';row['timestamp']=999999
        self.assertIsNone(running_sample('SKG',{'recent':[row]},snapshot))

    def test_log_ids_and_generation_window_survive_the_allowlist(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'usage.jsonl'
            path.write_text(json.dumps({'provider':'SKG','timestamp':1000000,'status':200,
                'durationMs':10000,'firstOutputMs':4000,'genStartMs':2000,'lastOutputMs':6000,
                'conversationId':'01a1810f-07d7-7441-b7fb-696712c83807','requestId':'ocx-a',
                'usage':{'outputTokens':80},'prompt':'never retain'})+'\n',encoding='utf-8')
            telemetry=Telemetry(path);telemetry._records();row=telemetry.rows['SKG'][0]
            self.assertEqual(row['decodeTokPerSecond'],20)
            self.assertEqual(row['requestId'],'ocx-a')
            self.assertIn('conversationId',row)
            self.assertNotIn('prompt',row)

    def test_newer_failed_request_is_not_hidden_by_an_older_success(self):
        snapshot={'tasks':{'tasks_running':[{'id':'t','provider':'SKG','startedAt':1000}]}}
        success={'outcome':'completed','timestamp':1001000,'conversationId':'t','decodeTokPerSecond':20}
        for outcome in ('failed','incomplete','aborted'):
            failure={'outcome':outcome,'timestamp':1002000,'conversationId':'t','firstOutputMs':500}
            self.assertIs(running_sample('SKG',{'recent':[success,failure]},snapshot),failure)

    def test_current_sample_survives_six_interleaved_other_conversations(self):
        snapshot={'tasks':{'tasks_running':[{'id':'t','provider':'SKG','startedAt':1000}]}}
        row={'outcome':'completed','timestamp':1001000,'conversationId':'t'}
        others=[{'outcome':'completed','timestamp':1002000+i,'conversationId':'other'} for i in range(7)]
        self.assertIs(running_sample('SKG',{'recent':others[-6:],'samples':[row]+others},snapshot),row)


class GatewayMetricsTests(unittest.TestCase):
    def details(self,**values):
        return {'generatedAt':1008000,'ageMs':0,'supportsLive':True,
                'requests':[{'requestId':'r','startedAt':1000000,'durationMs':8000,**values}]}

    def test_zero_counter_before_first_delta_is_still_waiting(self):
        for timing in ({},{'firstOutputMs':None,'lastOutputMs':None,'genStartMs':None}):
            metrics=request_metrics(self.details(outputTokens=0,**timing))
            self.assertEqual(metrics['first'],'等待 8.0s')
            self.assertEqual(metrics['output'],'—');self.assertEqual(metrics['state'],'waiting')

    def test_estimated_character_counter_displays_an_approximate_rate(self):
        details=self.details(outputTokens=80,firstOutputMs=2000,genStartMs=2000,
                             lastOutputMs=8000,estimated=True,usageStatus='estimated',usageSource='characters')
        metrics=request_metrics(details)
        self.assertEqual(metrics['output'],'约 13 tok/s')
        self.assertTrue(metrics['request']['estimated'])
        self.assertEqual(metrics['request']['usageSource'],'characters')
        self.assertFalse(metrics['finished'])

    def test_relative_timing_survives_wall_clock_jumps_and_offset(self):
        for stamp in (1,1008000,999999999999):
            details=self.details(outputTokens=80,firstOutputMs=2000,lastOutputMs=8000)
            details['generatedAt']=stamp
            metrics=request_metrics(details)
            self.assertEqual(metrics['first'],'2.0s');self.assertEqual(metrics['state'],'output')
            self.assertEqual(metrics['output'],'约 13 tok/s')
            details['ageMs']=2100
            self.assertEqual(request_metrics(details)['state'],'waiting')
            self.assertEqual(request_metrics(details)['output'],'约 13 tok/s')

    def test_clock_regression_without_relative_duration_does_not_make_zero_measurements(self):
        details=self.details(durationMs=None,outputTokens=0);details['generatedAt']=999000
        metrics=request_metrics(details)
        self.assertEqual(metrics['first'],'等待首字…');self.assertEqual(metrics['output'],'—')
        details['requests'][0].update(lastOutputAt=999500,outputTokens=None)
        self.assertEqual(request_metrics(details)['first'],'等待首字…')

    def test_missing_counter_or_generation_window_never_becomes_zero_speed(self):
        for fields in ({'outputTokens':None,'firstOutputMs':2000,'lastOutputMs':8000},
                       {'outputTokens':80,'lastOutputMs':8000},
                       {'outputTokens':80,'firstOutputMs':8000,'lastOutputMs':8000},
                       {'outputTokens':80,'firstOutputMs':2000,'lastOutputMs':9000},
                       {'outputTokens':80,'firstOutputMs':2000,'lastOutputMs':8000,'usageStatus':'unsupported'}):
            with self.subTest(fields=fields):
                metrics=request_metrics(self.details(**fields))
                self.assertNotIn('tok/s',metrics['output'])

    def test_same_time_concurrent_requests_use_a_deterministic_single_sample(self):
        details=self.details(outputTokens=80,firstOutputMs=2000,lastOutputMs=8000)
        details['requests'].append({'requestId':'z','startedAt':1000000,'durationMs':8000,'outputTokens':0})
        for rows in (details['requests'][:],list(reversed(details['requests']))):
            metrics=request_metrics({**details,'requests':rows})
            self.assertEqual(metrics['request']['requestId'],'z')
            self.assertEqual(metrics['output'],'—');self.assertEqual(metrics['first'],'等待 8.0s')

    def test_terminal_summary_preserves_final_rate_without_claiming_output(self):
        details=self.details(outputTokens=80,firstOutputMs=2000,lastOutputMs=6000)
        details['finishedRequests']=details.pop('requests');details['requests']=[]
        metrics=request_metrics(details)
        self.assertTrue(metrics['finished']);self.assertEqual(metrics['state'],'idle')
        self.assertEqual(metrics['first'],'2.0s');self.assertEqual(metrics['output'],'约 20 tok/s')
        details['ageMs']=2500
        self.assertEqual(request_metrics(details),metrics)
        details['requests']=[{'requestId':'new','startedAt':1007000,'durationMs':1000,'outputTokens':0}]
        metrics=request_metrics(details)
        self.assertFalse(metrics['finished']);self.assertEqual(metrics['request']['requestId'],'new')
        self.assertEqual(metrics['output'],'—')

    def test_terminal_no_output_does_not_keep_advancing_first_byte_wait(self):
        details=self.details(outputTokens=0)
        details['finishedRequests']=details.pop('requests');details['ageMs']=2000
        metrics=request_metrics(details)
        self.assertEqual(metrics['first'],'未提供');self.assertEqual(metrics['state'],'idle')
        self.assertEqual(metrics['output'],'—')

    def test_unavailable_or_non_live_payload_cannot_supply_live_metrics(self):
        for flags in ({'available':False},{'supportsLive':False}):
            details=self.details(outputTokens=80,firstOutputMs=2000,lastOutputMs=8000)
            self.assertIsNone(request_metrics({**details,**flags}))

    def test_first_delta_at_zero_is_a_valid_frozen_first_byte(self):
        details=self.details(outputTokens=80,firstOutputMs=0,genStartMs=0,lastOutputMs=8000)
        self.assertEqual(request_metrics(details)['first'],'0.0s')
        self.assertEqual(request_metrics(details)['output'],'约 10 tok/s')

    def test_activity_to_metrics_wait_output_finish_and_expiry(self):
        now=[100.0]
        request={'provider':'SKG','requestId':'r','conversationId':hashlib.sha256(b'thread').hexdigest()[:32],
                 'startedAt':1000000,'durationMs':2000,'outputTokens':0,'firstOutputMs':None,
                 'genStartMs':None,'lastOutputMs':None,'estimated':True,
                 'usageStatus':'estimated','usageSource':'characters','retries':1}
        data={'schemaVersion':3,'scope':'responses','source':'provider-activity','supportsLive':True,
              'generatedAt':1002000,'providers':{'SKG':{'active':1}},'requests':[request]}
        activity=ProviderActivity(lambda:data,lambda:now[0]);activity._refresh()
        metrics=request_metrics(activity.details('SKG'))
        self.assertEqual((metrics['first'],metrics['output']),('等待 2.0s','—'))
        request.update(durationMs=8000,firstOutputMs=2000,genStartMs=2000,lastOutputMs=8000,outputTokens=120)
        data['generatedAt']=1008000;now[0]=101;activity._refresh()
        details=activity.details('SKG');metrics=request_metrics(details)
        self.assertEqual((metrics['first'],metrics['output'],metrics['state']),('2.0s','约 20 tok/s','output'))
        self.assertEqual(metrics['request']['retries'],1)
        self.assertIsNone(request_metrics(activity.details('OCX')))
        data['requests']=[];data['finishedRequests']=[request];data['providers']['SKG']['active']=0
        now[0]=102;activity._refresh()
        metrics=request_metrics(activity.details('SKG'))
        self.assertTrue(metrics['finished']);self.assertEqual(metrics['state'],'idle')
        self.assertEqual(metrics['output'],'约 20 tok/s');self.assertEqual(activity.count('SKG'),0)
        now[0]=105.001
        self.assertIsNone(request_metrics(activity.details('SKG')))


class VisibleMetricsTests(unittest.TestCase):
    def plugin(self):
        plugin=SidebarPlugin.__new__(SidebarPlugin)
        plugin.tooltip_active=True;plugin.hover_item='skg'
        plugin.selected_item=MagicMock(return_value={'type':'opencodex_today'})
        plugin.update_tooltip=MagicMock()
        return plugin

    def test_stationary_pointer_refresh_is_throttled_and_stops_when_hidden(self):
        plugin=self.plugin()
        for now in (1,1.1,1.26,1.6):plugin.refresh_visible_metrics(now)
        self.assertEqual(plugin.update_tooltip.call_count,3)
        plugin.tooltip_active=False;plugin.refresh_visible_metrics(2)
        self.assertEqual(plugin.update_tooltip.call_count,3)

    def test_poll_hover_refreshes_while_pointer_remains_inside_card(self):
        plugin=self.plugin();plugin.test_host=1;plugin.win=MagicMock()
        plugin.target=11;plugin.tooltip=22;plugin.hwnd=0
        plugin.controller=33
        plugin.activity_frame=False
        plugin.config={'items':[],'activityEnabled':False}
        plugin.compact_rows=MagicMock();plugin.compact_rows.hidden=set()
        plugin.win.u.GetForegroundWindow.return_value=11
        plugin.win.u.IsIconic.return_value=False
        def cursor(point):point._obj.x=50;point._obj.y=50
        def bounds(hwnd,rect):
            rect._obj.left=0;rect._obj.top=0;rect._obj.right=100;rect._obj.bottom=100
        plugin.win.u.GetCursorPos.side_effect=cursor
        plugin.win.u.GetWindowRect.side_effect=bounds
        plugin.scale=1;plugin.hover_hit=MagicMock(return_value=None)
        for now in (1,1.3,1.6):
            with patch('sidebar_plugin.time.monotonic',return_value=now):plugin.poll_hover()
        self.assertEqual(plugin.update_tooltip.call_count,3)


class CardMetricsTests(unittest.TestCase):
    def render(self,details,telemetry,snapshot=None):
        from PIL import Image
        p=SidebarPlugin.__new__(SidebarPlugin)
        p.selected_item=MagicMock(return_value={'id':'skg','type':'opencodex_today','provider':'SKG','label':'SKG'})
        p.hover_item='skg';p.hover_focus=None;p.scale=1;p.tooltip=22;p.win=MagicMock()
        p.tooltip_active=False;p.last_tooltip_text=None
        p.config={'activityEnabled':True,'items':[p.selected_item()]}
        p.compact_rows=MagicMock();p.compact_rows.hidden=set()
        p.month_usage=None;p.week_usage=None;p.month_error=None;p.week_error=None
        p.row_usage=MagicMock(return_value=None);p.indicators=MagicMock(return_value=(True,False))
        p.chain_health=MagicMock(return_value=(None,None))
        p.activity=MagicMock();p.activity.state.return_value='output';p.activity.details.return_value=details
        p.telemetry=MagicMock();p.telemetry.view.return_value=telemetry
        p.modules=MagicMock();p.modules.view.return_value=snapshot or {'tasks':{}}
        with patch('sidebar_plugin.render_usage_card',return_value=Image.new('RGBA',(268,100))) as render,patch('sidebar_plugin.upload'),patch('sidebar_modules.provider_indicators',return_value=(False,False)):
            p.update_tooltip(force=True)
        return render.call_args

    def history(self):
        rows=[{'outcome':'completed','timestamp':t,'durationMs':4000,'firstOutputMs':1000,'decodeTokPerSecond':v,'conversationId':'t'}
              for t,v in ((990000,10),(1001000,20))]
        return {'latest':rows[-1],'previous':rows[0],'recent':rows,'trend':None}

    def test_live_card_uses_previous_completed_request_and_own_request_state(self):
        details={'generatedAt':1008000,'requests':[
            {'requestId':'old','startedAt':1002000,'firstOutputMs':1000,'lastOutputMs':6000,'outputTokens':100},
            {'requestId':'new','startedAt':1007000}]}
        call=self.render(details,self.history())
        self.assertEqual(call.kwargs['activity_status'],'waiting')
        self.assertEqual(call.kwargs['live_first'],'等待 1.0s')
        self.assertEqual(call.kwargs['live_output'],'—')
        self.assertEqual(call.kwargs['previous_speed'],'约 20 tok/s')

    def test_running_turn_shows_its_latest_completed_sample_without_claiming_live_speed(self):
        snapshot={'tasks':{'tasks_running':[{'id':'t','provider':'SKG','startedAt':1000,'title':'test'}]}}
        call=self.render({'requests':[]},self.history(),snapshot)
        self.assertEqual(call.kwargs['live_first'],'1.0s')
        self.assertEqual(call.kwargs['live_output'],'约 20 tok/s')
        self.assertEqual(call.kwargs['previous_speed'],'约 10 tok/s')
        self.assertIn('展示本轮最近请求；流中速度未提供',call.args[4])

    def test_other_turn_metrics_are_not_presented_as_current(self):
        call=self.render({'requests':[]},self.history(),{'tasks':{'tasks_running':[{'id':'other','provider':'SKG','startedAt':1000,'title':'test'}]}})
        self.assertIsNone(call.kwargs['live_first'])
        self.assertEqual(call.kwargs['live_output'],'未提供')


if __name__=='__main__':unittest.main()
