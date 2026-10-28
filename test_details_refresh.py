"""Exercise native polling without page replacement, including current gateway IDs."""
import copy
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import types
import unittest
from unittest.mock import patch

from PIL import Image
from sidebar_details import Details, chain_visible_content
from manager_widgets import RoundButton
from tooltip_card import Header


def fixture():
    now=time.time()
    latest={'timestamp':now*1000,'durationMs':15200,'outcome':'completed',
            'status':200,'servedModel':'custom_openai/gpt-5.6-sol',
            'firstOutputMs':8700,'decodeTokPerSecond':79,'requestedEffort':'high'}
    return {'updatedAt':now,'diagnostics':{'pluginVersion':'1.4.1'},
            'modules':{'updatedAt':now,'tasks':{'tasks_running':[],
                       'tasks_pending':[],'tasks_done':[]}},
            'chains':{provider:{'state':'idle','detailed':True,'active':None,
                     'health':[None,None],'requests':[],'latest':copy.deepcopy(latest),
                     'recent':[copy.deepcopy(latest) for _ in range(4)],'events':[]}
                      for provider in ('OCX','SKG','openai')}}


def widgets(parent):
    return [parent]+[item for child in parent.winfo_children() for item in widgets(child)]


def capture(app):
    w=app.win;hwnd=w.u.GetAncestor(app.root.winfo_id(),2);rect=W.RECT()
    w.u.GetWindowRect(hwnd,C.byref(rect));width,height=rect.right-rect.left,rect.bottom-rect.top
    w.g.CreateDIBSection.argtypes=[W.HDC,C.c_void_p,W.UINT,C.POINTER(C.c_void_p),W.HANDLE,W.DWORD]
    w.g.CreateDIBSection.restype=W.HBITMAP;w.u.PrintWindow.argtypes=[W.HWND,W.HDC,W.UINT]
    dc=w.g.CreateCompatibleDC(None);bits=C.c_void_p()
    header=Header(C.sizeof(Header),width,-height,1,32,0,0,0,0,0,0)
    bitmap=w.g.CreateDIBSection(dc,C.byref(header),0,C.byref(bits),None,0)
    previous=w.g.SelectObject(dc,bitmap)
    try:
        if not w.u.PrintWindow(hwnd,dc,2):raise RuntimeError('PrintWindow failed')
        return Image.frombytes('RGB',(width,height),C.string_at(bits,width*height*4),'raw','BGRX')
    finally:
        w.g.SelectObject(dc,previous);w.g.DeleteObject(bitmap);w.g.DeleteDC(dc)


class VisibleContentTests(unittest.TestCase):
    def test_sample_bookkeeping_and_other_providers_do_not_invalidate(self):
        data=fixture();before=chain_visible_content(data,'SKG')
        chain=data['chains']['SKG']
        chain.update(generatedAt=99,ageMs=2345,samples=[{'at':99}],finishedRequests=19)
        data['updatedAt']+=1;data['modules']['updatedAt']+=1
        data['chains']['OCX']['active']=4
        data['modules']['tasks']['tasks_done'].append({'id':'other','title':'Other','model':'OCX/test'})
        self.assertEqual(before,chain_visible_content(data,'SKG'))
        chain['latest']['decodeTokPerSecond']=80
        self.assertNotEqual(before,chain_visible_content(data,'SKG'))

    def test_selected_completion_and_current_request_id_invalidate(self):
        data=fixture();before=chain_visible_content(data,'SKG')
        data['modules']['tasks']['tasks_done'].append({'id':'selected','title':'Done','model':'SKG/test'})
        self.assertNotEqual(before,chain_visible_content(data,'SKG'))
        before=chain_visible_content(data,'SKG')
        data['chains']['SKG']['requests']=[{'requestId':'r-1','startedAt':time.time()*1000}]
        self.assertNotEqual(before,chain_visible_content(data,'SKG'))


class NativeRefreshTests(unittest.TestCase):
    def test_polling_text_updates_actions_scroll_and_header(self):
        data=fixture();original=Path.read_text;reads=[];errors=[]
        def reader(path,*args,**kwargs):
            if path.name=='details-snapshot.json':
                reads.append(time.perf_counter());data['updatedAt']=time.time()
                data['modules']['updatedAt']=time.time()
                data['chains']['SKG']['generatedAt']=time.time()*1000
                data['chains']['SKG']['ageMs']=len(reads)*137
                return json.dumps(data)
            if path.name=='details-request.json':raise FileNotFoundError()
            if path.name in ('status.json','sidebar-status.json'):return '{}'
            if path.name=='update-status.json':return '{"state":"up_to_date"}'
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',reader):
            app=Details('chain','SKG')
            app.root.report_callback_exception=lambda *args:errors.append(args)
            try:
                app.root.update()
                title=next(widget for widget in app.version_badge.master.winfo_children()
                           if isinstance(widget,tk.Label) and widget.cget('text')=='状态中心')
                self.assertLess(title.winfo_rootx(),app.version_badge.winfo_rootx())
                self.assertEqual(app.version_badge.winfo_width(),app.p(54))
                self.assertEqual(app.version_badge.fill,'#e8f6ed')
                self.assertEqual(app.version_badge.fg,'#287a4b')
                before=widgets(app.body);app.canvas.yview_moveto(.25);app.root.update()
                scroll=app.canvas.yview()
                # Windows may deliver child-window paints just after Tk's scroll
                # geometry settles. Sample complete frames, not that transition.
                for _ in range(3):app.root.update();time.sleep(.025)
                baseline=capture(app)
                # Let the real two-second polling run; only hidden fields change.
                deadline=time.perf_counter()+4.5;samples=0
                while time.perf_counter()<deadline:
                    app.root.update();self.assertEqual(before,widgets(app.body))
                    self.assertEqual(scroll,app.canvas.yview());samples+=1;time.sleep(.04)
                self.assertGreaterEqual(len(reads),3)
                for _ in range(3):app.root.update();time.sleep(.025)
                self.assertEqual(baseline.tobytes(),capture(app).tobytes())
                data['chains']['SKG']['latest']['decodeTokPerSecond']=103
                app.refresh();app.root.update()
                self.assertEqual(before,widgets(app.body))
                self.assertTrue(any(isinstance(widget,tk.Label) and '103 tok/s' in widget.cget('text') for widget in before))
                self.assertEqual(scroll,app.canvas.yview())
                # A changed structure creates one replacement; subsequent elapsed text reuses it.
                chain=data['chains']['SKG'];chain.update(state='output',active=1)
                chain['requests']=[{'requestId':'request-live-123','model':'test-model',
                                    'requestedEffort':'high','startedAt':time.time()*1000-10000}]
                app.refresh();app.root.update();active=widgets(app.body)
                self.assertIsNot(before[0],app.body)
                self.assertTrue(any(isinstance(widget,tk.Label) and '#request-live-123' in widget.cget('text') for widget in active))
                chain['requests'][0]['startedAt']-=2000
                app.refresh();app.root.update();self.assertEqual(active,widgets(app.body))
                # Same-sized task cards keep widgets but must open the new record.
                app.select('tasks_done')
                tasks=data['modules']['tasks'];tasks['tasks_done']=[{'id':'old-id','title':'Same title','model':'SKG/test'}]
                app.refresh();app.root.update();task_body=app.body
                tasks['tasks_done'][0]['id']='new-id';app.refresh();app.root.update()
                self.assertIs(task_body,app.body)
                action=next(widget for widget in widgets(app.body) if isinstance(widget,RoundButton) and widget.title=='打开会话 ↗')
                with patch('sidebar_details.open_thread') as opened:
                    action.invoke();opened.assert_called_once_with('new-id')
                timer=app.timer;app.refreshing=True
                app.refresh();app.focus_refresh(types.SimpleNamespace(widget=app.root))
                self.assertEqual(timer,app.timer);app.refreshing=False
                self.assertIn(app.timer,app.root.tk.call('after','info'))
                self.assertFalse(errors)
                app.select('chain');app.canvas.yview_moveto(0);app.root.update()
                output=Path(tempfile.mkdtemp(prefix='details-stability-'))/'status-center.png'
                capture(app).save(output)
                print(f'Native stability: {samples} samples, {len(reads)} reads; screenshot: {output}')
            finally:app.close()


if __name__=='__main__':unittest.main()
