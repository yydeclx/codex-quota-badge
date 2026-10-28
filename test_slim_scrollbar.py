"""Native rounded-thumb scrolling, underflow and both companion windows."""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from manager_widgets import SlimScrollbar
from native_badge import Win
from sidebar_details import Details
from sidebar_manager import Manager
from test_details_refresh import capture,fixture


class SlimScrollbarTests(unittest.TestCase):
    def test_drag_track_keyboard_and_hidden_thumb_at_all_scales(self):
        win=Win()
        for scale in (1,1.25,1.5,2,2.5):
            with self.subTest(scale=scale):
                root=tk.Tk();root.configure(bg='#faf9f8');root.geometry('320x260')
                try:
                    canvas=tk.Canvas(root,highlightthickness=0)
                    bar=SlimScrollbar(root,canvas.yview,scale);bar.pack(side='right',fill='y')
                    canvas.pack(fill='both',expand=True);canvas.configure(yscrollcommand=bar.set,scrollregion=(0,0,240,1800))
                    root.update();self.assertTrue(bar.needed)
                    self.assertEqual(len(bar.find_all()),1);self.assertEqual(bar.type(bar.thumb),'line')
                    first_id=bar.thumb;bar.set(bar.first,bar.last);self.assertEqual(bar.thumb,first_id)
                    top,bottom,travel=bar.bounds();bar.press(SimpleNamespace(y=(top+bottom)/2))
                    bar.drag(SimpleNamespace(y=bar.winfo_height()));bar.release(SimpleNamespace());root.update()
                    self.assertGreater(canvas.yview()[0],0);self.assertAlmostEqual(canvas.yview()[1],1)
                    bar.key_scroll(('moveto',0));root.update();self.assertEqual(canvas.yview()[0],0)
                    bar.press(SimpleNamespace(y=bar.winfo_height()/2));bar.release(SimpleNamespace());root.update()
                    self.assertGreater(canvas.yview()[0],0)
                    bar.key_scroll(('moveto',0));bar.key_scroll(('scroll',1,'pages'));root.update()
                    self.assertGreater(canvas.yview()[0],0)
                    # One native line is updated in place, including hover/drag states.
                    self.assertEqual(bar.thumb,first_id)
                    canvas.configure(scrollregion=(0,0,240,canvas.winfo_height()));canvas.yview_moveto(0);root.update()
                    self.assertFalse(bar.needed);self.assertFalse(bar.find_all())
                    with patch.object(bar,'command') as command:
                        bar.press(SimpleNamespace(y=20));bar.key_scroll(('scroll',1,'pages'))
                        command.assert_not_called()
                    self.assertFalse(root.tk.call('after','info'),'Scrollbar scheduled background work')
                finally:root.destroy()

    def test_details_short_long_and_short_pages(self):
        data=fixture();original=Path.read_text
        def reader(path,*args,**kwargs):
            if path.name=='details-snapshot.json':return json.dumps(data)
            if path.name=='details-request.json':raise FileNotFoundError()
            if path.name in ('status.json','sidebar-status.json'):return '{}'
            if path.name=='update-status.json':return '{"state":"up_to_date"}'
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',reader):
            app=Details('weekly_quota');errors=[]
            app.root.report_callback_exception=lambda *args:errors.append(args)
            try:
                app.root.update();self.assertFalse(app.scrollbar.needed)
                width=app.canvas.winfo_width()
                app.select('diagnostics');app.root.update();self.assertTrue(app.scrollbar.needed)
                app.scrollbar.key_scroll(('moveto',1));app.root.update();self.assertGreater(app.canvas.canvasy(0),0)
                self.assertEqual(app.canvas.winfo_width(),width)
                app.select('weekly_quota');app.root.update()
                self.assertFalse(app.scrollbar.needed);self.assertEqual(app.canvas.canvasy(0),0)
                self.assertFalse(errors)
            finally:app.close()

    def test_manager_resizing_and_page_switching_preserve_settings(self):
        # Geometry needs a fixed list length, independent of personal settings
        # and the smaller fresh-install defaults in a release checkout.
        from sidebar_data import validate_config
        items=[{'id':key,'type':key,'label':label,'enabled':False} for key,label in
               [('tasks_pending','待处理'),('tasks_running','运行中'),('tasks_done','待查看'),
                ('services','本地服务'),('shortcuts','快捷入口')]]
        items += [{'id':'provider_'+provider,'type':'opencodex_today','provider':provider,
                   'label':provider,'enabled':True} for provider in ('OCX','SKG','ChatGPT')]
        items.append({'id':'weekly_quota','type':'weekly_quota','label':'周额度','enabled':True})
        with patch('sidebar_manager.load_config',return_value=validate_config({'version':1,'items':items})):
            app=Manager()
        errors=[]
        app.root.report_callback_exception=lambda *args:errors.append(args)
        output=Path(tempfile.mkdtemp(prefix='slim-scrollbar-'))
        try:
            app.root.geometry(f'{app.px(800)}x{app.px(510)}');app.root.update()
            self.assertTrue(app.scrollbar.needed)
            for section in (1,2,0):
                app.show(section);app.root.update()
                app.scrollbar.key_scroll(('moveto',1));app.root.update()
            for _ in range(3):app.root.update();time.sleep(.02)
            capture(app).save(output/'manager.png')
            app.root.geometry(f'{app.px(800)}x{app.px(900)}');app.root.update()
            self.assertFalse(app.scrollbar.needed);self.assertEqual(app.canvas.canvasy(0),0)
            for _ in range(20):app.root.event_generate('<MouseWheel>',delta=120)
            app.root.update();self.assertEqual(app.canvas.canvasy(0),0)
            self.assertFalse(app.dirty);self.assertFalse(errors)
            print(f'Slim scrollbar native acceptance; manager preview: {output}')
        finally:app.root.destroy()


if __name__=='__main__':unittest.main()
