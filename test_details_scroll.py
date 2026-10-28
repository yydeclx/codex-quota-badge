"""Real Tk wheel/scrollbar regression for short pages and navigation affordances."""
import copy
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
from unittest.mock import patch
import unittest

from manager_widgets import RoundButton
from sidebar_details import Details
from test_details_refresh import capture,fixture,widgets


class DetailsScrollTests(unittest.TestCase):
    def test_wheel_bounds_resize_refresh_and_navigation(self):
        data=fixture();tasks=data['modules']['tasks']
        tasks['tasks_running']=[{'id':f'{index:036d}','title':f'本地任务 {index+1}',
                                  'startedAt':time.time()-index*120} for index in range(2)]
        short=copy.deepcopy(tasks['tasks_running']);original=Path.read_text
        def reader(path,*args,**kwargs):
            if path.name=='details-snapshot.json':return json.dumps(data)
            if path.name=='details-request.json':raise FileNotFoundError()
            if path.name in ('status.json','sidebar-status.json'):return '{}'
            if path.name=='update-status.json':return '{"state":"up_to_date"}'
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',reader):
            app=Details('tasks_running','SKG');errors=[]
            app.root.report_callback_exception=lambda *args:errors.append(args)
            try:
                app.root.update()
                subtitle=next(widget for widget in widgets(app.root) if isinstance(widget,tk.Label)
                              and str(widget.cget('textvariable'))==str(app.subtitle))
                for width in (380,440,600):
                    with self.subTest(width=width):
                        app.root.geometry(f'{app.p(width)}x{app.p(560)}');app.root.update()
                        self.assertLess(app.body.winfo_reqheight(),app.canvas.winfo_height())
                        start=app.canvas.winfo_rooty();line_height=subtitle.winfo_height()
                        for delta in (120,-120,120,-120):
                            for _ in range(15):app.root.event_generate('<MouseWheel>',delta=delta)
                            app.root.update()
                            self.assertEqual(app.canvas.canvasy(0),0)
                            self.assertEqual(app.body.winfo_y(),0)
                        for position in (-1,1,0.5):
                            app.scroll('moveto',position);self.assertEqual(app.canvas.canvasy(0),0)
                        # Repeated StringVar updates must not shrink the subtitle into lines.
                        for label in ('数据已过期 · 请启动侧栏','本机状态 · 自动更新')*5:
                            app.subtitle.set(label);app.root.update()
                            self.assertEqual(subtitle.winfo_height(),line_height)
                            self.assertEqual(app.canvas.winfo_rooty(),start)
                        for key,button in app.nav:
                            self.assertTrue(button.outlined);self.assertEqual(button.cget('cursor'),'hand2')
                        row_buttons=[widget for widget in app.body.winfo_children()[0].winfo_children()
                                     if isinstance(widget,RoundButton)]
                        self.assertEqual(len(row_buttons),3)
                        self.assertTrue(all(button.outlined for button in row_buttons))
                        self.assertEqual([button.title for button in row_buttons if button.selected],['运行中 2'])
                # Clicking still changes the selected page/provider with the new appearance.
                next(button for key,button in app.nav if key=='chain').invoke();app.root.update()
                provider_buttons=[widget for widget in app.body.winfo_children()[0].winfo_children()
                                  if isinstance(widget,RoundButton)]
                next(button for button in provider_buttons if button.title=='OCX').invoke();app.root.update()
                self.assertEqual(app.provider,'OCX')
                self.assertEqual(sum(button.selected for key,button in app.nav),1)
                next(button for key,button in app.nav if key=='tasks_running').invoke();app.root.update()
                # A genuinely long page scrolls, then shrinking it returns to a valid top.
                tasks['tasks_running']=[{'id':f'{index:036d}','title':f'任务 {index}',
                                        'startedAt':time.time()} for index in range(30)]
                app.refresh();app.root.update()
                for _ in range(30):app.root.event_generate('<MouseWheel>',delta=-120)
                app.root.update();self.assertGreater(app.canvas.canvasy(0),0)
                app.scroll('moveto',1);app.root.update()
                self.assertLessEqual(app.canvas.canvasy(0),app.body.winfo_height()-app.canvas.winfo_height()+1)
                tasks['tasks_running']=short;app.refresh();app.root.update()
                self.assertEqual(app.canvas.canvasy(0),0)
                app.root.geometry(f'{app.p(440)}x{app.p(560)}');app.root.update()
                for _ in range(3):app.root.update();time.sleep(.025)
                self.assertFalse(errors)
                output=Path(tempfile.mkdtemp(prefix='details-scroll-buttons-'))/'status-center.png'
                capture(app).save(output);print(f'Wheel bounds, resize, shrinking content, subtitle and buttons: PASS; capture: {output}')
            finally:app.close()


if __name__=='__main__':unittest.main()
