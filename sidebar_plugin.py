"""Configurable native sidebar companion: data adapters, rows and management."""
import ctypes as C
from ctypes import wintypes as W
import datetime as dt
import json
import os
import queue
import threading
import time
from native_badge import Badge, rgb, write_runtime_json
from sidebar_data import CONFIG, load_config, save_config, fetch_usage, item_usage, compact_tokens, sync_provider_items
from tooltip_card import render_usage_card, upload

from version import PLUGIN_VERSION

def breathing_level(now):
    """Return a restrained, smooth activity alpha for the running dot."""
    phase=(float(now)%2.0)/2.0
    # Periodic smootherstep easing keeps the first and second derivatives
    # continuous at both endpoints. This avoids visible alpha steps on the
    # tiny antialiased dot while leaving a short, natural dwell at each end.
    x=phase*2 if phase<.5 else (phase-.5)*2
    eased=x*x*x*(x*(x*6-15)+10)
    smooth=eased if phase<.5 else 1-eased
    return .60+.40*smooth


class SidebarPlugin(Badge):
    per_pixel_alpha=True
    hover_gutter=3  # Extra DIP on each side; keep the icon's screen position.
    hover_vertical_inset=2

    def __init__(self,test_host=None):
        from sidebar_activity import ProviderActivity
        self.config=load_config()
        self.activity=ProviderActivity(history_path=None if test_host else CONFIG.with_name('connection-history.json'),enabled=self.config.get('realtimeMetricsEnabled',True))
        from sidebar_telemetry import Telemetry
        self.telemetry=Telemetry()
        from sidebar_modules import Modules,QuotaHistory
        self.modules=Modules();self.quota_history=QuotaHistory();self.next_snapshot=0
        from sidebar_options import CompactRows,QuotaAlert
        self.compact_rows=CompactRows();self.quota_alert=QuotaAlert(CONFIG.with_name('quota-alert-state.json'))
        from reset_signals import ResetSignals
        self.reset_signals=ResetSignals(CONFIG.with_name('reset-signals.json'));self.reset_signals.nextPoll=max(self.reset_signals.nextPoll,time.time()+15);self.reset_signal_results=queue.Queue()
        self.activity_frame=False
        self.config_stamp=CONFIG.stat().st_mtime_ns
        self.config_error=None
        self.usage,self.usage_error=self.restore_last_usage() if not test_host else (None,None)
        self.usage_busy=False
        self.usage_results=queue.Queue()
        self.next_usage=0
        self.week_usage=None;self.week_error=None;self.month_usage=None;self.month_error=None;self.month_busy=False;self.next_month=time.time()+30;self.month_results=queue.Queue()
        self.gpt_active_state=None;self.gpt_active_count=0;self.gpt_boundary_due=0;self.gpt_boundary_active=False;self.last_gpt_boundary_refresh=0;self.gpt_active_since=0
        self.boundary_busy=False;self.boundary_results=queue.Queue();self.last_complete_sample=0
        self.pending_settlement=None
        self.next_maintenance=0
        self.next_update_check=time.time()+60;self.update_busy=False;self.update_check_seconds=21600;self.update_results=queue.Queue()
        cycle=self.quota_history.view() or {};self.last_quota_remaining=cycle.get('lastRemaining')
        self.hover_item=None
        super().__init__(test_host)
        self.refresh_usage()
        # Monthly history is secondary to the badge and is fetched by the
        # heartbeat after the startup grace period.
        self.refresh_reset_signals()
        self.check_for_update()
        self.power_ready=True
        self.sync_power_mode()

    @property
    def items(self):
        return [i for i in self.config['items'] if i.get('enabled',True) and i['id'] not in self.compact_rows.hidden]

    def item_height(self,item):
        if item['type']=='weekly_quota':
            from quota_styles import quota_height
            return quota_height(self.config.get('quotaStyle','01'))+2*self.hover_vertical_inset
        return 30+2*self.hover_vertical_inset

    def row_top(self,index):
        return sum(self.item_height(i)+i.get('gapAfter',0) for i in self.items[:index])

    def index_at(self,position,default=-1):
        # Negative gaps overlap row hit areas. Choose the nearest centre so
        # the lower icon is not intercepted by the preceding row's rectangle.
        candidates=[]
        for index,item in enumerate(self.items):
            top=self.row_top(index);height=self.item_height(item)
            if top<=position<top+height:candidates.append((abs(position-top-height/2),-index))
        return -min(candidates)[1] if candidates else default

    def layout(self):
        if not self.hwnd or not self.win.u.IsWindow(self.hwnd) or self.win.u.IsIconic(self.target):
            return
        if not self.items:
            self.win.u.ShowWindow(self.hwnd,0)
            return
        rect=W.RECT()
        if not self.win.u.GetClientRect(self.target,C.byref(rect)):
            return
        self.scale=(self.win.u.GetDpiForWindow(self.target) or 96)/96
        s=self.scale
        geometry=self.client_geometry(rect,s)
        if geometry != self.geometry or not self.win.u.IsWindowVisible(self.hwnd):
            if not self.win.u.SetWindowPos(self.hwnd,W.HWND(0),*geometry,0x10|0x40):
                return
            self.geometry=geometry
            self.layout_events+=1
            self.request_repaint()

    def client_geometry(self,rect,scale):
        """One DIP layout contract for child and owned-popup window surfaces."""
        height=round((self.row_top(len(self.items)-1)+self.item_height(self.items[-1]))*scale)
        x=round((6+self.config.get('horizontalOffset',0))*scale)-round(self.hover_gutter*scale)
        return (x,max(0,rect.bottom-round(self.config['bottomOffset']*scale)-height),
                round((34+2*self.hover_gutter)*scale),height)

    def request_repaint(self,rect=None):
        """Coalesce content changes; ULW surfaces do not rely on WM_PAINT.

        Parent backdrop changes are handled by the compositor without waking us.
        """
        if not self.hwnd or time.monotonic()<getattr(self,'alpha_retry_due',0):return
        box=None if rect is None else (rect.left,rect.top,rect.right,rect.bottom)
        if getattr(self,'alpha_paint_pending',False):
            old=self.alpha_dirty
            self.alpha_dirty=None if box is None or old is None else (min(box[0],old[0]),min(box[1],old[1]),max(box[2],old[2]),max(box[3],old[3]))
            return
        self.alpha_dirty=box;self.alpha_paint_pending=True
        if not self.win.u.PostMessageW(self.controller,0x8007,0,0):
            self.alpha_paint_pending=False;raise C.WinError(C.get_last_error())

    def row_usage(self,item):
        if self.usage_error:
            return None
        snapshot=self.usage
        if not isinstance(snapshot,dict):return None
        row=snapshot.get('providers',{}).get(item['provider'])
        if isinstance(row,dict):return row
        # A successful snapshot with no provider row means confirmed zero.
        # Keep the last successful snapshot visible while sleeping or while a
        # wake-up refresh is in flight; only an explicit query failure shows —.
        return {'totalTokens':0,'requests':0,'reportedRequests':0,
                'inputTokens':0,'outputTokens':0,'cachedInputTokens':0}

    @staticmethod
    def restore_last_usage():
        try:
            state=json.loads(CONFIG.with_name('sidebar-status.json').read_text(encoding='utf-8'))
            usage=state.get('usage')
            usage=usage if isinstance(usage,dict) and isinstance(usage.get('providers'),dict) else None
            return usage,state.get('usageError')
        except (OSError,ValueError,TypeError):return None,None

    def refresh(self):
        # A complete boundary sample already reads quota; do not launch a
        # second app-server process while that transaction is in flight.
        if getattr(self,'boundary_busy',False):
            self.next_refresh=time.time()+5;return
        return super().refresh()

    def refresh_usage(self):
        if self.usage_busy or getattr(self,'boundary_busy',False):
            if getattr(self,'boundary_busy',False):self.next_usage=time.time()+5
            return
        self.next_usage=time.time()+self.config['refreshSeconds']
        if self.test_host:
            return
        self.usage_busy=True
        def worker():
            try:
                self.usage_results.put((fetch_usage(),None))
            except Exception as exc:
                self.usage_results.put((None,type(exc).__name__))
            self.win.u.PostMessageW(self.controller,0x8003,0,0)
        try:threading.Thread(target=worker,daemon=True).start()
        except Exception:
            self.usage_busy=False;self.next_usage=time.time()+5
            self.usage_error='后台读取暂不可用'


    def refresh_month(self):
        if self.month_busy or self.test_host:return
        self.month_busy=True;self.next_month=time.time()+300
        def worker():
            results=[]
            for period in ('30d','7d'):
                try:results.extend((fetch_usage(period),None))
                except Exception as exc:results.extend((None,type(exc).__name__))
            self.month_results.put(tuple(results))
            self.win.u.PostMessageW(self.controller,0x8004,0,0)
        try:threading.Thread(target=worker,daemon=True).start()
        except Exception:
            self.month_busy=False;self.next_month=time.time()+5
            self.month_error=self.week_error='后台读取暂不可用'


    def paint_icon(self,dc,rect,s):
        brush=self.win.g.CreateSolidBrush(self.bg)
        self.win.u.FillRect(dc,C.byref(rect),brush)
        self.win.g.DeleteObject(brush)
        self.win.g.SetViewportOrgEx.argtypes=[W.HDC,C.c_int,C.c_int,C.c_void_p]
        self.win.g.SetViewportOrgEx.restype=W.BOOL
        origin=W.POINT()
        self.win.g.GetViewportOrgEx.argtypes=[W.HDC,C.POINTER(W.POINT)]
        self.win.g.GetViewportOrgEx(dc,C.byref(origin))
        gutter=round(round(self.hover_gutter*self.scale)*s/self.scale)
        self.win.g.SetViewportOrgEx(dc,origin.x+gutter,origin.y,None)
        try:self.paint_rows(dc,s)
        finally:self.win.g.SetViewportOrgEx(dc,origin.x,origin.y,None)

    def paint_rows(self,dc,s):
        self.win.g.SetBkMode(dc,1)
        for index,item in enumerate(self.items):
            y=round((self.row_top(index)+self.hover_vertical_inset)*s)
            clip=getattr(self,'paint_clip',None)
            if clip and (y>=clip[1] or y+round(self.item_height(item)*s)<=clip[0]):continue
            if item['type'] not in ('weekly_quota','opencodex_today'):
                from sidebar_styles import draw_module
                from sidebar_modules import module_value
                draw_module(self.win,dc,s,y,item['type'],module_value(item['type'],self.modules.view(),self.config),tuple((self.bg >> (8*n)) & 255 for n in range(3)))
                continue
            if item['type']=='weekly_quota':
                origin=W.POINT()
                self.win.g.GetViewportOrgEx.argtypes=[W.HDC,C.POINTER(W.POINT)]
                self.win.g.GetViewportOrgEx(dc,C.byref(origin))
                self.win.g.SetViewportOrgEx(dc,origin.x,origin.y+y,None)
                from quota_styles import draw_quota
                valid=self.valid();five_hour=self.data.get('fiveHour') if valid else None
                five_remaining=five_hour.get('remaining') if isinstance(five_hour,dict) and five_hour.get('resetsAt',0)>time.time() else None
                reset_signal=self.reset_signals.view();signal_state='confirmed' if reset_signal.get('accountConfirmation') else 'explicit' if reset_signal.get('state')=='signal' else None
                draw_quota(self.win,dc,s,0,self.data['remaining'] if valid else None,self.config.get('quotaStyle','01'),five_remaining,
                           tuple((self.bg >> (8*n)) & 255 for n in range(3)),signal_state)
                self.win.g.SetViewportOrgEx(dc,origin.x,origin.y,None)
                continue
            from sidebar_styles import draw_token
            usage=self.row_usage(item)
            value=compact_tokens(usage['totalTokens'] if usage else None)
            label='GPT' if item['provider']=='openai' else item['label']
            cached=getattr(self,'cached_indicator_map',{}).get(item['provider'])
            running,completed=(cached[0] if cached else self.indicators(item['provider']))
            # Keep the running marker visibly green throughout the breathing
            # cycle.  At 25% the antialiased 3.6-DIP dot blended into the
            # sidebar background and looked absent in normal screenshots.
            tick=getattr(self,'paint_time',None)
            level=breathing_level(tick if tick is not None else time.monotonic()) if running else 0
            health=cached[1] if cached else self.chain_health(item['provider'])
            color=(76,157,114) if running else {'red':(194,76,69),'amber':(190,137,49)}.get(health[0],(76,157,114))
            if health[0] and not running:level=1
            draw_token(self.win,dc,s,y,label,value,self.config['style'],level if self.config.get('activityEnabled',True) else None,color,completed,
                       background=tuple(self.bg>>shift&255 for shift in (0,8,16)))

    def indicators(self,provider):
        if not self.config.get('activityEnabled',True):return False,False
        from sidebar_modules import provider_indicators
        # The persisted thread snapshot can lag behind the live Codex turn:
        # some sessions keep an old rollout mtime while their turn is still
        # inProgress.  The authenticated local activity endpoint is the
        # authoritative short lived signal for an actually active request.
        activity=self.activity.details(provider)
        snapshot=self.modules.view()
        # OpenCodex 2.86 removed provider-activity.  When its replacement is
        # unavailable and the background module snapshot is stale, refresh the
        # existing read-only Codex session cache directly. This preserves the
        # real inProgress signal for the current Codex window without treating
        # completed usage logs as an active request.
        # Native subagents are read by the Modules background worker. Keep
        # task lists parent-only, but merge the already-cached running workers
        # for provider indicators. The paint path must never scan SQLite or
        # rollout files.
        running_subagents={'tasks_running':(snapshot.get('subagentTasks') or {}).get('tasks_running',[])}
        merged=dict(snapshot)
        tasks={k:list(v) for k,v in (snapshot.get('tasks') or {}).items()}
        existing={t.get('id') for t in tasks.get('tasks_running',[])}
        tasks['tasks_running']=tasks.get('tasks_running',[])+[t for t in running_subagents['tasks_running'] if t.get('id') not in existing]
        merged['tasks']=tasks; snapshot=merged
        if snapshot.get('taskError') or time.time()-snapshot.get('updatedAt',0)>20:
            # Keep the UI path read-only when the background snapshot is stale;
            # the next Modules refresh will replace it without blocking paint.
            pass
        return provider_indicators(provider,snapshot,
                                   live_requests=activity.get('requests',()),
                                   active=self.activity.active(provider))

    def gpt_running_count(self):
        snapshot=self.modules.view();requests=self.activity.details('openai').get('requests',())
        ids={r.get('conversationId') for r in requests if r.get('provider')=='openai' and r.get('conversationId')}
        tasks=snapshot.get('tasks',{}).get('tasks_running',[]) if not snapshot.get('taskError') else []
        count=0
        for task in tasks:
            model=task.get('model') or '';route=model.split('/',1)[0] if '/' in model else task.get('provider')
            if route=='openai' or task.get('id') in ids or task.get('innerId') in ids:count+=1
        return max(count,self.activity.count('openai') or 0)

    def chain_health(self,provider):
        # Optional request telemetry must not turn missing extension support into a link failure.
        if any(r.get('retries',0)>0 for r in self.activity.details(provider)['requests']):return ('amber','本次请求发生重试')
        return self.telemetry.view(provider)['health']

    def live_speed_metrics(self,provider,state):
        if state not in ('waiting','output','running'):return None,None
        from live_metrics import request_metrics
        metrics=request_metrics(self.activity.details(provider))
        return (metrics['first'],metrics['output']) if metrics else (None,None)

    def refresh_visible_metrics(self,now):
        """Repaint a visible Token card even when the pointer never moves."""
        if not getattr(self,'config',{}).get('realtimeMetricsEnabled',True):return
        if not self.tooltip_active or not self.hover_item:return
        if self.selected_item()['type']!='opencodex_today':return
        if now<getattr(self,'next_metrics_paint',0):return
        self.next_metrics_paint=now+.25
        self.update_tooltip()

    def set_hover_tile(self,item):
        if item==getattr(self,'hover_tile',None):return
        self.hover_tile=item
        # Only pointer entry/exit changes the backdrop; never animate/reposition
        # the child or add another timer for this small hover affordance.
        self.request_repaint()

    def poll_hover(self):
        if getattr(self,'foreground_mode',True) is False:
            self.set_hover_tile(None);return
        if not self.test_host and self.win.u.GetForegroundWindow() not in (self.target,self.tooltip):
            self.set_hover_tile(None);return
        # Do not reorder the child window on every 75 ms animation frame.
        # Chromium can repaint adjacent native UI when its sibling z-order is
        # repeatedly touched; the low-frequency heartbeat and WinEvent path
        # still repair stacking when the host actually changes it.
        now=time.monotonic()
        if now>=getattr(self,'next_indicator_check',0):
            states=[]
            if self.config.get('activityEnabled',True):
                for item in self.items:
                    if item['type']=='opencodex_today':
                        states.append((item['provider'],self.indicators(item['provider']),self.chain_health(item['provider'])))
            self.cached_indicator_states=tuple(states)
            self.cached_indicator_map={provider:(indicators,health) for provider,indicators,health in self.cached_indicator_states}
            self.next_indicator_check=now+.5
        states=self.cached_indicator_states
        active=any(indicators[0] for _,indicators,health in states)
        self.set_hover_cadence(75 if active else 150)
        changed=states!=getattr(self,'last_activity_states',None)
        self.last_activity_states=states
        if (active or self.activity_frame or changed) and self.hwnd and self.win.u.IsWindowVisible(self.hwnd) and not self.win.u.IsIconic(self.target):
            if changed:self.request_repaint()
            else:
                for index,item in enumerate(self.items):
                    if item['type']=='opencodex_today' and self.cached_indicator_map.get(item['provider'],((False,False),(None,None)))[0][0]:
                        top=self.row_top(index)
                        r=W.RECT(round((24+self.hover_gutter)*self.scale),round(top*self.scale),round((34+self.hover_gutter)*self.scale),round((top+17+self.hover_vertical_inset)*self.scale))
                        self.request_repaint(r)
        self.activity_frame=active
        # Resolve the actual icon under the pointer before handling its popup.
        # Moving onto the popup must remove the icon's hover wash immediately.
        item=None
        if self.hwnd and self.win.u.IsWindow(self.hwnd):
            point,bounds=W.POINT(),W.RECT()
            if self.win.u.GetCursorPos(C.byref(point)) and self.win.u.GetWindowRect(self.hwnd,C.byref(bounds)):
                position=(point.y-bounds.top)/self.scale
                index=self.index_at(position)
                if bounds.left<=point.x<bounds.right and bounds.top<=point.y<bounds.bottom and index>=0:item=self.items[index]['id']
        self.set_hover_tile(item)
        if getattr(self,'menu_active',False):return
        if self.config.get('realtimeMetricsEnabled',True):self.refresh_visible_metrics(now)
        if self.tooltip_active and self.tooltip:
            cursor,tip=W.POINT(),W.RECT()
            self.win.u.GetCursorPos(C.byref(cursor));self.win.u.GetWindowRect(self.tooltip,C.byref(tip))
            if self.win.u.IsIconic(self.target) or self.win.u.GetForegroundWindow() not in (self.target,self.tooltip):
                self.track_hover(False);return
            if tip.left<=cursor.x<tip.right and tip.top<=cursor.y<tip.bottom:
                self.hover_departed=None
                index=self.hover_hit((cursor.x-tip.left)/self.scale,(cursor.y-tip.top)/self.scale)
                if index!=getattr(self,'hover_focus',None):
                    self.hover_focus=index;self.last_tooltip_text=None;self.update_tooltip()
                return
        if self.hwnd and self.win.u.IsWindow(self.hwnd):
            if item is None and self.tooltip_active:
                if getattr(self,'hover_departed',None) is None:self.hover_departed=time.monotonic()
                if time.monotonic()-self.hover_departed<.4:return
            else:self.hover_departed=None
            if item!=self.hover_item:
                self.hover_focus=None;self.hover_offset=0
                self.track_hover(False)
                self.hover_item=item
                self.last_tooltip_text=None
        if self.hover_item is None:
            self.track_hover(False)
            return
        super().poll_hover()

    def hover_hit(self,x,y):
        return next((i for i,(rect,action) in enumerate(getattr(self,'hover_actions',[])) if rect[0]<=x<rect[2] and rect[1]<=y<rect[3]),None)

    def hover_action(self,index):
        action=self.hover_actions[index][1]
        try:
            from sidebar_details import open_thread,open_shortcut
            if action[0]=='thread':open_thread(action[1])
            elif action[0]=='shortcut':open_shortcut(action[1])
            elif action[0]=='url':
                from sidebar_modules import local_url
                import os
                local_url(action[1]);os.startfile(action[1])
            self.track_hover(False)
        except Exception:
            self.log_error()

    def selected_item(self):
        return next((i for i in self.items if i['id']==self.hover_item),self.items[-1])

    def show_item_card(self):
        """Show the hovered module card without opening Status Center."""
        if not self.items or not self.hwnd or not self.tooltip:return
        point=W.POINT();bounds=W.RECT()
        if not (self.win.u.GetCursorPos(C.byref(point)) and self.win.u.GetWindowRect(self.hwnd,C.byref(bounds))):return
        position=(point.y-bounds.top)/self.scale
        index=self.index_at(position,len(self.items)-1)
        item=self.items[index]
        if self.hover_item!=item['id']:
            self.hover_item=item['id'];self.hover_focus=None;self.hover_offset=0;self.last_tooltip_text=None
        self.update_tooltip(force=True);self.position_tooltip();self.win.u.ShowWindow(self.tooltip,4)
        self.tooltip_active=True;self.hover_since=time.monotonic()

    def update_tooltip(self,force=False):
        if not force and not self.tooltip_active:return
        if not self.tooltip or not self.items:
            return
        item=self.selected_item()
        if item['type']=='weekly_quota':
            self.hover_actions=[]
            from hover_panel import render_quota_chart
            cycle=self.quota_history.view() or {};cache_cycle={k:v for k,v in cycle.items() if k not in ('lastAt','lastRemaining')};valid=self.valid();focus=getattr(self,'hover_focus',None)
            remaining=self.data['remaining'] if valid else None;reset=self.data.get('resetsAt') if valid else cycle.get('reset')
            five_hour=self.data.get('fiveHour') if valid else None
            plan_type=self.data.get('planType') if valid else None
            if not (isinstance(five_hour,dict) and five_hour.get('resetsAt',0)>time.time()):five_hour=None
            reset_signal=self.reset_signals.view()
            age=reset_signal.get('syncAgeSeconds')
            if isinstance(age,(int,float)):
                reset_signal={**reset_signal,'syncAgeSeconds':min(3600,max(0,int(age//60)*60))}
            countdown_tick=int(time.time()/60) if isinstance((reset_signal.get('explicitSignal') or {}).get('targetAt'),(int,float)) else 0
            key=(item['id'],remaining,reset,str(five_hour),plan_type,str(cache_cycle),str(reset_signal),countdown_tick,focus,self.scale,dt.date.today())
            if key!=self.last_tooltip_text:
                self.tooltip_surface,self.quota_actions=render_quota_chart(self.scale,remaining,reset,cycle,focus,dt.date.today().isoformat(),five_hour,plan_type,reset_signal,sample_at=self.data.get('updatedAt') if valid else None)
                upload(self.win,self.tooltip,self.tooltip_surface);self.last_tooltip_text=key
                if self.tooltip_active:self.position_tooltip()
            self.hover_actions=getattr(self,'quota_actions',[])
            return
        if item['type']!='opencodex_today':
            from hover_panel import render_panel
            rows=[];snapshot=self.modules.view();kind=item['type']
            if kind.startswith('tasks_'):
                from hover_panel import topic_row
                rows=[topic_row(r,'completed' if kind=='tasks_done' else 'running') if kind!='tasks_pending' else (r['title'],'等待确认',('thread',r['id'])) for r in snapshot.get('tasks',{}).get(kind,[])]
                if not rows:rows=[('状态暂不可确认' if kind=='tasks_pending' else '暂无会话',snapshot.get('taskError') or '',None)]
            elif kind=='services':
                rows=[(r['label'],r['detail'],('url',r['url'])) for r in snapshot.get('services',[])]
            elif kind=='shortcuts':
                rows=[(r['label'],'打开',('shortcut',r)) for r in self.config.get('shortcuts',[])]
            else:
                cycle=self.quota_history.view() or {}
                rows=[('剩余周额度',f"{self.data['remaining']:g}%" if self.valid() else '—',None)]
                if self.valid():rows.append(('下次重置',dt.datetime.fromtimestamp(self.data['resetsAt']).strftime('%m/%d %H:%M'),None))
                rows.extend((day[5:].replace('-','/'),f"已观测 {r['points']:g} 个百分点",None) for day,r in sorted(cycle.get('days',{}).items()))
            offset=min(getattr(self,'hover_offset',0),max(0,len(rows)-6));self.hover_offset=offset
            shown=rows[offset:offset+6]
            key=(item['id'],str(rows),offset,getattr(self,'hover_focus',None),self.scale)
            if key!=self.last_tooltip_text:
                self.tooltip_surface,self.hover_actions=render_panel(self.scale,item['label'],shown,getattr(self,'hover_focus',None),f'{offset+1}–{min(offset+6,len(rows))} / {len(rows)} · 滚轮翻阅' if len(rows)>6 else '')
                upload(self.win,self.tooltip,self.tooltip_surface);self.last_tooltip_text=key
                if self.tooltip_active:self.position_tooltip()
            return
        self.hover_actions=[]
        row=self.row_usage(item)
        month=item_usage(self.month_usage,item['provider'],max_age=900) if not self.month_error else None
        week=item_usage(self.week_usage,item['provider'],max_age=900) if not self.week_error else None
        notes=[]
        activity_status=None
        running,completed=self.indicators(item['provider'])
        telemetry=self.telemetry.view(item['provider'])
        realtime=self.config.get('realtimeMetricsEnabled',True)
        if realtime and self.config.get('activityEnabled',True):
            state=self.activity.state(item['provider'])
            if running:activity_status=state if state in ('waiting','output') else 'running'
            health=self.chain_health(item['provider'])
            # The recent-request line already names a failed or incomplete
            # outcome. Keep only health notes that add specific information
            # such as retry, authentication, rate limit, or a failure streak.
            if health[0] and health[1] not in ('最近请求失败','最近请求未完成'):
                notes.append(health[1])
        from live_metrics import request_metrics,running_sample
        # Take one snapshot for both the chosen request's state and its metrics.
        activity_details=self.activity.details(item['provider']) if realtime and self.config.get('activityEnabled',True) else {}
        live=request_metrics(activity_details) if realtime else None
        live_first=live_output=None
        previous=telemetry.get('previous')
        recent_row=telemetry['latest']
        samples=telemetry.get('samples',telemetry.get('recent',[]))
        if live:
            activity_status=live['state'];live_first=live['first'];live_output=live['output']
            previous=next((r for r in reversed(samples)
                           if r.get('outcome')=='completed' and r.get('decodeTokPerSecond',0)>0
                           and r.get('timestamp',0)<live['request']['startedAt']),None)
        elif activity_status:
            # Stock OpenCodex may only expose completed request logs. A turn
            # can still be running tools after such a request has ended.
            sample=running_sample(item['provider'],telemetry,self.modules.view())
            if sample:
                recent_row=sample
                from sidebar_telemetry import previous_speed_description
                first=sample.get('firstOutputMs')
                live_first=f'{first/1000:.1f}s' if first is not None else '未提供'
                live_output=previous_speed_description(sample)
                previous=next((r for r in reversed(samples)
                               if r.get('outcome')=='completed' and r.get('decodeTokPerSecond',0)>0
                               and r.get('timestamp',0)<sample['timestamp']),None)
                notes.append('本次模型请求已结束，展示最终统计' if activity_details.get('supportsLive') else '展示本轮最近请求；流中速度未提供')
            else:
                live_output='未提供'
        key=(item['id'],str(row),str(week),str(month),str(telemetry),str(previous),tuple(notes),activity_status,live_first,live_output,running,completed,self.scale,getattr(self,'hover_focus',None),str(self.modules.view().get('tasks',{})))
        if telemetry['trend']:
            width=getattr(self,'chart_width',240)
            top=(130 if row else 90)+8;step=(width-36)/7
            self.hover_actions=[((18+i*step,top,18+(i+1)*step,top+73),('day',i)) for i in range(7)]
        self.hover_actions.extend(getattr(self,'token_links',[]) if getattr(self,'token_link_item',None)==item['id'] else [])
        if key!=self.last_tooltip_text:
            from sidebar_telemetry import recent_description,speed_description,previous_speed_description
            self.tooltip_surface=render_usage_card(self.scale,item['label'],row,month,notes,week=week,trend=telemetry['trend'],recent=recent_description(recent_row),speed=speed_description(recent_row),previous_speed=previous_speed_description(previous),activity_status=activity_status,live_first=live_first,live_output=live_output,hover_day=getattr(self,'hover_focus',None))
            if telemetry['trend']:
                width=self.tooltip_surface.width/self.scale;self.chart_width=width;step=(width-36)/7
                self.hover_actions=[((18+i*step,top,18+(i+1)*step,top+73),('day',i)) for i in range(7)]
            self.token_links=[];self.token_link_item=item['id']
            from sidebar_modules import provider_indicators
            from hover_panel import topic_row
            snapshot=self.modules.view();links=[]
            for kind in ('tasks_done','tasks_running'):
                for task in snapshot.get('tasks',{}).get(kind,[]):
                    sample={**snapshot,'tasks':{kind:[task]}}
                    if any(provider_indicators(item['provider'],sample)):
                        links.append(topic_row(task,'completed' if kind=='tasks_done' else 'running'))
            if links:
                from hover_panel import render_panel,TOPIC_PANEL_WIDTH
                from PIL import Image
                focus=getattr(self,'hover_focus',None);base=7 if telemetry['trend'] else 0
                panel,actions=render_panel(self.scale,'',links[:3],focus-base if focus is not None and focus>=base else None,width=TOPIC_PANEL_WIDTH)
                top_y=self.tooltip_surface.height
                panel_y=max(0,top_y-round(4*self.scale))
                combined=Image.new('RGBA',(max(panel.width,self.tooltip_surface.width),panel_y+panel.height))
                combined.alpha_composite(self.tooltip_surface);combined.alpha_composite(panel,(0,panel_y))
                self.tooltip_surface=combined
                self.token_links=[((r[0],r[1]+panel_y/self.scale,r[2],r[3]+panel_y/self.scale),action) for r,action in actions]
                self.hover_actions=[a for a in self.hover_actions if a[1][0]=='day']+self.token_links
            upload(self.win,self.tooltip,self.tooltip_surface)
            self.last_tooltip_text=key
            if self.tooltip_active:self.position_tooltip()

    def position_tooltip(self):
        # Compute the selected row's final anchor before touching the window.
        # The base implementation centers on the whole badge; calling it first
        # moved a visible popup twice on every live metrics/hover repaint.
        tip,badge,host=W.RECT(),W.RECT(),W.RECT()
        self.win.u.GetWindowRect(self.tooltip,C.byref(tip));self.win.u.GetWindowRect(self.hwnd,C.byref(badge))
        self.win.u.GetWindowRect(self.target,C.byref(host))
        index=self.items.index(self.selected_item())
        center=badge.top+round((self.row_top(index)+self.item_height(self.items[index])/2)*self.scale)
        width,height=tip.right-tip.left,tip.bottom-tip.top;gap=round(8*self.scale)
        x=min(badge.right+gap,host.right-width-gap)
        y=max(host.top+gap,min(center-height//2,host.bottom-height-gap))
        if (tip.left,tip.top)!=(x,y):
            self.win.u.SetWindowPos(self.tooltip,W.HWND(-1),x,y,0,0,0x1|0x10)

    def reload_config(self):
        stamp=CONFIG.stat().st_mtime_ns
        if stamp==self.config_stamp:return
        try:
            candidate=load_config()
        except Exception as exc:
            self.config_error=str(exc);return
        self.track_hover(False)
        self.config=candidate;self.config_stamp=stamp;self.config_error=None
        self.hover_tile=None
        self.activity.set_enabled(self.config.get('realtimeMetricsEnabled',True))
        self.hover_item=None;self.last_tooltip_text=None;self.geometry=None
        self.layout();self.request_repaint()
        self.next_usage=0

    def context_menu(self,hwnd,lparam):
        if not self.items:return
        self.track_hover(False)
        # The menu process can become the manager; its lifetime is not menu visibility.
        now=time.monotonic()
        if now-getattr(self,'last_context_open',0)<.45:return
        self.last_context_open=now
        import subprocess,sys
        from pathlib import Path
        point=W.POINT();self.win.u.GetCursorPos(C.byref(point))
        bounds=W.RECT();self.win.u.GetWindowRect(self.hwnd,C.byref(bounds))
        position=(point.y-bounds.top)/self.scale
        index=self.index_at(position,len(self.items)-1)
        from interaction_runtime import trace
        trace('menu_launch')
        self.context_process=subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')),str(CONFIG.with_name('sidebar_context.py')),str(point.x),str(point.y),self.items[index]['id']],creationflags=subprocess.CREATE_NO_WINDOW)

    def window_event(self,hook,event,hwnd,obj,child,thread,stamp):
        super().window_event(hook,event,hwnd,obj,child,thread,stamp)
        if event==3 and getattr(self,'power_ready',False) and not self.closing:
            # Queue on the UI message loop rather than refresh inside the WinEvent callback.
            self.win.u.PostMessageW(self.controller,0x8006,0,0)

    def sync_power_mode(self):
        foreground=self.win.u.GetForegroundWindow()
        from ui_presence import is_companion
        active=bool(self.test_host or is_companion(foreground) or (self.target and self.win.u.IsWindow(self.target)
                    and not self.win.u.IsIconic(self.target)
                    and foreground in (self.target,self.tooltip)))
        interactive=bool(active and (self.test_host or foreground in (self.target,self.tooltip)))
        if interactive!=getattr(self,'interaction_mode',None):
            self.interaction_mode=interactive
            if interactive:self.set_hover_cadence(150)
            else:
                self.win.u.KillTimer(self.controller,2);self.hover_timer_ms=None
                self.set_hover_tile(None);self.track_hover(False);self.activity_frame=False
        if active==getattr(self,'foreground_mode',None):return active
        self.foreground_mode=active
        self.win.u.SetTimer(self.controller,1,1000 if active else 10000,None)
        if active:
            # Keep the breathing animation smooth without waking the paint
            # path at 20 Hz. The dot itself is still redrawn as a small clip.
            self.next_indicator_check=0;self.last_tooltip_text=None
            # Refresh live state on return, but retain valid history/usage deadlines.
            if time.monotonic()-getattr(self,'last_resume_poll',0)>=2:
                self.modules.next_poll=0;self.telemetry.next_poll=0
                self.last_resume_poll=time.monotonic()
            self.next_snapshot=0
            self.win.u.PostMessageW(self.controller,0x113,1,0)
        else:
            self.set_hover_tile(None)
            self.track_hover(False);self.activity_frame=False
        return active

    def set_hover_cadence(self,milliseconds):
        if milliseconds!=getattr(self,'hover_timer_ms',None):
            self.win.u.SetTimer(self.controller,2,milliseconds,None)
            self.hover_timer_ms=milliseconds

    def detail_snapshot_interval(self):
        # Keep a ten-second heartbeat for consumers of the sanitized file.
        # Opening Details publishes immediately and a visible window gets 2s.
        self.win.u.FindWindowW.argtypes=[W.LPCWSTR,W.LPCWSTR]
        self.win.u.FindWindowW.restype=W.HWND
        hwnd=self.win.u.FindWindowW('TkTopLevel','侧栏 · 状态中心')
        if hwnd and not self.win.u.IsIconic(hwnd):
            from ui_presence import is_companion
            if is_companion(hwnd):return 2
        return 10

    def monitor_gpt_boundary(self):
        """Refresh account quota only at local GPT activity boundaries.

        Other providers never trigger this path.  The delayed idle edge avoids
        treating short tool/request gaps as separate sessions, while the
        minimum interval bounds local app-server and CLI process churn.
        """
        if self.test_host:return
        count=self.gpt_running_count();running=count>0
        now=time.monotonic()
        if self.gpt_active_state is None:
            self.gpt_active_state=running;self.gpt_active_count=count;self.gpt_active_since=now if running else 0;return
        if running!=self.gpt_active_state:
            self.gpt_active_state=running
            self.gpt_active_count=count
            self.gpt_active_since=now if running else 0
            self.gpt_boundary_due=now if running else now+8
            self.gpt_boundary_active=True
        elif running and self.gpt_active_since and now-self.gpt_active_since>=600 and now-self.last_gpt_boundary_refresh>=600:
            self.gpt_boundary_due=now;self.gpt_boundary_active=True
        elif running:self.gpt_active_count=count
        pending=self.pending_settlement
        if pending and not running and now>=pending['due']:
            self.pending_settlement=None;self.refresh_complete_sample(True,settlement=True)
        if self.gpt_boundary_due and now>=self.gpt_boundary_due and now-self.last_gpt_boundary_refresh>=20:
            self.gpt_boundary_due=0;self.last_gpt_boundary_refresh=now
            active=self.gpt_boundary_active;self.gpt_boundary_active=False
            self.refresh_complete_sample(active)

    def refresh_complete_sample(self,local_active,settlement=False):
        """Fetch quota and local GPT usage as one committed sample."""
        if self.boundary_busy or self.busy or self.usage_busy:
            self.gpt_boundary_due=time.monotonic()+2;self.gpt_boundary_active=self.gpt_boundary_active or bool(local_active);return
        if time.monotonic()-self.last_complete_sample<10:return
        self.boundary_busy=True
        def worker():
            try:
                from quota_badge import fetch_quota
                quota=fetch_quota();usage=fetch_usage();error=None
            except Exception as exc:quota=usage=None;error=type(exc).__name__
            self.boundary_results.put((quota,usage,bool(local_active),bool(settlement),error))
            self.win.u.PostMessageW(self.controller,0x8009,0,0)
        try:threading.Thread(target=worker,daemon=True).start()
        except Exception:self.boundary_busy=False

    def refresh_reset_signals(self):
        # The existing heartbeat also consumes a result if its wake-up was lost.
        self.apply_reset_signal_result()
        if not self.reset_signals.begin_refresh():return
        def worker():
            try:data=self.reset_signals.fetcher();error=None
            except Exception as exc:data=None;error=type(exc).__name__
            self.reset_signal_results.put((data,error))
            try:self.win.u.PostMessageW(self.controller,0x800A,0,0)
            except Exception:pass  # The heartbeat will consume the queued result.
        try:threading.Thread(target=worker,daemon=True).start()
        except Exception as exc:self.reset_signals.finish_refresh(error=type(exc).__name__)

    def apply_reset_signal_result(self):
        try:data,error=self.reset_signal_results.get_nowait()
        except queue.Empty:return
        if self.reset_signals.finish_refresh(data,error):
            try:
                self.last_tooltip_text=None;self.request_repaint();self.update_tooltip();self.publish_details()
            except Exception:self.log_error()

    def check_for_update(self):
        if not hasattr(self,'update_results') or not hasattr(self,'win'):return
        if self.test_host or getattr(self,'update_busy',False) or time.time()<getattr(self,'next_update_check',0):return
        try:
            from update_client import load_config
            update_config=load_config()
            self.update_check_seconds=update_config['checkSeconds'] if update_config else 21600
        except Exception:
            self.update_check_seconds=21600
        self.next_update_check=time.time()+self.update_check_seconds;self.update_busy=True
        def worker():
            try:
                from update_client import check
                self.update_results.put(check())
            except Exception as exc:self.update_results.put({'state':'error','error':type(exc).__name__})
            self.win.u.PostMessageW(self.controller,0x800B,0,0)
        try:threading.Thread(target=worker,daemon=True).start()
        except Exception:self.update_busy=False

    def heartbeat(self):
        # Release checks are independent of the visible animation and should
        # continue at a very low cadence while the host is in background mode.
        # All network work still runs on a worker thread.
        if not self.test_host:self.check_for_update()
        if not self.sync_power_mode():
            if not self.target or not self.win.u.IsWindow(self.target):self.find_target()
            return
        self.ensure_child_front()
        if not self.test_host:self.telemetry.poll()
        if not self.test_host:
            self.modules.poll(self.config)
        if not self.test_host and self.config.get('activityEnabled',True) and self.config.get('realtimeMetricsEnabled',True):self.activity.poll()
        if not self.test_host:self.refresh_reset_signals()
        if self.config.get('activityEnabled',True):self.monitor_gpt_boundary()
        try:self.reload_config()
        except OSError:self.config_error='配置文件暂不可读，保留上次有效配置'
        if time.time()>=self.next_usage and not self.usage_busy:self.refresh_usage()
        if time.time()>=self.next_month and not self.month_busy:self.refresh_month()
        if not self.test_host and time.time()>=self.next_maintenance:
            self.next_maintenance=time.time()+6*3600
            try:
                from sidebar_maintenance import maintain
                threading.Thread(target=maintain,daemon=True).start()
            except Exception:pass
        if self.compact_rows.update(self.config,self.modules.view()):
            self.track_hover(False);self.hover_item=None;self.last_tooltip_text=None;self.geometry=None
        retry=getattr(self,'alpha_retry_due',0)
        if retry and time.monotonic()>=retry:
            self.alpha_retry_due=0;self.request_repaint()
        super().heartbeat()
        # Legacy mode reuses the existing heartbeat/log worker. No live timer:
        # repaint only when a completed-request log actually changes.
        if not self.config.get('realtimeMetricsEnabled',True):
            signature=getattr(self.telemetry,'signature',None)
            if signature!=getattr(self,'legacy_tooltip_signature',None):
                self.legacy_tooltip_signature=signature
                if self.tooltip_active:self.update_tooltip()
        if self.valid():
            remaining=self.data.get('remaining')
            if self.reset_signals.dismiss_confirmation_after_use(remaining):
                self.last_tooltip_text=None;self.request_repaint()
            if self.last_quota_remaining is not None:
                if self.reset_signals.observe_account(self.last_quota_remaining,remaining,self.data.get('updatedAt')):
                    self.last_tooltip_text=None;self.request_repaint()
            self.last_quota_remaining=remaining
        if not self.test_host and self.valid() and self.quota_alert.due(self.config,self.data):
            import subprocess,sys
            from pathlib import Path
            process=getattr(self,'alert_process',None)
            if process is None or process.poll() is not None:
                self.alert_process=subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')),str(CONFIG.with_name('sidebar_options.py')),str(self.data['remaining']),str(self.config['quotaAlertThreshold'])],creationflags=subprocess.CREATE_NO_WINDOW)
                self.quota_alert.mark(self.config,self.data)
        if not self.test_host:
            if self.valid():
                local=item_usage(self.usage,'openai',max_age=max(180,3*self.config['refreshSeconds'])) if not self.usage_error else None
                if local is not None:
                    local={**local,'date':self.usage.get('date'),'updatedAt':self.usage.get('updatedAt')}
                self.quota_history.observe(self.data,local,bool(self.gpt_active_state),False)
            if time.time()>=self.next_snapshot:
                self.publish_details();self.next_snapshot=time.time()+self.detail_snapshot_interval()
        module_visual=str({k:v for k,v in self.modules.view().items() if k!='updatedAt'})
        if module_visual!=getattr(self,'last_module_visual',None):
            self.last_module_visual=module_visual
            if self.hwnd:self.request_repaint()
        visual=tuple((i['id'],compact_tokens((self.row_usage(i) or {}).get('totalTokens'))) for i in self.items if i['type']=='opencodex_today')
        if visual!=getattr(self,'last_usage_visual',None):
            self.last_usage_visual=visual
            if self.hwnd:self.request_repaint()
        signal=self.reset_signals.view();tick=int(time.time()/60) if isinstance((signal.get('explicitSignal') or {}).get('targetAt'),(int,float)) else 0
        if tick!=getattr(self,'last_reset_countdown_tick',None):
            self.last_reset_countdown_tick=tick
            if self.tooltip_active and self.hover_item and self.selected_item()['type']=='weekly_quota':self.update_tooltip()

    def write_status(self):
        super().write_status()
        status={'version':1,'items':self.items,'quotaStyle':self.config.get('quotaStyle'),'usage':self.usage,'usageError':self.usage_error,'configError':self.config_error,
                'refreshSeconds':self.config['refreshSeconds'],'boundaryBusy':self.boundary_busy,'lastCompleteSample':self.last_complete_sample,
                'activitySource':getattr(self.activity,'source',None),'realtimeMetricsEnabled':self.config.get('realtimeMetricsEnabled',True),'pluginVersion':PLUGIN_VERSION}
        if status!=getattr(self,'last_plugin_status',None):
            path=CONFIG.with_name('test-sidebar-status.json' if self.test_host else 'sidebar-status.json')
            if write_runtime_json(path,status):self.last_plugin_status=status

    def window_proc(self,hwnd,msg,wp,lp):
        if msg==0x800A:
            self.apply_reset_signal_result()
            return 0
        if msg==0x800B:
            try:
                result=self.update_results.get_nowait();self.update_busy=False
                if result.get('state')=='update_available':
                    import subprocess,sys
                    from pathlib import Path
                    subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')),str(CONFIG.with_name('update_prompt.py'))],creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception:self.update_busy=False
            return 0
        if msg==0x8009:
            try:
                quota,usage,active,settlement,error=self.boundary_results.get_nowait();self.boundary_busy=False
                if not error:
                    before=self.quota_history.view().get('lastRemaining') if self.quota_history.view() else None
                    self.accept_quota(quota);self.usage,self.usage_error=usage,None
                    local=item_usage(usage,'openai',max_age=max(180,3*self.config['refreshSeconds']))
                    if local is not None:local={**local,'date':usage.get('date'),'updatedAt':usage.get('updatedAt')}
                    self.quota_history.observe(quota,local,active,True,settlement);self.last_complete_sample=time.monotonic()
                    if active and not settlement and before is not None and quota.get('remaining')==before and not self.gpt_active_state:
                        self.pending_settlement={'due':time.monotonic()+60}
                    self.request_repaint();self.update_tooltip();self.publish_details();self.write_status()
            except Exception:self.boundary_busy=False;self.log_error()
            return 0
        if msg==0x8008:
            try:
                self.config_stamp=-1
                self.reload_config()
                self.write_status()
            except Exception:self.log_error()
            return 0
        if msg==0x8007:
            from sidebar_transparency import present
            box=getattr(self,'alpha_dirty',None);self.alpha_paint_pending=False
            if self.closing:return 0
            try:
                present(self,box);self.alpha_retry_due=0;self.alpha_failures=0
            except Exception:
                # A transient layered-window/GDI failure must not leave a
                # successful quota refresh visually stuck at the old value.
                self.alpha_surface_key=None
                self.alpha_failures=getattr(self,'alpha_failures',0)+1
                self.alpha_retry_due=time.monotonic()+min(30,.5*2**min(self.alpha_failures-1,6))
                self.log_error()
            return 0
        if msg==0xF and hwnd==self.hwnd:
            from native_badge import PAINTSTRUCT
            ps=PAINTSTRUCT();self.win.u.BeginPaint(hwnd,C.byref(ps))
            self.win.u.EndPaint(hwnd,C.byref(ps))
            return 0
        if msg==0x8006:
            if getattr(self,'power_ready',False):self.sync_power_mode()
            return 0
        if hwnd==self.tooltip:
            if msg==0x202:
                x=C.c_short(lp & 65535).value/self.scale;y=C.c_short((lp>>16)&65535).value/self.scale
                index=self.hover_hit(x,y)
                if index is not None and self.hover_actions[index][1][0]!='day':self.hover_action(index)
                return 0
            if msg==0x20A:
                delta=C.c_short((wp>>16)&65535).value
                self.hover_offset=max(0,getattr(self,'hover_offset',0)+(-1 if delta>0 else 1))
                self.hover_focus=None;self.last_tooltip_text=None;self.update_tooltip();return 0
        # Handle right-click explicitly: DefWindowProc forwards context messages
        # to the foreign parent, which can swallow them once our manager closes.
        if msg in (0x204,0x205,0x7B) and hwnd in (self.hwnd,self.tooltip):
            from interaction_runtime import trace
            trace("right_click",hwnd=hwnd,message=msg)
            if msg==0x205 and getattr(self,'right_press_handled',False):
                self.right_press_handled=False;return 0
            if msg==0x204:self.right_press_handled=True
            point=W.POINT();self.win.u.GetCursorPos(C.byref(point))
            self.context_menu(hwnd,(point.x & 0xffff)|((point.y & 0xffff)<<16))
            return 0
        if msg==0x8004:
            try:
                self.month_usage,self.month_error,self.week_usage,self.week_error=self.month_results.get_nowait();self.month_busy=False
                self.update_tooltip()
            except Exception:self.log_error()
            return 0
        if msg==0x8003:
            try:
                data,error=self.usage_results.get_nowait()
                self.usage,self.usage_error,self.usage_busy=data,error,False
                if data and not self.test_host:
                    try:
                        updated=sync_provider_items(self.config,data)
                        if updated!=self.config:
                            self.config=updated;save_config(updated);self.config_stamp=CONFIG.stat().st_mtime_ns
                            self.geometry=None
                    except Exception:self.config_error='供应商模块同步暂不可用'
                self.request_repaint()
                self.update_tooltip();self.write_status()
            except Exception:self.log_error()
            return 0
        if msg==0x8005:
            self.refresh_usage();self.refresh_month();self.refresh();self.telemetry.next_trend=0;self.telemetry.next_poll=0;return 0
        if msg==0x202 and hwnd==self.hwnd:
            from interaction_runtime import trace
            trace("left_click",hwnd=hwnd)
            try:self.show_item_card()
            except Exception:
                trace("sidebar_show_failed");self.log_error()
            return 0
        if msg==0x203 and hwnd==self.hwnd:
            from interaction_runtime import trace
            trace("double_click",hwnd=hwnd)
            try:self.open_details()
            except Exception:
                trace("details_open_failed");self.log_error()
            return 0
        return super().window_proc(hwnd,msg,wp,lp)

    def publish_details(self):
        chains={}
        for item in self.config['items']:
            if item['type']!='opencodex_today':continue
            provider=item['provider'];chains[provider]={**self.telemetry.view(provider),**self.activity.details(provider),'state':('running' if self.indicators(provider)[0] else 'unknown' if self.modules.view().get('taskError') else 'idle'),'active':self.activity.count(provider),'statusSource':'codex_sessions','health':self.chain_health(provider)}
        return write_runtime_json(CONFIG.with_name('details-snapshot.json'),{'version':3,'updatedAt':time.time(),'chains':chains,'modules':self.modules.view(),'quota':self.data if self.valid() else None,'quotaHistory':self.quota_history.view(),'resetSignals':self.reset_signals.view(),'diagnostics':{'pluginVersion':PLUGIN_VERSION,'quotaRead':'正常' if self.valid() else '不可用','usageRead':'正常' if not self.usage_error and self.usage else '不可用','activitySource':getattr(self.activity,'source',None) or 'codex_sessions','lastBoundarySample':self.quota_history.view().get('lastBoundaryAt') if self.quota_history.view() else None}})

    def open_details(self):
        if not self.items:return
        self.track_hover(False)
        import subprocess,sys
        from pathlib import Path
        point=W.POINT();bounds=W.RECT();self.win.u.GetCursorPos(C.byref(point));self.win.u.GetWindowRect(self.hwnd,C.byref(bounds))
        position=(point.y-bounds.top)/self.scale
        index=self.index_at(position,len(self.items)-1)
        item=self.items[index];section='chain' if item['type']=='opencodex_today' else item['type']
        self.publish_details()
        self.next_snapshot=time.time()+2
        from sidebar_modules import atomic_json
        atomic_json(CONFIG.with_name('details-request.json'),{'at':time.time(),'section':section,'provider':item.get('provider','openai')})
        process=getattr(self,'details_process',None)
        if process is not None and process.poll() is None:
            from interaction_runtime import wake
            wake(self.win,process.pid)
            return
        from interaction_runtime import trace
        trace('details_launch',section=section)
        self.details_process=subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')),str(CONFIG.with_name('sidebar_details.py')),section,item.get('provider','openai')],creationflags=subprocess.CREATE_NO_WINDOW)


if __name__=='__main__':
    try:SidebarPlugin().run()
    except Exception:
        import traceback
        CONFIG.with_name('sidebar-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        raise
