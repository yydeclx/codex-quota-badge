import unittest,tempfile,time
from pathlib import Path
from sidebar_options import CompactRows,QuotaAlert
from sidebar_data import load_config,validate_config
class OptionsTests(unittest.TestCase):
 def test_compact_delay_and_recovery(self):
  c={'compactMode':True,'items':[{'id':'a','type':'tasks_running'},{'id':'b','type':'tasks_pending'}]}
  s={'updatedAt':time.time(),'tasks':{}}
  m=CompactRows();m.update(c,s,0);self.assertFalse(m.hidden)
  m.update(c,s,5);self.assertEqual(m.hidden,{'a'})
  s['tasks']['tasks_running']=[{}];m.update(c,s,6);self.assertFalse(m.hidden)
  s['tasks']={};m.update(c,s,7);m.update(c,s,12);s['taskError']='offline';m.update(c,s,13);self.assertFalse(m.hidden)
 def test_alert_threshold_persistence_jitter_and_new_week(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'alert.json';a=QuotaAlert(p);c={'quotaAlertEnabled':True,'quotaAlertThreshold':20}
   data={'remaining':21,'resetsAt':1000000};self.assertFalse(a.due(c,data))
   data['remaining']=20;self.assertTrue(a.due(c,data));a.mark(c,data)
   a=QuotaAlert(p);data['resetsAt']+=1;self.assertFalse(a.due(c,data))
   data['resetsAt']+=604800;self.assertTrue(a.due(c,data))
   c['quotaAlertEnabled']=False;self.assertFalse(a.due(c,data))
 def test_invalid_threshold(self):
  for value in (-1,0,101,True,2.5):
   c=load_config();c['quotaAlertThreshold']=value
   with self.assertRaises(ValueError):validate_config(c)
if __name__=='__main__':unittest.main()
