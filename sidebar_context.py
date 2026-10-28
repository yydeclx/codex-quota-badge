"""Independent clickable context window; no cross-process Win32 menu loop."""
import tkinter as tk
from font_fallback import ui_family
import sys
from sidebar_data import load_config,save_config,ROOT

class MenuRow(tk.Canvas):
    """Compact menu row with aligned labels and a rounded hover surface."""
    def __init__(self,parent,title,callback,scale,bold=False,checked=None):
        from tkinter.font import Font
        self.s=scale;self.title=title;self.callback=callback;self.checked=checked;self.hot=False
        self.font=Font(family=ui_family(),size=-round(9.5*scale),weight='bold' if bold else 'normal')
        super().__init__(parent,bg='#faf9f7',width=self.font.measure(title)+round(38*scale),height=round(24*scale),highlightthickness=0,takefocus=True,cursor='hand2')
        self.bind('<Configure>',lambda e:self.draw())
        self.bind('<Enter>',lambda e:self.hover(True));self.bind('<Leave>',lambda e:self.hover(False))
        self.bind('<FocusIn>',lambda e:self.hover(True));self.bind('<FocusOut>',lambda e:self.hover(False))
        self.bind('<ButtonRelease-1>',lambda e:self.invoke());self.bind('<Return>',lambda e:self.invoke());self.bind('<space>',lambda e:self.invoke())
    def invoke(self):self.callback()
    def hover(self,on):self.hot=on;self.draw()
    def draw(self):
        from manager_widgets import surface
        from PIL import Image,ImageDraw,ImageTk
        self.delete('all');w,h=self.winfo_width(),self.winfo_height();s=self.s
        if self.hot:
            self.background=surface(self,w,h,'#eae9e6','#eae9e6',6*s)
            self.create_image(0,0,image=self.background,anchor='nw')
        self.create_text(round(8*s),h/2,text=self.title,font=self.font,fill='#262624',anchor='w')
        if self.checked:
            size=max(1,round(10*s));im=Image.new('RGBA',(size*3,size*3));d=ImageDraw.Draw(im)
            d.line([(round(x*s*3),round(y*s*3)) for x,y in ((1.5,5),(4,7.3),(8.5,2.7))],fill='#666661',width=max(1,round(1.1*s*3)),joint='curve')
            self.mark=ImageTk.PhotoImage(im.resize((size,size),Image.Resampling.LANCZOS))
            self.create_image(w-round(12*s),h/2,image=self.mark)

class ContextMenu:
    def __init__(self,x,y,selected=None):
        from native_badge import Win
        self.win=Win();self.root=tk.Tk();self.root.withdraw();self.root.overrideredirect(True)
        self.root.attributes('-topmost',True);self.root.configure(bg='#deddd9')
        self.pending=[];self.root.bind('<Destroy>',self.cleanup)
        self.shadow=None;self.icons=[]
        self.action=None;self.selected=selected;self.config=load_config();self.buttons={}
        # Place the empty window on the click monitor before sizing its contents.
        # Otherwise Tk can rescale a primary-monitor layout again on a second screen.
        self.root.update_idletasks()
        target_hwnd=self.win.u.GetAncestor(self.root.winfo_id(),2)
        self.win.u.SetWindowPos(target_hwnd,0,x,y,1,1,0x14)
        self.root.update_idletasks()
        scale=(self.win.u.GetDpiForWindow(target_hwnd) or 96)/96
        body=tk.Frame(self.root,bg='#faf9f7',padx=round(6*scale),pady=round(6*scale));body.pack(padx=1,pady=1,fill='both',expand=True)
        self.body=body;self.scale=scale
        self.build_page()
        self.root.update_idletasks()
        # Use the actual monitor work area, including negative desktop coordinates.
        import ctypes as C
        from ctypes import wintypes as W
        class Monitor(C.Structure):_fields_=[('size',W.DWORD),('monitor',W.RECT),('work',W.RECT),('flags',W.DWORD)]
        self.win.u.MonitorFromPoint.argtypes=[W.POINT,W.DWORD];self.win.u.MonitorFromPoint.restype=W.HANDLE
        self.win.u.GetMonitorInfoW.argtypes=[W.HANDLE,C.POINTER(Monitor)]
        info=Monitor();info.size=C.sizeof(info);self.win.u.GetMonitorInfoW(self.win.u.MonitorFromPoint(W.POINT(x,y),2),C.byref(info))
        self.work=info.work
        width,height=max(round(144*scale),self.root.winfo_reqwidth()),self.root.winfo_reqheight()
        x=max(info.work.left,min(x,info.work.right-width));y=max(info.work.top,min(y,info.work.bottom-height))
        self.root.geometry(f'{width}x{height}+0+0');self.root.deiconify();self.root.update_idletasks()
        hwnd=self.win.u.GetAncestor(self.root.winfo_id(),2);self.win.u.SetWindowPos(hwnd,-1,x,y,width,height,0x40)
        self.scale=scale
        from ui_presence import mark
        mark(self.root)
        self.decorate()
        self.pending.append(self.root.after(60,self.decorate))
        from interaction_runtime import trace
        trace('menu_ready')
        self.root.focus_force();self.root.bind('<Escape>',lambda e:self.switch_page(False) if self.modules_page else self.finish(None))
        self.pending.append(self.root.after(250,lambda:self.root.bind('<FocusOut>',self.blur)))
    def build_page(self,modules=False):
        for child in self.body.winfo_children():child.destroy()
        self.buttons={};self.modules_page=modules;s=self.scale
        def button(key,title,callback,bold=False,checked=None):
            row=MenuRow(self.body,title,callback,s,bold,checked)
            row.pack(fill='x');self.buttons[key]=row
        def divider():
            tk.Frame(self.body,height=1,bg='#e9e8e5').pack(fill='x',padx=round(5*s),pady=round(3*s))
        if modules:
            button('back','‹  返回',lambda:self.switch_page(False))
            divider()
            for item in self.config['items']:
                button(item['id'],item['label'],lambda i=item['id']:self.toggle(i),checked=item.get('enabled',True))
        else:
            button('manager','侧栏管理',lambda:self.finish('manager'),bold=True)
            button('refresh','刷新数据',lambda:self.finish('refresh'))
            button('modules','展示模块    ›',lambda:self.switch_page(True))
            items=self.config['items'];index=next((n for n,i in enumerate(items) if i['id']==self.selected),None)
            if index is not None:
                divider()
                if index>0:button('up','上移',lambda:self.move(-1))
                if index<len(items)-1:button('down','下移',lambda:self.move(1))
            divider()
            button('exit','退出插件',lambda:self.finish('exit'))
    def switch_page(self,modules):
        import ctypes as C
        from ctypes import wintypes as W
        hwnd=self.win.u.GetAncestor(self.root.winfo_id(),2);rect=W.RECT()
        self.win.u.GetWindowRect(hwnd,C.byref(rect))
        self.build_page(modules);self.root.update_idletasks()
        width=round(144*self.scale);height=self.root.winfo_reqheight()
        y=max(self.work.top,min(rect.top,self.work.bottom-height))
        self.root.geometry(f'{width}x{height}')
        self.root.update_idletasks()
        self.win.u.SetWindowPos(hwnd,-1,rect.left,y,width,height,0x40)
        self.decorate()
    def decorate(self):
        import ctypes as C
        from ctypes import wintypes as W
        from PIL import Image,ImageDraw,ImageFilter
        from tooltip_card import upload
        w=self.win;hwnd=w.u.GetAncestor(self.root.winfo_id(),2);rect=W.RECT();w.u.GetWindowRect(hwnd,C.byref(rect))
        width,height=rect.right-rect.left,rect.bottom-rect.top
        radius=round(12*self.scale)
        region=w.g.CreateRoundRectRgn(0,0,width+1,height+1,2*radius,2*radius)
        if region and not w.u.SetWindowRgn(hwnd,region,True):w.g.DeleteObject(region)
        pad=round(10*self.scale)
        image=Image.new('RGBA',(width+2*pad,height+2*pad))
        ImageDraw.Draw(image).rounded_rectangle((pad,pad+round(2*self.scale),pad+width,pad+height+round(2*self.scale)),radius=radius,fill=(0,0,0,38))
        image=image.filter(ImageFilter.GaussianBlur(round(4*self.scale)))
        if not self.shadow:
            self.shadow=w.u.CreateWindowExW(0x80000|0x20|0x80|0x08000000,'STATIC','Sidebar menu shadow',0x80000000|0x08000000,rect.left-pad,rect.top-pad,image.width,image.height,hwnd,None,w.instance,None)
        upload(w,self.shadow,image)
        w.u.SetWindowPos(self.shadow,hwnd,rect.left-pad,rect.top-pad,image.width,image.height,0x10|0x40)
    def cleanup(self,event):
        if event.widget==self.root:
            if self.shadow and self.win.u.IsWindow(self.shadow):self.win.u.DestroyWindow(self.shadow)
            self.shadow=None
            for timer in self.pending:
                try:self.root.after_cancel(timer)
                except tk.TclError:pass
            self.pending.clear()
    def blur(self,event):
        def check():
            if self.root.winfo_exists() and self.root.focus_displayof() is None:self.finish(None)
        self.pending.append(self.root.after(80,check))
    def finish(self,action):self.action=action;self.root.quit()
    def toggle(self,item_id):
        config=load_config()
        item=next(i for i in config['items'] if i['id']==item_id);item['enabled']=not item.get('enabled',True)
        save_config(config);self.config=config
        self.buttons[item_id].checked=item['enabled'];self.buttons[item_id].draw()
    def move(self,delta):
        config=load_config();items=config['items'];index=next((n for n,i in enumerate(items) if i['id']==self.selected),None)
        if index is not None and 0<=index+delta<len(items):
            items[index],items[index+delta]=items[index+delta],items[index];save_config(config)
        self.finish(None)
    def run(self):
        self.root.mainloop();self.root.destroy()
        if self.action=='manager':
            from sidebar_manager import run_manager
            run_manager()
        elif self.action in ('refresh','exit'):
            import json,ctypes as C
            from ctypes import wintypes as W
            state=json.loads((ROOT/'status.json').read_text(encoding='utf-8'));pid=W.DWORD()
            self.win.u.GetWindowThreadProcessId(state['hwnd'],C.byref(pid))
            if pid.value==state['pid'] and self.win.process_path(state['hwnd']).lower().endswith('pythonw.exe'):
                self.win.u.PostMessageW(state['hwnd'],0x8005 if self.action=='refresh' else 0x10,0,0)

if __name__=='__main__':ContextMenu(int(sys.argv[1]),int(sys.argv[2]),sys.argv[3] if len(sys.argv)>3 else None).run()
