"""Single-line topics retain their models, modest width and click geometry."""
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from PIL import Image,ImageDraw
from hover_panel import render_panel,topic_row,TOPIC_PANEL_WIDTH,TOPIC_ROW_HEIGHT
from sidebar_plugin import SidebarPlugin
from tooltip_card import render_usage_card


TASKS=[{'id':'00000000-0000-0000-0000-000000000001','title':'[Sol]BUG定位修复','model':'SKG/gpt-5.6-sol','provider':'openai'},
       {'id':'00000000-0000-0000-0000-000000000002','title':'[Sol]数据接入改造','model':'SKG/gpt-5.6-sol','provider':'openai'},
       {'id':'00000000-0000-0000-0000-000000000003','title':'[Sol]前端交互优化','model':'SKG/gpt-5.6-sol','provider':'openai'}]


class TopicPanelTests(unittest.TestCase):
    def test_topic_group_inset_is_equal_on_all_sides(self):
        for scale in (1,1.25,1.5,2,2.5):
            for count in (1,2,3):
                for details in (True,False):
                    with self.subTest(scale=scale,count=count,details=details):
                        rows=[topic_row(task,'running') for task in TASKS[:count]]
                        if not details:rows=[(row[0],'',row[2],row[3]) for row in rows]
                        image,actions=render_panel(scale,'',rows,focus=0,width=240)
                        height=24+count*TOPIC_ROW_HEIGHT
                        self.assertEqual(image.height,round(height*scale))
                        left,top=actions[0][0][:2];right,bottom=actions[-1][0][2:]
                        self.assertEqual((left-7,top-6,240-7-right,height-8-bottom),(6,6,6,6))

    def test_model_source_prefix_is_preserved_and_never_borrowed(self):
        self.assertEqual(topic_row(TASKS[0],'completed')[1],'未读 · SKG/gpt-5.6-sol')
        task={**TASKS[0],'model':'gpt-6.1-sol'}
        self.assertEqual(topic_row(task,'running')[1],'运行中 · openai/gpt-6.1-sol')
        task.pop('model')
        self.assertEqual(topic_row(task,'running')[1],'运行中 · 模型未提供')

    def test_single_line_model_pills_do_not_overlap_long_titles(self):
        for scale in (1,1.25,1.5,2,2.5):
            with self.subTest(scale=scale):
                task={**TASKS[0],'title':'[Sol]很长的话题标题需要截断但保留模型来源和完整型号'*4,
                      'model':'custom_openai/gpt-6.1-sol'}
                labels=[];original=ImageDraw.ImageDraw.text
                def record(draw,xy,text,*args,**kwargs):
                    labels.append((text,draw.textbbox(xy,text,font=kwargs['font'])))
                    return original(draw,xy,text,*args,**kwargs)
                with patch.object(ImageDraw.ImageDraw,'text',record):
                    image,_=render_panel.__wrapped__(scale,'',[topic_row(task,'running')],width=TOPIC_PANEL_WIDTH)
                model=next(bounds for text,bounds in labels if text=='custom_openai/gpt-6.1-sol')
                title=next(bounds for text,bounds in labels if text.startswith('[Sol]'))
                self.assertLess(title[2],model[0])
                self.assertLessEqual(abs((title[1]+title[3]-model[1]-model[3])/2),1)
                self.assertTrue(any(text.endswith('…') and text.startswith('[Sol]') for text,_ in labels))
                self.assertFalse(any('运行中' in text for text,_ in labels))
                self.assertLess(model[2]/3,image.width)

    def test_rows_have_one_background_and_stable_hover_size(self):
        rows=[topic_row(task,'completed' if index==0 else 'running') for index,task in enumerate(TASKS)]
        for scale in (1,1.25,1.5,2,2.5):
            with self.subTest(scale=scale):
                plain,actions=render_panel(scale,'',rows,width=240)
                hovered,_=render_panel(scale,'',rows,focus=1,width=240)
                self.assertEqual(plain.size,hovered.size)
                self.assertEqual(plain.width,round(240*scale))
                self.assertEqual(len(actions),3)
                # No per-row card outline or inset white tile remains at x=13.
                for rect,_ in actions:
                    y=rect[1]+2
                    self.assertEqual(plain.getpixel((round(15*scale),round(y*scale))),
                                     plain.getpixel((round(100*scale),round(y*scale))))
                self.assertIs(render_panel(scale,'',rows,width=240)[0],plain)

    def test_attached_rows_are_modestly_wider_and_keep_actions_at_all_dpi(self):
        for scale in (1,1.25,1.5,2,2.5):
            for trend in (None,{'labels':['10/'+str(day) for day in range(22,29)],'values':[1]*7}):
                with self.subTest(scale=scale,trend=bool(trend)):
                    p=SidebarPlugin.__new__(SidebarPlugin);p.scale=scale;p.tooltip=22;p.win=MagicMock()
                    p.config={'items':[{'id':'skg','type':'opencodex_today','provider':'SKG','label':'SKG'}]}
                    p.compact_rows=SimpleNamespace(hidden=set());p.hover_item='skg';p.hover_focus=None
                    p.tooltip_active=True;p.last_tooltip_text=None;p.position_tooltip=MagicMock()
                    p.month_usage=p.week_usage=None;p.month_error=p.week_error=None
                    p.row_usage=MagicMock(return_value=None);p.indicators=MagicMock(return_value=(False,True))
                    p.chain_health=MagicMock(return_value=(None,None));p.activity=MagicMock()
                    p.activity.details.return_value={'requests':[]};p.activity.state.return_value='idle'
                    p.telemetry=MagicMock();p.telemetry.view.return_value={'latest':None,'previous':None,'trend':trend,'samples':[]}
                    p.modules=MagicMock();p.modules.view.return_value={'updatedAt':time.time(),
                        'tasks':{'tasks_done':[TASKS[0]],'tasks_running':TASKS[1:]}}
                    with patch('sidebar_plugin.upload'),patch('sidebar_plugin.render_usage_card',wraps=render_usage_card) as card,patch('hover_panel.render_panel',wraps=render_panel) as panel:
                        p.update_tooltip()
                        expected=render_usage_card(*card.call_args.args,**card.call_args.kwargs)
                    self.assertEqual(p.tooltip_surface.width,round(TOPIC_PANEL_WIDTH*scale))
                    self.assertGreater(p.tooltip_surface.width,expected.width)
                    self.assertEqual(len(p.token_links),3)
                    self.assertEqual(panel.call_args.kwargs['width'],TOPIC_PANEL_WIDTH)
                    self.assertEqual(panel.call_args.args[2][1][1],'运行中 · SKG/gpt-5.6-sol')
                    for rect,action in p.token_links:
                        index=p.hover_hit((rect[0]+rect[2])/2,(rect[1]+rect[3])/2)
                        self.assertIsNotNone(index)
                        with patch('sidebar_details.open_thread') as opened:
                            p.hover_action(index);opened.assert_called_once_with(action[1])

    def test_preview(self):
        scale=2;today={'totalTokens':106900000,'inputTokens':106600000,'outputTokens':300000,'cachedInputTokens':78200000}
        trend={'labels':['10/'+str(day) for day in range(22,29)],'values':[12,22,15,16,24,28,12]}
        token=render_usage_card(scale,'SKG',today,{'totalTokens':2387300000},week={'totalTokens':1086900000},
            trend=trend,recent='最近 10/28 22:37 · 成功 · 23.7秒',speed='首字 8.7s',previous_speed='约 212 tok/s')
        rows=[topic_row(task,'completed' if index==0 else 'running') for index,task in enumerate(TASKS)]
        panel,_=render_panel(scale,'',rows,width=TOPIC_PANEL_WIDTH)
        y=token.height-round(4*scale);image=Image.new('RGBA',(panel.width,y+panel.height))
        image.alpha_composite(token);image.alpha_composite(panel,(0,y))
        output=Path(tempfile.mkdtemp(prefix='topic-panel-clean-'))/'preview.png';image.save(output)
        print(f'Topic layout preview (synthetic fixture): {output}')


if __name__=='__main__':unittest.main()
