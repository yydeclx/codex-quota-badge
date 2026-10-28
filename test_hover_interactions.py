import unittest
import ctypes as C
from ctypes import wintypes as W
from types import SimpleNamespace
from unittest.mock import patch
from hover_panel import render_panel
from sidebar_plugin import SidebarPlugin
from tooltip_card import render_usage_card

class HoverTests(unittest.TestCase):
    def positioned_card(self,scale=1,host=(100,100,1000,900)):
        b=SidebarPlugin.__new__(SidebarPlugin)
        b.scale=scale;b.tooltip=1;b.hwnd=2;b.target=3
        b.config={'items':[{'id':'skg','type':'opencodex_today','enabled':True}]}
        b.compact_rows=SimpleNamespace(hidden=set());b.hover_item='skg'
        rects={1:(0,0,round(240*scale),round(300*scale)),
               2:(110,500,110+round(34*scale),500+round(34*scale)),3:host}
        moves=[]
        def get_rect(hwnd,out):
            r=C.cast(out,C.POINTER(W.RECT)).contents
            r.left,r.top,r.right,r.bottom=rects[hwnd];return True
        def move(hwnd,after,x,y,width,height,flags):
            old=rects[hwnd];rects[hwnd]=(x,y,x+old[2]-old[0],y+old[3]-old[1])
            moves.append((x,y));return True
        b.win=SimpleNamespace(u=SimpleNamespace(GetWindowRect=get_rect,SetWindowPos=move))
        return b,rects,moves

    def test_refresh_moves_directly_to_selected_row_once(self):
        for scale in (1,1.25,1.5,2,2.5):
            with self.subTest(scale=scale):
                b,rects,moves=self.positioned_card(scale)
                b.position_tooltip()
                self.assertEqual(len(moves),1)
                expected=max(100+round(8*scale),min(500+round(17*scale)-round(300*scale)//2,
                                                   900-round(300*scale)-round(8*scale)))
                self.assertEqual(rects[1][1],expected)
                for _ in range(20):b.position_tooltip()
                self.assertEqual(len(moves),1,'Realtime repaint must not move a stationary popup')

    def test_resize_and_host_move_reanchor_once_within_host(self):
        b,rects,moves=self.positioned_card()
        b.position_tooltip();moves.clear()
        x,y,_,_=rects[1];rects[1]=(x,y,x+260,y+420)
        rects[3]=(100,100,400,700)
        b.position_tooltip()
        self.assertEqual(len(moves),1)
        self.assertEqual(rects[1],(132,272,392,692))

    def test_hitboxes_all_dpi(self):
        for scale in (1,1.25,1.5,2,2.5):
            image,actions=render_panel(scale,'会话',[('任务','打开',('thread','test')),('信息','无操作',None)])
            b=SidebarPlugin.__new__(SidebarPlugin);b.hover_actions=actions
            self.assertEqual(b.hover_hit(20,40),0)
            self.assertIsNone(b.hover_hit(20,80))
            self.assertLess(actions[0][0][3]*scale,image.height)
    def test_click_routes_to_session(self):
        b=SidebarPlugin.__new__(SidebarPlugin);b.hover_actions=[((0,0,20,20),('thread','abc'))]
        b.track_hover=lambda inside:None
        with patch('sidebar_details.open_thread') as opened:
            b.hover_action(0);opened.assert_called_once_with('abc')
    def test_daily_hover_changes_chart_without_resize(self):
        trend={'labels':['09/'+str(i) for i in range(23,30)],'values':[0,100,200,300,400,500,736000000]}
        images=[render_usage_card(2,'GPT',None,None,trend=trend,hover_day=i) for i in range(7)]
        self.assertEqual(len({im.size for im in images}),1)
        self.assertEqual(len({im.tobytes() for im in images}),7)

if __name__=='__main__':unittest.main()
