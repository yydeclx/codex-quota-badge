"""Small native Tk controls with antialiased rounded surfaces; no new runtime."""
import tkinter as tk
from PIL import Image,ImageDraw,ImageTk
from functools import lru_cache


def _surface_pixels(width,height,background,fill,border,radius):
    scale=2;im=Image.new('RGB',(width*scale,height*scale),background)
    d=ImageDraw.Draw(im);d.rounded_rectangle((1,1,width*scale-2,height*scale-2),radius=radius*scale,fill=fill,outline=border,width=scale)
    return im.resize((width,height),Image.Resampling.LANCZOS)


@lru_cache(maxsize=48)
def _small_surface(width,height,background,fill,border,radius):
    # Cache only small controls. Large settings panels must not accumulate
    # full-window rasters for every resize or expanded module combination.
    return _surface_pixels(width,height,background,fill,border,radius)

def surface(widget,width,height,fill,border,radius):
    if width<3 or height<3:return ImageTk.PhotoImage(Image.new('RGB',(max(1,width),max(1,height)),widget.cget('bg')),master=widget)
    render=_small_surface if width<=480 and height<=240 else _surface_pixels
    return ImageTk.PhotoImage(render(width,height,widget.cget('bg'),fill,border,radius),master=widget)

class SlimScrollbar(tk.Canvas):
    """One rounded thumb in a fixed gutter; no trough, images or polling timer."""
    def __init__(self,parent,command,scale=1):
        super().__init__(parent,bg=parent.cget('bg'),width=max(8,round(10*scale)),
                         highlightthickness=0,borderwidth=0,takefocus=False)
        self.command=command;self.s=scale;self.first=0.;self.last=1.
        self.hover=False;self.dragging=False;self.drag_offset=0;self.thumb=None;self._draw_key=None
        self.bind('<Configure>',lambda event:self.draw())
        self.bind('<Enter>',lambda event:self.over(True));self.bind('<Leave>',lambda event:self.over(False))
        self.bind('<Button-1>',self.press);self.bind('<B1-Motion>',self.drag);self.bind('<ButtonRelease-1>',self.release)
        for event,args in (('<Up>',('scroll',-1,'units')),('<Down>',('scroll',1,'units')),
                           ('<Prior>',('scroll',-1,'pages')),('<Next>',('scroll',1,'pages')),
                           ('<Home>',('moveto',0)),('<End>',('moveto',1))):
            self.bind(event,lambda event,values=args:self.key_scroll(values))
    @property
    def needed(self):return self.last-self.first<1-1e-6
    def set(self,first,last):
        first=max(0.,min(1.,float(first)));last=max(first,min(1.,float(last)))
        if (first,last)==(self.first,self.last):return
        self.first,self.last=first,last
        self.configure(cursor='hand2' if self.needed else '',takefocus=self.needed)
        self.draw()
    def bounds(self):
        pad=round(3*self.s);track=max(0,self.winfo_height()-2*pad)
        extent=self.last-self.first;length=min(track,max(round(24*self.s),track*extent))
        travel=track-length;top=pad+travel*self.first/max(1e-9,1-extent)
        return top,top+length,travel
    def draw(self):
        width,height=self.winfo_width(),self.winfo_height()
        top,bottom,travel=self.bounds()
        color='#92988f' if self.dragging else '#b5b6af' if self.hover else '#cdcec7'
        thickness=max(2,round((4 if self.hover or self.dragging else 3)*self.s))
        visible=self.needed and width>=3 and bottom>top
        key=(width,height,top,bottom,color,thickness,visible)
        if key==self._draw_key:return
        self._draw_key=key
        if not visible:
            if self.thumb is not None:self.delete(self.thumb);self.thumb=None
            return
        coordinates=(width/2,top+thickness/2,width/2,bottom-thickness/2)
        if self.thumb is None:self.thumb=self.create_line(*coordinates,width=thickness,fill=color,capstyle='round',tags='thumb')
        else:self.coords(self.thumb,*coordinates);self.itemconfigure(self.thumb,width=thickness,fill=color)
    def over(self,on):
        if self.hover!=on:self.hover=on;self.draw()
    def key_scroll(self,args):
        if self.needed:self.command(*args)
        return 'break'
    def move_to(self,y):
        top,bottom,travel=self.bounds()
        if travel>0:
            fraction=(y-self.drag_offset-round(3*self.s))/travel*(1-self.last+self.first)
            self.command('moveto',max(0,min(1-self.last+self.first,fraction)))
    def press(self,event):
        if not self.needed:return 'break'
        top,bottom,travel=self.bounds();self.focus_set();self.dragging=True
        self.drag_offset=event.y-top if top<=event.y<=bottom else (bottom-top)/2
        if not top<=event.y<=bottom:self.move_to(event.y)
        self.draw();return 'break'
    def drag(self,event):
        if self.dragging:self.move_to(event.y)
        return 'break'
    def release(self,event):
        self.dragging=False;self.draw();return 'break'

class RoundButton(tk.Canvas):
    def __init__(self,parent,text,command,font,scale=1,primary=False,width=100,outlined=False):
        super().__init__(parent,bg=parent.cget('bg'),width=round(width*scale),height=round(32*scale),highlightthickness=0,cursor='hand2',takefocus=True)
        self.title=text;self.command=command;self.font=font;self.s=scale;self.primary=primary;self.outlined=outlined;self.selected=False;self.hover=False;self.disabled=False
        self.bind('<Configure>',lambda e:self.draw());self.bind('<Enter>',lambda e:self.over(True));self.bind('<Leave>',lambda e:self.over(False))
        self.bind('<Button-1>',lambda e:self.invoke());self.bind('<Return>',lambda e:self.invoke());self.bind('<space>',lambda e:self.invoke())
        self.bind('<FocusIn>',lambda e:self.draw());self.bind('<FocusOut>',lambda e:self.draw())
    def invoke(self):
        if not self.disabled:self.command()
    def over(self,on):
        if self.hover!=on:self.hover=on;self.draw()
    def select(self,on):
        if self.selected!=on:self.selected=on;self.draw()
    def draw(self):
        w,h=self.winfo_width(),self.winfo_height()
        if w<3 or h<3:return
        fill=('#383838' if self.hover else '#252525') if self.primary else '#eae9e7' if self.selected or self.hover else self.cget('bg')
        border=fill;ink='white' if self.primary else '#292929'
        if self.outlined and not self.primary:
            fill=('#deebe2' if self.hover else '#e6eee8') if self.selected else '#eeede9' if self.hover else '#f4f3f0'
            border=('#9ebba8' if self.hover else '#a9c4b2') if self.selected else '#c4c2bc' if self.hover else '#deddd8'
            ink='#285b3f' if self.selected else '#393935'
        if self.focus_get()==self:border='#839887' if self.outlined else '#9b9b98'
        if self.disabled:ink='#b4b4b0'
        key=(w,h,fill,border,ink,self.title,self.disabled,self.font)
        if key==getattr(self,'_draw_key',None):return
        self._draw_key=key;self.delete('all')
        self.photo=surface(self,w,h,fill,border,8*self.s);self.create_image(0,0,image=self.photo,anchor='nw')
        self.create_text(w/2,h/2,text=self.title,font=self.font,fill=ink)

class VersionBadge(tk.Canvas):
    def __init__(self,parent,text,font,scale=1,bg='#e8f6ed',fg='#287a4b',width=78,height=27,radius=7):
        self.s=scale;self.value=text;self.font_value=font;self.fill=bg;self.fg=fg
        self.badge_width=round(width*scale);self.badge_height=round(height*scale);self.radius=radius*scale
        super().__init__(parent,bg=parent.cget('bg'),width=self.badge_width,height=self.badge_height,highlightthickness=0)
        self.bind('<Configure>',lambda _event:self.draw(),add='+')
        self.draw()
    def set(self,text,fg=None,bg=None):
        self.value=text
        if fg is not None:self.fg=fg
        if bg is not None:self.fill=bg
        self.draw()
    def draw(self):
        w=max(self.winfo_width(),self.badge_width);h=max(self.winfo_height(),self.badge_height)
        key=(w,h,self.value,self.fill,self.fg)
        if key==getattr(self,'_draw_key',None):return
        self._draw_key=key;self.delete('all')
        self.photo=surface(self,w,h,self.fill,self.fill,self.radius)
        self.create_image(0,0,image=self.photo,anchor='nw')
        self.create_text(w/2,h/2,text=self.value,font=self.font_value,fill=self.fg)

class RoundPanel(tk.Canvas):
    def __init__(self,parent,scale,padding=16):
        super().__init__(parent,bg=parent.cget('bg'),height=1,highlightthickness=0)
        self.s=scale;self.pad=round(padding*scale);self.body=tk.Frame(self,bg='#ffffff')
        self.slot=self.create_window(self.pad,self.pad,window=self.body,anchor='nw')
        self._resize_job=None
        self.body.bind('<Configure>',lambda e:self.queue_resize());self.bind('<Configure>',lambda e:self.queue_resize())
        self.bind('<Destroy>',self.cancel_resize,add='+')
    def queue_resize(self):
        if self._resize_job is None:self._resize_job=self.after_idle(self.resize)
    def cancel_resize(self,event):
        if event.widget==self and self._resize_job is not None:
            self.after_cancel(self._resize_job);self._resize_job=None
    def resize(self):
        self._resize_job=None;w=self.winfo_width()
        if w<3:return
        body_width=max(1,w-2*self.pad)
        if round(float(self.itemcget(self.slot,'width')))!=body_width:
            self.itemconfigure(self.slot,width=body_width)
            self.queue_resize();return
        h=self.body.winfo_reqheight()+2*self.pad
        if int(self.cget('height'))!=h:self.configure(height=h)
        key=(w,h)
        if key!=getattr(self,'key',None):
            self.key=key;self.photo=surface(self,w,h,'#ffffff','#e6e5e3',12*self.s)
            self.delete('surface');self.create_image(0,0,image=self.photo,anchor='nw',tags='surface');self.tag_lower('surface')

class StyleTile(tk.Canvas):
    def __init__(self,parent,code,name,photo,var,font,scale):
        super().__init__(parent,bg=parent.cget('bg'),height=round(96*scale),width=round(85*scale),highlightthickness=0,cursor='hand2',takefocus=True)
        self.code=code;self.name=name;self.icon=photo;self.var=var;self.font=font;self.s=scale;self.hover=False
        self.bind('<Configure>',lambda e:self.draw());self.bind('<Button-1>',lambda e:var.set(code));self.bind('<space>',lambda e:var.set(code))
        self.bind('<Enter>',lambda e:self.over(True));self.bind('<Leave>',lambda e:self.over(False))
    def over(self,on):self.hover=on;self.draw()
    def draw(self):
        w,h=self.winfo_width(),self.winfo_height()
        if w<3 or h<3:return
        selected=self.var.get()==self.code
        key=(w,h,selected,self.hover,str(self.icon),self.name)
        if key==getattr(self,'_draw_key',None):return
        self._draw_key=key;self.delete('all')
        self.photo=surface(self,w,h,'#f3f2f0' if selected else '#ffffff','#777773' if selected else '#d4d3d0' if self.hover else '#e7e6e4',10*self.s)
        self.create_image(0,0,image=self.photo,anchor='nw');self.create_image(w/2,37*self.s,image=self.icon)
        self.create_text(w/2,h-17*self.s,text=self.name,font=self.font,fill='#292929')
        self.create_text(11*self.s,12*self.s,text=self.code,font=self.font,fill='#92928e',anchor='w')
        if selected:self.create_oval(w-13*self.s,9*self.s,w-9*self.s,13*self.s,fill='#343434',outline='')
