from ui_render import font as ui_font, raster_cache
"""Compact interactive native hover cards; coordinates are logical DIP."""
from PIL import Image,ImageDraw,ImageFont,ImageFilter
from quota_accounting import quota_breakdown

def topic_row(task,status):
    """Use the conversation's own model metadata, keeping its source prefix."""
    model=task.get('model');provider=task.get('provider')
    if isinstance(model,str) and model.strip():
        model=model.strip()
        if '/' not in model and isinstance(provider,str) and provider:model=provider+'/'+model
    else:model='模型未提供'
    state={'running':'运行中','completed':'未读'}.get(status,'待处理')
    return (task['title'],state+' · '+model,('thread',task['id']),status)

@raster_cache
def render_panel(scale,title,rows,focus=None,footer='',width=298):
    ss=scale*3;p=lambda v:round(v*ss)
    normalized=[(row[0],row[1],row[2],row[3] if len(row)>3 else None) for row in rows]
    topic_cards=bool(normalized) and all(action and action[0]=='thread' for _,detail,action,_ in normalized)
    row_height=34 if normalized and all(not detail for _,detail,_,_ in normalized) else 39
    if topic_cards:row_height=42 if any(detail for _,detail,_,_ in normalized) else 34
    row_start=36 if title else 12 if topic_cards else 10
    # The frame starts at (7, 6), rows at x=13, and the final row ends
    # two DIP before its pitch. Keep the same six-DIP inset on every side.
    header_height=row_start+12 if topic_cards else 48 if title else 20
    height=header_height+len(rows)*row_height+(18 if footer else 0)
    im=Image.new('RGBA',(p(width),p(height)));shadow=Image.new('RGBA',im.size)
    ImageDraw.Draw(shadow).rounded_rectangle((p(7),p(9),p(width-7),p(height-5)),radius=p(10),fill=(0,0,0,25))
    im.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(p(3))));d=ImageDraw.Draw(im)
    d.rounded_rectangle((p(7),p(6),p(width-7),p(height-8)),radius=p(10),fill=(252,251,250,249),outline='#dcdbd9',width=max(1,p(.6)))
    regular=ui_font('C:/Windows/Fonts/msyh.ttc',p(10));small=ui_font('C:/Windows/Fonts/msyh.ttc',p(8));bold=ui_font('C:/Windows/Fonts/msyhbd.ttc',p(11))
    if title:d.text((p(18),p(15)),title,font=bold,fill='#292927')
    actions=[]
    for i,(label,detail,action,status) in enumerate(normalized):
        y=row_start+i*row_height
        row_top=y
        row_bottom=y+row_height-2
        focused=bool(action) and len(actions)==focus
        if topic_cards:
            if focused:d.rounded_rectangle((p(13),p(row_top),p(width-13),p(row_bottom)),radius=p(6),fill='#eff3ef')
            if i<len(normalized)-1:d.line((p(20),p(y+row_height-1),p(width-20),p(y+row_height-1)),fill='#ebeae7',width=max(1,p(.4)))
        if action:
            index=len(actions)
            actions.append(((13,row_top,width-13,row_bottom),action))
            if index==focus and not topic_cards:d.rounded_rectangle((p(13),p(y),p(width-13),p(row_bottom)),radius=p(6),fill='#eeedeb')
        text=label
        dot_color={'running':'#4c9d72','completed':'#0070d2'}.get(status)
        max_text_width=width-60 if dot_color else width-54
        while d.textlength(text,font=regular)>p(max_text_width):text=text[:-2]+'…'
        label_x=20
        if dot_color:
            # Keep the panel left aligned. The dot and topic share the same
            # vertical center instead of centering the whole row as a group.
            center_y=(row_top+row_bottom)/2 if topic_cards else y+row_height/2
            d.ellipse((p(20),p(center_y-2.5),p(25),p(center_y+2.5)),fill=dot_color)
            label_x=32
            d.text((p(label_x),p(y+6 if topic_cards and detail else center_y)),text,font=regular,fill='#292927',anchor='lt' if topic_cards and detail else 'lm')
        else:
            if topic_cards:d.text((p(label_x),p(y+6 if detail else (row_top+row_bottom)/2)),text,font=regular,fill='#292927',anchor='lt' if detail else 'lm')
            else:d.text((p(label_x),p(y+3)),text,font=regular,fill='#292927')
        text=detail
        while d.textlength(text,font=small)>p(width-(label_x+34)):text=text[:-2]+'…'
        if text:d.text((p(label_x),p(y+23 if topic_cards else y+20)),text,font=small,fill='#85847f')
        if action:
            if topic_cards:d.text((p(width-25),p((row_top+row_bottom)/2)),'›',font=regular,fill='#686863' if focused else '#85847f',anchor='lm')
            else:d.text((p(width-25),p(y+max(7,(row_height-19)/2))),'›',font=regular,fill='#85847f')
    if footer:d.text((p(20),p(height-27)),footer,font=small,fill='#85847f')
    return im.resize((round(width*scale),round(height*scale)),Image.Resampling.LANCZOS),actions


@raster_cache
def render_quota_chart(scale,remaining,reset,cycle,focus=None,day_key=None,five_hour=None,plan_type=None,reset_signal=None):
    import datetime as dt
    import time
    ss=scale*3;p=lambda v:round(v*ss);width=264
    is_pro = isinstance(plan_type,str) and plan_type.lower().startswith('pro') and five_hour is None
    module_offset=68 if reset_signal is not None else 0
    height=251+module_offset
    im=Image.new('RGBA',(p(width),p(height)));shadow=Image.new('RGBA',im.size)
    ImageDraw.Draw(shadow).rounded_rectangle((p(7),p(9),p(width-7),p(height-5)),radius=p(10),fill=(0,0,0,25))
    im.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(p(3))));d=ImageDraw.Draw(im)
    d.rounded_rectangle((p(7),p(6),p(width-7),p(height-8)),radius=p(10),fill=(252,251,250,249),outline='#dcdbd9',width=max(1,p(.6)))
    fonts=[ui_font('C:/Windows/Fonts/'+f,p(n)) for f,n in [('msyh.ttc',9),('msyhbd.ttc',11),('msyhbd.ttc',16),('msyh.ttc',9.5)]]
    def text(x,y,t,font=0,color='#85847f',anchor='lt'):d.text((p(x),p(y)),t,font=fonts[font],fill=color,anchor=anchor)
    if reset_signal is not None:
        text(18,16,'额度重置监测',1,'#292927')
        freshness=reset_signal.get('freshness','unavailable')
        fresh_style={'fresh':('#3d9462','正常'),'cached':('#aaa9a5','缓存'),'stale':('#c68b2c','过期'),'unavailable':('#c34b44','无数据')}
        dot_color,status_text=fresh_style.get(freshness,fresh_style['unavailable'])
        last_sync=reset_signal.get('lastSyncAt');age=reset_signal.get('syncAgeSeconds')
        if isinstance(last_sync,(int,float)):
            if isinstance(age,(int,float)) and age<60:sync_text='刚刚'
            elif isinstance(age,(int,float)) and age<3600:sync_text=f'{max(1,int(age//60))}分钟前'
            else:sync_text=dt.datetime.fromtimestamp(last_sync).strftime('%m/%d %H:%M')
        else:sync_text='未同步'
        status_line=f'{status_text} · {sync_text}'
        status_width=d.textlength(status_line,font=fonts[0])/ss
        status_right=width-18;dot_x=status_right-status_width-10
        d.ellipse((p(dot_x),p(19),p(dot_x+5),p(24)),fill=dot_color)
        text(dot_x+9,21.5,status_line,0,'#777671','lm')
        card_left,card_top,card_right,card_bottom=18,34,width-18,87
        d.rounded_rectangle((p(card_left),p(card_top),p(card_right),p(card_bottom)),radius=p(7),fill='#f6f5f3',outline='#dfdedb',width=max(1,p(.5)))
        confirmation=reset_signal.get('accountConfirmation');explicit=reset_signal.get('explicitSignal')
        if confirmation:
            d.ellipse((p(28),p(44),p(34),p(50)),fill='#3d9462')
            text(40,47,'本账号已确认重置',1,'#287447','lm')
            text(28,66,f"周额度 {confirmation.get('from'):g}%  →  {confirmation.get('to'):g}%",0,'#353532','lm')
            text(width-28,77,'连续采样确认',0,'#85847f','rm')
        elif reset_signal.get('state')=='signal':
            # A restrained amber panel distinguishes an actionable Tibo signal
            # while keeping the same geometry as forecast and confirmation.
            d.rounded_rectangle((p(card_left),p(card_top),p(card_right),p(card_bottom)),radius=p(7),fill='#fff9ef',outline='#e6d4ae',width=max(1,p(.6)))
            d.ellipse((p(28),p(44),p(34),p(50)),fill='#c68b2c')
            text(40,47,'Tibo 明确信号',1,'#8b621d','lm')
            stamp=explicit.get('at') if isinstance(explicit,dict) else None
            target=explicit.get('targetAt') if isinstance(explicit,dict) else None
            if isinstance(target,(int,float)):
                beijing=dt.datetime.fromtimestamp(target,dt.timezone(dt.timedelta(hours=8)))
                target_text=f'{beijing:%m/%d %H:%M} 前'
                countdown_seconds=max(0,target-time.time());hours=int(countdown_seconds//3600);minutes=int((countdown_seconds%3600)//60)
                countdown='已到预计时间' if countdown_seconds<=0 else f'倒计时 {hours}小时{minutes:02}分'
            else:target_text='时间待确认';countdown='倒计时待确认'
            countdown_width=d.textlength(countdown,font=fonts[0])/ss+12
            countdown_left=width-28-countdown_width
            d.rounded_rectangle((p(countdown_left),p(40),p(width-28),p(55)),radius=p(7.5),fill='#fff0d5',outline='#dfbd79',width=max(1,p(.5)))
            text(countdown_left+countdown_width/2,47.5,countdown,0,'#996515','mm')
            text(28,70,'预计重置',0,'#85847f','lm')
            text(78,70,target_text,0,'#353532','lm')
            text(width-28,70,'北京时间',0,'#85847f','rm')
        elif reset_signal.get('state')=='forecast':
            confidence={'low':'低','medium':'中','high':'高'}.get(reset_signal.get('confidence'),'未知')
            last=reset_signal.get('lastResetAt');last_text=dt.datetime.fromtimestamp(last).strftime('%m/%d %H:%M') if isinstance(last,(int,float)) else '未知'
            center_left=(card_left+width/2)/2;center_right=(width/2+card_right)/2
            d.line((p(width/2),p(40),p(width/2),p(81)),fill='#dfdedb',width=max(1,p(.5)))
            # Use the same three vertical centers for both columns.  Center
            # anchoring avoids font ascent differences making the gaps look
            # uneven even when the nominal baselines are equal.
            label_y,value_y,meta_y=42,59,76
            text(center_left,label_y,'未来 24 小时',0,'#777671','mm')
            text(center_right,label_y,'未来 48 小时',0,'#777671','mm')
            text(center_left,value_y,f"{reset_signal.get('probability24h','—')}%",1,'#353532','mm')
            text(center_right,value_y,f"{reset_signal.get('probability48h','—')}%",1,'#353532','mm')
            text(center_left,meta_y,'置信度 '+confidence,0,'#85847f','mm')
            text(center_right,meta_y,'最近重置 '+last_text,0,'#85847f','mm')
        else:
            d.ellipse((p(28),p(44),p(34),p(50)),fill='#aaa9a5')
            text(40,47,'预测暂不可用',1,'#666560','lm')
            text(28,69,'等待公开信号数据',0,'#85847f','lm')
        d.line((p(18),p(94),p(width-18),p(94)),fill='#e7e6e3',width=max(1,p(.5)))
    if reset_signal is None:text(18,16,'额度',1,'#292927')
    # Two equal columns prevent the two rate-limit windows from competing for
    # the same horizontal line.  Each column owns its label, value and reset.
    divider=width/2
    d.line((p(divider),p(34+module_offset),p(divider),p(84+module_offset)),fill='#e7e6e3',width=max(1,p(.5)))
    def quota_column(center,label,data,show_reset=True,reset_y=75,reset_anchor='mt'):
        valid = isinstance(data,dict) and isinstance(data.get('remaining'),(int,float)) and isinstance(data.get('resetsAt'),(int,float))
        text(center,34+module_offset,label,0,'#777671','mt')
        text(center,53+module_offset,f"{data['remaining']:g}%" if valid else '—',2,'#353532','mt')
        if show_reset:
            reset_text=dt.datetime.fromtimestamp(data['resetsAt']).strftime('%m/%d %H:%M') if valid else '—'
            text(center,reset_y+module_offset,reset_text,3,'#666560',reset_anchor)
    expected=None
    if is_pro and isinstance(reset,(int,float)):
        period=7*86400
        expected=round(max(0,min(100,100*(reset-time.time())/period)))
        left_center=(18+divider)/2
        quota_column(left_center,'时间进度应剩余',{'remaining':expected,'resetsAt':reset},False)
    else:
        quota_column((18+divider)/2,'5 小时额度',five_hour)
    right_center=(divider+width-18)/2
    safe_remaining=max(0,min(100,float(remaining))) if isinstance(remaining,(int,float)) else None
    quota_column(right_center,'实际周额度',{'remaining':safe_remaining,'resetsAt':reset} if safe_remaining is not None and reset else None,True,78.5 if is_pro else 75,'mm' if is_pro else 'mt')
    chart_offset=0
    if is_pro and expected is not None and isinstance(remaining,(int,float)):
        delta=round(remaining-expected)
        sign='+' if delta>0 else ''
        if delta>0:label=f'节省 {delta}%';fill,outline,color='#e8f5ed','#93c9a6','#287447'
        elif delta<0:label=f'超用 {abs(delta)}%';fill,outline,color='#fdeceb','#e1a09b','#bd3d36'
        else:label='进度持平';fill,outline,color='#f0efed','#c9c7c2','#666560'
        pad_x=7;capsule_width=d.textlength(label,font=fonts[3])/ss+pad_x*2
        cap_left=left_center-capsule_width/2;cap_top=72+module_offset;cap_bottom=85+module_offset
        d.rounded_rectangle((p(cap_left),p(cap_top),p(cap_left+capsule_width),p(cap_bottom)),radius=p(6),fill=fill,outline=outline,width=max(1,p(.6)))
        text(left_center,78.5+module_offset,label,3,color,'mm')
    d.line((p(18),p(92+module_offset),p(width-18),p(92+module_offset)),fill='#e7e6e3',width=p(.5))
    today=dt.date.fromisoformat(day_key) if day_key else dt.date.today()
    summary=quota_breakdown(cycle,safe_remaining,reset,day_key)
    days=[row['day'] for row in summary['days']]
    totals=[row['total'] for row in summary['days']]
    locals_=[row['local'] for row in summary['days']]
    others=[row['other'] for row in summary['days']]
    unknowns=[row['unknown'] for row in summary['days']]
    cycle_total_used=summary['total'];cycle_local=summary['local'];cycle_other=summary['other']
    if focus is not None and 0<=focus<len(days):
        selected_total=totals[focus]
        selected_local=locals_[focus]
        selected_other=others[focus]
        labels=summary['days'][focus]['labels']
        title=(f"{days[focus]:%m/%d}  总{labels['total']}% · 本机{labels['local']}% · 非本机{labels['other']}%"
               if selected_total is not None else f'{days[focus]:%m/%d} · 尚未记录')
        if labels['unknown']!='0':title+=f" · 待定{labels['unknown']}%"
    else:
        title=f"周期总{summary['labels']['total']}% · 归因估算"
        if summary['undated']>0:title+=f" · 日期待定位{summary['undated']:g}%"
    title_font=fonts[3] if d.textlength(title,font=fonts[3])<=p(width-36) else ui_font('C:/Windows/Fonts/msyh.ttc',p(8))
    d.text((p(18),p(102+module_offset)),title,font=title_font,fill='#555550')
    plot_left,plot_right=22,width-22
    step=(plot_right-plot_left)/len(days);maximum=max([v for v in totals if v is not None]+[1]);actions=[]
    for i,(day,total,local,other,unknown) in enumerate(zip(days,totals,locals_,others,unknowns)):
        x=plot_left+(i+.5)*step;baseline=201+module_offset
        if focus==i:d.rounded_rectangle((p(x-step/2+2),p(122+module_offset),p(x+step/2-2),p(209+module_offset)),radius=p(5),fill='#f0efed')
        if total is not None:
            h=max(1.5,49*total/maximum)
            if total>0:
                bar_width=p(16);bar_height=max(1,p(baseline)-p(baseline-h))
                bar=Image.new('RGBA',(bar_width,bar_height));paint=ImageDraw.Draw(bar);used=0
                for amount,color in ((local,'#5d5d59'),(other,'#b9b8b3'),(unknown,'#deddd9')):
                    bottom=round(bar_height*(1-used/total));used+=amount;top=round(bar_height*(1-used/total))
                    if bottom>top:paint.rectangle((0,top,bar_width-1,bottom-1),fill=color)
                mask=Image.new('L',bar.size);ImageDraw.Draw(mask).rounded_rectangle((0,0,bar_width-1,bar_height-1),radius=min(p(2),bar_height/2),fill=255)
                bar.putalpha(mask);im.alpha_composite(bar,(p(x-8),p(baseline)-bar_height))
            else:d.line((p(x-8),p(baseline),p(x+8),p(baseline)),fill='#b9b8b3',width=max(1,p(.7)))
            text(x,baseline-h-14,summary['days'][i]['labels']['total']+'%',3,'#62625e','mt')
        else:text(x,baseline,'—',3,'#b4b3ae','mt')
        text(x,207+module_offset,day.strftime('%m/%d'),3,'#555550' if day==today else '#999892','mt')
        actions.append(((plot_left+i*step,122+module_offset,plot_left+(i+1)*step,225+module_offset),('day',i)))
    legend_y=232+module_offset
    legend_font=fonts[3];x=18
    def legend_item(label,color,text_color='#666560',gap=9):
        nonlocal x
        d.rounded_rectangle((p(x),p(legend_y-3),p(x+5),p(legend_y+2)),radius=p(1),fill=color)
        x+=9
        d.text((p(x),p(legend_y)),label,font=legend_font,fill=text_color,anchor='lm')
        x+=d.textlength(label,font=legend_font)/ss+gap
    legend_item(f"本机 {summary['labels']['local']}%", '#5d5d59')
    legend_item(f"非本机 {summary['labels']['other']}%", '#b9b8b3')
    if summary['labels']['unknown']!='0':legend_item(f"待定 {summary['labels']['unknown']}%", '#deddd9', '#999892', 0)
    return im.resize((round(width*scale),round(height*scale)),Image.Resampling.LANCZOS),actions
