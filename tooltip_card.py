from ui_render import font as ui_font, raster_cache
"""Own layered hover card, independent of Windows tooltip theme."""
import ctypes as C
from ctypes import wintypes as W
from PIL import Image, ImageDraw, ImageFont, ImageFilter


def render_card(scale, remaining=None, reset=''):
    ss = scale * 3
    p = lambda v: round(v * ss)
    image = Image.new('RGBA', (p(264), p(74)))
    shadow = Image.new('RGBA', image.size)
    ImageDraw.Draw(shadow).rounded_rectangle((p(7), p(9), p(257), p(67)), radius=p(10), fill=(0,0,0,23))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(p(3))))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((p(7), p(6), p(257), p(64)), radius=p(10), fill=(252,251,250,249), outline=(220,219,217,245), width=max(1,p(.65)))
    font = ui_font('C:/Windows/Fonts/msyh.ttc', p(11))
    bold = ui_font('C:/Windows/Fonts/msyhbd.ttc', p(11))
    draw.text((p(18),p(16)), '周额度', font=bold, fill=(38,38,38,255), anchor='lt')
    label = f'剩余 {remaining:g}%' if remaining is not None else '暂不可用'
    draw.text((p(246),p(16)),label,font=font,fill=(110,109,108,255),anchor='rt')
    draw.ellipse((p(18),p(40),p(28),p(50)),outline=(144,143,141,255),width=p(1))
    draw.line([(p(23),p(42)),(p(23),p(45)),(p(25),p(46))],fill=(144,143,141,255),width=p(1))
    draw.text((p(35),p(39)),reset+' 重置' if remaining is not None else '点击重新读取',font=font,fill=(112,111,109,255),anchor='lt')
    return image.resize((round(264*scale),round(74*scale)),Image.Resampling.LANCZOS)


def render_info_card(scale, title, value, lines):
    ss=scale*3
    p=lambda v:round(v*ss)
    width,height=354,54+19*len(lines)
    image=Image.new('RGBA',(p(width),p(height+14)))
    shadow=Image.new('RGBA',image.size)
    ImageDraw.Draw(shadow).rounded_rectangle((p(7),p(9),p(width-7),p(height+7)),radius=p(10),fill=(0,0,0,23))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(p(3))))
    draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((p(7),p(6),p(width-7),p(height+4)),radius=p(10),fill=(252,251,250,249),outline=(220,219,217,245),width=max(1,p(.65)))
    regular=ui_font('C:/Windows/Fonts/msyh.ttc',p(10))
    bold=ui_font('C:/Windows/Fonts/msyhbd.ttc',p(11))
    draw.text((p(18),p(17)),title,font=bold,fill=(38,38,38,255),anchor='lt')
    draw.text((p(width-18),p(17)),value,font=bold,fill=(90,89,87,255),anchor='rt')
    for index,line in enumerate(lines):
        draw.text((p(18),p(43+19*index)),line,font=regular,fill=(112,111,109,255),anchor='lt')
    return image.resize((round(width*scale),round((height+14)*scale)),Image.Resampling.LANCZOS)


class Header(C.Structure):
    _fields_=[('size',W.DWORD),('width',W.LONG),('height',W.LONG),('planes',W.WORD),('bits',W.WORD),('compression',W.DWORD),('sizeImage',W.DWORD),('xppm',W.LONG),('yppm',W.LONG),('used',W.DWORD),('important',W.DWORD)]


class Blend(C.Structure):
    _fields_=[('op',W.BYTE),('flags',W.BYTE),('alpha',W.BYTE),('format',W.BYTE)]


def upload(win, hwnd, image):
    """Copy premultiplied BGRA to an owned per-pixel-alpha popup."""
    win.g.CreateDIBSection.argtypes=[W.HDC,C.c_void_p,W.UINT,C.POINTER(C.c_void_p),W.HANDLE,W.DWORD]
    win.g.CreateDIBSection.restype=W.HBITMAP
    win.u.UpdateLayeredWindow.argtypes=[W.HWND,W.HDC,C.POINTER(W.POINT),C.POINTER(W.SIZE),W.HDC,C.POINTER(W.POINT),W.DWORD,C.POINTER(Blend),W.DWORD]
    win.u.UpdateLayeredWindow.restype=W.BOOL
    width,height=image.size
    header=Header(C.sizeof(Header),width,-height,1,32,0,0,0,0,0,0)
    dc=win.g.CreateCompatibleDC(None)
    bits=C.c_void_p()
    bitmap=win.g.CreateDIBSection(dc,C.byref(header),0,C.byref(bits),None,0)
    if not bitmap:
        win.g.DeleteDC(dc)
        raise C.WinError(C.get_last_error())
    old=win.g.SelectObject(dc,bitmap)
    try:
        raw=image.convert('RGBa').tobytes('raw','BGRa')
        C.memmove(bits,raw,len(raw))
        size,origin,blend=W.SIZE(width,height),W.POINT(0,0),Blend(0,0,255,1)
        if not win.u.UpdateLayeredWindow(hwnd,None,None,C.byref(size),dc,C.byref(origin),0,C.byref(blend),2):
            raise C.WinError(C.get_last_error())
    finally:
        win.g.SelectObject(dc,old)
        win.g.DeleteObject(bitmap)
        win.g.DeleteDC(dc)

def compact_period_tokens(value):
    from decimal import Decimal, ROUND_HALF_UP
    if value is None:return '—'
    value=max(0,int(value))
    if 0<value<100000:return '<0.1M'
    amount=(Decimal(value)/1000000).quantize(Decimal('0.1'),rounding=ROUND_HALF_UP)
    return format(amount,'.1f')+'M'



def tabular_text(draw,position,value,font,fill):
    """Keep the original glyphs; center each decimal digit in an equal advance."""
    digit=max(draw.textlength(c,font=font) for c in '0123456789')
    advances=[digit if c in '0123456789' else draw.textlength(c,font=font) for c in value]
    x=position[0]-sum(advances)
    y=position[1]-font.getbbox(value)[1]
    for c,advance in zip(value,advances):
        glyph=draw.textlength(c,font=font)
        draw.text((x+(advance-glyph)/2,y),c,font=font,fill=fill,anchor='la')
        x+=advance
    return sum(advances)

@raster_cache
def render_usage_card(scale,title,today,month,notes=(),week=None,trend=None,recent=None,speed=None,previous_speed=None,hover_day=None,activity_status=None,status_running=False,status_completed=False,status_level=1.0,live_first=None,live_output=None):
    """Measured compact hierarchy: two totals, daily detail, exception-only notes."""
    ss=scale*3;p=lambda v:round(v*ss)
    width=240;ink=(42,42,40,255);muted=(123,122,119,255)
    regular=ui_font('C:/Windows/Fonts/msyh.ttc',p(10))
    bold=ui_font('C:/Windows/Fonts/msyhbd.ttc',p(11))
    number=ui_font('C:/Windows/Fonts/msyhbd.ttc',p(11))
    small=ui_font('C:/Windows/Fonts/msyh.ttc',p(8))
    probe=ImageDraw.Draw(Image.new('RGB',(1,1)))
    if today:
        largest=max(probe.textlength(compact_period_tokens(today[k]),font=number) for k in ('inputTokens','outputTokens','cachedInputTokens'))
        width=max(width,round(largest/ss)*3+60)
    width=max(width,round((probe.textlength(title+' · Token',font=bold)+probe.textlength('本机记录',font=regular))/ss)+48)
    if recent:width=max(width,round(probe.textlength(recent,font=regular)/ss)+36)
    wrapped=[]
    for note in notes:
        line=''
        for char in note:
            if probe.textlength(line+char,font=regular)>p(width-36):wrapped.append(line);line=''
            line+=char
        if line:wrapped.append(line)
    # All coordinates end at the last baseline plus a small, consistent inset.
    detail_end=130 if today else 90
    chart_top=detail_end+8
    if trend:detail_end+=87
    speed_top=detail_end+11
    has_speed_row=bool(speed or previous_speed or activity_status)
    if has_speed_row:detail_end+=45
    recent_top=detail_end+7
    if recent:detail_end+=21
    bottom=detail_end+len(wrapped)*17+9
    image=Image.new('RGBA',(p(width),p(bottom+9)));shadow=Image.new('RGBA',image.size)
    ImageDraw.Draw(shadow).rounded_rectangle((p(7),p(9),p(width-7),p(bottom+2)),radius=p(10),fill=(0,0,0,23))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(p(3))))
    d=ImageDraw.Draw(image);d.rounded_rectangle((p(7),p(6),p(width-7),p(bottom)),radius=p(10),fill=(252,251,250,249),outline=(220,219,217,245),width=max(1,p(.65)))
    def text(x,y,value,font=regular,color=muted,anchor='lt'):d.text((p(x),p(y)),value,font=font,fill=color,anchor=anchor)
    title_width=probe.textlength(title,font=bold)/ss
    cursor=18
    if status_running:
        alpha=max(.60,min(1.0,float(status_level)))
        bg=(252,251,250);fg=(76,157,114)
        color=tuple(round(base+(target-base)*alpha) for base,target in zip(bg,fg))
        d.ellipse((p(cursor),p(18),p(cursor+4),p(22)),fill=color)
        cursor+=8
    if status_completed:
        d.ellipse((p(cursor),p(18),p(cursor+4),p(22)),fill=(0,112,210,255))
        cursor+=8
    text(cursor,15,title,bold,ink)
    text(cursor+title_width+4,15,' · Token',bold,ink)
    text(width-18,16,'本机记录',anchor='rt')
    # Three measured columns: period, cumulative total, and daily average.
    # Draw each value exactly once so the columns cannot overlap.
    total_right=p(width*0.60)
    average_right=p(width-18)
    average_header_font=regular
    d.text((average_right,p(35)),'日均',font=average_header_font,fill=muted,anchor='rt')
    for label,row,y,days in (('今日',today,35,1),('近7天',week,54,7),('近30天',month,73,30)):
        text(18,y,label)
        total=compact_period_tokens(row['totalTokens']) if row else '—'
        tabular_text(d,(total_right,p(y)),total,number,ink)
        if row and days>1:
            average=compact_period_tokens(row['totalTokens']/days)
            tabular_text(d,(average_right,p(y)),average,number,ink)
    if today:
        d.line((p(18),p(94),p(width-18),p(94)),fill=(231,230,227,255),width=max(1,p(.5)))
        col=(width-36)/3
        for i,(label,key) in enumerate((('输入','inputTokens'),('输出','outputTokens'),('缓存 · 含于输入','cachedInputTokens'))):
            x=18+i*col
            text(x,102,label,font=small)
            value=compact_period_tokens(today[key])
            text(x,115,value,font=number,color=(85,84,81,255))
    if trend:
        text(18,chart_top,(f"{trend['labels'][hover_day]} · {compact_period_tokens(trend['values'][hover_day])}" if hover_day is not None and 0<=hover_day<7 else '每日用量'),color=ink)
        text(width-18,chart_top,'近7天',font=small,anchor='rt')
        values=trend['values'];maximum=max(max(values),1);step=(width-36)/7
        baseline=chart_top+54
        for i,(label,value) in enumerate(zip(trend['labels'],values)):
            x=18+(i+.5)*step;bar_height=max(1,31*value/maximum)
            d.rounded_rectangle((p(x-7),p(baseline-bar_height),p(x+7),p(baseline)),radius=p(min(2,bar_height/2)),fill=(87,87,84,255) if i==hover_day or hover_day is None and i==6 else (190,189,185,255))
            text(x,baseline+5,label,font=small,anchor='mt')
    if has_speed_row:
        parts=(speed or '').split(' · ')
        first=next((v.removeprefix('首字 ') for v in parts if v.startswith('首字 ')),'—')
        output=next((v.removeprefix('输出') for v in parts if v.startswith('输出')),'—')
        if activity_status:
            first=live_first or {'waiting':'等待响应…','output':'已收到内容','running':'会话运行中'}.get(activity_status,'—')
            output=live_output or ('接收中…' if activity_status=='output' else '—')
        d.line((p(18),p(speed_top-3),p(width-18),p(speed_top-3)),fill=(231,230,227,255),width=max(1,p(.5)))
        # Use three measured columns. Fixed x positions made the third value
        # overflow when the card width changed or a long speed was rendered.
        first=first.removeprefix('约 ')
        previous=(previous_speed or '—').removeprefix('约 ')
        # Keep the three columns visually compact while preserving a safe
        # inset for the longest label and speed value.
        left=18; right=width-18; col_width=(right-left)/3
        label_font=ui_font('C:/Windows/Fonts/msyh.ttc',p(9))
        value_font=ui_font('C:/Windows/Fonts/msyhbd.ttc',p(10))
        for i,(label,value) in enumerate((('首字等待',first),('输出速度',output),('上一轮速度',previous))):
            center=left+col_width*(i+.5)
            text(center,speed_top+3,label,font=label_font,anchor='mt')
            value_y=speed_top+18
            if value.strip()=='—':
                # Top anchors otherwise put the short dash at the top of the
                # digit row. Center it on the neighbouring numeric glyphs.
                digit=value_font.getmask('0').getbbox();dash=value_font.getmask('—').getbbox()
                value_y+=(digit[1]+digit[3]-dash[1]-dash[3])/(2*ss)
            text(center,value_y,value,font=value_font,color=(85,84,81,255),anchor='mt')
    if recent:text(18,recent_top,recent,font=regular)
    for i,note in enumerate(wrapped):text(18,detail_end+3+i*17,note,color=(137,116,83,255) if '不完整' in note or '不可用' in note else muted)
    return image.resize((round(width*scale),round((bottom+9)*scale)),Image.Resampling.LANCZOS)

def render_brief_card(scale,title,lines):
    ss=scale*3;p=lambda n:round(n*ss);width=268;height=48+17*len(lines)
    im=Image.new('RGBA',(p(width),p(height)));shadow=Image.new('RGBA',im.size)
    ImageDraw.Draw(shadow).rounded_rectangle((p(7),p(9),p(width-7),p(height-5)),radius=p(10),fill=(0,0,0,25))
    im.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(p(3))));d=ImageDraw.Draw(im)
    d.rounded_rectangle((p(7),p(6),p(width-7),p(height-8)),radius=p(10),fill=(252,251,250,249),outline=(220,219,217,245),width=max(1,p(.6)))
    bold=ui_font('C:/Windows/Fonts/msyhbd.ttc',p(11));regular=ui_font('C:/Windows/Fonts/msyh.ttc',p(10))
    d.text((p(18),p(16)),title,font=bold,fill='#292927')
    for index,line in enumerate(lines):
        while d.textlength(line,font=regular)>p(width-36):line=line[:-2]+'…'
        d.text((p(18),p(36+17*index)),line,font=regular,fill='#777672')
    return im.resize((round(width*scale),round(height*scale)),Image.Resampling.LANCZOS)
