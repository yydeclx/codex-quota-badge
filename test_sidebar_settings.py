import sys
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
        m.ready=True;m.edit_revision=2;m.dirty=True
        m.root=Mock();m.message=Mock();m.save_button=Mock()
        return m

    def test_switch_only_changes_sidebar_setting(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                m=self.manager(enabled)
                gateway=Mock(side_effect=AssertionError('Settings must not touch OpenCodex'))
                with patch.dict(sys.modules, patch_mode=types.SimpleNamespace(apply_mode=gateway)), \
                     patch('sidebar_manager.sidebar_startup.enabled', return_value=False), \
                     patch('sidebar_manager.sidebar_startup.set_enabled'), \
                     patch('sidebar_manager.save_config') as save, \
                     patch('sidebar_manager.notify_sidebar', return_value=True) as notify:
                    self.assertTrue(m.save())
                    gateway.assert_not_called()
                    self.assertIs(save.call_args.args[0]['realtimeMetricsEnabled'], enabled)
                    notify.assert_called_once()
                self.assertFalse(m.dirty)

    def test_close_saves_then_destroys(self):
        m=self.manager()
        with patch('sidebar_manager.messagebox.askyesnocancel', return_value=True), \
             patch.object(m, 'save', return_value=True):
            m.close()
        m.root.destroy.assert_called_once()

    def test_failed_persistence_restores_autostart(self):
        m=self.manager(False)
        with patch('sidebar_manager.sidebar_startup.enabled', return_value=False), \
             patch('sidebar_manager.sidebar_startup.set_enabled') as startup, \
             patch('sidebar_manager.save_config', side_effect=OSError('disk full')), \
             patch('sidebar_manager.messagebox.showerror'), \
             patch('sidebar_manager.notify_sidebar') as notify:
            self.assertFalse(m.save())
        self.assertEqual(startup.call_args_list,[unittest.mock.call(True),unittest.mock.call(False)])
        notify.assert_not_called()
        self.assertTrue(m.dirty)


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
