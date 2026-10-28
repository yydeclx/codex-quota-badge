"""Real Tk controls -> isolated saved config -> real native child geometry."""
import copy,ctypes as C,json,tempfile,unittest
from ctypes import wintypes as W
from pathlib import Path
from unittest.mock import patch
from sidebar_data import load_config,save_config,validate_config,GAP_MIN,GAP_MAX
from sidebar_manager import Manager
from sidebar_plugin import SidebarPlugin
from native_badge import Win


class SpacingTests(unittest.TestCase):
    def test_signed_bounds_apply_to_global_and_individual_spacing(self):
        base=load_config()
        for gap in (GAP_MIN,-4,0,20,GAP_MAX):
            config=copy.deepcopy(base);config['gap']=gap
            for item in config['items']:item['gapAfter']=gap
            self.assertEqual(validate_config(config)['gap'],gap)
        for field in ('global','individual'):
            for gap in (GAP_MIN-1,GAP_MAX+1):
                config=copy.deepcopy(base)
                if field=='global':config['gap']=gap
                else:config['items'][0]['gapAfter']=gap
                with self.assertRaises(ValueError):validate_config(config)

    def test_negative_overlap_hit_areas_choose_nearest_icon(self):
        b=SidebarPlugin.__new__(SidebarPlugin)
        b.config={'items':[{'id':str(i),'type':'opencodex_today','gapAfter':GAP_MIN} for i in range(3)]}
        from types import SimpleNamespace
        b.compact_rows=SimpleNamespace(hidden=set())
        self.assertEqual(b.index_at(28),0)
        self.assertEqual(b.index_at(30),1)
        for i,item in enumerate(b.items):self.assertEqual(b.index_at(b.row_top(i)+b.item_height(item)/2),i)

    def test_uniform_change_and_save_update_every_row_and_native_layout(self):
        fixture=load_config();fixture['gap']=0
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            with patch('sidebar_data.CONFIG',path),patch('sidebar_plugin.CONFIG',path):
                save_config(fixture)
                w=Win();host=w.u.CreateWindowExW(0x08000080,'STATIC','Spacing acceptance host',0x90000000,
                                               -20000,-20000,900,1600,None,None,w.instance,None)
                b=SidebarPlugin(host);m=Manager();m.root.update()
                w.u.KillTimer(b.controller,1);w.u.KillTimer(b.controller,2)
                heights=[]
                try:
                    for gap in (20,-4,GAP_MIN):
                        m.gap.set(gap)
                        self.assertTrue(all(value.get()==gap for _,value in m.rows))
                        with patch('sidebar_manager.sidebar_startup.set_enabled'),patch('sidebar_manager.notify_sidebar',return_value=True):
                            self.assertTrue(m.save())
                        saved=load_config()
                        self.assertEqual(saved['gap'],gap)
                        self.assertTrue(all(row['gapAfter']==gap for row in saved['items']))
                        b.reload_config();b.layout()
                        expected=sum(b.item_height(i) for i in b.items)+(len(b.items)-1)*gap
                        self.assertEqual(b.geometry[3],round(expected*b.scale))
                        rect=W.RECT();w.u.GetWindowRect(b.hwnd,C.byref(rect))
                        self.assertEqual(rect.bottom-rect.top,b.geometry[3])
                        heights.append(b.geometry[3])
                        for i,item in enumerate(b.items):
                            self.assertEqual(b.index_at(b.row_top(i)+b.item_height(item)/2),i)
                    self.assertGreater(heights[0],heights[1]);self.assertGreater(heights[1],heights[2])
                    # Typing a minus sign must not interrupt the user or save an
                    # invalid config before the digits have been entered.
                    before=path.read_bytes()
                    with patch('sidebar_manager.messagebox.showerror') as error:
                        m.gap.set('-');error.assert_not_called()
                        with patch('sidebar_manager.sidebar_startup.set_enabled'):
                            self.assertFalse(m.save())
                        error.assert_called_once()
                    self.assertEqual(path.read_bytes(),before)
                    print(json.dumps({'gaps':[20,-4,GAP_MIN],'nativePixelHeights':heights,'allRowsSynchronized':True}))
                finally:m.dirty=False;m.root.destroy();b.close();w.u.DestroyWindow(host)


if __name__=='__main__':unittest.main()
