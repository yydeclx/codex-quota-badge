"""Small cached, premultiplied-alpha surface for the native sidebar.

Two GDI mattes recover coverage without baking a host color into glyph edges.
No desktop capture, background polling, or host paint interception is needed.
"""
import ctypes as C
from ctypes import wintypes as W
import time
from functools import lru_cache
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from tooltip_card import Header, upload


def from_mattes(black, white, size):
    delta=ImageChops.subtract(white,black).split()
    alpha=ImageChops.invert(ImageChops.lighter(ImageChops.lighter(delta[0],delta[1]),delta[2]))
    channels=[ImageChops.darker(c,alpha) for c in black.split()]
    # RGBa is already premultiplied; upload must not multiply it a second time.
    return Image.merge('RGBa',(*channels,alpha)).resize(size,Image.Resampling.BOX)


def retain_row_hits(image, box, rows):
    """Give empty row pixels minimal alpha; keep inter-row gaps transparent.

    Alpha 1/255 prevents native layered-window hit testing passing through the
    cards. White premultiplied (1,1,1,1) avoids a visible dark hit-area rectangle.
    """
    left,top,right,bottom=box
    mask=Image.new('L',image.size,0);draw=ImageDraw.Draw(mask)
    for y0,y1 in rows:
        lo,hi=max(top,y0),min(bottom,y1)
        if lo<hi:draw.rectangle((0,lo-top,right-left-1,hi-top-1),fill=1)
    *channels,alpha=image.split()
    added=ImageChops.subtract(mask,alpha)
    return Image.merge('RGBa',(*(ImageChops.add(c,added) for c in channels),ImageChops.lighter(alpha,mask)))


@lru_cache(maxsize=32)
def hover_tile_surface(width,height,scale):
    """A full-tile neutral hover wash, with the same soft corners as host icons."""
    factor=3;p=lambda n:round(n*scale*factor)
    size=(width*factor,height*factor)
    image=Image.new('RGBA',size);shadow=Image.new('RGBA',size)
    bounds=(p(.6),p(.8),size[0]-p(.6)-1,size[1]-p(.3)-1)
    ImageDraw.Draw(shadow).rounded_rectangle(bounds,radius=p(8),fill=(0,0,0,4))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(p(.65))))
    ImageDraw.Draw(image).rounded_rectangle((p(.7),p(.4),size[0]-p(.7)-1,size[1]-p(.8)-1),
                                            radius=p(8),fill=(75,73,69,12))
    return image.resize((width,height),Image.Resampling.LANCZOS)


def add_hover_tile(owner,image,box):
    hovered=getattr(owner,'hover_tile',None)
    if hovered is None:return image
    for index,item in enumerate(owner.items):
        if item['id']!=hovered:continue
        top=round(owner.row_top(index)*owner.scale)
        bottom=round((owner.row_top(index)+owner.item_height(item))*owner.scale)
        if bottom<=box[1] or top>=box[3]:return image
        tile=hover_tile_surface(round((34+2*getattr(owner,'hover_gutter',0))*owner.scale),bottom-top,owner.scale)
        layer=Image.new('RGBA',image.size)
        layer.paste(tile,(-box[0],top-box[1]))
        # Put the hover wash behind the original glyph coverage. In particular,
        # never flatten to a sampled host color or fade the numbers and dots.
        return Image.alpha_composite(layer,image.convert('RGBA')).convert('RGBa')
    return image


def render_region(owner, box, size):
    left,top,right,bottom=box
    factor=3;width=(right-left)*factor;height=(bottom-top)*factor
    w=owner.win
    w.g.CreateDIBSection.argtypes=[W.HDC,C.c_void_p,W.UINT,C.POINTER(C.c_void_p),W.HANDLE,W.DWORD]
    w.g.CreateDIBSection.restype=W.HBITMAP
    w.g.SetViewportOrgEx.argtypes=[W.HDC,C.c_int,C.c_int,C.c_void_p]
    w.g.SetViewportOrgEx.restype=W.BOOL
    w.g.GdiFlush.restype=W.BOOL
    dc=w.g.CreateCompatibleDC(None)
    if not dc:raise C.WinError(C.get_last_error())
    bits=C.c_void_p();header=Header(C.sizeof(Header),width,-height,1,32,0,0,0,0,0,0)
    bitmap=w.g.CreateDIBSection(dc,C.byref(header),0,C.byref(bits),None,0)
    if not bitmap:
        w.g.DeleteDC(dc);raise C.WinError(C.get_last_error())
    previous=w.g.SelectObject(dc,bitmap)
    saved_bg=owner.bg;saved_clip=getattr(owner,'paint_clip',None)
    saved_time=getattr(owner,'paint_time',None);owner.paint_time=time.monotonic()
    try:
        w.g.SetViewportOrgEx(dc,-left*factor,-top*factor,None)
        owner.paint_clip=(top*factor,bottom*factor)
        mattes=[]
        for background in (0,0xffffff):
            owner.bg=background
            owner.paint_icon(dc,W.RECT(0,0,size[0]*factor,size[1]*factor),owner.scale*factor)
            w.g.GdiFlush()
            mattes.append(Image.frombytes('RGB',(width,height),C.string_at(bits,width*height*4),'raw','BGRX'))
        image=from_mattes(*mattes,(right-left,bottom-top))
        image=add_hover_tile(owner,image,box)
        rows=[(round(owner.row_top(i)*owner.scale),round((owner.row_top(i)+owner.item_height(item))*owner.scale)) for i,item in enumerate(owner.items)]
        return retain_row_hits(image,box,rows)
    finally:
        owner.bg=saved_bg;owner.paint_clip=saved_clip;owner.paint_time=saved_time
        w.g.SelectObject(dc,previous);w.g.DeleteObject(bitmap);w.g.DeleteDC(dc)


def present(owner, box=None):
    rect=W.RECT()
    if not owner.win.u.GetClientRect(owner.hwnd,C.byref(rect)):return
    size=(rect.right,rect.bottom)
    if min(size)<=0:return
    key=(owner.hwnd,owner.scale,size)
    if getattr(owner,'alpha_surface_key',None)!=key:
        owner.alpha_surface=Image.new('RGBa',size)
        owner.alpha_surface_key=key;box=None
    if box is None:box=(0,0,*size)
    box=(max(0,box[0]),max(0,box[1]),min(size[0],box[2]),min(size[1],box[3]))
    if box[0]>=box[2] or box[1]>=box[3]:return
    patch=render_region(owner,box,size)
    owner.alpha_surface.paste(patch,box[:2])
    upload(owner.win,owner.hwnd,owner.alpha_surface)
