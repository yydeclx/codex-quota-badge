"""Lightweight local management window; no web server or extra runtime."""
import sys
import subprocess
import copy
import queue
import threading
import tkinter as tk
from tkinter import ttk,messagebox,simpledialog
from pathlib import Path
from sidebar_data import load_config,save_config,validate_config,ROOT,GAP_MIN,GAP_MAX
from sidebar_styles import STYLES,preview
import sidebar_startup
from manager_widgets import RoundButton,RoundPanel,StyleTile,VersionBadge,SlimScrollbar
from quota_styles import QUOTA_STYLES
from font_fallback import ui_family

def notify_sidebar():
    """Ask the running local companion to reload the saved config now."""
    import ctypes as C,json
    from ctypes import wintypes as W
    status_path=ROOT/'status.json'
    if not status_path.exists():return False
    try:
        state=json.loads(status_path.read_text(encoding='utf-8'))
        hwnd=int(state.get('hwnd') or 0)
        from native_badge import Win
        win=Win()
        if not hwnd or not win.u.IsWindow(hwnd):return False
        pid=W.DWORD();win.u.GetWindowThreadProcessId(hwnd,C.byref(pid))
        if pid.value!=state.get('pid') or not win.process_path(hwnd).lower().endswith('pythonw.exe'):return False
        return bool(win.u.PostMessageW(hwnd,0x8008,0,0))
    except (OSError,ValueError,TypeError):return False

def launch():
    subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')),str(Path(__file__))],creationflags=subprocess.CREATE_NO_WINDOW)

class Toggle(tk.Canvas):
    def __init__(self,parent,var,scale):
        self.var=var;self.s=scale
        super().__init__(parent,width=round(42*scale),height=round(26*scale),bg='white',highlightthickness=0,cursor='hand2',takefocus=True)
        self.bind('<Button-1>',self.flip);self.bind('<space>',self.flip)
        var.trace_add('write',lambda *_:self.draw());self.draw()
    def flip(self,event=None):self.var.set(not self.var.get())
    def draw(self):
        self.delete('all');s=self.s;color='#222827' if self.var.get() else '#dadfdd'
        self.create_line(13*s,13*s,29*s,13*s,width=24*s,capstyle='round',fill=color)
        x=(29 if self.var.get() else 13)*s
        self.create_oval(x-9*s,4*s,x+9*s,22*s,fill='white',outline='')

class Manager:
    BG='#faf9f8';INK='#292927';MUTED='#83827e'
    def __init__(self,initial_page=0,initial_style=0):
        from native_badge import Win
        self.win=Win()  # Establish DPI awareness before creating Tk, not during previews.
        from manager_icons import prepare_identity,set_window_icons
        prepare_identity()
        from version import PLUGIN_VERSION
        self.plugin_version=PLUGIN_VERSION
        self.root=tk.Tk();self.root.withdraw();self.root.title('侧栏管理');self.root.iconbitmap(str(ROOT/'codex-monochrome.ico'))
        self.root.update_idletasks();self.s=(self.win.u.GetDpiForWindow(self.root.winfo_id()) or 96)/96
        self.root.geometry(f'{self.px(800)}x{min(self.px(610),self.root.winfo_screenheight()-self.px(70))}')
        self.root.minsize(self.px(800),self.px(510));self.root.configure(bg=self.BG)
        self.preview_timer=None;self.preview_cache={};self.loaded_galleries=set()
        self.current_style=initial_style;self.page_positions={};self.revealed=False
        self.config=load_config();self.rows=[];self.ready=False;self.dirty=False
        self.switching=False;self.edit_revision=0;self.save_results=queue.Queue()
        st=ttk.Style();st.theme_use('clam')
        st.configure('TButton',font=self.font(14),padding=(self.px(12),self.px(8)),borderwidth=0,relief='flat',background='#eeedea',foreground=self.INK)
        st.map('TButton',background=[('active','#e3e2df')])
        st.configure('Primary.TButton',background='#30302e',foreground='white');st.map('Primary.TButton',background=[('active','#484844')])
        st.configure('TSpinbox',font=self.font(15),padding=self.px(7),arrowsize=self.px(14),bordercolor='#dce1dd',lightcolor='white',darkcolor='white')
        shell=tk.Frame(self.root,bg=self.BG,padx=self.px(20),pady=self.px(16));shell.pack(fill='both',expand=True)
        self.nav_frame=tk.Frame(shell,bg=self.BG,width=self.px(148));self.nav_frame.pack(side='left',fill='y',padx=(0,self.px(20)));self.nav_frame.pack_propagate(False)
        title_row=tk.Frame(self.nav_frame,bg=self.BG);title_row.pack(fill='x',padx=self.px(10),pady=(self.px(3),self.px(22)))
        self.label(title_row,'侧栏管理',17,bold=True).pack(side='left')
        VersionBadge(title_row,f'V{self.plugin_version}',self.font(10,True),self.s,width=54,height=24,radius=12).pack(side='left',padx=(self.px(7),0))
        self.nav=[]
        for index,title in enumerate(('模块与顺序','外观与布局','行为与服务')):
            button=RoundButton(self.nav_frame,title,lambda i=index:self.show(i),self.font(14),self.s,width=128)
            button.configure(height=self.px(36));button.pack(fill='x',pady=(0,self.px(5)));self.nav.append(button)
        main=tk.Frame(shell,bg=self.BG);main.pack(fill='both',expand=True)
        footer=tk.Frame(main,bg=self.BG);footer.pack(side='bottom',fill='x',pady=(self.px(12),0))
        tk.Frame(footer,bg='#e6e5e3',height=1).pack(fill='x',pady=(0,self.px(10)))
        self.message=tk.StringVar(value='所有更改已保存')
        self.label(footer,'',12,muted=True,textvariable=self.message).pack(side='left')
        self.save_button=RoundButton(footer,'保存并应用',self.save,self.font(14),self.s,primary=True,width=96);self.save_button.pack(side='right')
        header=tk.Frame(main,bg=self.BG);header.pack(fill='x',pady=(0,self.px(14)))
        self.page_title=tk.StringVar();self.page_subtitle=tk.StringVar()
        self.label(header,'',22,bold=True,textvariable=self.page_title).pack(anchor='w')
        self.label(header,'',12,muted=True,textvariable=self.page_subtitle).pack(anchor='w',pady=(self.px(5),0))
        area=tk.Frame(main,bg=self.BG);area.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(area,bg=self.BG,highlightthickness=0);self.scrollbar=SlimScrollbar(area,self.scroll_content,self.s);scroll=self.scrollbar
        scroll.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True);self.canvas.configure(yscrollcommand=scroll.set)
        self.content=tk.Frame(self.canvas,bg=self.BG);self.window=self.canvas.create_window(0,0,window=self.content,anchor='nw')
        # Prepare visited pages behind the active page. Only the active page
        # determines the scroll region; unopened pages do no initial paint work.
        self.content.grid_propagate(False);self.content.columnconfigure(0,weight=1)
        self.canvas.bind('<Configure>',self.resize_content)
        self.root.bind('<MouseWheel>',self.mousewheel)
        self.pages=[tk.Frame(self.content,bg=self.BG) for _ in range(3)]
        for index,page in enumerate(self.pages):
            page.bind('<Configure>',lambda event,i=index:self.sync_page_height(i))
        modules,appearance,settings=self.pages
        self.heading(modules,'模块与顺序','按侧栏顺序排列 · 关闭开关隐藏，展开可调整间距')
        summary=tk.Frame(modules,bg=self.BG);summary.pack(fill='x',pady=(0,self.px(8)))
        self.enabled_count=tk.StringVar()
        self.label(summary,'',12,muted=True,textvariable=self.enabled_count).pack(side='left')
        self.label(summary,'顺序 / 详情 / 显示',12,muted=True).pack(side='right',padx=self.px(10))
        self.row_frame=tk.Frame(modules,bg=self.BG);self.row_frame.pack(fill='x');self.build_rows()
        self.row_frame.bind('<Configure>',lambda event:self.sync_page_height(0))
        actions=tk.Frame(modules,bg=self.BG);actions.pack(fill='x',pady=self.px(8))
        self.button(actions,text='＋ 用量模块',command=self.add).pack(side='left')
        self.button(actions,text='＋ 功能模块',command=self.add_module).pack(side='left',padx=self.px(8))
        self.style=tk.StringVar(value=self.config['style']);self.quota_style=tk.StringVar(value=self.config.get('quotaStyle','01'))
        self.style_cards=[];self.quota_cards=[]
        self.heading(appearance,'外观','独立设置 Token 与周额度样式，统一调整侧栏位置')
        tabs=tk.Frame(appearance,bg=self.BG);tabs.pack(fill='x',pady=(0,self.px(10)))
        self.style_tabs=[];self.style_grids=[]
        for index,title in enumerate(('Token 样式','周额度样式')):
            button=RoundButton(tabs,title,lambda i=index:self.show_styles(i),self.font(14),self.s,width=112)
            button.pack(side='left',padx=(0,self.px(5)));self.style_tabs.append(button)
        for index,(catalog,var,cards) in enumerate(((STYLES,self.style,self.style_cards),(QUOTA_STYLES,self.quota_style,self.quota_cards))):
            grid=tk.Frame(appearance,bg=self.BG);self.style_grids.append(grid)
            for n,(code,name) in enumerate(catalog.items()):
                tile=StyleTile(grid,code,name,None,var,self.font(12),self.s)
                tile.grid(row=n//5,column=n%5,sticky='nsew',padx=self.px(3),pady=self.px(3));grid.columnconfigure(n%5,weight=1,uniform='style')
                cards.append((code,tile))
        self.style.trace_add('write',lambda *_:self.select_style());self.quota_style.trace_add('write',lambda *_:self.select_style());self.select_style()
        self.show_styles(initial_style)
        spacing=self.card(appearance)
        self.label(spacing,'位置与间距',15,bold=True).pack(anchor='w',pady=(0,self.px(6)))
        self.bottom=tk.IntVar(value=self.config['bottomOffset']);self.gap=tk.IntVar(value=self.config['gap'])
        self.setting(spacing,'底部留白','数值越小，最下方的额度越往下',self.bottom,45,180,'DIP')
        self.horizontal=tk.IntVar(value=self.config.get('horizontalOffset',0))
        self.setting(spacing,'水平偏移','右移为正，左移为负；用于适配不同客户端侧栏宽度',self.horizontal,-40,40,'DIP')
        self.setting(spacing,'统一模块间距','正值拉开，负值收紧；保存后统一应用到各模块',self.gap,GAP_MIN,GAP_MAX,'DIP')
        self.label(appearance,'底部默认 87 DIP；调低时留意帮助按钮和头像的位置',12,muted=True).pack(anchor='w',pady=self.px(8))
        self.heading(settings,'行为与服务','活动提示、数据刷新与本地入口')
        links=self.card(settings)
        line=self.option(links,'服务与快捷入口','管理本地服务检测和快捷打开的地址',bold=True)
        self.button(line,text='配置入口',command=self.configure_links).pack(side='right')
        self.divider(links)
        self.autostart=tk.BooleanVar(value=sidebar_startup.enabled())
        self.option(links,'随客户端自动显示','登录后待命，随 Codex / ChatGPT 显示与隐藏',self.autostart)
        activity=self.card(settings)
        self.label(activity,'活动与显示',15,bold=True).pack(anchor='w',pady=(0,self.px(10)))
        self.activity_enabled=tk.BooleanVar(value=self.config.get('activityEnabled',True))
        self.option(activity,'会话状态指示','绿点呼吸：运行中 · 蓝点常亮：完成未读',self.activity_enabled)
        self.divider(activity)
        self.realtime_metrics_enabled=tk.BooleanVar(value=self.config.get('realtimeMetricsEnabled',True))
        self.option(activity,'实时指标补丁',('开启：实时首字等待与输出速度；关闭：请求结束后统计','切换需重启本地 OpenCodex，可能短暂中断连接'),self.realtime_metrics_enabled)
        self.divider(activity)
        self.compact_mode=tk.BooleanVar(value=self.config.get('compactMode',False))
        self.option(activity,'紧凑模式','运行中、待查看为 0 持续 5 秒后隐藏；有任务时恢复',self.compact_mode)
        alert=self.card(settings)
        self.label(alert,'额度与刷新',15,bold=True).pack(anchor='w',pady=(0,self.px(10)))
        self.alert_enabled=tk.BooleanVar(value=self.config.get('quotaAlertEnabled',False))
        self.option(alert,'周额度提醒','剩余额度达到或低于阈值时提醒，每周期同一阈值只提醒一次',self.alert_enabled)
        self.alert_threshold=tk.IntVar(value=self.config.get('quotaAlertThreshold',20))
        self.setting(alert,'提醒阈值','可直接输入 1–100 的整数',self.alert_threshold,1,100,'%')
        self.divider(alert);self.refresh=tk.IntVar(value=self.config['refreshSeconds'])
        self.setting(alert,'用量刷新频率','默认每 60 秒更新；间隔越长，查询越少',self.refresh,30,3600,'秒')
        recovery=self.card(settings)
        line=self.option(recovery,'侧栏没有显示？','保留当前设置，重新连接客户端窗口',bold=True)
        self.button(line,text='启动 / 恢复侧栏',command=self.start).pack(side='right')
        self.label(settings,'用量统计来自本机 OpenCodex，非账号全设备或公司账单总量',12,muted=True).pack(anchor='w',pady=self.px(10))
        for var in (self.style,self.quota_style,self.bottom,self.horizontal,self.gap,self.autostart,self.refresh,self.activity_enabled,self.realtime_metrics_enabled,self.compact_mode,self.alert_enabled,self.alert_threshold):var.trace_add('write',self.changed)
        self.gap.trace_add('write',self.apply_gap)
        self.ready=True;self.show(initial_page);self.root.protocol('WM_DELETE_WINDOW',self.close)
        set_window_icons(self.root)
        from ui_presence import mark
        mark(self.root)
        # Map once while transparent so native child geometries and panel idle
        # layouts have settled before the first opaque frame reaches the user.
        self.root.attributes('-alpha',0);self.root.deiconify()
        self.settle_page(initial_page)
        self.root.after_idle(self.reveal)
    def reveal(self):
        self.settle_page(self.current_page)
        self.root.attributes('-alpha',1);self.revealed=True
    def load_preview(self,index=None):
        index=self.current_style if index is None else index
        if index in self.loaded_galleries:return
        from PIL import ImageTk
        cards=self.quota_cards if index else self.style_cards
        for code,tile in cards:
            preview_scale=1.5*self.s
            if index:
                from quota_styles import quota_height
                preview_scale=min(preview_scale,54*self.s/quota_height(code))
            key=(index,code,preview_scale)
            if key not in self.preview_cache:
                self.preview_cache[key]=ImageTk.PhotoImage(preview(code,preview_scale,value=68 if index else '736M',quota=bool(index)),master=self.root)
            tile.icon=self.preview_cache[key];tile.draw()
        self.loaded_galleries.add(index)

    def px(self,n):return round(n*self.s)
    def font(self,n,bold=False):return (ui_family(),-self.px(n*.86),'bold' if bold else 'normal')
    def label(self,parent,text,size=14,bold=False,muted=False,**kw):
        return tk.Label(parent,text=text,font=self.font(size,bold),bg=parent.cget('bg'),fg=self.MUTED if muted else self.INK,anchor='w',justify='left',**kw)
    def heading(self,page,title,subtitle):
        page.heading=(title,subtitle)
    def button(self,parent,text,command,width=None,state='normal',**kwargs):
        button=RoundButton(parent,text,command,self.font(14),self.s,width=28 if width==2 else width if width is not None else max(64,len(text)*9+24))
        button.disabled=state=='disabled'
        return button
    def card(self,parent):
        panel=RoundPanel(parent,self.s,12);panel.pack(fill='x',pady=(0,self.px(10)));return panel.body
    def divider(self,parent):
        tk.Frame(parent,bg='#eeedea',height=1).pack(fill='x',pady=self.px(8))
    def description(self,parent,text):
        label=self.label(parent,text,12,muted=True,wraplength=self.px(360))
        def wrap(event):
            width=max(1,event.width)
            if int(label.cget('wraplength'))!=width:label.configure(wraplength=width)
        parent.bind('<Configure>',wrap,add='+')
        label.pack(anchor='w',fill='x',pady=(self.px(3),0));return label
    def option(self,parent,title,subtitle,var=None,bold=False):
        row=tk.Frame(parent,bg='white');row.pack(fill='x')
        controls=tk.Frame(row,bg='white');controls.pack(side='right',anchor='n',padx=(self.px(12),0))
        if var is not None:Toggle(controls,var,self.s).pack(side='right')
        text=tk.Frame(row,bg='white');text.pack(side='left',fill='x',expand=True)
        self.label(text,title,14,bold=bold).pack(anchor='w')
        for line in (subtitle if isinstance(subtitle,tuple) else (subtitle,)):self.description(text,line)
        return controls
    def stepper(self,parent,var,low,high):
        frame=tk.Frame(parent,bg='#f3f2ef',padx=self.px(2),pady=self.px(2),width=self.px(102),height=self.px(28));frame.pack_propagate(False)
        def adjust(delta):
            try:var.set(max(low,min(high,var.get()+delta)))
            except tk.TclError:var.set(low)
        for text,delta in (('−',-1),('+',1)):
            tk.Button(frame,text=text,font=self.font(14),bg='#f3f2ef',fg=self.INK,relief='flat',bd=0,width=2,padx=0,pady=0,cursor='hand2',command=lambda d=delta:adjust(d)).pack(side='left' if delta<0 else 'right',fill='y')
        tk.Entry(frame,textvariable=var,font=self.font(14),width=4,justify='center',relief='flat',bd=0,bg='white',fg=self.INK).pack(side='left',fill='both',expand=True)
        return frame
    def setting(self,parent,title,subtitle,var,low,high,unit,apply=None):
        row=tk.Frame(parent,bg='white');row.pack(fill='x',pady=self.px(6))
        controls=tk.Frame(row,bg='white');controls.pack(side='right',padx=(self.px(12),0))
        self.label(controls,unit,13,muted=True).pack(side='right',padx=self.px(8))
        self.stepper(controls,var,low,high).pack(side='right')
        text=tk.Frame(row,bg='white');text.pack(side='left',fill='x',expand=True)
        if apply:
            title_line=tk.Frame(text,bg='white');title_line.pack(fill='x')
            self.label(title_line,title,14).pack(side='left')
            action=self.button(title_line,text='应用到每项',command=apply,width=94)
            action.configure(height=self.px(26));action.pack(side='left',padx=(self.px(10),0))
        else:self.label(text,title,14).pack(anchor='w')
        self.description(text,subtitle)
    def resize_content(self,event):
        self.canvas.itemconfigure(self.window,width=event.width)
        if hasattr(self,'current_page'):self.sync_page_height(self.current_page)
    def scroll_content(self,*args):
        self.sync_page_height(self.current_page)
        if self.pages[self.current_page].winfo_reqheight()>self.canvas.winfo_height():self.canvas.yview(*args)
    def mousewheel(self,event):
        self.scroll_content('scroll',-int(event.delta/120)*3,'units')
        return 'break'
    def sync_page_height(self,index):
        if index!=getattr(self,'current_page',None):return
        height=self.pages[index].winfo_reqheight()
        if int(self.content.cget('height'))!=height:self.content.configure(height=height)
        viewport=max(1,self.canvas.winfo_height());region=(0,0,self.canvas.winfo_width(),max(height,viewport))
        if tuple(map(float,self.canvas.cget('scrollregion').split()))!=region:self.canvas.configure(scrollregion=region)
        if height<=viewport:self.canvas.yview_moveto(0)
    def settle_page(self,index):
        self.root.update_idletasks();self.sync_page_height(index);self.root.update_idletasks()
    def show(self,index):
        if hasattr(self,'current_page'):self.page_positions[self.current_page]=self.canvas.yview()[0]
        if index==1:self.prepare_gallery(self.current_style)
        self.pages[index].grid(row=0,column=0,sticky='nsew')
        if hasattr(self,'current_page') and self.current_page!=index:self.pages[index].lower(self.pages[self.current_page])
        self.root.update_idletasks()
        self.current_page=index;self.settle_page(index)
        self.canvas.yview_moveto(self.page_positions.get(index,0))
        self.page_title.set(self.pages[index].heading[0]);self.page_subtitle.set(self.pages[index].heading[1])
        for n,b in enumerate(self.nav):b.select(n==index)
        self.pages[index].tkraise()
    def prepare_gallery(self,index):
        # Decode just this gallery, synchronously, before exposing its tiles.
        self.load_preview(index)
        for i,grid in enumerate(self.style_grids):
            if i==index:grid.pack(fill='x',after=self.style_tabs[0].master)
            else:grid.pack_forget()
    def show_styles(self,index):
        self.current_style=index
        if getattr(self,'current_page',None)==1:
            self.prepare_gallery(index);self.settle_page(1)
        for i,button in enumerate(self.style_tabs):button.select(i==index)
    def select_style(self):
        for code,frame in self.style_cards+self.quota_cards:frame.draw()
    def changed(self,*_):
        if self.ready:
            self.dirty=True;self.edit_revision+=1
            if not self.switching:self.message.set('有未保存的更改')
    def close(self):
        if self.switching:
            self.message.set('正在切换补丁并重启 OpenCodex，请等待完成后关闭');return
        if self.dirty:
            answer=messagebox.askyesnocancel('未保存的更改','关闭前保存当前设置？',parent=self.root)
            if answer is None:return
            if answer and not self.save():return
        self.root.destroy()
    def build_rows(self):
        for child in self.row_frame.winfo_children():child.destroy()
        self.rows=[];self.module_rows=[]
        group=self.card(self.row_frame)
        for n,item in enumerate(self.config['items']):
            if n:tk.Frame(group,bg='#eeedea',height=1).pack(fill='x')
            enabled=tk.BooleanVar(value=item.get('enabled',True));gap=tk.IntVar(value=item.get('gapAfter',0));self.rows.append((enabled,gap))
            row=tk.Frame(group,bg='white',height=self.px(46));row.pack(fill='x');row.pack_propagate(False);self.module_rows.append(row)
            Toggle(row,enabled,self.s).pack(side='right',padx=(self.px(8),0))
            detail=tk.Frame(group,bg='white')
            expand=self.button(row,text='›',width=2,command=lambda:None)
            expand.pack(side='right')
            def fold(frame=detail,button=expand,anchor=row):
                if frame.winfo_manager():frame.pack_forget();button.title='›'
                else:frame.pack(fill='x',after=anchor,pady=(self.px(8),self.px(4)));button.title='⌄'
                button.draw()
            expand.command=fold
            self.button(row,text='下移',width=40,command=lambda i=n:self.move(i,1),state='normal' if n<len(self.config['items'])-1 else 'disabled').pack(side='right')
            self.button(row,text='上移',width=40,command=lambda i=n:self.move(i,-1),state='normal' if n else 'disabled').pack(side='right')
            self.label(row,f'{n+1:02d}',12,muted=True,width=3).pack(side='left',padx=(0,self.px(6)))
            text=tk.Frame(row,bg='white');text.pack(side='left',fill='x',expand=True,padx=(0,self.px(8)))
            self.label(text,item['label'],14,bold=True).pack(anchor='w')
            from sidebar_modules import MODULES
            description=MODULES.get(item['type'],('', '每周剩余额度' if item['type']=='weekly_quota' else '今日及历史 Token · '+item.get('provider','')))[1]
            description_label=self.label(text,description,12,muted=True)
            description_label.pack(anchor='w',fill='x',pady=(self.px(2),0))
            self.label(detail,'下方间距',12,muted=True).pack(side='left',padx=(self.px(25),self.px(8)))
            self.stepper(detail,gap,GAP_MIN,GAP_MAX).pack(side='left')
            self.label(detail,'DIP',12,muted=True).pack(side='left',padx=self.px(6))
            if item['id'] not in ('chatgpt_today','skg_today','weekly_quota'):
                self.button(detail,text='移除',command=lambda i=n:self.remove(i)).pack(side='right')
            enabled.trace_add('write',self.changed);enabled.trace_add('write',self.update_enabled_count);gap.trace_add('write',self.changed)
        self.update_enabled_count()
    def update_enabled_count(self,*_):
        self.enabled_count.set(f'已启用 {sum(enabled.get() for enabled,_ in self.rows)} / {len(self.rows)} 个模块')
    def collect(self):
        for item,(enabled,gap) in zip(self.config['items'],self.rows):item.update(enabled=enabled.get(),gapAfter=gap.get())
    def move(self,n,d):
        if not (0<=n<len(self.config['items']) and 0<=n+d<len(self.config['items'])):return
        try:self.collect()
        except tk.TclError:return messagebox.showerror('输入有误','间距请输入整数')
        a=self.config['items'];a[n],a[n+d]=a[n+d],a[n];self.build_rows();self.changed()
    def remove(self,n):
        self.collect();self.config['items'].pop(n);self.build_rows();self.changed()
    def add(self):
        if len(self.config['items'])>=16:return messagebox.showinfo('模块上限','最多可注册 16 个模块')
        provider=simpledialog.askstring('新增用量模块','输入 OpenCodex provider 名称（区分大小写）',parent=self.root)
        if not provider or not provider.strip():return
        provider=provider.strip()
        label=simpledialog.askstring('显示名称','输入短标签（建议 1–4 个字符）',initialvalue=provider[:4],parent=self.root)
        if not label:return
        self.collect()
        import uuid
        self.config['items'].insert(max(0,len(self.config['items'])-1),dict(id='provider_'+uuid.uuid4().hex[:8],type='opencodex_today',label=label[:14],provider=provider,enabled=True,gapAfter=self.gap.get()))
        self.build_rows();self.changed()
    def add_module(self):
        from sidebar_modules import MODULES
        popup=tk.Toplevel(self.root);popup.title('添加功能模块');popup.configure(bg=self.BG)
        for kind,(title,description) in MODULES.items():
            def add(k=kind,t=title):
                self.collect()
                if any(i['type']==k for i in self.config['items']):
                    for i in self.config['items']:
                        if i['type']==k:i['enabled']=True
                elif len(self.config['items'])<16:self.config['items'].insert(0,{'id':k,'type':k,'label':t,'enabled':True,'gapAfter':self.gap.get()})
                self.build_rows();self.changed();popup.destroy()
            self.button(popup,text=title+' · '+description,command=add).pack(fill='x',padx=self.px(16),pady=self.px(6))
    def configure_links(self):
        from sidebar_details import LinkEditor
        LinkEditor(self.root,self.config,self.changed)
    def apply_gap(self,*trace):
        try:
            value=self.gap.get()
            if not GAP_MIN<=value<=GAP_MAX:raise ValueError()
            for _,gap in self.rows:gap.set(value)
        except (ValueError,tk.TclError):
            # A lone '-' is a normal intermediate state while typing a signed
            # value. Validate on save rather than interrupting each keystroke.
            if not trace:messagebox.showerror('输入有误','间距范围为 -10–30')
    def save(self):
        if self.switching:
            self.message.set('正在切换补丁并重启 OpenCodex，请等待完成后保存');return False
        try:
            snapshot=copy.deepcopy(self.config)
            for item,(enabled,gap) in zip(snapshot['items'],self.rows):item.update(enabled=enabled.get(),gapAfter=gap.get())
            snapshot.update(realtimeMetricsEnabled=self.realtime_metrics_enabled.get(),compactMode=self.compact_mode.get(),quotaAlertEnabled=self.alert_enabled.get(),quotaAlertThreshold=self.alert_threshold.get(),activityEnabled=self.activity_enabled.get(),style=self.style.get(),quotaStyle=self.quota_style.get(),bottomOffset=self.bottom.get(),horizontalOffset=self.horizontal.get(),gap=self.gap.get(),refreshSeconds=self.refresh.get());validate_config(snapshot)
            previous_mode=load_config().get('realtimeMetricsEnabled',True)
            previous=sidebar_startup.enabled();desired_autostart=self.autostart.get()
            if snapshot['realtimeMetricsEnabled']!=previous_mode:
                self.switching=True;self.save_button.disabled=True;self.save_button.draw()
                self.message.set('正在切换补丁并重启 OpenCodex…')
                revision=self.edit_revision
                try:
                    threading.Thread(target=self.switch_mode_worker,args=(self.save_results,snapshot,desired_autostart,previous,previous_mode,revision),daemon=False).start()
                except Exception:
                    self.switching=False;self.save_button.disabled=False;self.save_button.draw();raise
                self.root.after(100,self.poll_save_result)
                return False  # Pending: a close handler must keep the window alive.
            sidebar_startup.set_enabled(desired_autostart)
            try:save_config(snapshot)
            except Exception:
                sidebar_startup.set_enabled(previous);raise
            self.saved(snapshot,self.edit_revision);return True
        except Exception as exc:
            self.message.set('保存失败 · 更改未保存');messagebox.showerror('未保存',str(exc),parent=self.root);return False
    @staticmethod
    def switch_mode_worker(results,snapshot,desired_autostart,previous_autostart,previous_mode,revision):
        # All Tk values are captured by save(); communicate only through the queue.
        mode_applied=False;startup_attempted=False
        try:
            from patch_mode import apply_mode
            apply_mode(snapshot['realtimeMetricsEnabled']);mode_applied=True
            startup_attempted=True;sidebar_startup.set_enabled(desired_autostart)
            save_config(snapshot)
        except Exception as exc:
            errors=[str(exc)]
            if mode_applied:
                try:apply_mode(previous_mode)
                except Exception as rollback:errors.append('补丁模式回滚失败：'+str(rollback))
            if startup_attempted:
                try:sidebar_startup.set_enabled(previous_autostart)
                except Exception as rollback:errors.append('自启动回滚失败：'+str(rollback))
            results.put((snapshot,revision,'\n'.join(errors)))
        else:results.put((snapshot,revision,None))
    def poll_save_result(self):
        try:snapshot,revision,error=self.save_results.get_nowait()
        except queue.Empty:
            self.root.after(100,self.poll_save_result);return
        self.switching=False;self.save_button.disabled=False;self.save_button.draw()
        if error is not None:
            self.dirty=True;self.message.set('切换失败 · 更改未保存')
            messagebox.showerror('未保存',error,parent=self.root)
        else:self.saved(snapshot,revision)
    def saved(self,snapshot,revision):
        # Edits made while the worker ran remain pending instead of being discarded.
        self.dirty=self.edit_revision!=revision
        if not self.dirty:self.config=snapshot
        try:applied=notify_sidebar()
        except Exception:applied=False  # Config has already been committed.
        self.message.set('已保存 · 仍有未保存的更改' if self.dirty else '已保存并应用' if applied else '已保存 · 侧栏将在下次启动应用')
    def start(self):
        subprocess.Popen([r'C:\Program Files\PowerShell\7\pwsh.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'start.ps1')],creationflags=subprocess.CREATE_NO_WINDOW)
        self.message.set('已请求启动，已有实例时不会重复创建')
    def run(self):self.root.mainloop()

def run_manager(quota=False):
    import ctypes as C
    from ctypes import wintypes as W
    import time
    kernel=C.WinDLL('kernel32',use_last_error=True);user=C.WinDLL('user32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[C.c_void_p,W.BOOL,W.LPCWSTR];kernel.CreateMutexW.restype=W.HANDLE
    kernel.CloseHandle.argtypes=[W.HANDLE]
    user.FindWindowW.argtypes=[W.LPCWSTR,W.LPCWSTR];user.FindWindowW.restype=W.HWND
    user.ShowWindow.argtypes=[W.HWND,C.c_int];user.SetForegroundWindow.argtypes=[W.HWND]
    handle=kernel.CreateMutexW(None,False,'Local\\CodexQuotaSidebarManager')
    if not handle:raise C.WinError(C.get_last_error())
    exists=C.get_last_error()==183
    try:
        if exists:
            for _ in range(20):
                hwnd=user.FindWindowW('TkTopLevel','侧栏管理')
                if hwnd:
                    user.ShowWindow(hwnd,9);user.SetForegroundWindow(hwnd);return
                time.sleep(.05)
            return
        manager=Manager(initial_page=1 if quota else 0,initial_style=1 if quota else 0)
        manager.run()
    finally:kernel.CloseHandle(handle)

if __name__=='__main__':run_manager('--quota' in sys.argv)
