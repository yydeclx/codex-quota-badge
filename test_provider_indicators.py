import unittest
from unittest.mock import MagicMock,patch
from sidebar_modules import provider_indicators
from sidebar_plugin import SidebarPlugin,breathing_level

class Indicators(unittest.TestCase):
    def test_breathing_curve_is_smooth_and_restrained(self):
        values=[breathing_level(i/20) for i in range(100)]
        self.assertGreaterEqual(min(values),.60)
        self.assertLessEqual(max(values),1.0)
        self.assertLess(max(abs(values[i+1]-values[i]) for i in range(len(values)-1)),.06)
        eps=.001
        self.assertAlmostEqual(breathing_level(0)-breathing_level(-eps),breathing_level(eps)-breathing_level(0),places=4)
        self.assertAlmostEqual(breathing_level(0),breathing_level(2.0),places=6)
        self.assertAlmostEqual(breathing_level(.5),breathing_level(1.5),places=6)
    def state(self,running=(),done=()):return {'updatedAt':100,'tasks':{'tasks_running':list(running),'tasks_done':list(done)}}
    def test_gateway_prefix_overrides_codex_provider(self):
        task={'provider':'openai','model':'SKG/gpt-6-astra'}
        state=self.state([task],[task])
        self.assertEqual(provider_indicators('SKG',state,now=100),(True,True))
        self.assertEqual(provider_indicators('openai',state,now=100),(False,False))
    def test_read_clears_blue_without_stopping_green(self):
        task={'provider':'openai','model':'gpt-6-astra'}
        self.assertEqual(provider_indicators('openai',self.state([task],[task]),now=100),(True,True))
        self.assertEqual(provider_indicators('openai',self.state([task],[]),now=100),(True,False))
        self.assertEqual(provider_indicators('openai',self.state([],[task]),now=100),(False,True))
    def test_tools_running_without_active_request(self):
        self.assertEqual(provider_indicators('openai',self.state([{'provider':'openai'}]),active=False,now=100),(True,False))
    def test_live_route_and_staleness(self):
        task={'id':'a','provider':'openai'};requests=[{'conversationId':'a','provider':'SKG'}]
        self.assertEqual(provider_indicators('SKG',self.state([task]),requests,now=100),(True,False))
        self.assertEqual(provider_indicators('SKG',self.state([task]),requests,active=True,now=200),(True,False))
        self.assertEqual(provider_indicators('openai',self.state([],[task]),now=200),(False,False))

    def test_sidebar_uses_live_activity_when_rollout_is_stale(self):
        sidebar=SidebarPlugin.__new__(SidebarPlugin)
        sidebar.config={'activityEnabled':True}
        sidebar.modules=MagicMock()
        sidebar.modules.view.return_value={'updatedAt':0,'tasks':{'tasks_running':[],'tasks_done':[]}}
        sidebar.activity=MagicMock()
        sidebar.activity.details.return_value={'requests':[{'provider':'SKG'}]}
        sidebar.activity.active.return_value=True
        self.assertEqual(sidebar.indicators('SKG'),(True,False))
        sidebar.activity.active.assert_called_once_with('SKG')

    def test_sidebar_does_not_scan_tasks_from_the_paint_path_when_activity_is_unavailable(self):
        sidebar=SidebarPlugin.__new__(SidebarPlugin)
        sidebar.config={'activityEnabled':True}
        sidebar.modules=MagicMock()
        sidebar.modules.view.return_value={'updatedAt':0,'taskError':'activity unavailable','tasks':{}}
        sidebar.activity=MagicMock()
        sidebar.activity.details.return_value={'requests':[]}
        sidebar.activity.active.return_value=False
        with patch('sidebar_modules.read_tasks') as read:
            self.assertEqual(sidebar.indicators('SKG'),(False,False))
            read.assert_not_called()

    def test_native_subagent_route_keeps_gateway_green_without_parent_task(self):
        subagent={'id':'worker-ocx','provider':'openai','model':'OCX/glm-5.3','thread_source':'subagent'}
        state=self.state([subagent],[])
        self.assertEqual(provider_indicators('OCX',state,now=100),(True,False))
        self.assertEqual(provider_indicators('SKG',state,now=100),(False,False))

    def test_multiple_subagents_are_independent_by_model_route(self):
        state=self.state([
            {'id':'worker-ocx','provider':'openai','model':'OCX/glm-5.3','thread_source':'subagent'},
            {'id':'worker-skg','provider':'openai','model':'SKG/gpt-5.6-sol','thread_source':'subagent'}],[])
        self.assertTrue(provider_indicators('OCX',state,now=100)[0])
        self.assertTrue(provider_indicators('SKG',state,now=100)[0])

if __name__=='__main__':unittest.main()
