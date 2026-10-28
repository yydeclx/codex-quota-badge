"""On-demand native detail panels; reads the companion's sanitized snapshot."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from sidebar_data import ROOT,load_config,validate_config
from sidebar_modules import MODULES,local_url
from sidebar_telemetry import recent_description,speed_description
from manager_widgets import RoundButton,RoundPanel,VersionBadge,SlimScrollbar
from font_fallback import ui_family

def open_thread(identity):
    if not re.fullmatch(r'[0-9a-f-]{36}',identity):raise ValueError('Invalid thread identity')
    os.startfile('codex://threads/'+identity)

def open_shortcut(item):
    kind=item['kind']
    if kind=='manager':
        from sidebar_manager import launch
        launch()
    elif kind=='folder':
        target=Path(item['target'])
        if not target.is_dir():raise ValueError('目录不存在')
        os.startfile(str(target))
    else:
        root=Path(os.environ.get('OPENCODEX_HOME') or Path.home()/'.opencodex')
        state=json.loads((root/'runtime-port.json').read_text(encoding='utf-8'));port=state['port']
        if type(port)!=int or not 1<=port<=65535:raise ValueError('服务端口无效')
        os.startfile(f'http://127.0.0.1:{port}/'+('#/usage' if kind=='usage' else ''))

def diagnostic(snapshot,provider):
    chain=snapshot.get('chains',{}).get(provider,{})
    latest=chain.get('latest') or {}
    # Explicit schema: never serialize source records wholesale into the clipboard.
    return json.dumps({'component':'Codex Sidebar 2','capturedAt':snapshot.get('updatedAt'),
        'provider':provider,'state':chain.get('state'),'health':chain.get('health'),
        'request':{k:latest.get(k) for k in ('timestamp','durationMs','status','outcome','servedModel','resolvedModel','requestedEffort','firstOutputMs','decodeTokPerSecond','retries')},
        'connectionEvents':chain.get('events',[])[-5:]},ensure_ascii=False,indent=2)


def chain_visible_content(snapshot,provider):
    """Only fields rendered on this provider's page may invalidate its view."""
    chains=snapshot.get('chains',{});chain=chains.get(provider,{})
    def pick(row,keys):return {key:row.get(key) for key in keys}
    metrics=('timestamp','status','outcome','durationMs','firstOutputMs','decodeTokPerSecond',
             'servedModel','resolvedModel','requestedEffort','retries')
    requests=chain.get('requests',[]);modules=snapshot.get('modules',{});tasks=modules.get('tasks',{})
    from sidebar_modules import provider_indicators
    completed=[pick(task,('id','title')) for task in tasks.get('tasks_done',[])
               if provider_indicators(provider,{**modules,'tasks':{'tasks_done':[task]}})[1]]
    identities={r.get('conversationId') for r in requests if r.get('conversationId')}
    linked=[pick(task,('id','innerId','title')) for group in tasks.values() if isinstance(group,list)
            for task in group if task.get('id') in identities or task.get('innerId') in identities]
    return [list(chains),chain.get('state'),chain.get('health'),chain.get('detailed'),chain.get('active'),
            [pick(row,('id','requestId','model','effort','requestedEffort','startedAt','retries','conversationId')) for row in requests],
            pick(chain.get('latest') or {},metrics),[pick(row,metrics) for row in chain.get('recent',[])[-4:]],
            chain.get('events',[])[-5:],completed,linked]


class WrappingLabel(tk.Label):
    """Wrap to allocated pixels, including after a resize, without a polling job."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.bind('<Configure>',self.fit_width,add='+')
    def fit_width(self,event):
        inset=sum(self.winfo_pixels(self.cget(key)) for key in ('padx','borderwidth','highlightthickness'))*2
        width=max(1,event.width-inset)
        if self.winfo_pixels(self.cget('wraplength'))!=width:self.configure(wraplength=width)


def matching_widgets(old,new):
    """Validate the entire shape before changing any visible widget."""
    if type(old) is not type(new) or not isinstance(old,(tk.Frame,tk.Label,RoundPanel,RoundButton)):return None
    before=old.winfo_children();after=new.winfo_children()
    if len(before)!=len(after):return None
    pairs=[(old,new)]
    for left,right in zip(before,after):
        children=matching_widgets(left,right)
        if children is None:return None
        pairs.extend(children)
    return pairs


def update_widgets(pairs):
    for old,new in pairs:
        if isinstance(old,tk.Label):
            keys=('text','font','foreground','background','anchor','justify')
            if not isinstance(old,WrappingLabel):keys+=('wraplength',)
            changes={key:new.cget(key) for key in keys
                     if str(old.cget(key))!=str(new.cget(key))}
            if changes:old.configure(**changes)
        elif isinstance(old,RoundButton):
            old.command=new.command  # Actions must follow the latest record, even if its title is unchanged.
            for key in ('title','font','disabled','selected','primary','outlined'):setattr(old,key,getattr(new,key))
            old.draw()
        if old.winfo_manager()=='pack' and new.winfo_manager()=='pack':
            before=old.pack_info();after=new.pack_info()
            changes={key:value for key,value in after.items() if key!='in' and str(before.get(key))!=str(value)}
            if changes:old.pack_configure(**changes)

class Details:
    BG='#faf9f8';INK='#292927';MUTED='#85847f'
    def __init__(self,section='tasks_running',provider='openai'):
        from native_badge import Win
        from manager_icons import prepare_identity,set_window_icons
        self.win=Win();prepare_identity();self.root=tk.Tk();self.root.withdraw();self.root.title('侧栏 · 状态中心')
        self.root.iconbitmap(str(ROOT/'codex-monochrome.ico'));self.root.update_idletasks()
        self.s=(self.win.u.GetDpiForWindow(self.root.winfo_id()) or 96)/96
        style=ttk.Style();style.theme_use('clam')
        self.root.geometry(f'{self.p(440)}x{min(self.p(560),self.root.winfo_screenheight()-self.p(75))}')
        self.root.minsize(self.p(380),self.p(360));self.root.configure(bg=self.BG)
        self.section=section;self.provider=provider;self.snapshot={};self.runtime_status={};self.sidebar_status={};self.update_status={};self.key=None;self.timer=None;self.request_stamp=time.time()
        self.refreshing=False;self.rendering=False;self.closed=False;self.view_identity=None;self.reveal_timer=None
        head=tk.Frame(self.root,bg=self.BG,padx=self.p(20),pady=self.p(15));head.pack(fill='x')
        title_row=tk.Frame(head,bg=self.BG);title_row.pack(fill='x')
        self.label(title_row,'状态中心',16,True).pack(side='left')
        self.version_badge=VersionBadge(title_row,'V—',self.font(10,True),self.s,width=54,height=24,radius=12)
        self.version_badge.pack(side='left',padx=(self.p(7),0))
        self.subtitle=tk.StringVar();self.label(head,'',11,variable=self.subtitle,muted=True).pack(fill='x',pady=(self.p(4),0))
        nav=tk.Frame(self.root,bg=self.BG,padx=self.p(12));nav.pack(fill='x')
        self.nav=[]
        for title,key in [('任务','tasks_running'),('链路','chain'),('额度','weekly_quota'),('诊断','diagnostics'),('服务','services'),('入口','shortcuts')]:
            b=RoundButton(nav,title,lambda k=key:self.select(k),self.font(11),self.s,width=1,outlined=True);b.pack(side='left',fill='x',expand=True,padx=self.p(2));self.nav.append((key,b))
        area=tk.Frame(self.root,bg=self.BG);area.pack(fill='both',expand=True,padx=self.p(16),pady=self.p(12))
        self.canvas=tk.Canvas(area,bg=self.BG,highlightthickness=0);self.scrollbar=SlimScrollbar(area,self.scroll,self.s);scroll=self.scrollbar
        scroll.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True);self.canvas.configure(yscrollcommand=scroll.set)
        self.body=tk.Frame(self.canvas,bg=self.BG);self.slot=self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.canvas.bind('<Configure>',self.resize_canvas)
        self.body.bind('<Configure>',lambda e:self.sync_scrollregion())
        self.root.bind('<MouseWheel>',self.mousewheel)
        self.root.bind('<Escape>',lambda e:self.close());self.root.protocol('WM_DELETE_WINDOW',self.close)
        from ui_presence import mark
        mark(self.root)
        self.refresh();set_window_icons(self.root)
        self.root.attributes('-alpha',0);self.root.deiconify();self.root.update_idletasks()
        self.reveal_timer=self.root.after_idle(self.reveal)
        self.root.bind('<FocusIn>',self.focus_refresh,add='+')
        from interaction_runtime import trace
        trace('details_ready',pid=os.getpid())
    def p(self,x):return round(x*self.s)
    def resize_canvas(self,event):
        self.canvas.itemconfigure(self.slot,width=event.width)
        self.sync_scrollregion()
    def sync_scrollregion(self,position=None):
        viewport=max(1,self.canvas.winfo_height());height=max(viewport,self.body.winfo_reqheight())
        region=(0,0,max(1,self.canvas.winfo_width()),height)
        if tuple(map(float,self.canvas.cget('scrollregion').split()))!=region:self.canvas.configure(scrollregion=region)
        if height==viewport:self.canvas.yview_moveto(0)
        elif position is not None:self.canvas.yview_moveto(max(0,min(position,(height-viewport)/height)))
    def scroll(self,*args):
        self.sync_scrollregion()
        if self.body.winfo_reqheight()>self.canvas.winfo_height():self.canvas.yview(*args)
    def mousewheel(self,event):
        self.scroll('scroll',-int(event.delta/120)*2,'units')
        return 'break'
    def reveal(self):
        self.reveal_timer=None
        if not self.closed:self.root.update_idletasks();self.root.attributes('-alpha',1)
    def font(self,n,bold=False):return (ui_family(),-self.p(n),'bold' if bold else 'normal')
    def label(self,parent,text,size=11,bold=False,muted=False,variable=None):
        return WrappingLabel(parent,text=text,textvariable=variable,font=self.font(size,bold),bg=parent.cget('bg'),fg=self.MUTED if muted else self.INK,anchor='w',justify='left',wraplength=self.p(285))
    def note(self,text):
        frame=tk.Frame(self.body,bg=self.BG,padx=self.p(12));frame.pack(fill='x',pady=(self.p(2),self.p(8)))
        self.label(frame,text,10,muted=True).pack(fill='x')
    def card(self,title=None,description=None):
        panel=RoundPanel(self.body,self.s,12);panel.pack(fill='x',pady=(0,self.p(9)));body=panel.body
        if title:self.label(body,title,12,True).pack(anchor='w',fill='x')
        if description:self.label(body,description,10,muted=True).pack(anchor='w',fill='x',pady=(self.p(5),0))
        return body
    def button(self,parent,text,fn,width=100):
        def safe():
            try:fn()
            except Exception as exc:messagebox.showerror('暂时无法打开',str(exc),parent=self.root)
        b=RoundButton(parent,text,safe,self.font(11),self.s,width=width);b.pack(anchor='w',pady=(self.p(5),0));return b
    def select(self,key):
        if self.section!=key:self.section=key;self.key=None
        self.render();self.canvas.yview_moveto(0)
    def focus_refresh(self,event):
        if event.widget!=self.root or self.refreshing or self.rendering or self.closed:return
        if self.timer is not None:self.root.after_cancel(self.timer)
        self.timer=self.root.after_idle(self.refresh)

    def refresh(self):
        if self.closed or self.refreshing:return
        if self.timer is not None:self.root.after_cancel(self.timer);self.timer=None
        if self.root.state()=='iconic':
            self.timer=self.root.after(10000,self.refresh);return
        self.refreshing=True
        try:self._refresh()
        finally:self.refreshing=False

    def _refresh(self):
        try:
            data=json.loads((ROOT/'details-snapshot.json').read_text(encoding='utf-8'))
            self.snapshot=data
        except (OSError,ValueError):pass
        for name,attribute in (('status.json','runtime_status'),('sidebar-status.json','sidebar_status')):
            try:
                value=json.loads((ROOT/name).read_text(encoding='utf-8'))
                setattr(self,attribute,value if isinstance(value,dict) else {})
            except (OSError,ValueError):
                setattr(self,attribute,{})
        try:
            value=json.loads((ROOT/'update-status.json').read_text(encoding='utf-8'))
            self.update_status=value if isinstance(value,dict) else {}
        except (OSError,ValueError):self.update_status={}
        try:
            request=json.loads((ROOT/'details-request.json').read_text(encoding='utf-8'))
            if request.get('at',0)>self.request_stamp:
                self.request_stamp=request['at'];self.section=request['section'];self.provider=request.get('provider','openai');self.key=None
                self.root.deiconify();self.root.lift();self.root.focus_force()
        except (OSError,ValueError,KeyError):pass
        try:
            self.render()
        except Exception:
            self.subtitle.set('更新暂不可用 · 保留上次内容，稍后重试')
        finally:
            # Task/chain pages benefit from faster updates; quota and
            # diagnostics are mostly static and should not reread the snapshot
            # every two seconds while open.
            cadence=2000 if self.section.startswith('tasks_') or self.section=='chain' else 5000
            if not self.closed:self.timer=self.root.after(cadence,self.refresh)
    def render(self):
        if self.closed or self.rendering:return
        self.rendering=True
        try:self.render_page()
        finally:self.rendering=False

    def render_page(self):
        # Only visible data invalidates this page; polling timestamps are not UI changes.
        def stable(value):
            if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if k not in ('updatedAt','lastAt')}
            if isinstance(value,list):return [stable(v) for v in value]
            return value
        self.update_version_badge()
        if self.section.startswith('tasks_'):
            content=[self.snapshot.get('modules',{}).get('tasks',{}),self.snapshot.get('modules',{}).get('taskError')]
        elif self.section=='chain':
            content=chain_visible_content(self.snapshot,self.provider)
        elif self.section=='weekly_quota':content=[self.snapshot.get('quota'),self.snapshot.get('quotaHistory')]
        elif self.section=='diagnostics':content=[self.snapshot.get('quota'),self.snapshot.get('quotaHistory'),self.snapshot.get('diagnostics'),self.runtime_status,self.sidebar_status]
        elif self.section=='services':content=self.snapshot.get('modules',{}).get('services',[])
        else:content=load_config().get('shortcuts',[])
        stale=time.time()-self.snapshot.get('updatedAt',0)>20
        # Durations change only at the displayed resolution.
        tick=int(time.time()/60) if self.section.startswith('tasks_') else int(time.time()/2) if self.section=='chain' and self.snapshot.get('chains',{}).get(self.provider,{}).get('requests') else int(time.time()/3600) if self.section in ('weekly_quota','diagnostics') else 0
        key=(self.section,self.provider,json.dumps(stable(content),ensure_ascii=False,sort_keys=True),stale,tick)
        if key==self.key:return
        position=self.canvas.yview()[0]
        previous=self.body
        staged=tk.Frame(self.canvas,bg=self.BG)
        # Lay out the replacement offscreen while the old page remains visible.
        staged.place(x=-100000,y=0,width=max(1,self.canvas.winfo_width()))
        self.body=staged
        try:
            if not self.snapshot:self.card('正在读取','打开侧栏后自动加载')
            elif self.section.startswith('tasks_'):self.tasks()
            elif self.section=='chain':self.chain()
            elif self.section=='weekly_quota':self.quota()
            elif self.section=='diagnostics':self.diagnostics_page()
            elif self.section=='services':self.services()
            else:self.shortcuts()
            pairs=matching_widgets(previous,staged) if self.view_identity==(self.section,self.provider) else None
            if pairs is not None:
                update_widgets(pairs);self.body=previous;staged.destroy()
                self.root.update_idletasks()
                self.sync_scrollregion(position)
            else:
                self.root.update_idletasks()
        except Exception:
            self.body=previous;staged.destroy()
            raise
        if pairs is None:
            staged.place_forget()
            self.canvas.itemconfigure(self.slot,window=staged,width=max(1,self.canvas.winfo_width()))
            staged.bind('<Configure>',lambda e:self.sync_scrollregion())
            self.root.update_idletasks();self.sync_scrollregion(position)
            previous.destroy()
        self.view_identity=(self.section,self.provider)
        self.key=key
        for section,b in self.nav:b.select(section==self.section or section=='tasks_running' and self.section.startswith('tasks_'))
        self.subtitle.set('数据已过期 · 请启动侧栏' if stale else '本机状态 · 自动更新')
    def update_version_badge(self):
        extra=self.snapshot.get('diagnostics') or {};local=extra.get('pluginVersion') or self.sidebar_status.get('pluginVersion') or '—'
        state=self.update_status.get('state')
        checked=self.update_status.get('checkedAt')
        if state=='update_available':
            try:
                from update_client import version_tuple
                pending=version_tuple(self.update_status.get('version'))>version_tuple(local)
            except Exception:pending=True
            label,color,bg=('待更新','#966719','#fff3d8') if pending else ('最新','#287a4b','#e8f6ed')
        elif state=='error':label,color,bg='检查异常','#a4423c','#fdebea'
        elif state in ('up_to_date','updated'):label,color,bg='最新','#287a4b','#e8f6ed'
        elif checked:label,color,bg='已检查','#5f625e','#eeefed'
        else:label,color,bg='未检查','#777672','#eeedea'
        self.version_badge.set(f'V{local}',fg=color,bg=bg)
    def tasks(self):
        modules=self.snapshot.get('modules',{});tasks=modules.get('tasks',{})
        bar=tk.Frame(self.body,bg=self.BG);bar.pack(fill='x',pady=(0,self.p(8)))
        for kind in ('tasks_pending','tasks_running','tasks_done'):
            count=len(tasks.get(kind,[]));value='—' if kind=='tasks_pending' and not count else str(count)
            b=RoundButton(bar,f'{MODULES[kind][0]} {value}',lambda k=kind:self.select(k),self.font(11),self.s,width=1,outlined=True);b.pack(side='left',fill='x',expand=True,padx=self.p(2));b.select(kind==self.section)
        if modules.get('taskError'):self.card('暂不可用',modules['taskError']);return
        rows=tasks.get(self.section,[])
        if not rows:
            if self.section=='tasks_pending':self.card('待处理状态暂不可确认','本地记录无法确认实时审批或补充信息请求。\n“—”表示待处理状态未知。')
            else:self.card('暂时没有'+MODULES[self.section][0]+'任务')
        for row in rows:
            elapsed=max(0,int((time.time()-(row.get('startedAt') or time.time()))/60))
            detail=('已运行 '+str(elapsed)+' 分钟') if self.section!='tasks_done' else '已完成 · 尚未查看'
            body=self.card(row['title'],detail)
            self.button(body,'打开会话 ↗',lambda r=row:open_thread(r['id']))
        self.note('仅显示本机会话，运行状态依据近期回合记录。\n旧的未结束记录不计入运行状态。')
        if tasks.get('unverified'):self.note(f"另有 {len(tasks['unverified'])} 条旧未结束记录，状态未确认")
    def chain(self):
        chains=self.snapshot.get('chains',{});bar=tk.Frame(self.body,bg=self.BG);bar.pack(fill='x',pady=(0,self.p(8)))
        for provider in chains:
            def choose(p=provider):self.provider=p;self.key=None;self.render()
            b=RoundButton(bar,'ChatGPT' if provider=='openai' else provider,choose,self.font(11),self.s,width=1,outlined=True);b.pack(side='left',fill='x',expand=True,padx=self.p(2));b.select(provider==self.provider)
        chain=chains.get(self.provider,{})
        health=chain.get('health') or [None,None]
        body=self.card('链路状态',health[1] or {'running':'会话运行中','output':'正在接收内容','waiting':'等待模型响应','idle':'当前空闲'}.get(chain.get('state'),'状态暂不可用'))
        self.button(body,'复制诊断摘要',self.copy_diagnostic,125)
        from sidebar_modules import provider_indicators
        modules=self.snapshot.get('modules',{})
        completed=modules.get('tasks',{}).get('tasks_done',[])
        for task in completed:
            sample={**modules,'tasks':{'tasks_done':[task]}}
            if provider_indicators(self.provider,sample)[1]:
                body=self.card('● 已完成待查看',task['title'])
                self.button(body,'查看会话 ↗',lambda t=task:open_thread(t['id']))
        requests=chain.get('requests',[])
        if chain.get('detailed'):self.label(self.body,f"在途请求 · {chain.get('active') if chain.get('active') is not None else '—'}",11,True).pack(anchor='w',fill='x',padx=self.p(12),pady=(self.p(3),self.p(8)))
        for r in requests:
            elapsed=max(0,int((time.time()*1000-r['startedAt'])/1000));text=f"{elapsed} 秒 · {r.get('effort') or r.get('requestedEffort') or '档位未提供'}"
            if r.get('retries'):text+=f" · 重试 {r['retries']} 次"
            body=self.card(f"#{r.get('requestId',r.get('id','—'))} · "+(r.get('model') or '模型未提供'),text)
            for group in self.snapshot.get('modules',{}).get('tasks',{}).values():
                for task in group:
                    if r.get('conversationId') and r['conversationId'] in (task.get('id'),task.get('innerId')):
                        self.label(body,task['title'],10,muted=True).pack(anchor='w',fill='x');self.button(body,'打开会话 ↗',lambda t=task:open_thread(t['id']))
                        break
        self.note('状态来源：Codex 本机会话')
        latest=chain.get('latest') or {}
        if latest:
            model=latest.get('servedModel') or latest.get('resolvedModel') or '模型未提供'
            body=self.card('最近模型 · '+model,(latest.get('requestedEffort') or '档位未提供')+' · '+recent_description(latest))
            if latest.get('servedModel') and latest.get('resolvedModel') and latest['servedModel']!=latest['resolvedModel']:self.label(body,'返回模型与路由模型不同',10,muted=True).pack(anchor='w',fill='x')
            prior=chain.get('recent',[])
            if len(prior)>1:
                before=prior[-2].get('servedModel') or prior[-2].get('resolvedModel')
                if before and before!=model:self.label(body,'上次模型：'+before,10,muted=True).pack(anchor='w',fill='x')
            self.label(body,speed_description(latest) or '速度数据暂不可用',10,muted=True).pack(anchor='w',fill='x',pady=self.p(4))
            if latest.get('retries'):self.label(body,f"该请求重试 {latest['retries']} 次",10,muted=True).pack(anchor='w',fill='x')
        recent=chain.get('recent',[])
        if recent:
            body=self.card('最近几次')
            for r in reversed(recent[-4:]):self.label(body,recent_description(r)+'\n'+(speed_description(r) or '速度未提供'),10,muted=True).pack(anchor='w',fill='x',pady=self.p(4))
        events=chain.get('events',[])
        if events:
            body=self.card('最近连接事件')
            for e in reversed(events[-5:]):self.label(body,dt.datetime.fromtimestamp(e['at']).strftime('%m/%d %H:%M:%S')+'  '+{'red':'未连接','amber':'状态读取异常','recovered':'已恢复'}.get(e['state'],e['state']),10,muted=True).pack(anchor='w',fill='x')
    def copy_diagnostic(self):
        self.root.clipboard_clear();self.root.clipboard_append(diagnostic(self.snapshot,self.provider));self.root.update();self.subtitle.set('诊断摘要已复制 · 不含密钥和正文')
    def quota(self):
        q=self.snapshot.get('quota') or {};cycle=self.snapshot.get('quotaHistory')
        self.card('周额度',f"剩余 {q.get('remaining','—')}%")
        if not cycle:self.card('开始记录后显示每日变化');return
        reset=dt.datetime.fromtimestamp(cycle['reset']);hours=max(0,int((cycle['reset']-time.time())/3600))
        self.card('距重置',f'{hours//24} 天 {hours%24} 小时 · {reset:%m/%d %H:%M}')
        from quota_accounting import quota_breakdown
        summary=quota_breakdown(cycle,q.get('remaining'),cycle['reset'])
        labels=summary['labels'];description=f"总 {labels['total']}% · 本机 {labels['local']}% · 非本机 {labels['other']}%"
        if labels['unknown']!='0':description+=f" · 来源待定 {labels['unknown']}%"
        body=self.card('本周期 · 消耗归因（估算）',description)
        for row in summary['days']:
            day=row['day']
            if day>dt.date.today():break
            labels=row['labels'];value=f"总 {labels['total']}% · 本机 {labels['local']}% · 非本机 {labels['other']}%" if row['total'] is not None else '暂无记录'
            if labels['unknown']!='0':value+=f" · 待定 {labels['unknown']}%"
            self.label(body,f'{day:%m/%d}     {value}',11,bold=day==dt.date.today(),muted=row['total'] is None).pack(anchor='w',fill='x',pady=self.p(5))
        self.card('记录范围',f"从 {dt.datetime.fromtimestamp(cycle['firstAt']):%m/%d %H:%M} 开始采样。\n本机按活动与用量增量估算；证据不足时保留来源待定，跨日缺少采样时保留日期待定位。"+(f"\n检测到 {cycle['corrections']} 次额度回调，已从消耗中扣减。" if cycle['corrections'] else ''))
    def diagnostics_page(self):
        snapshot=self.snapshot;quota=snapshot.get('quota') or {};cycle=snapshot.get('quotaHistory') or {};extra=snapshot.get('diagnostics') or {}
        runtime=self.runtime_status or {};sidebar=self.sidebar_status or {};usage=sidebar.get('usage') or {}
        chains=snapshot.get('chains') or {};gpt=chains.get('openai') or {};latest=gpt.get('latest') or {}
        unknown='未知'
        def value(obj,key,formatter=str):
            item=obj.get(key) if isinstance(obj,dict) else None
            return unknown if item is None or item=='' else formatter(item)
        def time_value(stamp,milliseconds=False):
            if not isinstance(stamp,(int,float)) or stamp<=0:return unknown
            if milliseconds:stamp/=1000
            return dt.datetime.fromtimestamp(stamp).strftime('%Y-%m-%d %H:%M:%S')
        def percent(item):return unknown if item is None else f'{item:g}%' if isinstance(item,(int,float)) else str(item)
        plans={'pro':'Pro','prolite':'Pro','plus':'Plus','team':'Team','enterprise':'Enterprise'}
        plan=plans.get(str(quota.get('planType','')).lower(),value(quota,'planType'))
        quota_read=extra.get('quotaRead') or ('失败' if runtime.get('readError') else '正常' if quota else unknown)
        usage_read=extra.get('usageRead') or ('失败' if sidebar.get('usageError') else '正常' if usage else unknown)
        self.card('套餐与周额度',f'套餐：{plan}\n周额度剩余：{percent(quota.get("remaining"))}\n读取状态：{quota_read}\n最近更新：{time_value(quota.get("updatedAt"))}')
        body=self.card('OpenCodex 用量',f'读取状态：{usage_read}\n来源：{value(usage,"source",str)}\n范围：{value(usage,"scope",str)}\n更新时间：{time_value(usage.get("updatedAt"))}\n部分数据：{value(usage,"partial",lambda x:"是" if x else "否")}')
        providers=usage.get('providers') if isinstance(usage,dict) else None
        gpt_usage=providers.get('openai') if isinstance(providers,dict) else None
        if isinstance(gpt_usage,dict):
            self.label(body,f"GPT 本机 Token：{value(gpt_usage,'totalTokens')} · 请求：{value(gpt_usage,'requests')}",10,muted=True).pack(anchor='w',fill='x',pady=(self.p(5),0))
        activity_source=extra.get('activitySource') or gpt.get('statusSource') or unknown
        self.card('GPT 活动来源',f'活动来源：{activity_source}\n状态来源：{value(gpt,"statusSource",str)}\n当前状态：{value(gpt,"state",str)}\n可用：{value(gpt,"available",lambda x:"是" if x else "否")}\n最近结果：{value(latest,"outcome",str)} · HTTP {value(latest,"status")}\n最近活动：{time_value(latest.get("timestamp"),True)}')
        boundary=extra.get('lastBoundarySample') or cycle.get('lastBoundaryAt') or sidebar.get('lastCompleteSample')
        self.card('额度采样',f'上次边界采样：{time_value(boundary)}\n本周期完整采样：{value(cycle,"completeSamples")}\n首次采样：{time_value(cycle.get("firstAt"))}\n最后采样：{time_value(cycle.get("lastAt"))}')
        self.card('下降归因',f'未分配下降：{percent(cycle.get("unallocated"))}\n其他来源下降：{percent(cycle.get("otherUsed"))}\n修正次数：{value(cycle,"corrections")}')
        plugin_version=extra.get('pluginVersion') or sidebar.get('pluginVersion') or runtime.get('pluginVersion') or unknown
        self.card('插件运行信息',f'插件版本：{plugin_version}\n快照版本：{value(snapshot,"version")}\n侧栏状态版本：{value(sidebar,"version")}\n刷新间隔：{value(sidebar,"refreshSeconds",lambda x:f"{x} 秒")}\n运行模式：{value(runtime,"mode",str)}\n当前可见：{value(runtime,"visible",lambda x:"是" if x else "否")}\n背景模式：{value(runtime,"background",str)}')
        self.note('诊断仅使用本地脱敏状态，缺少字段显示“未知”。\n不读取密钥、Cookie 或会话正文。')
    def services(self):
        rows=self.snapshot.get('modules',{}).get('services',[])
        if not rows:self.card('尚未配置服务','在侧栏管理中添加本机服务地址')
        for row in rows:
            body=self.card(row['label'],('● ' if row['state']=='online' else '○ ')+row['detail']+' · '+row['url'])
            def launch(r=row):local_url(r['url']);os.startfile(r['url'])
            self.button(body,'打开 ↗',launch)
        self.note('端口可连接表示进程正在监听，业务状态需进一步确认。')
    def shortcuts(self):
        for item in load_config().get('shortcuts',[]):
            body=self.card(item['label'],item.get('target') or None);self.button(body,'打开 ↗',lambda i=item:open_shortcut(i))
    def close(self):
        self.closed=True
        if self.timer:self.root.after_cancel(self.timer)
        if self.reveal_timer:self.root.after_cancel(self.reveal_timer)
        self.root.destroy()
    def run(self):self.root.mainloop()

class LinkEditor:
    def __init__(self,parent,config,changed):
        self.config=config;self.changed=changed;self.root=tk.Toplevel(parent);self.root.title('服务与快捷入口');self.root.geometry('600x420')
        self.root.configure(bg='#faf9f8');self.list=tk.Listbox(self.root,font=(ui_family(),11),relief='flat',highlightthickness=0)
        self.list.pack(fill='both',expand=True,padx=16,pady=16)
        bar=tk.Frame(self.root,bg='#faf9f8');bar.pack(fill='x',padx=16,pady=12)
        for title,command in [('添加服务',self.service),('添加目录',self.folder),('移除选中',self.remove)]:ttk.Button(bar,text=title,command=command).pack(side='left',padx=5)
        self.draw()
    def draw(self):
        self.list.delete(0,'end');self.keys=[]
        for key in ('services','shortcuts'):
            for i,row in enumerate(self.config.get(key,[])):
                self.keys.append((key,i));self.list.insert('end',('服务 · ' if key=='services' else '入口 · ')+row['label'])
    def service(self):
        url=simpledialog.askstring('本机服务','地址，例如 http://127.0.0.1:8000',parent=self.root)
        if not url:return
        try:local_url(url)
        except ValueError as exc:return messagebox.showerror('地址无效',str(exc),parent=self.root)
        label=simpledialog.askstring('服务名称','显示名称',parent=self.root)
        if label and len(self.config['services'])<8:self.config['services'].append({'label':label[:30],'url':url});self.changed();self.draw()
    def folder(self):
        path=filedialog.askdirectory(parent=self.root)
        if path and len(self.config['shortcuts'])<12:self.config['shortcuts'].append({'kind':'folder','label':Path(path).name[:30],'target':path});self.changed();self.draw()
    def remove(self):
        if self.list.curselection():
            key,index=self.keys[self.list.curselection()[0]];self.config[key].pop(index);self.changed();self.draw()

if __name__=='__main__':Details(sys.argv[1] if len(sys.argv)>1 else 'tasks_running',sys.argv[2] if len(sys.argv)>2 else 'openai').run()

