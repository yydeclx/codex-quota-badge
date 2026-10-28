"""Shared monochrome quota styles, rendered at the sidebar's actual dimensions."""
import math,ctypes as C
from ctypes import wintypes as W
from native_badge import rgb
QUOTA_STYLES={'01':'经典圆环','02':'进度圆环','03':'百分比胶囊','04':'圆角方章','05':'底部进度','06':'电池余量','07':'五段刻度','08':'半圆仪表','09':'极简百分比','10':'侧边刻度','11':'双周期卡片','12':'双横条','13':'双胶囊','14':'上下双环','15':'同心双环','16':'双竖刻度','17':'颜色标签'}
ALERT_COLOR=(194,76,69)

def quota_value_color(value,normal):
    return ALERT_COLOR if isinstance(value,(int,float)) and value<20 else normal

def quota_label(value):
    if not isinstance(value,(int,float)):return '—'
    value=round(max(0,min(100,float(value))))
    return '100' if value==100 else f'{value}%'

def quota_height(style='01'):
    return {'11':34,'12':40,'13':40,'14':48,'15':34,'16':40,'17':40}.get(style,30)

def _dual_image(style,scale,weekly,five_hour,background):
    """Supersampled compact quota variants; every coordinate is in DIP."""
    from PIL import Image,ImageDraw
    from ui_render import font as ui_font
    import math
    height=quota_height(style);ss=scale*4;p=lambda v:round(v*ss)
    image=Image.new('RGB',(p(34),p(height)),background);draw=ImageDraw.Draw(image)
    frame='#a8a7a4';label='#9e9d9a';value='#4c4b49';rule='#dad9d6';track='#deddda';blue='#6796b9';green='#75aa88'
    regular='C:/Windows/Fonts/msyh.ttc';bold='C:/Windows/Fonts/msyhbd.ttc'
    def font(size,strong=False):return ui_font(bold if strong else regular,p(size))
    def pct(v):return f'{round(max(0,min(100,float(v))))}%' if isinstance(v,(int,float)) else '—'
    def fit(text,max_size,width,strong=True):
        size=max_size
        while size>5 and draw.textlength(text,font=font(size,strong))>p(width):size-=.25
        return font(size,strong)
    def centered(x,y,text,size,color=value,strong=True):draw.text((p(x),p(y)),text,font=font(size,strong),fill=color,anchor='mm')
    def round_box(box,radius,fill=None,outline=frame,width=.9):
        draw.rounded_rectangle(tuple(p(v) for v in box),radius=p(radius),fill=fill,outline=outline,width=max(1,p(width)))
    if style=='12':
        for top,lab,val in ((3,'5H',five_hour),(21,'周',weekly)):
            round_box((2,top,32,top+16),5)
            draw.text((p(5),p(top+6.5)),lab,font=font(5.5,True),fill=label,anchor='lm')
            shown=pct(val);draw.text((p(29),p(top+6.5)),shown,font=fit(shown,7.5,13),fill=value,anchor='rm')
            draw.line((p(5),p(top+12.5),p(29),p(top+12.5)),fill=track,width=max(1,p(1.5)))
            if isinstance(val,(int,float)):draw.line((p(5),p(top+12.5),p(5+24*max(0,min(100,val))/100),p(top+12.5)),fill=value,width=max(1,p(1.5)))
    elif style=='13':
        for top,lab,val,fill,stroke in ((4,'5H',five_hour,'#eff5fa','#c8d9e7'),(22,'周',weekly,'#edf7f1','#b7d6c1')):
            round_box((2,top,32,top+14),7,fill,stroke,.8)
            draw.ellipse((p(5),p(top+5),p(8),p(top+8)),fill=blue if lab=='5H' else green)
            draw.text((p(10),p(top+7)),lab,font=font(5.5,True),fill=label,anchor='lm')
            shown=pct(val);draw.text((p(29),p(top+7)),shown,font=fit(shown,7.5,12.5),fill=value,anchor='rm')
    elif style=='14':
        for cy,lab,val,color in ((13,'5H',five_hour,blue),(35,'周',weekly,value)):
            radius=8.5;box=(p(17-radius),p(cy-radius),p(17+radius),p(cy+radius))
            draw.ellipse(box,outline=track,width=max(1,p(1.7)))
            if isinstance(val,(int,float)):
                draw.arc(box,-90,-90+3.6*max(0,min(100,val)),fill=color,width=max(1,p(1.9)))
            shown=pct(val);centered(17,cy,shown,5.8,value,True)
            draw.text((p(29),p(cy)),lab,font=font(5.2,True),fill=label,anchor='mm')
    elif style=='15':
        cx,cy=17,17
        for radius,val,color,width in ((13,weekly,value,1.9),(9,five_hour,blue,1.7)):
            box=(p(cx-radius),p(cy-radius),p(cx+radius),p(cy+radius));draw.ellipse(box,outline=track,width=max(1,p(width)))
            if isinstance(val,(int,float)):draw.arc(box,-90,-90+3.6*max(0,min(100,val)),fill=color,width=max(1,p(width)))
        shown=str(round(max(0,min(100,float(weekly))))) if isinstance(weekly,(int,float)) else '—';centered(cx,cy,shown,7.3,value,True)
        draw.text((p(17),p(31)), '外周 · 内5H',font=font(4.2),fill=label,anchor='mm')
    elif style=='16':
        for cy,lab,val,color in ((11,'5H',five_hour,blue),(29,'周',weekly,value)):
            draw.text((p(4),p(cy)),lab,font=font(5.5,True),fill=label,anchor='lm')
            draw.rounded_rectangle((p(14),p(cy-6),p(17),p(cy+6)),radius=p(1.5),fill=track)
            if isinstance(val,(int,float)):
                bottom=cy+6;top=bottom-12*max(0,min(100,val))/100
                draw.rounded_rectangle((p(14),p(top),p(17),p(bottom)),radius=p(1.5),fill=color)
            shown=pct(val);draw.text((p(30),p(cy)),shown,font=fit(shown,8,11.5),fill=value,anchor='rm')
    elif style=='17':
        for cy,lab,val,fill,stroke,color in ((11,'5H',five_hour,'#eff5fa','#c8d9e7','#527a99'),(29,'周',weekly,'#edf7f1','#b7d6c1','#347653')):
            round_box((3,cy-6,14,cy+6),6,fill,stroke,.7)
            centered(8.5,cy,lab,5,color,True)
            shown=pct(val);draw.text((p(30),p(cy)),shown,font=fit(shown,9.5,14),fill=value,anchor='rm')
    return image.resize((round(34*scale),round(height*scale)),Image.Resampling.LANCZOS)

def _paint_image(w,dc,image,y=0):
    from tooltip_card import Header
    width,height=image.size;header=Header(C.sizeof(Header),width,-height,1,32,0,0,0,0,0,0)
    raw=image.tobytes('raw','BGRX')
    w.g.SetDIBitsToDevice.argtypes=[W.HDC,C.c_int,C.c_int,W.DWORD,W.DWORD,C.c_int,C.c_int,W.UINT,W.UINT,C.c_void_p,C.c_void_p,W.UINT]
    w.g.SetDIBitsToDevice(dc,0,y,width,height,0,0,0,height,raw,C.byref(header),0)

def draw_quota(w,dc,s,y,value,style='01',five_hour=None,background=(243,242,240),signal=None):
    g=w.g;u=w.u;ink=(105,104,101);muted=(176,175,172)
    valid=value is not None;pct=max(0,min(100,float(value))) if valid else 0
    label=str(round(pct)) if valid else '—'
    def line(points,color=ink,width=1.2):
        pen=g.CreatePen(0,max(1,round(width*s)),rgb(*color));old=g.SelectObject(dc,pen)
        g.MoveToEx(dc,round(points[0][0]*s),y+round(points[0][1]*s),None)
        for x,t in points[1:]:g.LineTo(dc,round(x*s),y+round(t*s))
        g.SelectObject(dc,old);g.DeleteObject(pen)
    def shape(x,t,r,b,radius=4,color=muted,fill=None,width=1):
        pen=g.CreatePen(0,max(1,round(width*s)),rgb(*color));old=g.SelectObject(dc,pen)
        brush=g.CreateSolidBrush(rgb(*fill)) if fill else None;ob=g.SelectObject(dc,brush or g.GetStockObject(5))
        g.RoundRect(dc,round(x*s),y+round(t*s),round(r*s),y+round(b*s),round(radius*s),round(radius*s))
        g.SelectObject(dc,ob);g.SelectObject(dc,old);g.DeleteObject(pen)
        if brush:g.DeleteObject(brush)
    def text(v,size=11,top=0,bottom=30,left=2,right=32):
        g.SetBkMode(dc,1);g.SetTextColor(dc,rgb(*ink))
        for px in range(max(1,round(size*s)),0,-1):
            f=g.CreateFontW(-px,0,0,0,700,0,0,0,1,0,0,4,0,'Calibri');old=g.SelectObject(dc,f)
            box=W.RECT();u.DrawTextW(dc,v,-1,C.byref(box),0x400|32|0x800)
            if box.right<=round((right-left)*s):break
            g.SelectObject(dc,old);g.DeleteObject(f)
        box=W.RECT(round(left*s),y+round(top*s),round(right*s),y+round(bottom*s));u.DrawTextW(dc,v,-1,C.byref(box),1|4|32|0x800)
        g.SelectObject(dc,old);g.DeleteObject(f)
    def arc(start,end,cx=17,cy=15,r=11,color=ink,width=1.7):
        points=[(cx+r*math.cos(math.radians(a)),cy+r*math.sin(math.radians(a))) for a in [start+(end-start)*i/100 for i in range(101)]]
        line(points,color,width)
    if style in ('12','13','14','15','16','17'):
        _paint_image(w,dc,_dual_image(style,s,value,five_hour,background),y)
    # The visible reference ring measures about 42 px at the current scale;
    # 10 DIP gives the matching outer diameter after native rasterization.
    elif style=='01':arc(0,360,r=10);text(label,10.5)
    elif style=='02':
        arc(0,360,color=muted,width=1.6)
        # Used quota opens clockwise from twelve o'clock; dark arc is remaining.
        if valid and pct:arc(-90+3.6*(100-pct),270,width=2)
        text(label,11,left=8,right=26)
    elif style=='03':shape(2,6,32,24,18);text(label+'%' if valid else label,10,6,24)
    elif style=='04':shape(6,4,28,26,6,color=ink,width=1.1);text(label,11)
    elif style=='05':
        text(label+'%' if valid else label,11,0,23);line([(5,26),(29,26)],muted,2)
        if valid and pct:line([(5,26),(5+24*pct/100,26)],ink,2)
    elif style=='06':
        shape(2,7,30,24,4);line([(32,12),(32,19)],muted,2);text(label,10,5,23,left=2,right=30)
        if valid and pct:line([(5,21),(5+22*pct/100,21)],ink,1.5)
    elif style=='07':
        text(label,11,0,21)
        for i in range(5):line([(5+i*5,24),(8+i*5,24)],ink if valid and pct>=20*(i+1) else muted,2.5)
    elif style=='08':
        arc(180,360,cy=18,r=12,color=muted,width=2)
        if valid and pct:arc(180,180+1.8*pct,cy=18,r=12,width=2)
        text(label,10,13,29)
    elif style=='09':text(label,14,0,30,left=2,right=25);text('%' if valid else '',7,3,25,left=25,right=32)
    elif style=='10':
        line([(5,5),(5,25)],muted,2)
        if valid and pct:line([(5,25),(5,25-20*pct/100)],ink,2)
        text(label,12,left=9,right=32)
    elif style=='11':
        # Reuse the Token card frame colors and horizontal geometry. Style 11
        # is four DIP taller to give the larger values enough breathing room.
        from sidebar_styles import draw_frame,LABEL_COLOR,VALUE_COLOR
        draw_frame(w,dc,s,y,3,30,5)
        line([(5,16.5),(29,16.5)],(218,217,214),.65)
        def row(label_text,row_value,top,bottom):
            g.SetBkMode(dc,1)
            g.SetTextColor(dc,rgb(*LABEL_COLOR))
            font=g.CreateFontW(-max(1,round(6*s)),0,0,0,600,0,0,0,1,0,0,4,0,'Segoe UI')
            old=g.SelectObject(dc,font);box=W.RECT(round(5*s),y+round(top*s),round(15*s),y+round(bottom*s))
            u.DrawTextW(dc,label_text,-1,C.byref(box),0|4|32|0x800);g.SelectObject(dc,old);g.DeleteObject(font)
            shown=quota_label(row_value)
            g.SetTextColor(dc,rgb(*quota_value_color(row_value,VALUE_COLOR)))
            measured=shown
            # 79% shrinks to about 9 pt to fit the fixed value column. Use the
            # same final size for plain 100 so their visible glyph heights match.
            start_size=7.75 if shown=='100' else 10.5
            for px in range(max(1,round(start_size*s)),0,-1):
                font=g.CreateFontW(-px,0,0,0,700,0,0,0,1,0,0,4,0,'Segoe UI');old=g.SelectObject(dc,font)
                measure=W.RECT();u.DrawTextW(dc,measured,-1,C.byref(measure),0x400|32|0x800)
                if measure.right<=round(15*s):break
                g.SelectObject(dc,old);g.DeleteObject(font)
            left=14.625 if shown=='100' else 15
            box=W.RECT(round(left*s),y+round(top*s),round(30*s),y+round(bottom*s))
            alignment=0 if shown=='100' else 2
            u.DrawTextW(dc,shown,-1,C.byref(box),alignment|4|32|0x800);g.SelectObject(dc,old);g.DeleteObject(font)
        row('5H',five_hour,3,16.5)
        row('周',value,16.5,30)
    if signal in ('explicit','confirmed'):
        color=(198,139,44) if signal=='explicit' else (61,148,98)
        pen=g.CreatePen(0,1,rgb(*color));brush=g.CreateSolidBrush(rgb(*color));old_pen=g.SelectObject(dc,pen);old_brush=g.SelectObject(dc,brush)
        g.Ellipse(dc,round(27.4*s),y+round(2.4*s),round(31*s),y+round(6*s))
        g.SelectObject(dc,old_brush);g.SelectObject(dc,old_pen);g.DeleteObject(brush);g.DeleteObject(pen)
