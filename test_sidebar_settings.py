import queue
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch
from sidebar_manager import Manager
from sidebar_data import validate_config


def sample_config(mode=True):
    return validate_config({'version':1,'items':[{'id':'weekly','type':'weekly_quota','label':'周额度'}],
                            'realtimeMetricsEnabled':mode})


class ManagerSaveTests(unittest.TestCase):
    def manager(self, mode=True):
        m=Manager.__new__(Manager)
        m.config=sample_config();m.rows=[(Mock(get=Mock(return_value=True)),Mock(get=Mock(return_value=4)))]
        values={'realtime_metrics_enabled':mode,'compact_mode':False,'alert_enabled':False,
                'alert_threshold':20,'activity_enabled':True,'style':'10','quota_style':'01',
                'bottom':87,'horizontal':0,'gap':0,'refresh':60,'autostart':True}
        for name,value in values.items():setattr(m,name,Mock(get=Mock(return_value=value)))
        m.switching=False;m.ready=True;m.edit_revision=2;m.dirty=True
        m.save_results=queue.Queue();m.root=Mock();m.message=Mock();m.save_button=Mock()
        return m

    def test_unchanged_mode_saves_synchronously(self):
        m=self.manager()
        with patch('sidebar_manager.load_config',return_value=sample_config()),              patch('sidebar_manager.sidebar_startup.enabled',return_value=False),              patch('sidebar_manager.sidebar_startup.set_enabled') as startup,              patch('sidebar_manager.save_config') as save,              patch('sidebar_manager.notify_sidebar',return_value=True) as notify,              patch('sidebar_manager.threading.Thread') as thread:
            self.assertTrue(m.save());thread.assert_not_called();startup.assert_called_once_with(True)
            self.assertTrue(save.call_args.args[0]['realtimeMetricsEnabled']);notify.assert_called_once()
        self.assertFalse(m.dirty)

    def test_save_compares_disk_mode_and_captures_ui_values(self):
        m=self.manager(False);m.config['realtimeMetricsEnabled']=False
        with patch('sidebar_manager.load_config',return_value=sample_config(True)),              patch('sidebar_manager.sidebar_startup.enabled',return_value=False),              patch('sidebar_manager.threading.Thread') as thread,              patch('sidebar_manager.save_config') as save:
            self.assertFalse(m.save());self.assertTrue(m.switching);self.assertTrue(m.save_button.disabled)
            save.assert_not_called();args=thread.call_args.kwargs['args']
            self.assertEqual(args[2:],(True,False,True,2))
            self.assertFalse(args[1]['realtimeMetricsEnabled']);self.assertEqual(args[1]['items'][0]['gapAfter'],4)
            m.config['items'][0]['label']='新标签'
            self.assertEqual(args[1]['items'][0]['label'],'周额度')
            m.root.after.assert_called_once_with(100,m.poll_save_result)

    def test_switch_blocks_duplicate_save_and_close(self):
        m=self.manager();m.switching=True
        with patch('sidebar_manager.load_config') as load,patch('sidebar_manager.messagebox.askyesnocancel') as ask:
            self.assertFalse(m.save());m.close();load.assert_not_called();ask.assert_not_called()
        m.root.destroy.assert_not_called()

    def test_close_keeps_window_open_for_pending_save(self):
        m=self.manager(False)
        with patch('sidebar_manager.messagebox.askyesnocancel',return_value=True),              patch('sidebar_manager.load_config',return_value=sample_config()),              patch('sidebar_manager.sidebar_startup.enabled',return_value=False),              patch('sidebar_manager.threading.Thread'):
            m.close()
        m.root.destroy.assert_not_called();self.assertTrue(m.switching)

    def test_close_destroys_after_synchronous_save(self):
        m=self.manager()
        with patch('sidebar_manager.messagebox.askyesnocancel',return_value=True),              patch.object(m,'save',return_value=True):m.close()
        m.root.destroy.assert_called_once()

    def test_worker_switches_before_persisting_without_tk(self):
        results=queue.Queue();snapshot=sample_config(False);events=[]
        helper=types.SimpleNamespace(apply_mode=lambda enabled:events.append(('mode',enabled)))
        with patch.dict(sys.modules,patch_mode=helper),              patch('sidebar_manager.sidebar_startup.set_enabled',side_effect=lambda value:events.append(('startup',value))),              patch('sidebar_manager.save_config',side_effect=lambda config:events.append(('save',config['realtimeMetricsEnabled']))):
            Manager.switch_mode_worker(results,snapshot,True,False,True,2)
        self.assertEqual(events,[('mode',False),('startup',True),('save',False)])
        self.assertEqual(results.get_nowait(),(snapshot,2,None))

    def test_failed_persistence_rolls_back_mode_and_autostart(self):
        results=queue.Queue();snapshot=sample_config(False);mode=Mock()
        with patch.dict(sys.modules,patch_mode=types.SimpleNamespace(apply_mode=mode)),              patch('sidebar_manager.sidebar_startup.set_enabled') as startup,              patch('sidebar_manager.save_config',side_effect=OSError('disk full')):
            Manager.switch_mode_worker(results,snapshot,True,False,True,2)
        self.assertEqual(mode.call_args_list,[unittest.mock.call(False),unittest.mock.call(True)])
        self.assertEqual(startup.call_args_list,[unittest.mock.call(True),unittest.mock.call(False)])
        self.assertIn('disk full',results.get_nowait()[2])

    def test_atomic_write_failure_keeps_disk_settings_unchanged(self):
        import tempfile
        from pathlib import Path
        from sidebar_data import load_config,save_config
        results=queue.Queue();mode=Mock()
        with tempfile.TemporaryDirectory() as directory,patch('sidebar_data.CONFIG',Path(directory)/'config.json'):
            save_config(sample_config(True));before=(Path(directory)/'config.json').read_bytes()
            with patch.dict(sys.modules,patch_mode=types.SimpleNamespace(apply_mode=mode)), \
                 patch('sidebar_manager.sidebar_startup.set_enabled'), \
                 patch.object(Path,'replace',side_effect=OSError('atomic write failed')):
                Manager.switch_mode_worker(results,sample_config(False),True,False,True,2)
            self.assertEqual((Path(directory)/'config.json').read_bytes(),before)
            self.assertTrue(load_config()['realtimeMetricsEnabled'])
            self.assertEqual(list(Path(directory).glob('*.tmp')),[])
        self.assertIn('atomic write failed',results.get_nowait()[2]);self.assertEqual(mode.call_count,2)

    def test_autostart_failure_rolls_back_mode_and_skips_save(self):
        results=queue.Queue();mode=Mock()
        with patch.dict(sys.modules,patch_mode=types.SimpleNamespace(apply_mode=mode)), \
             patch('sidebar_manager.sidebar_startup.set_enabled',side_effect=[OSError('startup failed'),None]) as startup, \
             patch('sidebar_manager.save_config') as save:
            Manager.switch_mode_worker(results,sample_config(False),True,False,True,2)
        save.assert_not_called();self.assertEqual(startup.call_count,2);self.assertEqual(mode.call_count,2)
        self.assertIn('startup failed',results.get_nowait()[2])

    def test_helper_failure_does_not_write_config_or_autostart(self):
        results=queue.Queue()
        with patch.dict(sys.modules,patch_mode=types.SimpleNamespace(apply_mode=Mock(side_effect=RuntimeError('restart failed')))),              patch('sidebar_manager.sidebar_startup.set_enabled') as startup,              patch('sidebar_manager.save_config') as save:
            Manager.switch_mode_worker(results,sample_config(False),True,False,True,2)
        startup.assert_not_called();save.assert_not_called();self.assertIn('restart failed',results.get_nowait()[2])

    def test_rollbacks_are_both_attempted_and_errors_reported(self):
        results=queue.Queue();mode=Mock(side_effect=[None,RuntimeError('rollback failed')])
        with patch.dict(sys.modules,patch_mode=types.SimpleNamespace(apply_mode=mode)),              patch('sidebar_manager.sidebar_startup.set_enabled',side_effect=[None,OSError('startup rollback failed')]) as startup,              patch('sidebar_manager.save_config',side_effect=OSError('disk full')):
            Manager.switch_mode_worker(results,sample_config(False),True,False,True,2)
        self.assertEqual(startup.call_count,2)
        error=results.get_nowait()[2]
        for message in ('disk full','补丁模式回滚失败','自启动回滚失败'):self.assertIn(message,error)

    def test_poll_is_only_ui_completion_and_preserves_later_edits(self):
        m=self.manager(False);snapshot=sample_config(False)
        m.switching=True;m.changed();m.save_results.put((snapshot,2,None))
        with patch('sidebar_manager.notify_sidebar',return_value=True) as notify:m.poll_save_result()
        notify.assert_called_once();self.assertFalse(m.switching);self.assertFalse(m.save_button.disabled)
        self.assertTrue(m.dirty);self.assertTrue(m.config['realtimeMetricsEnabled'])
        self.assertIn('仍有未保存',m.message.set.call_args.args[0])

    def test_poll_failure_keeps_prior_config_and_does_not_notify(self):
        m=self.manager(False);m.switching=True;m.save_results.put((sample_config(False),2,'restart failed'))
        with patch('sidebar_manager.messagebox.showerror') as error,patch('sidebar_manager.notify_sidebar') as notify:
            m.poll_save_result();error.assert_called_once();notify.assert_not_called()
        self.assertTrue(m.config['realtimeMetricsEnabled']);self.assertTrue(m.dirty);self.assertFalse(m.switching)

    def test_empty_poll_reschedules_on_ui_thread(self):
        m=self.manager();m.switching=True;m.poll_save_result()
        m.root.after.assert_called_once_with(100,m.poll_save_result);self.assertTrue(m.switching)

    def test_synchronous_persistence_failure_restores_autostart(self):
        m=self.manager()
        with patch('sidebar_manager.load_config',return_value=sample_config()),              patch('sidebar_manager.sidebar_startup.enabled',return_value=False),              patch('sidebar_manager.sidebar_startup.set_enabled') as startup,              patch('sidebar_manager.save_config',side_effect=OSError('disk full')),              patch('sidebar_manager.messagebox.showerror'),patch('sidebar_manager.notify_sidebar') as notify:
            self.assertFalse(m.save());notify.assert_not_called()
        self.assertEqual(startup.call_args_list,[unittest.mock.call(True),unittest.mock.call(False)])
        self.assertTrue(m.dirty)

    def test_background_save_returns_before_helper_finishes(self):
        m=self.manager(False);entered=threading.Event();release=threading.Event()
        def apply(enabled):
            entered.set()
            if not release.wait(5):raise RuntimeError('test timed out')
        with patch.dict(sys.modules,patch_mode=types.SimpleNamespace(apply_mode=apply)),              patch('sidebar_manager.load_config',return_value=sample_config()),              patch('sidebar_manager.sidebar_startup.enabled',return_value=False),              patch('sidebar_manager.sidebar_startup.set_enabled'),patch('sidebar_manager.save_config') as save,              patch('sidebar_manager.notify_sidebar',return_value=True):
            try:
                self.assertFalse(m.save());self.assertTrue(entered.wait(2))
                self.assertTrue(m.save_results.empty());save.assert_not_called()
            finally:release.set()
            result=m.save_results.get(timeout=2);m.save_results.put(result);m.poll_save_result()
        self.assertFalse(m.dirty);self.assertFalse(m.config['realtimeMetricsEnabled'])


def native_settings_check():
    import copy,ctypes as C
    from ctypes import wintypes as W
    from PIL import Image,ImageDraw
    from sidebar_styles import STYLES,preview
    from sidebar_data import load_config,validate_config
    from sidebar_plugin import SidebarPlugin
    from native_badge import Win
    w=Win();host=w.u.CreateWindowExW(0x08000080,'STATIC','Sidebar settings test',0x90000000,-20000,-20000,900,650,None,None,w.instance,None)
    b=SidebarPlugin(host)
    try:
     original=copy.deepcopy(b.config)
     for gap in (-10,-4,0,7,30):
      for i in b.config['items']:i['gapAfter']=gap
      for bottom in (45,87,180):
       b.config['bottomOffset']=bottom;b.geometry=None;b.layout()
       count=len(b.items)
       assert b.geometry[3]==round((sum(b.item_height(i) for i in b.items)+(count-1)*gap)*b.scale)
       assert b.row_top(2)==sum(b.item_height(i) for i in b.items[:2])+2*gap
     for i in b.config['items']:i['enabled']=False
     validate_config(b.config);b.layout();b.heartbeat();b.poll_hover();assert not w.u.IsWindowVisible(b.hwnd)
     b.config=original;b.layout();assert w.u.IsWindowVisible(b.hwnd)
     print('spacing, bottom offset, all hidden and restored: PASS')
    finally:b.close();w.u.DestroyWindow(host)
    canvas=Image.new('RGB',(680,600),'#f3f2f0');draw=ImageDraw.Draw(canvas)
    for n,code in enumerate(STYLES):
     x=n%5*136;y=n//5*300;draw.text((x+10,y+5),code,fill='black')
     for k,value in enumerate(('736M','128B','9999B')):
      canvas.paste(preview(code,2,value), (x+30,y+25+k*85))
     for scale in (1,1.25,1.5,2,3):
      for value in ('736M','128B','9999B','—','0'):preview(code,scale,value)
    canvas.save('styles-actual-check.png')
    print('10 styles x 5 DPI x 5 values rendered')

if __name__=='__main__':
    result=unittest.main(exit=False)
    if not result.result.wasSuccessful():sys.exit(1)
    native_settings_check()
