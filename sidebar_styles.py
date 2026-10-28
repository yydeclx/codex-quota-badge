"""Shared GDI styles: native widget and manager previews use identical rendering."""
import ctypes as C
from ctypes import wintypes as W
from native_badge import rgb
STYLES={'01':'双层细框','02':'纯数字','03':'无标签胶囊','04':'浅底数字','05':'框外标签','06':'上下分隔','07':'缩写角标','08':'单位弱化','09':'侧线识别','10':'标签底线'}
FRAME_COLOR=(168,167,164)
LABEL_COLOR=(158,157,154)
VALUE_COLOR=(76,75,73)

from functools import lru_cache

@lru_cache(maxsize=128)
def module_image(scale,kind,value,background=(243,242,240)):
    """Shared compact tile, supersampled once per value and DPI."""
    from PIL import Image,ImageDraw,ImageFont
    ss=scale*4
    p=lambda v:round(v*ss)
    im=Image.new('RGB',(p(34),p(30)),background);d=ImageDraw.Draw(im)
    import math
    ink='#686863';lw=max(1,p(1.3))
    def line(points,closed=False):
        pixels=[(p(x),p(y)) for x,y in points]
        if closed:pixels.append(pixels[0])
        d.line(pixels,fill=ink,width=lw,joint='curve')
        # Rounded caps and joins use the exact same stroke diameter.
        r=(lw-1)/2
        for x,z in pixels:d.ellipse((x-r,z-r,x+r,z+r),fill=ink)
    def arc(cx,cy,rx,ry,start,end):
        return [(cx+rx*math.cos(math.radians(start+(end-start)*i/32)),cy+ry*math.sin(math.radians(start+(end-start)*i/32))) for i in range(33)]
    def box(x,z,width,height,radius):
        points=[]
        for cx,cy,start in ((x+width-radius,z+radius,-90),(x+width-radius,z+height-radius,0),(x+radius,z+height-radius,90),(x+radius,z+radius,180)):
            points+=arc(cx,cy,radius,radius,start,start+90)
        line(points,True)
    if kind=='tasks_running':
        line(arc(11.5,14.5,5,5,0,360),True)
        line([(11.5,11.5),(11.5,14.5),(13.6,15.9)])
    elif kind=='tasks_done':
        line(arc(11.5,14.5,5,5,0,360),True)
        line([(8.9,14.5),(10.6,16.1),(14.1,12.8)])
    elif kind=='tasks_pending':
        # Slim pending checklist with two clean, rounded detail strokes.
        box(7.7,9.2,7.6,10.6,1.35)
        line([(10,12.7),(13,12.7)])
        line([(10,16.2),(12,16.2)])
    elif kind=='services':
        for z in (9.8,15.6):
            box(6.5,z,10,3.7,1.15)
            # Short indicator stroke stays legible without a heavy filled dot.
            line([(8.8,z+1.85),(8.9,z+1.85)])
    else:
        for x,z in ((6.8,9.8),(12.6,9.8),(6.8,15.6),(12.6,15.6)):
            box(x,z,3.6,3.6,.9)
    value=str(value)
    if value.isdigit() and int(value)>99:value='99+'
    size=10
    while True:
        font=ImageFont.truetype('C:/Windows/Fonts/seguisb.ttf',p(size))
        if d.textlength(value,font=font)<=p(11) or size<=6:break
        size-=.5
    d.text((p(24),p(14.5)),value,font=font,fill='#4c4b49' if value not in ('0','—') else '#93928e',anchor='mm')
    return im.resize((round(34*scale),round(30*scale)),Image.Resampling.LANCZOS)

def draw_frame(w,dc,s,y,top=3,bottom=26,radius=5,fill=None):
    """Same native border for feature tiles and Token cards; radius is GDI diameter."""
    g=w.g
    # The border must remain readable beneath Codex's modal dimmer while
    # staying lighter than the text in the normal view.
    pen=g.CreatePen(0,max(1,round(.9*s)),rgb(*FRAME_COLOR));op=g.SelectObject(dc,pen)
    brush=g.CreateSolidBrush(rgb(*fill)) if fill else None
    ob=g.SelectObject(dc,brush or g.GetStockObject(5))
    g.RoundRect(dc,round(2*s),y+round(top*s),round(32*s),y+round(bottom*s),round(radius*s),round(radius*s))
    g.SelectObject(dc,ob);g.SelectObject(dc,op);g.DeleteObject(pen)
    if brush:g.DeleteObject(brush)

def draw_module(w,dc,s,y,kind,value,background=(243,242,240)):
    from tooltip_card import Header
    im=module_image(s,kind,str(value),background);width,height=im.size
    header=Header(C.sizeof(Header),width,-height,1,32,0,0,0,0,0,0)
    raw=im.tobytes('raw','BGRX')
    w.g.SetDIBitsToDevice.argtypes=[W.HDC,C.c_int,C.c_int,W.DWORD,W.DWORD,C.c_int,C.c_int,W.UINT,W.UINT,C.c_void_p,C.c_void_p,W.UINT]
    w.g.SetDIBitsToDevice(dc,0,y,width,height,0,0,0,height,raw,C.byref(header),0)
    draw_frame(w,dc,s,y)

def draw_token(w,dc,s,y,label,value,style,activity=None,activity_color=(76,157,114),completed=False,background=(243,242,240)):
    g=w.g;u=w.u
    def box(top,bottom,radius,fill=None):
        draw_frame(w,dc,s,y,top,bottom,radius,fill)
    def text(value,size,top,bottom,light=False,left=4,right=30):
        if activity is not None:
            if light and style in ('01','10'):
                # Reserve equal space on both sides so the label stays centered
                # on the frame while clearing the activity dot at the right.
                left=max(left,9);right=min(right,25)
        g.SetBkMode(dc,1)
        # Measure with the same selected font and shrink to fit; never clip units.
        for pixels in range(max(1,round(size*s)),0,-1):
            font=g.CreateFontW(-pixels,0,0,0,700,0,0,0,1,0,0,4,0,'Calibri');old=g.SelectObject(dc,font)
            r=W.RECT(0,0,0,0);u.DrawTextW(dc,value,-1,C.byref(r),0x400|32|0x800)
            if r.right<=round((right-left)*s):break
            g.SelectObject(dc,old);g.DeleteObject(font)
        g.SetTextColor(dc,rgb(*(LABEL_COLOR if light else VALUE_COLOR)))
        r=W.RECT(round(left*s),y+round(top*s),round(right*s),y+round(bottom*s))
        u.DrawTextW(dc,value,-1,C.byref(r),1|4|32|0x800)
        g.SelectObject(dc,old);g.DeleteObject(font)
    if style=='10':
        text(label,7.5,1,11,True);text(value,11.5,11,25)
        brush=g.CreateSolidBrush(rgb(160,159,156));r=W.RECT(round(10*s),y+round(27*s),round(24*s),y+round(28*s));u.FillRect(dc,C.byref(r),brush);g.DeleteObject(brush)
    elif style=='01':
        box(3,26,5)
        text(label,7.5,3,12,True);text(value,11.5,12,25)
    elif style=='02':text(value,11,0,30)
    elif style=='03':box(6,24,18);text(value,11.5,9 if activity is not None else 5,25)
    elif style=='04':box(5,25,8,tuple(max(0,c-d) for c,d in zip(background,(12,12,13))));text(value,11.5,9 if activity is not None else 5,25)
    elif style=='05':text(label,6.5,0,9,True);box(10,28,6);text(value,11.5,16 if activity is not None else 10,28)
    elif style=='06':
        text(value,11.5,2,24)
        brush=g.CreateSolidBrush(rgb(178,177,174));r=W.RECT(round(7*s),y+round(28*s),round(27*s),y+round(28*s)+max(1,round(.5*s)));u.FillRect(dc,C.byref(r),brush);g.DeleteObject(brush)
    elif style=='07':text(label[:1],7,1,11,True);text(value,11,10,28)
    elif style=='08' and value[-1:] in ('K','M','B'):
        text(value[:-1],11,0,30,left=2,right=24);text(value[-1],8,3,30,True,left=24,right=32)
    elif style=='09':
        brush=g.CreateSolidBrush(rgb(165,164,161));r=W.RECT(round(1*s),y+round(10*s),round(2*s),y+round(20*s));u.FillRect(dc,C.byref(r),brush);g.DeleteObject(brush)
        text(value,11.5,0,30,left=4,right=32)
    else:text(value,11,0,30)

    right,top,diameter={'01':(4,5.2,3.6),'03':(4.5,8.3,3.6),'04':(4,7,3.6),'05':(4,11.5,3.6),'10':(4,4.8,3.6)}.get(style,(1,1,3.6))
    def dot(x,color,size=diameter):
        pen=w.g.CreatePen(0,1,rgb(*color));brush=w.g.CreateSolidBrush(rgb(*color));op=w.g.SelectObject(dc,pen);ob=w.g.SelectObject(dc,brush)
        offset=(size-diameter)/2
        w.g.Ellipse(dc,round((x-offset)*s),y+round((top-offset)*s),round((x+diameter+offset)*s),y+round((top+diameter+offset)*s))
        w.g.SelectObject(dc,ob);w.g.SelectObject(dc,op);w.g.DeleteObject(brush);w.g.DeleteObject(pen)
    if activity is not None and activity>0:
        alpha=max(.12,min(1,activity));color=tuple(round(bg+(fg-bg)*alpha) for bg,fg in zip(background,activity_color))
        dot(34-right-diameter,color,diameter)
    if completed:dot(right,(0,112,210))

def preview(style,scale=3,value='736M',label='GPT',quota=False,activity=None,completed=False,five_hour=None):
    from native_badge import Win
    from tooltip_card import Header
    from PIL import Image
    from quota_styles import quota_height
    logical_height=quota_height(style) if quota else 30
    target=(round(34*scale),round(logical_height*scale));scale*=3
    w=Win();width,height=round(34*scale),round(logical_height*scale)
    w.g.CreateDIBSection.argtypes=[W.HDC,C.c_void_p,W.UINT,C.POINTER(C.c_void_p),W.HANDLE,W.DWORD];w.g.CreateDIBSection.restype=W.HBITMAP
    dc=w.g.CreateCompatibleDC(None);bits=C.c_void_p();header=Header(C.sizeof(Header),width,-height,1,32,0,0,0,0,0,0)
    bitmap=w.g.CreateDIBSection(dc,C.byref(header),0,C.byref(bits),None,0);old=w.g.SelectObject(dc,bitmap)
    try:
        brush=w.g.CreateSolidBrush(rgb(243,242,240));r=W.RECT(0,0,width,height);w.u.FillRect(dc,C.byref(r),brush);w.g.DeleteObject(brush)
        if quota:
            from quota_styles import draw_quota
            dual_value=(42 if five_hour is None else five_hour) if style in ('11','12','13','14','15','16','17') else None
            draw_quota(w,dc,scale,0,value,style,dual_value)
        else:draw_token(w,dc,scale,0,label,value,style,activity,completed=completed)
        return Image.frombytes('RGB',(width,height),C.string_at(bits,width*height*4),'raw','BGRX').resize(target,Image.Resampling.LANCZOS)
    finally:w.g.SelectObject(dc,old);w.g.DeleteObject(bitmap);w.g.DeleteDC(dc)
