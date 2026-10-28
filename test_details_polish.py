"""Native typography, narrow-window containment and themed-caption acceptance."""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import font
import unittest
from unittest.mock import patch

from manager_widgets import RoundButton
from manager_icons import set_titlebar_colors
from sidebar_details import Details, WrappingLabel
from test_details_refresh import capture, fixture, widgets


class DetailsPolishTests(unittest.TestCase):
    def test_all_pages_at_narrow_default_and_wide_sizes(self):
        data=fixture();now=time.time()
        data['modules']['tasks']['tasks_running']=[{'id':'test-running',
            'title':'正在核对本机会话的运行状态与长标题换行，保留清晰的文字层级和一致间距',
            'startedAt':now-120}]
        data['modules']['tasks']['tasks_done']=[{'id':'test-done','title':'已完成的本地任务','model':'SKG/test'}]
        data['modules']['services']=[{'label':'本地服务','state':'online','detail':'端口可连接',
            'url':'http://127.0.0.1:10100/a-long-service-path-for-wrapping'}]
        data['quota']={'remaining':59}
        data['quotaHistory']={'reset':now+3*86400,'firstAt':now-86400,'corrections':0}
        original=Path.read_text
        def reader(path,*args,**kwargs):
            if path.name=='details-snapshot.json':return json.dumps(data)
            if path.name=='details-request.json':raise FileNotFoundError()
            if path.name in ('status.json','sidebar-status.json'):return '{}'
            if path.name=='update-status.json':return '{"state":"up_to_date"}'
            return original(path,*args,**kwargs)
        config={'shortcuts':[{'label':'项目目录','kind':'folder',
                 'target':'D:/Projects/a-very-long-folder-name/another-long-folder-name/source'}]}
        with patch.object(Path,'read_text',reader),patch('sidebar_details.load_config',return_value=config):
            app=Details('tasks_pending','SKG');errors=[]
            app.root.report_callback_exception=lambda *args:errors.append(args)
            output=Path(tempfile.mkdtemp(prefix='details-polish-'))
            try:
                app.root.update()
                # The OS caption and client now use the exact same color.
                self.assertTrue(set_titlebar_colors(app.root))
                app.root.update();time.sleep(.04)
                self.assertEqual(capture(app).getpixel((app.p(200),app.p(10))),(250,249,248))
                for width in (380,440,600):
                    app.root.geometry(f'{app.p(width)}x{app.p(560)}');app.root.update()
                    for section in ('tasks_pending','tasks_running','tasks_done','chain',
                                    'weekly_quota','diagnostics','services','shortcuts'):
                        with self.subTest(width=width,section=section):
                            app.select(section)
                            for _ in range(3):app.root.update();time.sleep(.015)
                            for button_key,button in app.nav:
                                self.assertLessEqual(button.winfo_x()+button.winfo_width(),button.master.winfo_width())
                                self.assertLess(font.Font(app.root,font=button.font).measure(button.title),button.winfo_width())
                            for widget in widgets(app.body):
                                if widget is app.body:continue
                                self.assertLessEqual(widget.winfo_x()+widget.winfo_width(),widget.master.winfo_width()+1)
                                if isinstance(widget,WrappingLabel):
                                    self.assertLessEqual(int(widget.cget('wraplength')),widget.winfo_width())
                                    self.assertGreaterEqual(widget.winfo_height(),widget.winfo_reqheight()-1)
                                if isinstance(widget,RoundButton):
                                    self.assertLess(font.Font(app.root,font=widget.font).measure(widget.title),widget.winfo_width())
                            if section=='tasks_pending':
                                notes=[widget for widget in widgets(app.body) if isinstance(widget,tk.Label)
                                       and widget.cget('text').startswith('仅显示')]
                                titles=[widget for widget in widgets(app.body) if isinstance(widget,tk.Label)
                                        and widget.cget('text')=='待处理状态暂不可确认']
                                self.assertEqual(notes[0].winfo_rootx(),titles[0].winfo_rootx())
                                self.assertEqual(len(notes[0].cget('text').splitlines()),2)
                            if width in (380,440):capture(app).save(output/f'{width}-{section}.png')
                            app.canvas.yview_moveto(1);app.root.update()
                self.assertFalse(errors)
                print(f'All 8 pages checked at 380/440/600 DIP; themed caption verified; captures: {output}')
            finally:app.close()


if __name__=='__main__':unittest.main()
